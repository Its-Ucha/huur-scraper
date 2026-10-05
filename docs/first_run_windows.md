# First run on Windows (local)

You can run this without Discord first; CLI runs write alerts to the log.

## 1) Python and venv
- Install Python 3.12+
- In PowerShell:
  - `./scripts/setup_local.ps1`

This script uses only `./.venv/Scripts/python.exe` and does not install globally.

## 2) Environment file
- Copy `.env.example` to `.env`
- You can leave the `DISCORD_*` values empty for CLI runs

## 3) First run
- `./scripts/run_once.ps1`

The SQLite database is auto-created at `data/huur_scraper.db`.

## 4) Check output data quickly
Use any SQLite viewer or run:
- `.\.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/huur_scraper.db'); print(c.execute('select count(*) from listings').fetchone())"`

## 5) Run the Discord bot locally (optional)
1. Create the bot and collect the IDs as in [deploy_portainer.md](deploy_portainer.md) steps 1–2.
2. Put the `DISCORD_*` values in `.env`.
3. Run `.\.venv\Scripts\python.exe -m src.bot` and try `/status` in your server.
