from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SERVER_ROOT = ROOT / "server"
DATA_ROOT = ROOT / "data"
CACHE_ROOT = DATA_ROOT / "cache"
CATALOG_ROOT = DATA_ROOT / "catalog"
RUNTIME_ROOT = ROOT / "runtime"

STAT_ORDER = ("hp", "atk", "def", "spa", "spd", "spe")


def ensure_data_dirs() -> None:
    for path in (
        DATA_ROOT,
        CACHE_ROOT,
        CACHE_ROOT / "smogon_teams",
        CACHE_ROOT / "showdown_sets",
        CACHE_ROOT / "showdown_core",
        CATALOG_ROOT,
        RUNTIME_ROOT,
    ):
        path.mkdir(parents=True, exist_ok=True)


def to_id(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def render_evs_or_ivs(label: str, values: dict[str, int] | None) -> list[str]:
    if not values:
        return []
    chunks = [f"{values[stat]} {stat.upper()}" for stat in STAT_ORDER if values.get(stat) is not None]
    return [f"{label}: {' / '.join(chunks)}"] if chunks else []


def export_team(team: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for mon in team:
        species = mon["species"]
        header = species
        item = mon.get("item")
        if item:
            header = f"{header} @ {item}"
        lines = [header]
        ability = mon.get("ability")
        if ability:
            lines.append(f"Ability: {ability}")
        gender = mon.get("gender")
        if gender:
            lines.append(f"Gender: {gender}")
        level = mon.get("level")
        if level:
            lines.append(f"Level: {level}")
        shiny = mon.get("shiny")
        if shiny:
            lines.append("Shiny: Yes")
        happiness = mon.get("happiness")
        if happiness is not None:
            lines.append(f"Happiness: {happiness}")
        pokeball = mon.get("pokeball")
        if pokeball:
            lines.append(f"Pokeball: {pokeball}")
        tera_type = mon.get("teraType")
        if tera_type:
            lines.append(f"Tera Type: {tera_type}")
        lines.extend(render_evs_or_ivs("EVs", mon.get("evs")))
        nature = mon.get("nature")
        if nature:
            lines.append(f"{nature} Nature")
        lines.extend(render_evs_or_ivs("IVs", mon.get("ivs")))
        for move in mon.get("moves", []):
            lines.append(f"- {move}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _run_showdown_cli(command: str, team_text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", "pokemon-showdown", command],
        cwd=SERVER_ROOT,
        input=team_text,
        capture_output=True,
        text=True,
        check=False,
    )


def pack_team(team: list[dict[str, Any]]) -> str:
    exported = export_team(team)
    result = _run_showdown_cli("pack-team", exported)
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(details or "Pokemon Showdown pack-team failed")
    return result.stdout.strip()


def validate_team(format_id: str, team: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    exported = export_team(team)
    result = subprocess.run(
        ["node", "pokemon-showdown", "validate-team", format_id],
        cwd=SERVER_ROOT,
        input=exported,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode == 0:
        return True, []
    errors = [line for line in combined.splitlines() if line.strip()]
    return False, errors


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
