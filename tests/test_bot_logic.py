from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from poke_env.battle.move_category import MoveCategory

from bot.competitive_bot import CompetitiveShowdownBot
from bot.team_repo import TeamRepository


@dataclass
class DummyMove:
    id: str
    base_power: int = 0
    category: MoveCategory = MoveCategory.STATUS
    priority: int = 0
    accuracy: int | bool = True
    type: str | None = None
    force_switch: bool = False
    self_switch: bool = False
    heal: tuple[int, int] | None = None
    drain: tuple[int, int] | None = None
    recoil: tuple[int, int] | None = None
    damage: int | None = None
    z_move_power: int | None = None
    expected_hits: int | None = 1


@dataclass
class DummyPokemon:
    species: str
    current_hp_fraction: float = 1.0
    ability: str | None = None
    item: str | None = None
    level: int = 100
    gen: int = 9
    status: object | None = None
    fainted: bool = False
    tera_type: str | None = None
    is_terastallized: bool = False
    types: tuple[str, ...] = ("Normal",)
    base_stats: dict[str, int] | None = None
    stats: dict[str, int] | None = None
    boosts: dict[str, int] | None = None
    moves: dict[str, object] | None = None
    name: str | None = None
    base_species: str | None = None

    def __post_init__(self) -> None:
        self.base_stats = dict(self.base_stats or {})
        self.stats = dict(self.stats or {})
        self.boosts = dict(self.boosts or {})
        self.moves = dict(self.moves or {})
        if self.name is None:
            self.name = self.species
        if self.base_species is None:
            self.base_species = self.species

    def damage_multiplier(self, move: object) -> float:
        return 1.0


def make_bot(repository: object | None = None, format_id: str = "gen9ou") -> CompetitiveShowdownBot:
    bot = CompetitiveShowdownBot.__new__(CompetitiveShowdownBot)
    bot.repository = repository or SimpleNamespace(infer_opponent_sets=lambda *args, **kwargs: [])
    bot.selected_team = SimpleNamespace(format_id=format_id)
    bot._switch_history = {}
    bot._last_seen_active = {}
    return bot


