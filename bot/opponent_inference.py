from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Protocol

from .showdown_tools import to_id


class RepositoryProtocol(Protocol):
    def infer_opponent_sets(
        self,
        format_id: str,
        species: str,
        *,
        ability: str | None = None,
        item: str | None = None,
        revealed_moves: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        ...


@dataclass(frozen=True)
class InferredCandidate:
    ability: str | None
    item: str | None
    nature: str | None
    evs: dict[str, int]
    ivs: dict[str, int]
    moves: list[str]
    level: int
    base_stats: dict[str, int]

    @property
    def attack(self) -> int:
        return computed_stat(self, "atk")

    @property
    def special_attack(self) -> int:
        return computed_stat(self, "spa")

    @property
    def speed(self) -> int:
        return computed_stat(self, "spe")


@dataclass(frozen=True)
class OpponentProfile:
    species: str
    revealed_moves: tuple[str, ...]
    candidate_count: int
    best_candidate: InferredCandidate | None
    best_match_score: int
    estimated_attack: int
    estimated_special_attack: int
    estimated_speed: int


class OpponentInference:
    def __init__(self, repository: RepositoryProtocol, format_id: str):
        self.repository = repository
        self.format_id = format_id

    def candidate_sets(
        self,
        pokemon: Any,
        *,
        ability: str | None = None,
        item: str | None = None,
        revealed_moves: Iterable[str] | None = None,
    ) -> list[InferredCandidate]:
        species = pokemon_species(pokemon)
        candidates = self.repository.infer_opponent_sets(
            self.format_id,
            species,
            ability=ability if ability is not None else pokemon_ability(pokemon),
            item=item if item is not None else pokemon_item(pokemon),
            revealed_moves={to_id(move) for move in (revealed_moves or pokemon_moves(pokemon))},
        )
        return [candidate_from_dict(pokemon, candidate) for candidate in candidates]

    def best_candidate(
        self,
        pokemon: Any,
        *,
        ability: str | None = None,
        item: str | None = None,
        revealed_moves: Iterable[str] | None = None,
    ) -> InferredCandidate | None:
        candidates = self.candidate_sets(
            pokemon,
            ability=ability,
            item=item,
            revealed_moves=revealed_moves,
        )
        if not candidates:
            return None
        best_score = -1
        best_candidate: InferredCandidate | None = None
        revealed = {to_id(move) for move in (revealed_moves or pokemon_moves(pokemon))}
        for candidate in candidates:
            score = match_score(candidate.moves, revealed)
            if score > best_score:
                best_score = score
                best_candidate = candidate
        return best_candidate

    def stat_snapshot(
        self,
        pokemon: Any,
        *,
        ability: str | None = None,
        item: str | None = None,
        revealed_moves: Iterable[str] | None = None,
    ) -> InferredCandidate:
        best = self.best_candidate(
            pokemon,
            ability=ability,
            item=item,
            revealed_moves=revealed_moves,
        )
        if best is not None:
            return best
        return fallback_candidate(pokemon)

    def estimated_speed(
        self,
        pokemon: Any,
        *,
        ability: str | None = None,
        item: str | None = None,
        revealed_moves: Iterable[str] | None = None,
    ) -> int:
        return self.stat_snapshot(
            pokemon,
            ability=ability,
            item=item,
            revealed_moves=revealed_moves,
        ).speed

    def offensive_profile(
        self,
        pokemon: Any,
        *,
        ability: str | None = None,
        item: str | None = None,
        revealed_moves: Iterable[str] | None = None,
    ) -> dict[str, int | str | None]:
        snapshot = self.stat_snapshot(
            pokemon,
            ability=ability,
            item=item,
            revealed_moves=revealed_moves,
        )
        moves = {to_id(move) for move in snapshot.moves}
        physical = sum(1 for move in moves if move in PHYSICAL_PRESSURE_MOVES)
        special = sum(1 for move in moves if move in SPECIAL_PRESSURE_MOVES)
        utility = sum(1 for move in moves if move in UTILITY_PRESSURE_MOVES)
        return {
            "species": pokemon_species(pokemon),
            "ability": snapshot.ability,
            "item": snapshot.item,
            "physical_pressure": physical,
            "special_pressure": special,
            "utility_pressure": utility,
            "speed": snapshot.speed,
            "attack": snapshot.attack,
            "special_attack": snapshot.special_attack,
        }

    def profile(
        self,
        pokemon: Any,
        *,
        ability: str | None = None,
        item: str | None = None,
        revealed_moves: Iterable[str] | None = None,
    ) -> OpponentProfile:
        revealed = tuple(sorted({to_id(move) for move in (revealed_moves or pokemon_moves(pokemon))}))
        candidates = self.candidate_sets(
            pokemon,
            ability=ability,
            item=item,
            revealed_moves=revealed,
        )
        best = self.best_candidate(
            pokemon,
            ability=ability,
            item=item,
            revealed_moves=revealed,
        )
        best_score = match_score(best.moves, set(revealed)) if best else 0
        return OpponentProfile(
            species=pokemon_species(pokemon),
            revealed_moves=revealed,
            candidate_count=len(candidates),
            best_candidate=best,
            best_match_score=best_score,
            estimated_attack=best.attack if best else fallback_candidate(pokemon).attack,
            estimated_special_attack=best.special_attack if best else fallback_candidate(pokemon).special_attack,
            estimated_speed=best.speed if best else fallback_candidate(pokemon).speed,
        )


PHYSICAL_PRESSURE_MOVES = {
    "earthquake",
    "closecombat",
    "knockoff",
    "suckerpunch",
    "ironhead",
    "woodhammer",
    "flareblitz",
    "uturn",
    "rapidspin",
    "liquidation",
    "iciclecrash",
    "playrough",
    "bodypress",
    "dragonclaw",
    "crunch",
    "icedynamicpunch",
    "poltergeist",
    "pyroball",
}

SPECIAL_PRESSURE_MOVES = {
    "moonblast",
    "shadowball",
    "dracometeor",
    "hydropump",
    "surf",
    "earthpower",
    "futuresight",
    "psyshock",
    "flashcannon",
    "airslash",
    "icebeam",
    "flamethrower",
    "thunderbolt",
    "makeitrain",
    "blizzard",
    "freezedry",
}

UTILITY_PRESSURE_MOVES = {
    "stealthrock",
    "spikes",
    "toxicspikes",
    "stickyweb",
    "defog",
    "rapidspin",
    "mortalspin",
    "courtchange",
    "roost",
    "recover",
    "softboiled",
    "wish",
    "protect",
    "taunt",
    "thunderwave",
    "toxic",
    "willowisp",
    "encore",
    "chillyreception",
    "uturn",
    "voltswitch",
    "flipturn",
    "partingshot",
}


def candidate_from_dict(pokemon: Any, candidate: Mapping[str, Any]) -> InferredCandidate:
    return InferredCandidate(
        ability=_as_str(candidate.get("ability")),
        item=_as_str(candidate.get("item")),
        nature=_as_str(candidate.get("nature")),
        evs=dict(candidate.get("evs", {}) or {}),
        ivs=dict(candidate.get("ivs", {}) or {}),
        moves=[_as_str(move) for move in candidate.get("moves", []) if _as_str(move)],
        level=int(candidate.get("level") or pokemon_level(pokemon) or 100),
        base_stats=dict(getattr(pokemon, "base_stats", {}) or candidate.get("base_stats", {}) or {}),
    )


def fallback_candidate(pokemon: Any) -> InferredCandidate:
    return InferredCandidate(
        ability=pokemon_ability(pokemon),
        item=pokemon_item(pokemon),
        nature=getattr(pokemon, "nature", None),
        evs=dict(getattr(pokemon, "evs", {}) or {}),
        ivs=dict(getattr(pokemon, "ivs", {}) or {}),
        moves=pokemon_moves(pokemon),
        level=pokemon_level(pokemon) or 100,
        base_stats=dict(getattr(pokemon, "base_stats", {}) or {}),
    )


def match_score(candidate_moves: Iterable[str], revealed_moves: set[str]) -> int:
    candidate_set = {to_id(move) for move in candidate_moves}
    return sum(1 for move in revealed_moves if move in candidate_set)


def pokemon_species(pokemon: Any) -> str:
    return _as_str(
        getattr(pokemon, "species", None)
        or getattr(pokemon, "base_species", None)
        or getattr(pokemon, "name", None)
        or pokemon_get(pokemon, "species")
        or pokemon_get(pokemon, "base_species")
        or pokemon_get(pokemon, "name")
        or ""
    )


def pokemon_moves(pokemon: Any) -> list[str]:
    raw = pokemon_get(pokemon, "moves")
    if isinstance(raw, Mapping):
        return [_as_str(move_id) for move_id in raw.keys() if _as_str(move_id)]
    if isinstance(raw, Iterable) and not isinstance(raw, (str, bytes)):
        return [_as_str(move) for move in raw if _as_str(move)]
    return []


def pokemon_ability(pokemon: Any) -> str | None:
    return _as_str(
        getattr(pokemon, "ability", None)
        or pokemon_get(pokemon, "ability")
    ) or None


def pokemon_item(pokemon: Any) -> str | None:
    return _as_str(
        getattr(pokemon, "item", None)
        or pokemon_get(pokemon, "item")
    ) or None


def pokemon_level(pokemon: Any) -> int | None:
    raw = getattr(pokemon, "level", None)
    if raw is None:
        raw = pokemon_get(pokemon, "level")
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def pokemon_get(pokemon: Any, key: str) -> Any:
    if isinstance(pokemon, Mapping):
        return pokemon.get(key)
    return getattr(pokemon, key, None)


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def computed_stat(candidate: InferredCandidate, stat: str) -> int:
    base = candidate.base_stats.get(stat, 100)
    iv = candidate.ivs.get(stat, 31)
    ev = candidate.evs.get(stat, 0)
    level = candidate.level or 100
    nature = 1.0
    lowered, raised = nature_modifiers(candidate.nature)
    if stat == raised:
        nature = 1.1
    elif stat == lowered:
        nature = 0.9
    if stat == "hp":
        return math.floor(((2 * base + iv + ev // 4) * level) / 100) + level + 10
    return math.floor((math.floor(((2 * base + iv + ev // 4) * level) / 100) + 5) * nature)


def nature_modifiers(nature: str | None) -> tuple[str | None, str | None]:
    table = {
        "adamant": ("spa", "atk"),
        "bold": ("atk", "def"),
        "brave": ("spe", "atk"),
        "calm": ("atk", "spd"),
        "careful": ("spa", "spd"),
        "gentle": ("def", "spd"),
        "hasty": ("def", "spe"),
        "impish": ("spa", "def"),
        "jolly": ("spa", "spe"),
        "lax": ("spd", "def"),
        "lonely": ("def", "atk"),
        "mild": ("def", "spa"),
        "modest": ("atk", "spa"),
        "naive": ("spd", "spe"),
        "naughty": ("spd", "atk"),
        "quiet": ("spe", "spa"),
        "rash": ("spd", "spa"),
        "relaxed": ("spe", "def"),
        "sassy": ("spe", "spd"),
        "timid": ("atk", "spe"),
    }
    return table.get(to_id(nature or ""), (None, None))


__all__ = [
    "InferredCandidate",
    "OpponentInference",
    "OpponentProfile",
    "candidate_from_dict",
    "computed_stat",
    "fallback_candidate",
    "match_score",
    "nature_modifiers",
    "pokemon_ability",
    "pokemon_get",
    "pokemon_item",
    "pokemon_level",
    "pokemon_moves",
    "pokemon_species",
]
