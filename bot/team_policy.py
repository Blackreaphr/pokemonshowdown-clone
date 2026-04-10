"""Helpers for competitive team scoring and roster shaping.

The functions here are intentionally framework-light so `team_repo.py` and
other bot modules can reuse them without pulling in battle-engine state.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from .showdown_tools import to_id

ROLE_MOVES = {
    "hazards": {"stealthrock", "spikes", "toxicspikes", "stickyweb"},
    "removal": {"rapidspin", "defog", "mortalspin", "courtchange", "tidyup"},
    "speed": {"thunderwave", "icywind", "tailwind", "trickroom", "electroweb", "stunspore", "glare"},
    "pivot": {"uturn", "voltswitch", "flipturn", "teleport", "partingshot", "chillyreception", "shedtail"},
    "recovery": {
        "recover",
        "roost",
        "slackoff",
        "softboiled",
        "moonlight",
        "synthesis",
        "wish",
        "morningsun",
        "shoreup",
        "milkdrink",
        "strengthsap",
        "junglehealing",
    },
    "setup": {"swordsdance", "dragondance", "nastyplot", "calmmind", "quiverdance", "shellsmash", "bulkup", "irondefense", "agility"},
    "phasing": {"whirlwind", "roar", "dragontail", "circlethrow"},
    "anti_setup": {"taunt", "encore", "haze", "yawn"},
    "protect": {"protect", "detect", "kingsshield", "banefulbunker", "spikyshield", "silktrap"},
}

CORE_ROLES_SINGLE = ("hazards", "removal", "speed", "recovery", "pivot")
CORE_ROLES_DOUBLE = ("protect", "speed", "pivot", "recovery")

ROLE_WEIGHTS = {
    "hazards": 12,
    "removal": 11,
    "speed": 10,
    "pivot": 8,
    "recovery": 8,
    "setup": 7,
    "phasing": 6,
    "anti_setup": 6,
    "protect": 5,
}

ITEM_QUALITY = {
    "assaultvest": 4,
    "blacksludge": 2,
    "boosterenergy": 4,
    "choiceband": 4,
    "choicescarf": 4,
    "choicespecs": 4,
    "eviolite": 4,
    "expertbelt": 2,
    "focussash": 3,
    "heavydutyboots": 5,
    "leftovers": 4,
    "lifeorb": 4,
    "mentalherb": 2,
    "muscleband": 1,
    "redcard": 1,
    "rockyhelmet": 3,
    "shedshell": 2,
    "sitrusberry": 2,
    "thickclub": 2,
    "toxicorb": 2,
    "flameorb": 2,
    "whiteherb": 1,
}

FORMAT_RE = re.compile(r"gen(\d+)")


@dataclass(frozen=True)
class FormatProfile:
    """Parsed view of a Showdown format id."""

    format_id: str
    generation: int
    is_doubles: bool
    is_national_dex: bool
    is_past_generation: bool


@dataclass(frozen=True)
class TeamAssessment:
    """Summary of a team-level score breakdown."""

    score: float
    role_counts: dict[str, int]
    coverage: dict[str, bool]
    unique_species: int
    unique_items: int
    duplicate_species: int
    duplicate_items: int


def format_profile(format_id: str | None) -> FormatProfile:
    """Return a normalized profile for a Showdown format string."""

    normalized = to_id(format_id or "gen9ou")
    match = FORMAT_RE.search(normalized)
    generation = int(match.group(1)) if match else 9
    is_doubles = any(token in normalized for token in ("doubles", "vgc", "triples"))
    is_national_dex = "nationaldex" in normalized or "natdex" in normalized
    return FormatProfile(
        format_id=normalized,
        generation=generation,
        is_doubles=is_doubles,
        is_national_dex=is_national_dex,
        is_past_generation=generation < 9,
    )


def candidate_moves(candidate: Mapping[str, Any] | Any) -> set[str]:
    """Return a normalized move-id set for a candidate set-like object."""

    return {to_id(move) for move in _field_sequence(candidate, "moves") if to_id(move)}


def candidate_species(candidate: Mapping[str, Any] | Any) -> str:
    """Return a stable species key for a candidate set-like object."""

    species = _get(candidate, "species", None) or _get(candidate, "name", None) or _get(candidate, "pokemon", None)
    return to_id(species or "")


def candidate_roles(candidate: Mapping[str, Any] | Any) -> set[str]:
    """Infer coarse competitive roles from a candidate's move list."""

    moves = candidate_moves(candidate)
    roles = set()
    for role, role_moves in ROLE_MOVES.items():
        if moves & role_moves:
            roles.add(role)
    return roles


