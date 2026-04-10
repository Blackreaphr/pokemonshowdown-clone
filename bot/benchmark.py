from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Literal, Sequence

from poke_env.player.baselines import RandomPlayer, SimpleHeuristicsPlayer
from poke_env.ps_client.account_configuration import AccountConfiguration
from poke_env.ps_client.server_configuration import LocalhostServerConfiguration

from .competitive_bot import CompetitiveShowdownBot
from .team_repo import SelectedTeam, TeamRepository

OpponentStyle = Literal["simple", "random"]

__all__ = [
    "BenchmarkCase",
    "BenchmarkResult",
    "BenchmarkSummary",
    "build_cases",
    "print_diagnostics_report",
    "render_diagnostics_report",
    "run_benchmark_case",
    "run_benchmark_suite",
    "summarize_benchmark_results",
]


@dataclass(frozen=True)
class BenchmarkCase:
    """A single local self-test workload for one format."""

    format_id: str
    battles: int = 3
    opponent_style: OpponentStyle = "simple"


@dataclass(frozen=True)
class BenchmarkResult:
    """Outcome and timing data for one benchmark case."""

    format_id: str
    battles: int
    wins: int
    losses: int
    incomplete: int
    duration_seconds: float
    average_seconds_per_battle: float
    opponent_style: OpponentStyle
    team_source: str
    team_name: str
    bot_name: str

    @property
    def completed_battles(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        if self.completed_battles <= 0:
            return 0.0
        return self.wins / self.completed_battles


@dataclass(frozen=True)
class BenchmarkSummary:
    """Aggregated benchmark totals across all requested formats."""

    cases: int
    requested_battles: int
    completed_battles: int
    wins: int
    losses: int
    incomplete: int
    duration_seconds: float

    @property
    def average_seconds_per_battle(self) -> float:
        if self.requested_battles <= 0:
            return 0.0
        return self.duration_seconds / self.requested_battles

    @property
    def win_rate(self) -> float:
        if self.completed_battles <= 0:
            return 0.0
        return self.wins / self.completed_battles


def build_cases(
    format_ids: Sequence[str],
    *,
    battles: int = 3,
    opponent_style: OpponentStyle = "simple",
) -> list[BenchmarkCase]:
    """Normalize format ids into a concrete list of benchmark cases."""

    return [BenchmarkCase(format_id=format_id, battles=battles, opponent_style=opponent_style) for format_id in format_ids]


def run_benchmark_case(
    format_id: str,
    *,
    battles: int = 3,
    opponent_style: OpponentStyle = "simple",
    seed: int | None = None,
) -> BenchmarkResult:
    """Run one local benchmark case and return the result."""

    return asyncio.run(_run_benchmark_case_async(BenchmarkCase(format_id, battles, opponent_style), seed=seed))


def run_benchmark_suite(
    format_ids: Sequence[str],
    *,
    battles: int = 3,
    opponent_style: OpponentStyle = "simple",
    seed: int | None = None,
) -> list[BenchmarkResult]:
    """Run local benchmark cases sequentially across one or more formats."""

    cases = build_cases(format_ids, battles=battles, opponent_style=opponent_style)
    return asyncio.run(_run_benchmark_suite_async(cases, seed=seed))


def summarize_benchmark_results(results: Sequence[BenchmarkResult]) -> BenchmarkSummary:
    """Summarize benchmark results into compact totals."""

    cases = len(results)
    requested_battles = sum(result.battles for result in results)
    completed_battles = sum(result.completed_battles for result in results)
    wins = sum(result.wins for result in results)
    losses = sum(result.losses for result in results)
    incomplete = sum(result.incomplete for result in results)
    duration_seconds = sum(result.duration_seconds for result in results)
    return BenchmarkSummary(
        cases=cases,
        requested_battles=requested_battles,
        completed_battles=completed_battles,
        wins=wins,
        losses=losses,
        incomplete=incomplete,
        duration_seconds=duration_seconds,
    )


def render_diagnostics_report(results: Sequence[BenchmarkResult]) -> str:
    """Render a compact human-readable diagnostics report."""

    summary = summarize_benchmark_results(results)
    lines = [
        "Offline Pokemon Showdown bot diagnostics",
        f"Cases: {summary.cases} | Requested battles: {summary.requested_battles} | Completed battles: {summary.completed_battles}",
        f"Wins: {summary.wins} | Losses: {summary.losses} | Incomplete: {summary.incomplete}",
        f"Total duration: {summary.duration_seconds:.2f}s | Avg per requested battle: {summary.average_seconds_per_battle:.2f}s | Win rate: {summary.win_rate:.1%}",
    ]
    for result in results:
        lines.append(
            f"- {result.format_id}: {result.wins}/{result.battles} wins, "
            f"{result.losses} losses, {result.incomplete} incomplete, "
            f"{result.average_seconds_per_battle:.2f}s/battle, "
            f"team {result.team_source}:{result.team_name}"
        )
    return "\n".join(lines)


def print_diagnostics_report(results: Sequence[BenchmarkResult]) -> None:
    """Print the compact diagnostics report to stdout."""

    print(render_diagnostics_report(results))


async def _run_benchmark_suite_async(cases: Sequence[BenchmarkCase], *, seed: int | None = None) -> list[BenchmarkResult]:
    results: list[BenchmarkResult] = []
    for index, case in enumerate(cases):
        case_seed = None if seed is None else seed + index
        results.append(await _run_benchmark_case_async(case, seed=case_seed))
    return results


async def _run_benchmark_case_async(case: BenchmarkCase, *, seed: int | None = None) -> BenchmarkResult:
    repository = TeamRepository(seed=seed)
    selected_team = repository.pick_team(case.format_id)
    bot_name = _make_guest_name("BM")
    opponent_name = _make_guest_name("OP")
    bot = _build_bot(
        repository=repository,
        selected_team=selected_team,
        format_id=case.format_id,
        bot_name=bot_name,
    )
    opponent_repository = TeamRepository(seed=None if seed is None else seed + 1)
    opponent_team = opponent_repository.pick_team(case.format_id)
    opponent = _build_opponent(
        case.format_id,
        case.opponent_style,
        opponent_name,
        opponent_team,
    )

    bot_start = bot.n_won_battles
    opponent_start = opponent.n_won_battles
    started_at = time.perf_counter()
    await bot.battle_against(opponent, n_battles=case.battles)
    duration_seconds = time.perf_counter() - started_at

    wins = bot.n_won_battles - bot_start
    losses = opponent.n_won_battles - opponent_start
    completed_battles = wins + losses
    incomplete = max(0, case.battles - completed_battles)
    return BenchmarkResult(
        format_id=case.format_id,
        battles=case.battles,
        wins=wins,
        losses=losses,
        incomplete=incomplete,
        duration_seconds=duration_seconds,
        average_seconds_per_battle=duration_seconds / max(1, case.battles),
        opponent_style=case.opponent_style,
        team_source=selected_team.source,
        team_name=selected_team.name,
        bot_name=bot_name,
    )


def _build_bot(
    *,
    repository: TeamRepository,
    selected_team: SelectedTeam,
    format_id: str,
    bot_name: str,
) -> CompetitiveShowdownBot:
    account = AccountConfiguration(bot_name, "")
    return CompetitiveShowdownBot(
        repository=repository,
        selected_team=selected_team,
        account_configuration=account,
        battle_format=format_id,
        server_configuration=LocalhostServerConfiguration,
        start_listening=True,
        log_level=logging.WARNING,
    )


def _build_opponent(
    format_id: str,
    opponent_style: OpponentStyle,
    opponent_name: str,
    opponent_team: SelectedTeam,
):
    account = AccountConfiguration(opponent_name, "")
    player_kwargs = {
        "account_configuration": account,
        "battle_format": format_id,
        "server_configuration": LocalhostServerConfiguration,
        "start_listening": True,
        "team": opponent_team.packed,
        "log_level": logging.WARNING,
    }
    if opponent_style == "random":
        return RandomPlayer(**player_kwargs)
    return SimpleHeuristicsPlayer(**player_kwargs)


def _make_guest_name(prefix: str) -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark the offline Pokemon Showdown bot locally.")
    parser.add_argument("formats", nargs="+", help="One or more format ids, such as gen9ou or gen5ou.")
    parser.add_argument("--battles", type=int, default=3, help="Number of battles to run for each format.")
    parser.add_argument(
        "--opponent-style",
        choices=("simple", "random"),
        default="simple",
        help="Opponent baseline to use for the benchmark.",
    )
    parser.add_argument("--seed", type=int, default=None, help="Optional seed for deterministic team selection.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)
    results = run_benchmark_suite(
        args.formats,
        battles=args.battles,
        opponent_style=args.opponent_style,
        seed=args.seed,
    )
    print_diagnostics_report(results)


if __name__ == "__main__":
    main()
