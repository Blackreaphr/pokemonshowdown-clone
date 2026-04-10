from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from poke_env.battle.move import Move
from poke_env.battle.move_category import MoveCategory
from poke_env.battle.pokemon import Pokemon
from poke_env.battle.side_condition import SideCondition

from .showdown_tools import to_id

SETUP_MOVES = {
    'swordsdance': 18,
    'dragondance': 18,
    'nastyplot': 18,
    'calmmind': 16,
    'quiverdance': 18,
    'shellsmash': 22,
    'bulkup': 14,
    'agility': 12,
    'irondefense': 12,
}

RECOVERY_MOVES = {'recover', 'roost', 'slackoff', 'softboiled', 'moonlight', 'synthesis', 'wish'}
HAZARD_MOVES = {
    'stealthrock': SideCondition.STEALTH_ROCK,
    'spikes': SideCondition.SPIKES,
    'toxicspikes': SideCondition.TOXIC_SPIKES,
    'stickyweb': SideCondition.STICKY_WEB,
}
REMOVAL_MOVES = {'rapidspin', 'defog', 'mortalspin', 'tidyup', 'courtchange'}
STATUS_MOVES = {'thunderwave', 'toxic', 'willowisp', 'spore', 'sleeppowder', 'stunspore', 'glare', 'yawn'}
PIVOT_MOVES = {'uturn', 'voltswitch', 'flipturn', 'partingshot', 'teleport', 'chillyreception'}


class DamageEstimator(Protocol):
    def __call__(
        self,
        attacker: Pokemon,
        defender: Pokemon,
        move: Move,
        battle: Any,
        *,
        candidate_set: Any | None = None,
    ) -> float: ...


class SpeedEstimator(Protocol):
    def __call__(self, battle: Any, pokemon: Pokemon) -> int: ...


class StatGetter(Protocol):
    def __call__(self, pokemon: Pokemon, stat: str) -> int: ...


class CounterDamageEstimator(Protocol):
    def __call__(self, battle: Any, opponent: Pokemon, defender: Pokemon) -> float: ...


@dataclass(slots=True)
class MoveScore:
    score: float
    flags: dict[str, bool] = field(default_factory=dict)


@dataclass(slots=True)
class MovePolicyConfig:
    setup_moves: dict[str, int] = field(default_factory=lambda: dict(SETUP_MOVES))
    recovery_moves: set[str] = field(default_factory=lambda: set(RECOVERY_MOVES))
    hazard_moves: dict[str, SideCondition] = field(default_factory=lambda: dict(HAZARD_MOVES))
    removal_moves: set[str] = field(default_factory=lambda: set(REMOVAL_MOVES))
    status_moves: set[str] = field(default_factory=lambda: set(STATUS_MOVES))
    pivot_moves: set[str] = field(default_factory=lambda: set(PIVOT_MOVES))


@dataclass(slots=True)
class MovePolicyState:
    recent_actions: dict[str, list[str]] = field(default_factory=dict)
    recent_switches: dict[str, list[tuple[int, str]]] = field(default_factory=dict)
    last_seen_active: dict[str, tuple[int, str]] = field(default_factory=dict)