def team_role_counts(team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any) -> Counter[str]:
    """Count the inferred roles present across a team or team-like object."""

    counts: Counter[str] = Counter()
    for entry in _team_entries(team):
        counts.update(candidate_roles(entry))
    return counts


def team_shortfalls(team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any, *, format_id: str | None = None) -> set[str]:
    """Return the core roles that are missing from a roster."""

    profile = format_profile(format_id)
    required = CORE_ROLES_DOUBLE if profile.is_doubles else CORE_ROLES_SINGLE
    counts = team_role_counts(team)
    return {role for role in required if counts.get(role, 0) == 0}


def candidate_item_quality(candidate: Mapping[str, Any] | Any) -> float:
    """Score an item slot using a light-weight competitive prior."""

    item = to_id(_get(candidate, "item", "") or "")
    if not item:
        return 0.0
    return float(ITEM_QUALITY.get(item, 1))


def candidate_spread_quality(candidate: Mapping[str, Any] | Any) -> float:
    """Score EV/IV/nature spread information for a candidate."""

    score = 0.0
    evs = _get(candidate, "evs", {}) or {}
    ivs = _get(candidate, "ivs", {}) or {}
    nature = to_id(_get(candidate, "nature", "") or "")
    tera_type = to_id(_get(candidate, "teraType", "") or "")
    level = _get(candidate, "level", None)

    ev_total = 0
    if isinstance(evs, Mapping):
        for value in evs.values():
            try:
                ev_total += max(0, int(value))
            except (TypeError, ValueError):
                continue
    score += min(ev_total, 510) / 60 if ev_total else 0.0
    if ev_total in {508, 510}:
        score += 2.0

    if isinstance(ivs, Mapping):
        score += min(len(ivs), 6) * 0.25
        if any(_is_non_default_iv(value) for value in ivs.values()):
            score += 0.5

    if nature:
        score += 1.5
    if tera_type:
        score += 1.0
    if level is not None:
        score += 0.5
    return score


def candidate_move_quality(candidate: Mapping[str, Any] | Any, *, format_id: str | None = None) -> float:
    """Score the move list itself, independent of roster context."""

    profile = format_profile(format_id)
    moves = candidate_moves(candidate)
    roles = candidate_roles(candidate)
    score = 0.0

    score += min(len(moves), 4) * 2.0
    score += len(roles) * 3.5
    score += sum(ROLE_WEIGHTS.get(role, 0) for role in roles) * 0.6

    if "protect" in moves:
        score += 4.0 if profile.is_doubles else 1.5
    if moves & ROLE_MOVES["hazards"]:
        score += 2.5
    if moves & ROLE_MOVES["removal"]:
        score += 2.5
    if moves & ROLE_MOVES["speed"]:
        score += 2.0
    if moves & ROLE_MOVES["recovery"]:
        score += 1.5
    if moves & ROLE_MOVES["anti_setup"]:
        score += 1.5
    return score


def score_candidate_set(
    candidate: Mapping[str, Any] | Any,
    *,
    format_id: str | None = None,
    current_team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any | None = None,
    opponent_team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any | None = None,
) -> float:
    """Score a single candidate set for roster selection."""

    profile = format_profile(format_id)
    moves = candidate_moves(candidate)
    roles = candidate_roles(candidate)
    score = 6.0

    score += candidate_move_quality(candidate, format_id=format_id)
    score += candidate_item_quality(candidate)
    score += candidate_spread_quality(candidate)
    score += len(roles) * 2.0

    if profile.is_doubles:
        if "protect" in moves:
            score += 3.0
        if "speed" in roles:
            score += 3.0
        if "pivot" in roles:
            score += 1.5
    else:
        if "hazards" in roles:
            score += 4.0
        if "removal" in roles:
            score += 3.0
        if "speed" in roles:
            score += 3.0
        if "recovery" in roles:
            score += 2.0

    if current_team:
        current_counts = team_role_counts(current_team)
        score += _roster_gap_bonus(roles, current_counts, profile)

    if opponent_team:
        opponent_counts = team_role_counts(opponent_team)
        score += _matchup_bonus(roles, opponent_counts, profile)

    return score


