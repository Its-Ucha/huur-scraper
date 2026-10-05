# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A compliance-first rental listing aggregator for the Delft/The Hague area. Each run scrapes the enabled sources, filters listings against a profile set in `.env`, stores them in SQLite with dedupe, and sends Telegram alerts for new or changed matches. In production it runs on a Raspberry Pi through a systemd timer (`deploy/systemd/`, installed by `scripts/setup_pi.sh`) that fires every ~10 minutes. There is no long-running process.

## Commands

All commands must run from the repo root, because `src/main.py` loads `src/config/sources.yaml` with a relative path and the default DB and log paths are relative too.

```powershell
./scripts/setup_local.ps1                         # create .venv + install requirements (Windows)
./scripts/run_once.ps1                            # one scrape cycle, all SCRAPE-mode sources
./scripts/run_once.ps1 --sources vbent,wobeco     # only selected sources
.\.venv\Scripts\python.exe -m src.main --listings --limit 100
.\.venv\Scripts\python.exe -m src.main --prune-non-matches   # delete stored rows that no longer match the profile
```

On the Pi, use `./.venv/bin/python -m src.main ...` instead.

Tests use `unittest` style, and pytest is installed:

```powershell
.\.venv\Scripts\python.exe -m pytest tests
.\.venv\Scripts\python.exe -m pytest tests/test_vbent.py -k pagination
```

There is no linter or formatter configured. `test.py` at the root is a scratch script and not a test.

## Architecture

Pipeline: `main.py` → `scrapers/runner.py::run_all_sources` → per source: `scraper.search()` → `filtering/rules.py::evaluate_listing` → `storage/sqlite_store.py::upsert_listing` → `notify/telegram.py`.

- **Source registration is split across two places.** `src/config/sources.yaml` declares the policy for each source (`mode`, `max_retries`, `auto_disable_on_block`). The `source_factories` dict in `src/main.py` maps the source name to its scraper class. The runner iterates over the YAML. Only `mode: SCRAPE` sources run (`policy/risk_policy.py::is_collection_allowed`). A YAML entry without a factory is logged as an `error` source run. `ALERT_INGEST` is a placeholder mode for gated sites and has no pipeline yet. `min_interval_seconds` is parsed but not enforced at the moment.
- **Scrapers** (`src/scrapers/sites/*.py`) subclass `BaseScraper`, set `source_name`, and implement `search(max_retries=...) -> list[Listing]`. Always go through `request_with_backoff` / `fetch_json`. They add jittered sleeps before every request, retry on 5xx, and raise `SourceBlockedError` on repeated 403/429. The runner handles that error (health tracking plus a `[SOURCE_BLOCKED]` Telegram message). Most sources hit a JSON API with filters baked into the URL or request (for example the Wobeco query string, or the Vb&t `filter_properties` cookie in `VBentScraper.filter_template`), and are not HTML parsers.
- **Normalization conventions for `Listing`:** `rent_price` is the base monthly rent. Service and parking charges go into `raw_features` (all values are strings). Do not infer `bedrooms` from total rooms; leave it `None`. Use `map_status_to_available` for status strings in Dutch or English.
- **Matching** (`filtering/rules.py`): a *hard match* needs price ≤ `MAX_RENT_EUR`, area ≥ `MIN_SIZE_M2`, an allowed city, and availability. A *close match* allows +10% price and −10% area. City names go through `normalize_city_name` aliases (`'s-Gravenhage`/`The Hague` → `denhaag`), so add new spellings there. With `STORE_ONLY_MATCHES=true` (the default), non-matches are never stored.
- **Dedupe/alerting:** `Listing.dedupe_key()` is `source:id:city:price:area`. A price or area change therefore produces a new row and a new alert, which is intentional. For an existing key, `upsert_listing` reports `changed` only when title, price, area, availability, or status changed, and alerts go out only for changed matches.
- **Storage:** the SQLite schema is created and migrated inline in `SQLiteStore._init_db` with `ALTER TABLE` when a column is missing. Add new columns the same way. Every source attempt also writes a row to `source_runs`.
- **Config:** `src/config.py` (`load_settings`, frozen `Settings` dataclass from `.env`) is a module that sits beside the `src/config/` data directory, which holds only YAML. `.env.example` lists every variable.

## Adding a source

Follow `docs/source_onboarding.md`:
1. Add `src/scrapers/sites/<name>.py`.
2. Register the class in `source_factories` in `src/main.py`.
3. Add a conservative policy entry to `sources.yaml`.
4. Test it on its own with `--sources <name>`.

If the site blocks the scraper or is unstable, switch it to `ALERT_INGEST` and don't add evasion. Keep request rates low. For a parser unit test, follow `tests/test_vbent.py`: build the scraper with `Mock(spec=Settings)`, call the parse helper on a fixture dict, and patch `fetch_json` to test pagination.
