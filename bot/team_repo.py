from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from .showdown_tools import CACHE_ROOT, export_team, load_json, pack_team, to_id, validate_team
from .team_policy import assess_team, score_candidate_set, select_team_from_pool

ROLE_MOVES = {
    "hazards": {"stealthrock", "spikes", "toxicspikes", "stickyweb"},
    "removal": {"rapidspin", "defog", "mortalspin", "courtchange", "tidyup"},
    "speed": {"thunderwave", "icywind", "tailwind", "trickroom"},
    "pivot": {"uturn", "voltswitch", "flipturn", "teleport", "partingshot", "chillyreception"},
    "recovery": {"recover", "roost", "slackoff", "softboiled", "moonlight", "synthesis", "wish"},
    "setup": {"swordsdance", "dragondance", "nastyplot", "calmmind", "quiverdance", "shellsmash", "bulkup", "irondefense", "agility"},
}


@dataclass
class SelectedTeam:
    format_id: str
    source: str
    name: str
    team: list[dict[str, Any]]
    packed: str
    exported: str


class TeamRepository:
    def __init__(self, seed: int | None = None):
        self.random = random.Random(seed)
        self.teams_root = CACHE_ROOT / "smogon_teams"
        self.sets_root = CACHE_ROOT / "showdown_sets"
        self.team_index = load_json(self.teams_root / "index.json") if (self.teams_root / "index.json").exists() else {}
        self._species_sets_cache: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def available_formats(self) -> list[str]:
        formats = {path.stem for path in self.sets_root.glob("*.json")}
        formats.update(self.team_index.keys())
        return sorted(formats)

    def load_team_candidates(self, format_id: str) -> list[dict[str, Any]]:
        path = self.teams_root / f"{format_id}.json"
        return load_json(path) if path.exists() else []

    def load_species_sets(self, format_id: str) -> dict[str, list[dict[str, Any]]]:
        if format_id in self._species_sets_cache:
            return self._species_sets_cache[format_id]
        path = self.sets_root / f"{format_id}.json"
        if not path.exists():
            self._species_sets_cache[format_id] = {}
            return {}
        payload = load_json(path)
        species_sets: dict[str, list[dict[str, Any]]] = {}
        for species, named_sets in payload.get("dex", {}).items():
            normalized: list[dict[str, Any]] = []
            for set_name, details in named_sets.items():
                normalized.append(
                    {
                        "species": species,
                        "setName": set_name,
                        "ability": details.get("ability"),
                        "item": details.get("item"),
                        "nature": details.get("nature"),
                        "teraType": details.get("teraType"),
                        "gender": details.get("gender"),
                        "level": details.get("level"),
                        "evs": details.get("evs", {}),
                        "ivs": details.get("ivs", {}),
                        "moves": details.get("moves", []),
                    }
                )
            species_sets[species] = normalized
        self._species_sets_cache[format_id] = species_sets
        return species_sets

    def infer_opponent_sets(
        self,
        format_id: str,
        species: str,
        *,
        ability: str | None = None,
        item: str | None = None,
        revealed_moves: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        revealed_moves = {to_id(move) for move in (revealed_moves or set())}
        candidates = []
        for candidate in self.load_species_sets(format_id).get(species, []):
            if ability and candidate.get("ability") and to_id(candidate["ability"]) != to_id(ability):
                continue
            if item and candidate.get("item") and to_id(candidate["item"]) != to_id(item):
                continue
            candidate_moves = {to_id(move) for move in candidate.get("moves", [])}
            if not revealed_moves.issubset(candidate_moves):
                continue
            candidates.append(candidate)
        return candidates

    def pick_team(self, format_id: str) -> SelectedTeam:
        team_candidates = self.load_team_candidates(format_id)
        scored_teams: list[tuple[float, dict[str, Any]]] = []
        for candidate in team_candidates:
            valid, _ = validate_team(format_id, candidate["data"])
            if not valid:
                continue
            score = assess_team(candidate["data"], format_id=format_id).score
            scored_teams.append((score, candidate))
        if scored_teams:
            best_score, best_candidate = max(scored_teams, key=lambda item: item[0])
            exported = export_team(best_candidate["data"])
            return SelectedTeam(
                format_id=format_id,
                source=f"smogon-teams:{best_score:.1f}",
                name=best_candidate.get("name", format_id),
                team=best_candidate["data"],
                packed=pack_team(best_candidate["data"]),
                exported=exported,
            )
        return self._build_team_from_sets(format_id)

    def _build_team_from_sets(self, format_id: str) -> SelectedTeam:
        species_sets = self.load_species_sets(format_id)
        if not species_sets:
            raise ValueError(f"No cached teams or sets found for format '{format_id}'. Run sync-data first.")

        pool = []
        for sets in species_sets.values():
            for candidate in sets:
                pool.append(candidate)
        ranked_pool = sorted(pool, key=lambda candidate: score_candidate_set(candidate, format_id=format_id), reverse=True)

        best_team: list[dict[str, Any]] | None = None
        best_score = float("-inf")
        search_starts = min(24, max(1, len(ranked_pool) - 5))
        window = min(len(ranked_pool), 96)
        for start in range(search_starts):
            subset = ranked_pool[start : start + window]
            if len(subset) < 6:
                break
            try:
                chosen = select_team_from_pool(subset, team_size=6, format_id=format_id)
            except ValueError:
                continue
            valid, _ = validate_team(format_id, chosen)
            if not valid:
                continue
            score = assess_team(chosen, format_id=format_id).score
            if score > best_score:
                best_score = score
                best_team = list(chosen)

        if best_team is None:
            best_team = self._fallback_random_assembly(format_id, ranked_pool)
            best_score = assess_team(best_team, format_id=format_id).score

        exported = export_team(best_team)
        return SelectedTeam(
            format_id=format_id,
            source=f"showdown-sets:{best_score:.1f}",
            name=f"{format_id}-assembled",
            team=best_team,
            packed=pack_team(best_team),
            exported=exported,
        )

    def _fallback_random_assembly(self, format_id: str, pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
        weighted_pool = [(candidate, self._role_score(candidate)) for candidate in pool]
        weighted_pool.sort(key=lambda item: item[1], reverse=True)
        for _ in range(150):
            chosen: list[dict[str, Any]] = []
            chosen_species: set[str] = set()
            role_counts: dict[str, int] = {}
            for candidate, _ in weighted_pool:
                species = candidate.get("species", "")
                if species in chosen_species:
                    continue
                if len(chosen) >= 6:
                    break
                candidate_roles = self._roles(candidate)
                bonus = sum(2 for role in candidate_roles if role_counts.get(role, 0) == 0)
                if len(chosen) < 3 or bonus > 0 or self.random.random() > 0.75:
                    chosen.append(candidate)
                    chosen_species.add(species)
                    for role in candidate_roles:
                        role_counts[role] = role_counts.get(role, 0) + 1
            if len(chosen) == 6:
                valid, _ = validate_team(format_id, chosen)
                if valid:
                    return chosen
        raise ValueError(f"Could not assemble a legal team for format '{format_id}' from cached data.")

    def _roles(self, candidate: dict[str, Any]) -> set[str]:
        roles = set()
        moves = {to_id(move) for move in candidate.get("moves", [])}
        for role, role_moves in ROLE_MOVES.items():
            if moves & role_moves:
                roles.add(role)
        return roles

    def _role_score(self, candidate: dict[str, Any]) -> int:
        score = len(self._roles(candidate)) * 10
        if candidate.get("item"):
            score += 2
        if candidate.get("teraType"):
            score += 1
        score += len(candidate.get("moves", []))
        return score
