# Offline Pokemon Showdown

This workspace contains:

- the official `smogon/pokemon-showdown` server in `server/`
- the official `smogon/pokemon-showdown-client` in `client/`
- a local Python bot in `bot/` that connects to `localhost`
- cached Smogon and Showdown competitive data under `data/`

## What the bot does

The bot is built for singles formats and is format-aware.

- It uses cached Smogon sample teams when available.
- If a sample-team file is missing for a format, it assembles a team from Showdown's competitive sample sets.
- Teams are validated locally with Pokemon Showdown before use.
- Move choice scores damage, typing, speed control, hazards, recovery, setup, status, pivoting, and switch quality.
- Opponent sets are inferred from cached Showdown sample sets using revealed species, moves, item, and ability.

## One-time sync

Run this once to cache the offline format data:

```powershell
cd C:\Users\ndalba\pokemon-showdown-offline
.\.venv\Scripts\python.exe -m bot.bot_cli sync-data
```

This writes:

- `data\cache\showdown_core\*.json`
- `data\cache\showdown_sets\*.json`
- `data\cache\smogon_teams\*.json`
- `data\catalog\competitive-sets.json`
- `data\catalog\species-by-generation.json`

## Build the local client

Run this once after dependency install:

```powershell
cd C:\Users\ndalba\pokemon-showdown-offline\client
node build
```

## Guest-only local play

The local server is started with `--no-security`, so you do not need a registered Showdown account.

- In the browser client, you can stay on the default Guest account.
- The bot can auto-generate a temporary guest name if you do not pass `-BotName`.
- This is local-only; nothing needs to authenticate against the public Showdown login flow.

## Start everything

Use the one-click launcher:

```powershell
cd C:\Users\ndalba\pokemon-showdown-offline
.\start_offline_stack.ps1 -Format gen9ou
```

Open:

`http://127.0.0.1:8080/testclient.html?~~localhost:8000`

Stay as Guest in the browser, then challenge the printed bot guest name in the same format you launched.

## Individual commands

Server:

```powershell
.\start_server.ps1
```

Client:

```powershell
.\start_client.ps1
```

Bot with an auto-generated guest name:

```powershell
.\start_bot.ps1 -Format gen4ou
```

Bot with a fixed guest name:

```powershell
.\start_bot.ps1 -Format gen4ou -BotName OfflineBoss
```

Dump the team the bot selected for a format:

```powershell
.\.venv\Scripts\python.exe -m bot.bot_cli dump-team --format gen5ou
```

Run a local self-test against a baseline bot:

```powershell
.\.venv\Scripts\python.exe -m bot.bot_cli selftest --format gen9ou --battles 5
```

Stop background processes started by the stack launcher:

```powershell
.\stop_offline_stack.ps1
```

## Notes

- The bot currently targets singles formats. Doubles/VGC need a separate doubles battle class.
- Legality comes from local Pokemon Showdown validation and Showdown learnsets.
- Competitive team content comes from Smogon/Showdown cached data, so the setup still works after you go offline.
