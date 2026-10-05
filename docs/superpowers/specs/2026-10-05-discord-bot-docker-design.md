# Discord bot + Docker deployment — design

Date: 2026-10-05
Status: approved in brainstorming, pending spec review

## Goal

Replace the Telegram alerting and the Raspberry Pi systemd deployment with:

1. A long-running **Discord bot** that posts listing alerts and answers slash commands.
2. A **Docker** image deployed on an x86_64 homelab via a **Portainer Git stack** that builds from this private GitHub repo.

Telegram, systemd units and the Pi setup are removed entirely (no dual support).

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Discord integration | Real bot (`discord.py`), not a webhook |
| v1 commands | `/listings`, `/status`, `/scrape`, `/pause`, `/resume`, `/profile` (no live settings editing) |
| Server scope | Single private guild; commands synced to `DISCORD_GUILD_ID` |
| Alert routing | Listings → alert channel; blocked/error → ops channel (falls back to alert channel) |
| Mentions | Hard matches mention `DISCORD_MENTION_USER_ID`; close matches silent |
| Permissions | Read commands open to all; control commands (`/scrape`, `/pause`, `/resume`) restricted to configured user IDs or role |
| Process model | One process: bot + `tasks.loop` scheduler running the existing synchronous pipeline via `asyncio.to_thread` |
| Deployment | Portainer stack from Git repository, `build: .`, GitOps polling; GitHub fine-grained PAT (Contents: read) as stack credential |
| Arch | x86_64 only; no multi-arch builds |

## Architecture

```
container: huur-scraper (python:3.12-slim, restart: unless-stopped)
└─ python -m src.bot                      # new long-running entrypoint
   ├─ HuurBot (discord.Client + app_commands.CommandTree, synced to guild)
   ├─ scrape loop (tasks.loop every SCRAPE_INTERVAL_MINUTES; skips when paused)
   │    └─ asyncio.to_thread(run_all_sources(..., notifier=DiscordNotifier))
   └─ slash commands
volume huur-data → /app/data  (SQLite DB incl. new bot_state table)
logs → stdout only (docker logs / Portainer)
```

The CLI (`python -m src.main`) remains for `--listings`, `--prune-non-matches`, and `--once`. CLI `--once` uses a `LogNotifier` (log-only) so it works without the bot being online.

## Components

### `src/notify/base.py` — `Notifier` protocol
```python
class Notifier(Protocol):
    def notify_listing(self, listing: Listing, match: MatchResult) -> None: ...
    def notify_ops(self, text: str) -> None: ...
```
`LogNotifier` implements it by logging. Both methods are synchronous because they are called from the worker thread that runs the pipeline.

### `src/notify/discord_notifier.py` — `DiscordNotifier`
- Constructed with the bot's event loop, the alert channel, an optional ops channel, and an optional mention user ID.
- `notify_listing`: builds an embed with `build_listing_embed(listing, match)`. Title is the listing title, linked to `source_url`. Fields are price, area, bedrooms, city, site, and score. Missing values show as `n/a`. Color is green for a hard match and amber for a close match. Content is `<@id>` only for hard matches when the mention ID is set.
- `notify_ops`: small red embed to the ops channel, or to the alert channel when there is no ops channel.
- Sends via `asyncio.run_coroutine_threadsafe(channel.send(...), loop).result(timeout=30)`. On exception it logs and does not raise. The listing is already stored, so it is not re-alerted, which matches the current Telegram behavior.
- `build_listing_embed` is a pure, module-level function so it can be unit tested.

### `src/scrapers/runner.py` changes
- New signature: `run_all_sources(settings, store, source_factories, registry_file, notifier, selected_sources=None) -> RunSummary`.
- Removes the `TelegramNotifier` construction and the `format_listing_message` call. Calls `notifier.notify_listing(listing, match)` for changed matches, and `notifier.notify_ops(...)` for `[SOURCE_BLOCKED]` / `[SOURCE_ERROR]`.
- Returns a `RunSummary` dataclass: a list of `SourceResult(name, status, listings, changed, alerted, details)`. This is used by `/scrape` and for logging.
- `source_factories` moves out of `main()` into a module-level `SOURCE_FACTORIES` in a new `src/scrapers/factories.py` so both entrypoints share it. CLAUDE.md is updated to point to the new location.

### `src/storage/sqlite_store.py` additions
- New table `bot_state(key TEXT PRIMARY KEY, value TEXT NOT NULL)`, created in `_init_db`.
- `get_state(key, default=None) -> str | None` and `set_state(key, value)`.
- `get_latest_source_runs() -> list[Row]`: the latest row per `source_site`, used by `/status`.

### `src/bot/` — Discord bot package
- `__main__.py`: loads settings, configures logging, validates the Discord settings (fails fast with a clear message), and runs `HuurBot`.
- `client.py`: `HuurBot(discord.Client)`
  - `setup_hook`: registers the commands, copies them to the guild, and syncs.
  - `on_ready`: resolves the channels (fails with a logged error and exits non-zero if the alert channel is not found), builds the `DiscordNotifier`, and starts the scrape loop.
  - Scrape loop: `tasks.loop(minutes=SCRAPE_INTERVAL_MINUTES)`. `before_loop` waits until the bot is ready and then sleeps about 30 s. Each tick: skip if `bot_state.paused == "1"`, otherwise `await self.run_cycle()`.
  - `run_cycle(selected_sources=None) -> RunSummary | None`: guarded by an `asyncio.Lock`. Returns `None` when a cycle is already running. Runs `run_all_sources` via `asyncio.to_thread`. Any unexpected exception is logged and sent with `notify_ops`, and the loop continues.
  - Records `last_cycle_at` and the next run time for `/status`.