def score_move(
    battle: Any,
    active: Pokemon,
    opponent: Pokemon,
    move: Move,
    *,
    damage_estimator: DamageEstimator,
    speed_estimator: SpeedEstimator,
    stat_getter: StatGetter,
    counter_damage_estimator: CounterDamageEstimator,
    can_tera: bool = False,
    can_mega: bool = False,
    can_zmove: bool = False,
    can_dynamax: bool = False,
    config: MovePolicyConfig | None = None,
) -> MoveScore:
    """Score a move using a framework-neutral heuristic bundle.

    The caller supplies the expensive battle-specific estimators. This keeps the
    module reusable from a bot class, benchmark harness, or tests with stubs.
    """

    policy = config or MovePolicyConfig()
    move_id = to_id(move.id)
    score = 0.0
    flags = {'tera': False, 'mega': False, 'zmove': False, 'dynamax': False}

    damage = damage_estimator(active, opponent, move, battle)
    damage_pct = damage * 100
    hp_remaining = max(1.0, (getattr(opponent, 'current_hp_fraction', None) or 1.0) * 100)

    if damage_pct:
        score += damage_pct
        if damage_pct >= hp_remaining:
            score += 35
        if getattr(move, 'priority', 0) > 0 and damage_pct >= hp_remaining:
            score += 12
        if stat_getter(active, 'spe') > speed_estimator(battle, opponent):
            score += 6

    if move_id in policy.setup_moves:
        score += setup_score(
            battle,
            active,
            opponent,
            move_id,
            counter_damage_estimator=counter_damage_estimator,
            stat_getter=stat_getter,
            config=policy,
        )
    if move_id in policy.recovery_moves:
        score += recovery_score(
            battle,
            active,
            opponent,
            counter_damage_estimator=counter_damage_estimator,
        )
    if move_id in policy.hazard_moves:
        score += hazard_score(battle, move_id, config=policy)
    if move_id in policy.removal_moves:
        score += removal_score(battle, active, stat_getter=stat_getter)
    if move_id in policy.status_moves:
        score += status_score(opponent, move_id, speed_estimator=speed_estimator, stat_getter=stat_getter)
    if move_id in policy.pivot_moves:
        score += 6

    if getattr(move, 'force_switch', False):
        score += 10
    if getattr(move, 'self_switch', False):
        score += 8
    if getattr(move, 'heal', None):
        score += ratio_component(move.heal) * 20
    if getattr(move, 'drain', None):
        score += ratio_component(move.drain) * damage * 25
    if getattr(move, 'recoil', None):
        score -= ratio_component(move.recoil) * 18
    if getattr(move, 'accuracy', True) is not True:
        score *= float(move.accuracy) / 100

    if can_tera and should_tera(active, move, damage_pct=damage_pct, target_hp_pct=hp_remaining):
        flags['tera'] = True
        score += 10
    if can_mega and getattr(active, 'item', None) and 'ite' in to_id(active.item):
        flags['mega'] = True
        score += 4
    if can_zmove and damage_pct >= hp_remaining * 0.75:
        flags['zmove'] = True
        score += 8
    if can_dynamax and getattr(active, 'current_hp_fraction', 0.0) > 0.7 and damage_pct >= 35:
        flags['dynamax'] = True
        score += 6

    return MoveScore(score=score, flags=flags)


def setup_score(
    battle: Any,
    active: Pokemon,
    opponent: Pokemon,
    move_id: str,
    *,
    counter_damage_estimator: CounterDamageEstimator,
    stat_getter: StatGetter,
    config: MovePolicyConfig | None = None,
) -> float:
    policy = config or MovePolicyConfig()
    if (getattr(active, 'current_hp_fraction', 0.0) or 0.0) < 0.55:
        return -10.0
    counter = counter_damage_estimator(battle, opponent, active)
    safe_bonus = max(0.0, 35.0 - counter)
    boosts = sum(max(0, boost) for boost in getattr(active, 'boosts', {}).values())
    return float(policy.setup_moves.get(move_id, 0)) + safe_bonus - boosts * 6


def recovery_score(
    battle: Any,
    active: Pokemon,
    opponent: Pokemon,
    *,
    counter_damage_estimator: CounterDamageEstimator,
) -> float:
    if (getattr(active, 'current_hp_fraction', 0.0) or 0.0) > 0.75:
        return -12.0
    threatened = counter_damage_estimator(battle, opponent, active)
    return (1 - (getattr(active, 'current_hp_fraction', 0.0) or 0.0)) * 55 - threatened


def hazard_score(battle: Any, move_id: str, *, config: MovePolicyConfig | None = None) -> float:
    policy = config or MovePolicyConfig()
    condition = policy.hazard_moves[move_id]
    opponent_side_conditions = getattr(battle, 'opponent_side_conditions', {}) or {}
    if condition in opponent_side_conditions:
        return -18.0
    opponent_team = getattr(battle, 'opponent_team', {}) or {}
    living_targets = sum(1 for mon in opponent_team.values() if not getattr(mon, 'fainted', False))
    return float(max(0, living_targets - 1) * 6)


def removal_score(battle: Any, active: Pokemon, *, stat_getter: StatGetter) -> float:
    side_conditions = getattr(battle, 'side_conditions', {}) or {}
    if not side_conditions:
        return -10.0
    hazard_cost = entry_hazard_penalty(side_conditions, active, stat_getter=stat_getter)
    return 10.0 + hazard_cost


def status_score(
    opponent: Pokemon,
    move_id: str,
    *,
    speed_estimator: SpeedEstimator,
    stat_getter: StatGetter,
) -> float:
    if getattr(opponent, 'status', None):
        return -10.0
    if move_id == 'thunderwave':
        return 14.0 if speed_estimator(None, opponent) > 100 else 7.0
    if move_id == 'willowisp':
        return 16.0 if is_physical_attacker(opponent, stat_getter=stat_getter) else 5.0
    if move_id == 'toxic':
        return 11.0 if (getattr(opponent, 'current_hp_fraction', None) or 1.0) > 0.6 else 4.0
    if move_id in {'spore', 'sleeppowder'}:
        return 17.0
    return 8.0