class BotLogicTests(unittest.TestCase):
    def test_switch_loop_penalty_reduces_repeat_switch_scores(self) -> None:
        bot = make_bot()
        battle = SimpleNamespace(
            battle_tag="battle-1",
            turn=20,
            side_conditions={},
            opponent_active_pokemon=DummyPokemon("Primarina", current_hp_fraction=1.0),
        )
        repeated = DummyPokemon("Landorus-Therian", current_hp_fraction=1.0)
        fresh = DummyPokemon("Great Tusk", current_hp_fraction=1.0)

        bot._switch_history["battle-1"] = [(16, "landorustherian"), (18, "landorustherian")]
        bot._best_move_damage = lambda *args, **kwargs: 10.0
        bot._best_counter_damage = lambda *args, **kwargs: 8.0
        bot._entry_hazard_penalty = lambda *args, **kwargs: 0.0
        bot._pokemon_stat = lambda pokemon, stat: 200 if stat == "spe" else 100
        bot._estimated_speed = lambda *args, **kwargs: 100

        repeated_score = bot._score_switch(battle, repeated)
        fresh_score = bot._score_switch(battle, fresh)

        self.assertGreater(fresh_score, repeated_score)
        self.assertGreaterEqual(fresh_score - repeated_score, 40.0)

    def test_move_scoring_prioritizes_ko_damage_over_chip_and_status(self) -> None:
        bot = make_bot()
        battle = SimpleNamespace(
            can_tera=False,
            can_mega_evolve=False,
            can_z_move=False,
            can_dynamax=False,
        )
        active = DummyPokemon("Gholdengo", current_hp_fraction=0.8)
        opponent = DummyPokemon("Primarina", current_hp_fraction=1.0)
        chip = DummyMove("shadowball", base_power=80, category=MoveCategory.SPECIAL, accuracy=100, type="Ghost")
        finisher = DummyMove("makeitrain", base_power=120, category=MoveCategory.SPECIAL, accuracy=100, type="Steel")
        status = DummyMove("toxic", category=MoveCategory.STATUS, accuracy=True)

        def fake_estimate_damage(attacker, defender, move, battle, candidate_set=None):
            if move.id == "makeitrain":
                return 1.0
            if move.id == "shadowball":
                return 0.35
            return 0.0

        bot._estimate_damage = fake_estimate_damage
        bot._best_counter_damage = lambda *args, **kwargs: 0.0
        bot._estimated_speed = lambda *args, **kwargs: 50
        bot._pokemon_stat = lambda pokemon, stat: 150 if stat == "spe" else 100
        bot._should_tera = lambda *args, **kwargs: False

        chip_score, _ = bot._score_move(battle, active, opponent, chip)
        finisher_score, _ = bot._score_move(battle, active, opponent, finisher)
        status_score, _ = bot._score_move(battle, active, opponent, status)

        self.assertGreater(finisher_score, chip_score)
        self.assertGreater(finisher_score, status_score)
        self.assertGreater(chip_score, status_score)

    def test_move_scoring_accepts_scalar_heal_values(self) -> None:
        bot = make_bot()
        battle = SimpleNamespace(
            can_tera=False,
            can_mega_evolve=False,
            can_z_move=False,
            can_dynamax=False,
            opponent_side_conditions={},
            side_conditions={},
            opponent_team={},
        )
        active = DummyPokemon("Gholdengo", current_hp_fraction=0.4)
        opponent = DummyPokemon("Primarina", current_hp_fraction=1.0)
        heal_move = DummyMove("recover", category=MoveCategory.STATUS, accuracy=True, heal=0.5)

        bot._estimate_damage = lambda *args, **kwargs: 0.0
        bot._best_counter_damage = lambda *args, **kwargs: 10.0
        bot._estimated_speed = lambda *args, **kwargs: 50
        bot._pokemon_stat = lambda pokemon, stat: 150 if stat == "spe" else 100
        bot._should_tera = lambda *args, **kwargs: False

        score, _ = bot._score_move(battle, active, opponent, heal_move)

        self.assertGreater(score, 0.0)

    def test_repository_filters_opponent_candidates_by_revealed_details(self) -> None:
        repo = TeamRepository.__new__(TeamRepository)
        repo.load_species_sets = lambda format_id: {
            "Zapdos": [
                {
                    "ability": "Pressure",
                    "item": "Heavy-Duty Boots",
                    "nature": "Timid",
                    "moves": ["Discharge", "Roost", "Heat Wave", "Hurricane"],
                },
                {
                    "ability": "Static",
                    "item": "Heavy-Duty Boots",
                    "nature": "Timid",
                    "moves": ["Discharge", "Roost", "Heat Wave", "Hurricane"],
                },
                {
                    "ability": "Pressure",
                    "item": "Heavy-Duty Boots",
                    "nature": "Timid",
                    "moves": ["Thunderbolt", "Roost", "Heat Wave", "Hurricane"],
                },
            ]
        }

        matches = repo.infer_opponent_sets(
            "gen9ou",
            "Zapdos",
            ability="Pressure",
            item="Heavy-Duty Boots",
            revealed_moves={"Discharge", "Roost"},
        )

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["ability"], "Pressure")
        self.assertEqual(matches[0]["moves"], ["Discharge", "Roost", "Heat Wave", "Hurricane"])

    def test_bot_picks_candidate_with_most_matching_revealed_moves(self) -> None:
        candidates = [
            {
                "ability": "Pressure",
                "item": "Heavy-Duty Boots",
                "nature": "Timid",
                "evs": {"spe": 252},
                "ivs": {"atk": 0},
                "level": 100,
                "moves": ["Discharge", "Roost"],
            },
            {
                "ability": "Pressure",
                "item": "Heavy-Duty Boots",
                "nature": "Timid",
                "evs": {"spe": 252},
                "ivs": {"atk": 0},
                "level": 100,
                "moves": ["Discharge", "Roost", "Heat Wave", "Hurricane"],
            },
        ]
        repo = SimpleNamespace(
            infer_opponent_sets=lambda *args, **kwargs: candidates,
        )
        bot = make_bot(repo)
        pokemon = DummyPokemon(
            "Zapdos",
            ability="Pressure",
            item="Heavy-Duty Boots",
            moves={"discharge": object(), "roost": object(), "heatwave": object()},
            base_stats={"hp": 90, "atk": 90, "def": 85, "spa": 125, "spd": 90, "spe": 100},
        )

        inferred = bot._infer_opponent_set(None, pokemon)

        self.assertEqual(inferred.moves, ["Discharge", "Roost", "Heat Wave", "Hurricane"])
        self.assertEqual(inferred.ability, "Pressure")
        self.assertEqual(inferred.item, "Heavy-Duty Boots")


if __name__ == "__main__":
    unittest.main()