- `commands.py`: thin slash-command handlers that call pure helpers:
  - `/listings [limit: 1–25 = 10]`: ephemeral, `build_listings_embed(rows)`.
  - `/status`: ephemeral, `build_status_embed(paused, next_run, last_cycle_at, source_rows)`.
  - `/profile`: ephemeral, `build_profile_embed(settings)`.
  - `/scrape [sources: str]`: control only. Calls `defer()`, then `run_cycle(selected)`, then a public reply with `build_summary_embed(summary)`, or "a cycle is already running".
  - `/pause`, `/resume`: control only. Sets `bot_state.paused` and replies ephemerally.
- `permissions.py`: pure `is_control_user(user_id, role_ids, settings) -> bool`.

### Config (`src/config.py`, `.env.example`)
Remove `telegram_bot_token` and `telegram_chat_id`. Add:

| Env var | Type | Default |
|---|---|---|
| `DISCORD_BOT_TOKEN` | str | `""` (required by the bot) |
| `DISCORD_GUILD_ID` | int | required by the bot |
| `DISCORD_ALERT_CHANNEL_ID` | int | required by the bot |
| `DISCORD_OPS_CHANNEL_ID` | int \| None | None |
| `DISCORD_MENTION_USER_ID` | int \| None | None |
| `DISCORD_CONTROL_USER_IDS` | list[int] | empty |
| `DISCORD_CONTROL_ROLE_ID` | int \| None | None |
| `SCRAPE_INTERVAL_MINUTES` | int | 10 |

These stay optional in `load_settings` so the CLI and tests work without Discord config. `src/bot/__main__.py` validates the required ones.

### Removed
`src/notify/telegram.py`, `src/notify/formatter.py` (replaced by the embed builders), `deploy/systemd/`, `scripts/setup_pi.sh`, `docs/install_pi.md`, and the Telegram/Pi sections of README and CLAUDE.md.

## Docker / deployment

- `Dockerfile`: `python:3.12-slim`, `PYTHONUNBUFFERED=1`, `WORKDIR /app`, install `requirements.txt` (without pytest; split into `requirements-dev.txt`), copy `src/`, non-root `app` user owning `/app/data`, `CMD ["python", "-m", "src.bot"]`. `WORKDIR /app` keeps the relative `src/config/sources.yaml` path valid.
- `docker-compose.yml`: one service `huur-scraper`, `build: .`, `restart: unless-stopped`, volume `huur-data:/app/data`, and an `environment:` block with `${VAR}` placeholders for every setting. Container defaults: `DATABASE_PATH=/app/data/huur_scraper.db`, `LOG_TO_CONSOLE=true`, file logging disabled.
- Logging: `LOG_FILE_PATH` is allowed to be empty, which disables the file handler (small change to `logging_setup.py`).
- `.dockerignore`: `.venv`, `data`, `logs`, `.env`, `.git`, `tests`, `docs`, `__pycache__`.
- `docs/deploy_portainer.md`: create the Discord application and bot, copy the token, invite URL with scopes `bot applications.commands` and permissions to view channels, send messages, and embed links, enable Developer Mode to copy IDs, create a GitHub fine-grained PAT (this repo, Contents: read-only), then Portainer → Stacks → Add stack → Repository (URL, `main`, compose path, authentication, GitOps polling), set the env vars, and deploy. Also covers running CLI commands with `docker exec huur-scraper python -m src.main --listings`.
- No healthcheck in v1.

## Error handling

- Per-source errors and blocks: unchanged behavior (written to `source_runs`), notification via `notify_ops`.
- Unexpected cycle exception: logged, `notify_ops`, the loop continues.
- Discord send failure: logged, not retried, does not affect storage.
- Startup misconfiguration (missing token, guild, or alert channel, or a channel not found): clear log line, non-zero exit, Docker restarts. The operator then fixes the env.
- Concurrent `/scrape` during a scheduled cycle: the lock rejects it with a message. A scheduled tick while `/scrape` is running is skipped.

## Testing

unittest style (`tests/test_*.py`), no live Discord or network:
- `test_discord_notifier.py`: embed contents and colors, mention only on hard match, `n/a` handling, ops-channel fallback, and a send exception that is swallowed and logged (fake channel and a real event loop in a thread).
- `test_runner.py`: a fake notifier, a fake scraper factory, and a temp SQLite DB. Covers alerts only for changed matches, `notify_ops` on `SourceBlockedError` and on generic errors, and the `RunSummary` contents.
- `test_bot_helpers.py`: `is_control_user`, `build_status_embed`, `build_listings_embed`, `build_profile_embed`, `build_summary_embed`.
- `test_sqlite_store_state.py`: `bot_state` get/set round trip and persistence across `SQLiteStore` instances, and `get_latest_source_runs`.
- Existing `tests/test_vbent.py` keeps passing.
- Manual: `docker compose up --build` locally against a test guild, then exercise every command and check that an alert arrives.

## Out of scope (v1)
Editing settings live from Discord, multi-guild support, healthcheck or metrics, a GHCR/CI image pipeline, `ALERT_INGEST` ingestion, and enforcing `min_interval_seconds`.
