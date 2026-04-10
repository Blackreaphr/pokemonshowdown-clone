from __future__ import annotations

import argparse
import json
import re
from typing import Any

import requests

from .showdown_tools import CACHE_ROOT, CATALOG_ROOT, ensure_data_dirs, load_json, write_json

SHOWDOWN_SET_INDEX = "https://play.pokemonshowdown.com/data/sets/"
SHOWDOWN_CORE_BASE = "https://play.pokemonshowdown.com/data"
SMOGON_TEAM_INDEX = "https://pkmn.github.io/smogon/data/teams/index.json"

SHOWDOWN_CORE_FILES = (
    "learnsets.json",
    "moves.json",
    "pokedex.json",
)


def _download_json(session: requests.Session, url: str) -> Any:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def sync_showdown_sets(session: requests.Session) -> list[str]:
    listing = session.get(SHOWDOWN_SET_INDEX, timeout=60)
    listing.raise_for_status()
    files = sorted(set(re.findall(r'href="([a-z0-9-]+\.json)"', listing.text)))
    target_root = CACHE_ROOT / "showdown_sets"
    target_root.mkdir(parents=True, exist_ok=True)
    for filename in files:
        payload = _download_json(session, f"{SHOWDOWN_SET_INDEX}{filename}")
        write_json(target_root / filename, payload)
    return files


def sync_showdown_core(session: requests.Session) -> None:
    target_root = CACHE_ROOT / "showdown_core"
    target_root.mkdir(parents=True, exist_ok=True)
    for filename in SHOWDOWN_CORE_FILES:
        payload = _download_json(session, f"{SHOWDOWN_CORE_BASE}/{filename}")
        write_json(target_root / filename, payload)


def sync_smogon_teams(session: requests.Session) -> dict[str, list[int]]:
    index = _download_json(session, SMOGON_TEAM_INDEX)
    target_root = CACHE_ROOT / "smogon_teams"
    target_root.mkdir(parents=True, exist_ok=True)
    write_json(target_root / "index.json", index)
    for format_id in sorted(index):
        payload = _download_json(session, f"https://pkmn.github.io/smogon/data/teams/{format_id}.json")
        write_json(target_root / f"{format_id}.json", payload)
    return index


def build_catalogs() -> None:
    core_root = CACHE_ROOT / "showdown_core"
    sets_root = CACHE_ROOT / "showdown_sets"
    teams_root = CACHE_ROOT / "smogon_teams"

    pokedex = load_json(core_root / "pokedex.json")
    species_by_generation: dict[str, list[str]] = {}
    for species, data in pokedex.items():
        if data.get("num", 0) <= 0:
            continue
        generation = f"gen{data.get('gen', 0)}"
        species_by_generation.setdefault(generation, []).append(species)
    for generation in species_by_generation:
        species_by_generation[generation].sort()
    write_json(CATALOG_ROOT / "species-by-generation.json", species_by_generation)

    competitive_sets: dict[str, dict[str, list[dict[str, Any]]]] = {}
    format_summary: dict[str, dict[str, int]] = {}
    for path in sorted(sets_root.glob("*.json")):
        format_id = path.stem
        payload = load_json(path)
        format_sets: dict[str, list[dict[str, Any]]] = {}
        for species, named_sets in payload.get("dex", {}).items():
            flattened: list[dict[str, Any]] = []
            for set_name, details in named_sets.items():
                normalized = {
                    "setName": set_name,
                    "species": species,
                    "ability": details.get("ability"),
                    "item": details.get("item"),
                    "nature": details.get("nature"),
                    "teraType": details.get("teraType"),
                    "gender": details.get("gender"),
                    "level": details.get("level"),
                    "moves": details.get("moves", []),
                    "evs": details.get("evs", {}),
                    "ivs": details.get("ivs", {}),
                }
                flattened.append(normalized)
            format_sets[species] = flattened
        competitive_sets[format_id] = format_sets
        format_summary[format_id] = {
            "species": len(format_sets),
            "sets": sum(len(entries) for entries in format_sets.values()),
        }

    team_index = load_json(teams_root / "index.json") if (teams_root / "index.json").exists() else {}
    for format_id, stats in team_index.items():
        summary = format_summary.setdefault(format_id, {})
        summary["teamFileBytes"] = stats[0]
        summary["teamEntries"] = stats[1]

    write_json(CATALOG_ROOT / "competitive-sets.json", competitive_sets)
    write_json(CATALOG_ROOT / "format-summary.json", format_summary)


def sync_everything() -> None:
    ensure_data_dirs()
    with requests.Session() as session:
        session.headers.update({"User-Agent": "offline-showdown-bot/1.0"})
        sync_showdown_core(session)
        sync_showdown_sets(session)
        sync_smogon_teams(session)
    build_catalogs()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Smogon and Pokemon Showdown competitive data locally.")
    parser.parse_args()
    sync_everything()
    summary_path = CATALOG_ROOT / "format-summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(f"Synced offline data for {len(summary)} formats into {CACHE_ROOT}")


if __name__ == "__main__":
    main()