def assess_team(
    team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any,
    *,
    format_id: str | None = None,
    opponent_team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any | None = None,
) -> TeamAssessment:
    """Return a team-level assessment with coverage and diversity details."""

    profile = format_profile(format_id)
    entries = _team_entries(team)
    role_counts: Counter[str] = Counter()
    species_counts: Counter[str] = Counter()
    item_counts: Counter[str] = Counter()
    score = 0.0

    for entry in entries:
        score += score_candidate_set(entry, format_id=format_id, current_team=entries, opponent_team=opponent_team)
        role_counts.update(candidate_roles(entry))
        species = candidate_species(entry)
        if species:
            species_counts[species] += 1
        item = to_id(_get(entry, "item", "") or "")
        if item:
            item_counts[item] += 1

    required = CORE_ROLES_DOUBLE if profile.is_doubles else CORE_ROLES_SINGLE
    coverage = {role: role_counts.get(role, 0) > 0 for role in required}
    score += _coverage_bonus(role_counts, required)
    score += len(role_counts) * 2.5
    score += sum(min(count, 2) for count in role_counts.values())
    score -= _duplicate_penalty(species_counts, item_counts)

    if opponent_team:
        score += _team_matchup_adjustment(role_counts, team_role_counts(opponent_team), profile)

    return TeamAssessment(
        score=score,
        role_counts=dict(role_counts),
        coverage=coverage,
        unique_species=sum(1 for count in species_counts.values() if count > 0),
        unique_items=sum(1 for count in item_counts.values() if count > 0),
        duplicate_species=sum(max(0, count - 1) for count in species_counts.values()),
        duplicate_items=sum(max(0, count - 1) for count in item_counts.values()),
    )


def score_team(
    team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any,
    *,
    format_id: str | None = None,
    opponent_team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any | None = None,
) -> float:
    """Return the scalar score from :func:`assess_team`."""

    return assess_team(team, format_id=format_id, opponent_team=opponent_team).score


def rank_candidates(
    candidates: Sequence[Mapping[str, Any] | Any],
    *,
    format_id: str | None = None,
    current_team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any | None = None,
    opponent_team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any | None = None,
) -> list[tuple[float, Any]]:
    """Return candidates ordered from best to worst for the given context."""

    ranked = [
        (
            score_candidate_set(
                candidate,
                format_id=format_id,
                current_team=current_team,
                opponent_team=opponent_team,
            ),
            candidate,
        )
        for candidate in candidates
    ]
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def choose_best_candidate(
    candidates: Sequence[Mapping[str, Any] | Any],
    *,
    format_id: str | None = None,
    current_team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any | None = None,
    opponent_team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any | None = None,
) -> Any:
    """Pick the single best candidate from a pool."""

    if not candidates:
        raise ValueError("No candidates were provided.")
    return max(
        candidates,
        key=lambda candidate: score_candidate_set(
            candidate,
            format_id=format_id,
            current_team=current_team,
            opponent_team=opponent_team,
        ),
    )


def select_team_from_pool(
    candidates: Sequence[Mapping[str, Any] | Any],
    *,
    team_size: int = 6,
    format_id: str | None = None,
    opponent_team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any | None = None,
) -> list[Any]:
    """Greedily select a diverse team from a candidate pool."""

    if team_size <= 0:
        raise ValueError("team_size must be positive.")

    remaining = list(candidates)
    selected: list[Any] = []
    chosen_species: set[str] = set()

    while remaining and len(selected) < team_size:
        best_index = None
        best_score = float("-inf")
        for index, candidate in enumerate(remaining):
            species = candidate_species(candidate)
            if species and species in chosen_species:
                continue
            score = score_candidate_set(
                candidate,
                format_id=format_id,
                current_team=selected,
                opponent_team=opponent_team,
            )
            if score > best_score:
                best_score = score
                best_index = index
        if best_index is None:
            break
        chosen = remaining.pop(best_index)
        selected.append(chosen)
        species = candidate_species(chosen)
        if species:
            chosen_species.add(species)

    if len(selected) < team_size:
        raise ValueError("Could not assemble a full team from the supplied pool.")
    return selected


