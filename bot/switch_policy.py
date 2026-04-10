from __future__ import annotations

from dataclasses import dataclass, field
import math
import re
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "SwitchDecision",
    "SwitchEvent",
    "SwitchHistory",
    "SwitchPolicy",
    "best_switch",
    "score_switch_candidate",
    "should_switch",
    "stay_bias_threshold",
    "to_id",
]


_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


def to_id(value: Any) -> str:
    """Normalize a Showdown identifier."""

    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _lookup(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text not in {"", "0", "false", "none", "no"}


def _parse_hp_fraction(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 1:
            return max(0.0, min(1.0, float(value) / 100.0))
        return max(0.0, min(1.0, float(value)))

    if isinstance(value, str):
        text = value.strip().lower()
        if "fnt" in text:
            return 0.0
        if "%" in text:
            match = _NUMBER_RE.search(text)
            if match:
                return max(0.0, min(1.0, float(match.group(1)) / 100.0))
        parts = _NUMBER_RE.findall(text)
        if len(parts) >= 2:
            current = float(parts[0])
            maximum = float(parts[1])
            if maximum > 0:
                return max(0.0, min(1.0, current / maximum))
        if len(parts) == 1:
            value = float(parts[0])
            if value > 1:
                return max(0.0, min(1.0, value / 100.0))
            return max(0.0, min(1.0, value))
        return None

    current = _lookup(value, "current_hp", "hp", default=None)
    maximum = _lookup(value, "max_hp", "maxhp", default=None)
    if current is not None and maximum:
        try:
            return max(0.0, min(1.0, float(current) / float(maximum)))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    condition = _lookup(value, "condition", default=None)
    if condition is not None:
        return _parse_hp_fraction(condition)
    return None


def _identity(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return to_id(value)
    for name in ("species", "base_species", "name", "details", "ident", "id", "pokemon_id"):
        candidate = _lookup(value, name, default=None)
        if candidate:
            return to_id(candidate)
    return to_id(value)


def _speed(value: Any, default: int = 0) -> int:
    raw = _lookup(value, "spe", "speed", default=None)
    if raw is None:
        raw = _lookup(_lookup(value, "stats", default=None), "spe", "speed", default=None)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _status(value: Any) -> str:
    raw = _lookup(value, "status", default=None)
    return to_id(raw)


def _recent_ids(history: Sequence["SwitchEvent"]) -> list[str]:
    return [event.active_id for event in history if event.active_id]


@dataclass(slots=True)
class SwitchEvent:
    """One recorded active-mon transition."""

    turn: int | None
    active_id: str
    battle_tag: str | None = None


@dataclass(slots=True)
class SwitchHistory:
    """Tracks recent active mons so the policy can damp bounce-back loops."""

    max_entries: int = 8
    events: list[SwitchEvent] = field(default_factory=list)

    def record(self, active: Any, turn: int | None = None, battle_tag: str | None = None) -> None:
        active_id = _identity(active)
        if not active_id:
            return
        self.events.append(SwitchEvent(turn=turn, active_id=active_id, battle_tag=to_id(battle_tag) or None))
        if len(self.events) > self.max_entries:
            del self.events[: len(self.events) - self.max_entries]

    def last_active_id(self) -> str | None:
        return self.events[-1].active_id if self.events else None

    def bounce_back_penalty(self, candidate: Any, turn: int | None = None, battle_tag: str | None = None) -> float:
        candidate_id = _identity(candidate)
        if not candidate_id or not self.events:
            return 0.0

        recent = [event for event in self.events if not battle_tag or event.battle_tag in {None, to_id(battle_tag)}]
        if not recent:
            return 0.0

        penalty = 0.0
        last = recent[-1]
        if last.active_id == candidate_id:
            age = _turn_distance(turn, last.turn)
            if age is None or age <= 4:
                penalty += 28.0

        if len(recent) >= 2:
            previous = recent[-2]
            if previous.active_id == candidate_id:
                age = _turn_distance(turn, previous.turn)
                if age is None or age <= 6:
                    penalty += 18.0

        if len(recent) >= 3:
            a, b, c = recent[-3], recent[-2], recent[-1]
            if a.active_id == c.active_id == candidate_id and a.active_id != b.active_id:
                penalty += 22.0

        return penalty

    def stay_bias(self, active: Any = None, *, turn: int | None = None, offensive_pressure: float = 0.0, defensive_risk: float = 0.0, trapped: bool = False) -> float:
        if trapped:
            return math.inf

        hp = _parse_hp_fraction(active)
        status = _status(active)

        bias = 12.0
        if hp is not None:
            if hp >= 0.85:
                bias += 8.0
            elif hp >= 0.65:
                bias += 4.0
            elif hp <= 0.35:
                bias -= 4.0

        if status:
            bias += 4.0

        if offensive_pressure >= 30.0:
            bias += 6.0
        if offensive_pressure >= defensive_risk * 0.8:
            bias += 6.0

        if turn is not None and turn <= 2:
            bias += 4.0

        if self.events:
            bias += min(6.0, len(self.events) * 0.5)

        return bias


def _turn_distance(current: int | None, previous: int | None) -> int | None:
    if current is None or previous is None:
        return None
    return max(0, int(current) - int(previous))


@dataclass(slots=True)
class SwitchDecision:
    """Result of comparing the best switch against the best move."""

    should_switch: bool
    threshold: float
    margin: float
    best_switch_score: float
    best_move_score: float


@dataclass(slots=True)
class SwitchPolicy:
    """Pure-Python switching policy with battle-history awareness."""

    history: SwitchHistory = field(default_factory=SwitchHistory)
    base_switch_margin: float = 12.0

    def record_active(self, active: Any, turn: int | None = None, battle_tag: str | None = None) -> None:
        self.history.record(active, turn=turn, battle_tag=battle_tag)

    def score_switch(
        self,
        candidate: Any,
        *,
        turn: int | None = None,
        battle_tag: str | None = None,
        active: Any = None,
        opponent: Any = None,
        offensive_pressure: float = 0.0,
        defensive_risk: float = 0.0,
        entry_penalty: float = 0.0,
        speed_advantage: bool | None = None,
        status_penalty: float | None = None,
        hp_fraction: float | None = None,
        trapped: bool = False,
    ) -> float:
        """Score a switch candidate.

        The caller can pass either real objects or dict-like snapshots. Any
        missing pieces fall back to defensive defaults.
        """

        if trapped or _as_bool(_lookup(candidate, "trapped", default=False)):
            return -math.inf
        if _as_bool(_lookup(candidate, "fainted", default=False)):
            return -math.inf

        hp = hp_fraction if hp_fraction is not None else _parse_hp_fraction(candidate)
        if hp is None:
            hp = 1.0

        offense = _as_float(offensive_pressure)
        defense = _as_float(defensive_risk)
        entry = _as_float(entry_penalty)

        score = hp * 40.0
        score += offense
        score -= defense
        score -= entry

        candidate_speed = _speed(candidate)
        opponent_speed = _speed(opponent)
        if speed_advantage is None:
            speed_advantage = candidate_speed > opponent_speed if opponent is not None else False
        if speed_advantage:
            score += 5.0

        status = status_penalty if status_penalty is not None else (8.0 if _status(candidate) else 0.0)
        score -= _as_float(status)

        score -= self.history.bounce_back_penalty(candidate, turn=turn, battle_tag=battle_tag)
        return score

    def score_switches(self, candidates: Iterable[Any], **kwargs: Any) -> list[tuple[float, Any]]:
        """Score and sort candidate switches from best to worst."""

        scored = [(self.score_switch(candidate, **kwargs), candidate) for candidate in candidates]
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    def best_switch(self, candidates: Iterable[Any], **kwargs: Any) -> Any | None:
        scored = self.score_switches(candidates, **kwargs)
        return scored[0][1] if scored else None

    def stay_bias_threshold(
        self,
        *,
        active: Any = None,
        opponent: Any = None,
        turn: int | None = None,
        offensive_pressure: float = 0.0,
        defensive_risk: float = 0.0,
        trapped: bool = False,
    ) -> float:
        """Return the margin the best switch must beat before we abandon the field."""

        if trapped:
            return math.inf

        threshold = self.base_switch_margin + self.history.stay_bias(
            active,
            turn=turn,
            offensive_pressure=offensive_pressure,
            defensive_risk=defensive_risk,
            trapped=trapped,
        )

        if opponent is not None and _status(opponent):
            threshold += 2.0

        if _as_float(_lookup(active, "current_hp_fraction", "hp_fraction", default=None), default=-1.0) <= 0.0:
            threshold = min(threshold, 10.0)

        return threshold

    def should_switch(
        self,
        best_switch_score: float,
        best_move_score: float,
        *,
        active: Any = None,
        opponent: Any = None,
        turn: int | None = None,
        offensive_pressure: float = 0.0,
        defensive_risk: float = 0.0,
        trapped: bool = False,
        threshold: float | None = None,
    ) -> bool:
        margin = self.stay_bias_threshold(
            active=active,
            opponent=opponent,
            turn=turn,
            offensive_pressure=offensive_pressure,
            defensive_risk=defensive_risk,
            trapped=trapped,
        )
        if threshold is not None:
            margin = threshold
        return best_switch_score > best_move_score + margin

    def decision(
        self,
        best_switch_score: float,
        best_move_score: float,
        *,
        active: Any = None,
        opponent: Any = None,
        turn: int | None = None,
        offensive_pressure: float = 0.0,
        defensive_risk: float = 0.0,
        trapped: bool = False,
        threshold: float | None = None,
    ) -> SwitchDecision:
        margin = self.stay_bias_threshold(
            active=active,
            opponent=opponent,
            turn=turn,
            offensive_pressure=offensive_pressure,
            defensive_risk=defensive_risk,
            trapped=trapped,
        )
        if threshold is not None:
            margin = threshold
        return SwitchDecision(
            should_switch=best_switch_score > best_move_score + margin,
            threshold=margin,
            margin=best_switch_score - best_move_score,
            best_switch_score=best_switch_score,
            best_move_score=best_move_score,
        )


def score_switch_candidate(
    candidate: Any,
    *,
    history: SwitchHistory | None = None,
    turn: int | None = None,
    battle_tag: str | None = None,
    active: Any = None,
    opponent: Any = None,
    offensive_pressure: float = 0.0,
    defensive_risk: float = 0.0,
    entry_penalty: float = 0.0,
    speed_advantage: bool | None = None,
    status_penalty: float | None = None,
    hp_fraction: float | None = None,
    trapped: bool = False,
) -> float:
    policy = SwitchPolicy(history=history or SwitchHistory())
    return policy.score_switch(
        candidate,
        turn=turn,
        battle_tag=battle_tag,
        active=active,
        opponent=opponent,
        offensive_pressure=offensive_pressure,
        defensive_risk=defensive_risk,
        entry_penalty=entry_penalty,
        speed_advantage=speed_advantage,
        status_penalty=status_penalty,
        hp_fraction=hp_fraction,
        trapped=trapped,
    )


def stay_bias_threshold(
    *,
    history: SwitchHistory | None = None,
    active: Any = None,
    opponent: Any = None,
    turn: int | None = None,
    offensive_pressure: float = 0.0,
    defensive_risk: float = 0.0,
    trapped: bool = False,
    base_switch_margin: float = 12.0,
) -> float:
    policy = SwitchPolicy(history=history or SwitchHistory(), base_switch_margin=base_switch_margin)
    return policy.stay_bias_threshold(
        active=active,
        opponent=opponent,
        turn=turn,
        offensive_pressure=offensive_pressure,
        defensive_risk=defensive_risk,
        trapped=trapped,
    )


def should_switch(
    best_switch_score: float,
    best_move_score: float,
    *,
    history: SwitchHistory | None = None,
    active: Any = None,
    opponent: Any = None,
    turn: int | None = None,
    offensive_pressure: float = 0.0,
    defensive_risk: float = 0.0,
    trapped: bool = False,
    threshold: float | None = None,
    base_switch_margin: float = 12.0,
) -> bool:
    policy = SwitchPolicy(history=history or SwitchHistory(), base_switch_margin=base_switch_margin)
    return policy.should_switch(
        best_switch_score,
        best_move_score,
        active=active,
        opponent=opponent,
        turn=turn,
        offensive_pressure=offensive_pressure,
        defensive_risk=defensive_risk,
        trapped=trapped,
        threshold=threshold,
    )


def best_switch(candidates: Iterable[Any], *, history: SwitchHistory | None = None, **kwargs: Any) -> Any | None:
    policy = SwitchPolicy(history=history or SwitchHistory())
    return policy.best_switch(candidates, **kwargs)
