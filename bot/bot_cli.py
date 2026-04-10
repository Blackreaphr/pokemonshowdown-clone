from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from poke_env.concurrency import handle_threaded_coroutines
from poke_env.player.baselines import RandomPlayer, SimpleHeuristicsPlayer
from poke_env.ps_client.account_configuration import AccountConfiguration
from poke_env.ps_client.server_configuration import LocalhostServerConfiguration

from .benchmark import print_diagnostics_report, run_benchmark_suite
from .competitive_bot import CompetitiveShowdownBot
from .data_sync import sync_everything
from .team_repo import TeamRepository


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline competitive Pokemon Showdown bot controller.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser("sync-data", help="Refresh cached Smogon and Showdown data.")
    sync_parser.set_defaults(func=run_sync)

    accept_parser = subparsers.add_parser("accept", help="Log in and accept a local challenge.")
    accept_parser.add_argument("--format", default="gen9ou")
    accept_parser.add_argument("--bot-name", default="OfflineBoss")
    accept_parser.add_argument("--opponent")
    accept_parser.add_argument("--challenges", type=int, default=9999)
    accept_parser.set_defaults(func=run_accept)

    challenge_parser = subparsers.add_parser("challenge", help="Challenge another local user.")
    challenge_parser.add_argument("--format", default="gen9ou")
    challenge_parser.add_argument("--bot-name", default="OfflineBoss")
    challenge_parser.add_argument("--opponent", required=True)
    challenge_parser.add_argument("--challenges", type=int, default=1)
    challenge_parser.set_defaults(func=run_challenge)

    dump_parser = subparsers.add_parser("dump-team", help="Print the currently selected local team for a format.")
    dump_parser.add_argument("--format", default="gen9ou")
    dump_parser.set_defaults(func=run_dump_team)

    selftest_parser = subparsers.add_parser("selftest", help="Battle local baseline bots to verify the setup.")
    selftest_parser.add_argument("--format", default="gen9ou")
    selftest_parser.add_argument("--battles", type=int, default=3)
    selftest_parser.add_argument("--opponent-style", choices=("simple", "random"), default="simple")
    selftest_parser.set_defaults(func=run_selftest)

    benchmark_parser = subparsers.add_parser("benchmark", help="Run repeated local diagnostics across one or more formats.")
    benchmark_parser.add_argument("formats", nargs="+", help="One or more format ids such as gen9ou or gen5ou.")
    benchmark_parser.add_argument("--battles", type=int, default=3)
    benchmark_parser.add_argument("--opponent-style", choices=("simple", "random"), default="simple")
    benchmark_parser.add_argument("--seed", type=int, default=None)
    benchmark_parser.set_defaults(func=run_benchmark)

    return parser


def create_bot(args) -> CompetitiveShowdownBot:
    repository = TeamRepository()
    selected_team = repository.pick_team(args.format)
    bot_name = getattr(args, "bot_name", "OfflineBoss")
    account = AccountConfiguration(bot_name, "")
    return CompetitiveShowdownBot(
        repository=repository,
        selected_team=selected_team,
        account_configuration=account,
        battle_format=args.format,
        server_configuration=LocalhostServerConfiguration,
        start_listening=True,
        log_level=logging.INFO,
    )


def run_sync(_args) -> None:
    sync_everything()
    print("Synced Smogon and Showdown competitive data.")


def run_dump_team(args) -> None:
    repository = TeamRepository()
    selected_team = repository.pick_team(args.format)
    print(f"# source: {selected_team.source}")
    print(f"# name: {selected_team.name}")
    print(selected_team.exported)


async def _join_visibility_rooms(bot: CompetitiveShowdownBot) -> None:
    async def _join_lobby() -> None:
        await bot.ps_client.logged_in.wait()
        await bot.ps_client.send_message("/join lobby")

    await handle_threaded_coroutines(_join_lobby(), bot.ps_client.loop)


def run_accept(args) -> None:
    async def _main():
        bot = create_bot(args)
        print(f"{bot.username} ready on {args.format} using {bot.selected_team.source}:{bot.selected_team.name}")
        await _join_visibility_rooms(bot)
        await bot.accept_challenges(args.opponent, args.challenges, packed_team=bot.selected_team.packed)

    asyncio.run(_main())


def run_challenge(args) -> None:
    async def _main():
        bot = create_bot(args)
        print(f"{bot.username} challenging {args.opponent} on {args.format}")
        await _join_visibility_rooms(bot)
        await bot.send_challenges(args.opponent, args.challenges)

    asyncio.run(_main())


def run_selftest(args) -> None:
    async def _main():
        suffix = uuid.uuid4().hex[:8]
        repository = TeamRepository(seed=1)
        args.bot_name = f"OB{suffix}"
        bot = create_bot(args)
        opponent_team = repository.pick_team(args.format)
        opponent_account = AccountConfiguration(f"OS{suffix}", "")
        if args.opponent_style == "random":
            opponent = RandomPlayer(
                account_configuration=opponent_account,
                battle_format=args.format,
                server_configuration=LocalhostServerConfiguration,
                start_listening=True,
                team=opponent_team.packed,
            )
        else:
            opponent = SimpleHeuristicsPlayer(
                account_configuration=opponent_account,
                battle_format=args.format,
                server_configuration=LocalhostServerConfiguration,
                start_listening=True,
                team=opponent_team.packed,
            )
        await bot.battle_against(opponent, n_battles=args.battles)
        print(f"{bot.username} won {bot.n_won_battles} / {args.battles} local test battles.")

    asyncio.run(_main())


def run_benchmark(args) -> None:
    results = run_benchmark_suite(
        args.formats,
        battles=args.battles,
        opponent_style=args.opponent_style,
        seed=args.seed,
    )
    print_diagnostics_report(results)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
