# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A compliance-first rental listing aggregator for the Delft/The Hague area. Each run scrapes the enabled sources, filters listings against a profile set in `.env`, stores them in SQLite with dedupe, and posts Discord alerts for new or changed matches. In production it runs as a long-lived Discord bot (`python -m src.bot`) in Docker on a homelab, deployed through a Portainer Git stack (`docker-compose.yml`, `docs/deploy_portainer.md`). The bot's `tasks.loop` runs a scrape cycle every `SCRAPE_INTERVAL_MINUTES` in a worker thread.

## Commands

All commands must run from the repo root, because `REGISTRY_FILE` in `src/scrapers/factories.py` points to `src/config/sources.yaml` with a relative path and the default DB and log paths are relative too.

```powershell
./scripts/setup_local.ps1                         # create .venv + install requirements-dev.txt (Windows)
./scripts/run_once.ps1                            # one scrape cycle, all SCRAPE-mode sources
./scripts/run_once.ps1 --sources vbent,wobeco     # only selected sources
.\.venv\Scripts\python.exe -m src.main --listings --limit 100
.\.venv\Scripts\python.exe -m src.main --prune-non-matches   # delete stored rows that no longer match the profile
.\.venv\Scripts\python.exe -m src.bot                      # run the Discord bot locally (needs DISCORD_* in .env)
docker exec huur-scraper python -m src.main --listings     # CLI inside the deployed container
```

Runtime dependencies are in `requirements.txt` (the Docker image installs only these). `requirements-dev.txt` adds pytest.

Tests use `unittest` style, and pytest is installed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe -m pytest tests/test_vbent.py -k pagination
```

There is no linter or formatter configured. `test.py` at the root is a scratch script and not a test.

## Architecture

Pipeline: `src/bot/client.py::HuurBot.run_cycle` (or `main.py --once`) → `scrapers/runner.py::run_all_sources` → per source: `scraper.search()` → `filtering/rules.py::evaluate_listing` → `storage/sqlite_store.py::upsert_listing` → `Notifier.notify_listing` (`notify/discord_notifier.py` in the bot, `notify/base.py::LogNotifier` in the CLI).

- **Source registration is split across two places.** `src/config/sources.yaml` declares the policy for each source (`mode`, `max_retries`, `auto_disable_on_block`). `SOURCE_FACTORIES` in `src/scrapers/factories.py` maps the source name to its scraper class. The runner iterates over the YAML. Only `mode: SCRAPE` sources run (`policy/risk_policy.py::is_collection_allowed`). A YAML entry without a factory is logged as an `error` source run. `ALERT_INGEST` is a placeholder mode for gated sites and has no pipeline yet. `min_interval_seconds` is parsed but not enforced at the moment.
- **Scrapers** (`src/scrapers/sites/*.py`) subclass `BaseScraper`, set `source_name`, and implement `search(max_retries=...) -> list[Listing]`. Always go through `request_with_backoff` / `fetch_json`. They add jittered sleeps before every request, retry on 5xx, and raise `SourceBlockedError` on repeated 403/429. The runner handles that error (health tracking plus a `[SOURCE_BLOCKED]` ops message). Most sources hit a JSON API with filters baked into the URL or request (for example the Wobeco query string, or the Vb&t `filter_properties` cookie in `VBentScraper.filter_template`), and are not HTML parsers. The exceptions are `bpd_woningfonds` and `ikwilhuren`, two sites on the same MVGM platform. They parse server-rendered listing cards with BeautifulSoup through the shared `sites/mvgm.py` base. Ikwilhuren only reads its first `max_pages` (3) newest-first pages, because the full national list has 100+ pages.
- **Normalization conventions for `Listing`:** `rent_price` is the base monthly rent. Service and parking charges go into `raw_features` (all values are strings). Do not infer `bedrooms` from total rooms; leave it `None`. Use `map_status_to_available` for status strings in Dutch or English.
- **Matching** (`filtering/rules.py`): a *hard match* needs price ≤ `MAX_RENT_EUR`, area ≥ `MIN_SIZE_M2`, an allowed city, and availability. A *close match* allows +10% price and −10% area. City names go through `normalize_city_name` aliases (`'s-Gravenhage`/`The Hague` → `denhaag`), so add new spellings there. With `STORE_ONLY_MATCHES=true` (the default), non-matches are never stored.
- **Dedupe/alerting:** `Listing.dedupe_key()` is `source:id:city:price:area`. A price or area change therefore produces a new row and a new alert, which is intentional. For an existing key, `upsert_listing` reports `changed` only when title, price, area, availability, or status changed, and alerts go out only for changed matches.
- **Bot:** `src/bot/client.py` (`HuurBot`: scheduler loop, and `run_cycle` guarded by an `asyncio.Lock`, so a second request raises `CycleBusyError`), `commands.py` (thin slash handlers), `embeds.py`/`checks.py` (pure, unit-tested). `DiscordNotifier` is called from the worker thread and posts via `run_coroutine_threadsafe`. Never call it from the event loop thread. Ops messages (`[SOURCE_BLOCKED]`/`[SOURCE_ERROR]`/`[SOURCE_RECOVERED]`) fire only when a source's status changes, based on its last two `source_runs` rows. `[SOURCE_BLOCKED]` posts on the first block, `[SOURCE_ERROR]` only on the second error in a row, and `[SOURCE_RECOVERED]` only after a failure that was announced. The paused flag is in the `bot_state` table. A second loop (`clear_loop`, every 15 min) deletes the bot's own unpinned messages from the alert channel once a week (`LISTINGS_CLEAR_DAY`/`LISTINGS_CLEAR_HOUR_UTC`) and keeps the last run in `bot_state.listings_cleared_at`, so a slot missed while the bot was down still runs after it starts again.
- **Storage:** the SQLite schema is created and migrated inline in `SQLiteStore._init_db` with `ALTER TABLE` when a column is missing. Add new columns the same way. Every source attempt also writes a row to `source_runs`. Key/value bot state lives in `bot_state` (`get_state`/`set_state`).
- **Config:** `src/config.py` (`load_settings`, frozen `Settings` dataclass from `.env`) is a module that sits beside the `src/config/` data directory, which holds only YAML. `.env.example` lists every variable.

## Adding a source

Follow `docs/source_onboarding.md`:
1. Add `src/scrapers/sites/<name>.py`.
2. Register the class in `SOURCE_FACTORIES` in `src/scrapers/factories.py`.
3. Add a conservative policy entry to `sources.yaml`.
4. Test it on its own with `--sources <name>`, or with `/scrape sources:<name>` in Discord.

If the site blocks the scraper or is unstable, switch it to `ALERT_INGEST` and don't add evasion. Keep request rates low. For a parser unit test, follow `tests/test_vbent.py`: build the scraper with `Mock(spec=Settings)`, call the parse helper on a fixture dict, and patch `fetch_json` to test pagination.