def pivot_bonus(move: Move) -> float:
    return 6.0 if to_id(move.id) in PIVOT_MOVES else 0.0


def should_tera(active: Pokemon, move: Move, *, damage_pct: float, target_hp_pct: float) -> bool:
    """Return True when tera materially changes the current exchange."""

    if not getattr(active, 'tera_type', None) or not getattr(move, 'type', None):
        return False
    if to_id(str(active.tera_type)) != to_id(str(move.type)):
        return False
    return damage_pct >= target_hp_pct * 0.9 or ((getattr(active, 'current_hp_fraction', 0.0) or 0.0) < 0.4 and damage_pct >= 45)


def burst_mechanic_flags(
    active: Pokemon,
    move: Move,
    *,
    damage_pct: float,
    target_hp_pct: float,
    can_mega: bool = False,
    can_zmove: bool = False,
    can_dynamax: bool = False,
) -> dict[str, bool]:
    """Return burst-mechanic eligibility flags without mutating battle state."""

    flags = {'tera': False, 'mega': False, 'zmove': False, 'dynamax': False}
    if should_tera(active, move, damage_pct=damage_pct, target_hp_pct=target_hp_pct):
        flags['tera'] = True
    if can_mega and getattr(active, 'item', None) and 'ite' in to_id(active.item):
        flags['mega'] = True
    if can_zmove and damage_pct >= target_hp_pct * 0.75:
        flags['zmove'] = True
    if can_dynamax and (getattr(active, 'current_hp_fraction', 0.0) or 0.0) > 0.7 and damage_pct >= 35:
        flags['dynamax'] = True
    return flags


def entry_hazard_penalty(conditions: dict[Any, int], pokemon: Pokemon, *, stat_getter: StatGetter) -> float:
    penalty = 0.0
    if SideCondition.STEALTH_ROCK in conditions:
        try:
            rock_move = Move('stoneedge', gen=pokemon.gen)
            penalty += pokemon.damage_multiplier(rock_move) * 12.5
        except Exception:
            penalty += 12.5
    spikes = conditions.get(SideCondition.SPIKES, 0)
    if spikes:
        if spikes == 1:
            penalty += 12.5
        elif spikes == 2:
            penalty += 16.67
        else:
            penalty += 25.0
    if SideCondition.STICKY_WEB in conditions and stat_getter(pokemon, 'spe') > 100:
        penalty += 8.0
    if SideCondition.TOXIC_SPIKES in conditions and 'poison' not in {to_id(str(t)) for t in getattr(pokemon, 'types', [])}:
        penalty += 10.0
    return penalty


def is_physical_attacker(pokemon: Pokemon, *, stat_getter: StatGetter) -> bool:
    if stat_getter(pokemon, 'atk') > stat_getter(pokemon, 'spa'):
        return True
    physical = 0
    special = 0
    for move in getattr(pokemon, 'moves', {}).values():
        if isinstance(move, Move):
            if move.category == MoveCategory.PHYSICAL:
                physical += 1
            elif move.category == MoveCategory.SPECIAL:
                special += 1
    return physical >= special


def best_move_damage(
    attacker: Pokemon,
    defender: Pokemon,
    *,
    damage_estimator: DamageEstimator,
    battle: Any | None = None,
) -> float:
    moves: list[Move] = []
    for move_id, move in getattr(attacker, 'moves', {}).items():
        if isinstance(move, Move):
            moves.append(move)
            continue
        try:
            moves.append(Move(move_id, gen=attacker.gen))
        except Exception:
            continue
    if not moves:
        return 0.0
    return max(damage_estimator(attacker, defender, move, battle) * 100 for move in moves)


def best_counter_damage(
    battle: Any,
    opponent: Pokemon,
    defender: Pokemon,
    *,
    damage_estimator: DamageEstimator,
    candidate_moves: list[str] | None = None,
    candidate_set: Any | None = None,
) -> float:
    moves = candidate_moves or list(getattr(opponent, 'moves', {}).keys())
    damages = []
    for move_name in moves:
        try:
            move = move_name if isinstance(move_name, Move) else Move(move_name, gen=opponent.gen)
        except Exception:
            continue
        damages.append(damage_estimator(opponent, defender, move, battle, candidate_set=candidate_set) * 100)
    return max(damages) if damages else 0.0


def move_should_force_switch(move: Move) -> bool:
    return bool(getattr(move, 'force_switch', False) or getattr(move, 'self_switch', False))


def ratio_component(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (tuple, list)):
        if not value:
            return 0.0
        return float(value[0])
    return float(value)