def _team_entries(team: Sequence[Mapping[str, Any] | Any] | Mapping[str, Any] | Any) -> list[Any]:
    if isinstance(team, Sequence) and not isinstance(team, (str, bytes)):
        return list(team)
    wrapper_team = _get(team, "team", None)
    if isinstance(wrapper_team, Sequence) and not isinstance(wrapper_team, (str, bytes)):
        return list(wrapper_team)
    return [team]


def _get(item: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _field_sequence(item: Mapping[str, Any] | Any, key: str) -> list[Any]:
    value = _get(item, key, [])
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value)
    if value is None:
        return []
    return [value]


def _is_non_default_iv(value: Any) -> bool:
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return False
    return iv != 31


def _required_roles(profile: FormatProfile) -> tuple[str, ...]:
    return CORE_ROLES_DOUBLE if profile.is_doubles else CORE_ROLES_SINGLE


def _coverage_bonus(role_counts: Counter[str], required: Sequence[str]) -> float:
    bonus = 0.0
    for role in required:
        count = role_counts.get(role, 0)
        if count == 0:
            bonus -= ROLE_WEIGHTS.get(role, 5) * 1.25
        elif count == 1:
            bonus += ROLE_WEIGHTS.get(role, 5)
        elif count == 2:
            bonus += ROLE_WEIGHTS.get(role, 5) * 0.5
        else:
            bonus -= (count - 2) * 1.5
    return bonus


def _duplicate_penalty(species_counts: Counter[str], item_counts: Counter[str]) -> float:
    species_penalty = sum(max(0, count - 1) for count in species_counts.values()) * 12.0
    item_penalty = max(0, sum(max(0, count - 1) for count in item_counts.values()) - 2) * 1.5
    return species_penalty + item_penalty


def _roster_gap_bonus(roles: set[str], current_counts: Counter[str], profile: FormatProfile) -> float:
    bonus = 0.0
    for role in _required_roles(profile):
        if role in roles:
            count = current_counts.get(role, 0)
            if count == 0:
                bonus += ROLE_WEIGHTS.get(role, 5) * 0.9
            elif count == 1:
                bonus += ROLE_WEIGHTS.get(role, 5) * 0.25
            else:
                bonus -= (count - 1) * 1.0
    if "setup" in roles and current_counts.get("setup", 0) == 0:
        bonus += 4.0
    if "phasing" in roles and current_counts.get("setup", 0) > 0:
        bonus += 4.0
    if "anti_setup" in roles and current_counts.get("setup", 0) > 0:
        bonus += 3.0
    return bonus


def _matchup_bonus(roles: set[str], opponent_counts: Counter[str], profile: FormatProfile) -> float:
    bonus = 0.0
    if opponent_counts.get("hazards", 0) and "removal" in roles:
        bonus += 5.0
    if opponent_counts.get("speed", 0) and "speed" in roles:
        bonus += 4.0
    if opponent_counts.get("setup", 0) and ("phasing" in roles or "anti_setup" in roles):
        bonus += 4.0
    if opponent_counts.get("recovery", 0) and "setup" in roles:
        bonus += 1.5
    if profile.is_doubles and opponent_counts.get("pivot", 0) and "protect" in roles:
        bonus += 2.5
    return bonus


def _team_matchup_adjustment(role_counts: Counter[str], opponent_counts: Counter[str], profile: FormatProfile) -> float:
    adjustment = 0.0
    if opponent_counts.get("hazards", 0) and role_counts.get("removal", 0) == 0:
        adjustment -= 8.0
    if opponent_counts.get("speed", 0) and role_counts.get("speed", 0) == 0:
        adjustment -= 6.0
    if opponent_counts.get("setup", 0) and role_counts.get("phasing", 0) == 0 and role_counts.get("anti_setup", 0) == 0:
        adjustment -= 6.0
    if profile.is_doubles and role_counts.get("protect", 0) == 0:
        adjustment -= 5.0
    if not profile.is_doubles and role_counts.get("pivot", 0) == 0:
        adjustment -= 2.5
    return adjustment


__all__ = [
    "FormatProfile",
    "TeamAssessment",
    "assess_team",
    "candidate_item_quality",
    "candidate_moves",
    "candidate_move_quality",
    "candidate_roles",
    "candidate_species",
    "choose_best_candidate",
    "format_profile",
    "rank_candidates",
    "score_candidate_set",
    "score_team",
    "select_team_from_pool",
    "team_role_counts",
    "team_shortfalls",
]


