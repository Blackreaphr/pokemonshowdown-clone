from __future__ import annotations

from typing import Any

from poke_env.battle.move import Move
from poke_env.battle.move_category import MoveCategory
from poke_env.battle.pokemon import Pokemon
from poke_env.player.player import Player

from .move_policy import (
    HAZARD_MOVES,
    best_counter_damage as policy_best_counter_damage,
    best_move_damage as policy_best_move_damage,
    entry_hazard_penalty as policy_entry_hazard_penalty,
    is_physical_attacker as policy_is_physical_attacker,
    score_move as policy_score_move,
)
from .opponent_inference import InferredCandidate, OpponentInference, computed_stat
from .showdown_tools import to_id
from .switch_policy import SwitchPolicy
from .team_repo import SelectedTeam, TeamRepository

CandidateSet = InferredCandidate


class CompetitiveShowdownBot(Player):
    def __init__(self, repository: TeamRepository, selected_team: SelectedTeam, *args: Any, **kwargs: Any):
        kwargs.setdefault("battle_format", selected_team.format_id)
        kwargs.setdefault("team", selected_team.packed)
        super().__init__(*args, **kwargs)
        self.repository = repository
        self.selected_team = selected_team
        self._opponent_inference = OpponentInference(repository, selected_team.format_id)
        self._switch_history: dict[str, list[tuple[int, str]]] = {}
        self._last_seen_active: dict[str, tuple[int, str]] = {}

    def choose_move(self, battle):
        self._track_switch_history(battle)
        if battle.force_switch or not battle.available_moves:
            return self._best_switch_order(battle)

        active = battle.active_pokemon
        opponent = battle.opponent_active_pokemon
        move_scores = []
        for move in battle.available_moves:
            score, flags = self._score_move(battle, active, opponent, move)
            move_scores.append((score, move, flags))

        best_move_score, best_move, flags = max(move_scores, key=lambda item: item[0])

        best_switch_score = float("-inf")
        if battle.available_switches and not battle.trapped:
            best_switch_score = self._best_switch_score(battle)

        offensive_pressure = self._best_move_damage(active, opponent)
        defensive_risk = self._best_counter_damage(battle, opponent, active)
        switch_policy = self._switch_policy_for_battle(battle)
        decision = switch_policy.decision(
            best_switch_score,
            best_move_score,
            active=active,
            opponent=opponent,
            turn=getattr(battle, "turn", 0),
            offensive_pressure=offensive_pressure,
            defensive_risk=defensive_risk,
            trapped=getattr(battle, "trapped", False),
        )
        if decision.should_switch:
            return self._best_switch_order(battle)

        order_kwargs = {}
        if getattr(battle, "can_tera", False) and flags.get("tera"):
            order_kwargs["terastallize"] = True
        if getattr(battle, "can_mega_evolve", False) and flags.get("mega"):
            order_kwargs["mega"] = True
        if getattr(battle, "can_z_move", False) and flags.get("zmove"):
            order_kwargs["z_move"] = True
        if getattr(battle, "can_dynamax", False) and flags.get("dynamax"):
            order_kwargs["dynamax"] = True
        return self.create_order(best_move, **order_kwargs)

    def teampreview(self, battle):
        members = list(battle.team.values())
        if isinstance(battle.teampreview_opponent_team, dict):
            opponent_team = list(battle.teampreview_opponent_team.values())
        else:
            opponent_team = list(battle.teampreview_opponent_team)
        if not opponent_team:
            opponent_team = list(battle.opponent_team.values())
        lead_scores = []
        for index, pokemon in enumerate(members, start=1):
            score = self._lead_score(pokemon, opponent_team)
            lead_scores.append((score, index, pokemon))
        lead_scores.sort(reverse=True, key=lambda item: item[0])
        ordered_indices = [index for _, index, _ in lead_scores]
        for _, _, pokemon in lead_scores:
            pokemon._selected_in_teampreview = True
        return "/team " + "".join(str(index) for index in ordered_indices)

    def _lead_score(self, pokemon: Pokemon, opponents: list[Pokemon]) -> float:
        if not opponents:
            return 0.0
        pressures = [self._best_move_damage(pokemon, opponent) - self._best_move_damage(opponent, pokemon) for opponent in opponents]
        utility = 0.0
        known_moves = {to_id(move) for move in pokemon.moves}
        if known_moves & set(HAZARD_MOVES):
            utility += 12
        if known_moves & {"taunt", "encore"}:
            utility += 5
        return sum(sorted(pressures, reverse=True)[:3]) / min(3, len(pressures)) + utility

    def _best_switch_order(self, battle):
        best_switch = max(battle.available_switches, key=lambda switch: self._score_switch(battle, switch))
        return self.create_order(best_switch)

    def _best_switch_score(self, battle) -> float:
        return max(self._score_switch(battle, switch) for switch in battle.available_switches)

    def _track_switch_history(self, battle) -> None:
        active = getattr(battle, "active_pokemon", None)
        if not active:
            return
        battle_tag = getattr(battle, "battle_tag", "")
        if not battle_tag:
            return
        active_id = self._pokemon_identity(active)
        if not active_id:
            return
        turn = getattr(battle, "turn", 0)
        last_seen = self._last_seen_active.get(battle_tag)
        if last_seen == (turn, active_id):
            return
        history = self._switch_history.setdefault(battle_tag, [])
        if not history or history[-1] != (turn, active_id):
            history.append((turn, active_id))
            if len(history) > 8:
                del history[:-8]
        self._last_seen_active[battle_tag] = (turn, active_id)

    def _switch_policy_for_battle(self, battle) -> SwitchPolicy:
        policy = SwitchPolicy()
        battle_tag = getattr(battle, "battle_tag", "")
        for turn, active_id in self._switch_history.get(battle_tag, []):
            policy.record_active(active_id, turn=turn, battle_tag=battle_tag)
        return policy

    def _recent_switch_penalty(self, battle, candidate: Pokemon) -> float:
        switch_policy = self._switch_policy_for_battle(battle)
        return switch_policy.history.bounce_back_penalty(
            candidate,
            turn=getattr(battle, "turn", 0),
            battle_tag=getattr(battle, "battle_tag", ""),
        )

    def _stay_bias(self, battle, active: Pokemon, opponent: Pokemon) -> float:
        switch_policy = self._switch_policy_for_battle(battle)
        offensive_pressure = self._best_move_damage(active, opponent)
        defensive_risk = self._best_counter_damage(battle, opponent, active)
        threshold = switch_policy.stay_bias_threshold(
            active=active,
            opponent=opponent,
            turn=getattr(battle, "turn", 0),
            offensive_pressure=offensive_pressure,
            defensive_risk=defensive_risk,
            trapped=getattr(battle, "trapped", False),
        )
        return max(0.0, threshold - switch_policy.base_switch_margin)

    def _pokemon_identity(self, pokemon: Pokemon) -> str:
        return to_id(getattr(pokemon, "species", None) or getattr(pokemon, "base_species", None) or getattr(pokemon, "name", None) or "")

    def _score_switch(self, battle, candidate: Pokemon) -> float:
        opponent = battle.opponent_active_pokemon
        offensive_pressure = self._best_move_damage(candidate, opponent)
        defensive_risk = self._best_counter_damage(battle, opponent, candidate)
        entry_penalty = self._entry_hazard_penalty(battle.side_conditions, candidate)
        speed_advantage = self._pokemon_stat(candidate, "spe") > self._estimated_speed(battle, opponent)
        status_penalty = 8 if candidate.status else 0
        switch_policy = self._switch_policy_for_battle(battle)
        return switch_policy.score_switch(
            candidate,
            turn=getattr(battle, "turn", 0),
            battle_tag=getattr(battle, "battle_tag", ""),
            active=getattr(battle, "active_pokemon", None),
            opponent=opponent,
            offensive_pressure=offensive_pressure,
            defensive_risk=defensive_risk,
            entry_penalty=entry_penalty,
            speed_advantage=speed_advantage,
            status_penalty=status_penalty,
            hp_fraction=getattr(candidate, "current_hp_fraction", None),
            trapped=getattr(battle, "trapped", False),
        )

    def _score_move(self, battle, active: Pokemon, opponent: Pokemon, move: Move) -> tuple[float, dict[str, bool]]:
        damage = self._estimate_damage(active, opponent, move, battle)
        damage_pct = damage * 100
        hp_remaining = (opponent.current_hp_fraction or 1.0) * 100
        result = policy_score_move(
            battle,
            active,
            opponent,
            move,
            damage_estimator=self._estimate_damage,
            speed_estimator=self._estimated_speed,
            stat_getter=self._pokemon_stat,
            counter_damage_estimator=self._best_counter_damage,
            can_tera=False,
            can_mega=getattr(battle, "can_mega_evolve", False),
            can_zmove=getattr(battle, "can_z_move", False),
            can_dynamax=getattr(battle, "can_dynamax", False),
        )
        score = result.score
        flags = dict(result.flags)
        if getattr(battle, "can_tera", False) and self._should_tera(active, move, damage_pct, hp_remaining):
            flags["tera"] = True
            score += 10
        return score, flags

    def _should_tera(self, active: Pokemon, move: Move, damage_pct: float, target_hp_pct: float) -> bool:
        if not active.tera_type or not move.type:
            return False
        if to_id(str(active.tera_type)) != to_id(str(move.type)):
            return False
        return damage_pct >= target_hp_pct * 0.9 or (active.current_hp_fraction < 0.4 and damage_pct >= 45)

    def _best_move_damage(self, attacker: Pokemon, defender: Pokemon) -> float:
        return policy_best_move_damage(
            attacker,
            defender,
            damage_estimator=self._estimate_damage,
            battle=None,
        )

    def _best_counter_damage(self, battle, opponent: Pokemon, defender: Pokemon) -> float:
        inferred = self._infer_opponent_set(battle, opponent)
        return policy_best_counter_damage(
            battle,
            opponent,
            defender,
            damage_estimator=self._estimate_damage,
            candidate_moves=inferred.moves or list(opponent.moves.keys()),
            candidate_set=inferred,
        )

    def _estimate_damage(
        self,
        attacker: Pokemon,
        defender: Pokemon,
        move: Move,
        battle,
        *,
        candidate_set: CandidateSet | None = None,
    ) -> float:
        if move.category == MoveCategory.STATUS and not move.damage:
            return 0.0
        if move.damage:
            if isinstance(move.damage, int):
                return min(1.0, move.damage / max(1, self._pokemon_stat(defender, "hp")))
            return 0.35
        base_power = move.base_power or move.z_move_power or 0
        if base_power <= 0:
            return 0.0
        if move.type:
            multiplier = defender.damage_multiplier(move)
            if multiplier == 0:
                return 0.0
        else:
            multiplier = 1.0

        stats = candidate_set or self._stats_from_pokemon(attacker)
        attack_stat = stats.attack if move.category == MoveCategory.PHYSICAL else stats.special_attack
        defense_stat = self._pokemon_stat(defender, "def" if move.category == MoveCategory.PHYSICAL else "spd")
        level = stats.level
        stab = self._stab_bonus(attacker, move)
        accuracy = 1.0 if move.accuracy is True else float(move.accuracy) / 100
        hits = move.expected_hits or 1
        defender_hp = self._pokemon_stat(defender, "hp")

        raw = (((2 * level / 5 + 2) * base_power * attack_stat / max(1, defense_stat)) / 50) + 2
        estimated = raw * stab * multiplier * accuracy * hits
        return max(0.0, min(1.0, estimated / max(1, defender_hp)))

    def _stab_bonus(self, attacker: Pokemon, move: Move) -> float:
        if not move.type:
            return 1.0
        attacker_types = {str(pokemon_type) for pokemon_type in attacker.types}
        if move.type in attacker_types or str(move.type) in attacker_types:
            return 1.5
        if attacker.is_terastallized and attacker.tera_type and to_id(str(attacker.tera_type)) == to_id(str(move.type)):
            return 1.5
        return 1.0

    def _entry_hazard_penalty(self, conditions, pokemon: Pokemon) -> float:
        return policy_entry_hazard_penalty(conditions, pokemon, stat_getter=self._pokemon_stat)

    def _is_physical_attacker(self, pokemon: Pokemon) -> bool:
        return policy_is_physical_attacker(pokemon, stat_getter=self._pokemon_stat)

    def _opponent_model(self) -> OpponentInference:
        model = getattr(self, "_opponent_inference", None)
        if model is None:
            model = OpponentInference(self.repository, self.selected_team.format_id)
            self._opponent_inference = model
        return model

    def _estimated_speed(self, battle, pokemon: Pokemon) -> int:
        return self._opponent_model().estimated_speed(pokemon)

    def _infer_opponent_set(self, battle, pokemon: Pokemon) -> CandidateSet:
        return self._opponent_model().stat_snapshot(pokemon)

    def _stats_from_pokemon(self, pokemon: Pokemon) -> CandidateSet:
        return CandidateSet(
            ability=pokemon.ability,
            item=pokemon.item,
            nature=getattr(pokemon, "nature", None),
            evs=dict(getattr(pokemon, "evs", {}) or {}),
            ivs=dict(getattr(pokemon, "ivs", {}) or {}),
            moves=list(pokemon.moves.keys()),
            level=pokemon.level or 100,
            base_stats=dict(getattr(pokemon, "base_stats", {}) or {}),
        )

    def _pokemon_stat(self, pokemon: Pokemon, stat: str) -> int:
        stats = getattr(pokemon, "stats", {}) or {}
        value = stats.get(stat)
        if value is not None:
            return value
        base_stats = dict(getattr(pokemon, "base_stats", {}) or {})
        level = getattr(pokemon, "level", None) or 100
        temp = CandidateSet(
            ability=None,
            item=None,
            nature=None,
            evs={stat: 252},
            ivs={},
            moves=[],
            level=level,
            base_stats=base_stats,
        )
        return computed_stat(temp, stat)
