# Discord Bot + Docker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Telegram alerts and the Raspberry Pi systemd deployment with a long-running Discord bot, packaged as a Docker image and deployed through a Portainer Git stack.

**Architecture:** One process (`python -m src.bot`) runs a `discord.py` client. A `tasks.loop` runs the existing synchronous scrape pipeline (`run_all_sources`) in a worker thread through `asyncio.to_thread`. The runner no longer builds its own notifier. It receives a `Notifier` (`DiscordNotifier` in the bot, `LogNotifier` in the CLI) and returns a `RunSummary`. Slash commands read SQLite and control the loop. The paused flag lives in a new `bot_state` table, so it survives redeploys.

**Tech Stack:** Python 3.12, discord.py 2.7.1, httpx, SQLite, unittest/pytest, Docker (`python:3.12-slim`), Portainer Git stack.

**Spec:** `docs/superpowers/specs/2026-10-05-discord-bot-docker-design.md`

## Global Constraints

- Run every command from the repo root. `src/config/sources.yaml` and the default DB/log paths are relative.
- Python commands use `.\.venv\Scripts\python.exe` (Windows). If `.venv` does not exist, run `./scripts/setup_local.ps1` first (after Task 1 it also installs dev requirements).
- Pin new dependency: `discord.py==2.7.1`. No other new runtime dependencies.
- Tests use `unittest.TestCase` style (pytest is the runner). No test may touch the network or a real Discord connection.
- New code follows repo idiom: `from __future__ import annotations` (except `src/bot/commands.py`, see Task 6), module-level `logger = logging.getLogger(__name__)`, dataclasses.
- Telegram, `deploy/systemd/`, `scripts/setup_pi.sh`, `docs/install_pi.md` are removed. No dual support.
- Single guild: commands are synced to `DISCORD_GUILD_ID` only.
- Control commands (`/scrape`, `/pause`, `/resume`) require the user ID to be in `DISCORD_CONTROL_USER_IDS` or the user to hold the `DISCORD_CONTROL_ROLE_ID` role. Read commands (`/listings`, `/status`, `/profile`) are open to everyone. All replies are ephemeral except the `/scrape` result.
- Hard matches mention `DISCORD_MENTION_USER_ID` (when set). Close matches never mention anyone.
- Ops messages (`[SOURCE_BLOCKED]`, `[SOURCE_ERROR]`, `[SOURCE_RECOVERED]`, `[CYCLE_ERROR]`) go to `DISCORD_OPS_CHANNEL_ID`, falling back to `DISCORD_ALERT_CHANNEL_ID`.
- Container: `WORKDIR /app`, DB at `/app/data/huur_scraper.db` on named volume `huur-data`, logs to stdout only, non-root user.
- Commit after each task. End every commit message with:
  `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`

## Deviations from spec (flagged to the user)

- **Ops message dedupe + recovery message (Task 4).** The spec says per-source blocked/error notification is "unchanged". In a long-running bot that means a broken source posts every 10 minutes (about 144 times a day). The runner now notifies only when a source's status changes into `blocked`/`error`, and posts `[SOURCE_RECOVERED] <name>` when it returns to `ok`. It uses the previous `source_runs` row, so this works across restarts.
- **`/scrape` ignores pause.** A manual `/scrape` runs even when the scheduler is paused. Pause only stops scheduled ticks.

## Review Focus

1. **A source that fails every cycle.** Expected: one `[SOURCE_ERROR]`/`[SOURCE_BLOCKED]` on the first failure, silence while it stays broken, one `[SOURCE_RECOVERED]` when it works again. Not one message every 10 minutes. Covered by tests in Task 4.
2. **Listings with messy data.** A 300+ character title, `None` price/area/city/bedrooms, or a relative/empty URL must still produce a valid embed: title ≤ 256 chars, no empty field values, `url` only when absolute http(s). Covered by tests in Task 2.
3. **Discord send failure or hang mid-cycle.** A deleted channel, missing permissions, or a timeout must not stop the cycle or roll back storage. The listing is still stored, the error is logged, and later listings still get sent. Covered by tests in Task 2.
4. **Overlapping cycles.** `/scrape` during a scheduled cycle, or a scheduled tick during `/scrape`. Never two concurrent `run_all_sources`. `/scrape` replies "already running", the tick skips silently. Covered by tests in Task 6.
5. **Sloppy env values from the Portainer UI.** `" 111 , 222,,"` parses to `[111, 222]`. Empty optional IDs become `None`. Non-numeric IDs fail at startup with an error naming the variable. Missing required bot settings exit with a list of what is missing. Covered by tests in Task 1 and Task 5.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `requirements.txt` | modify | runtime deps (+ `discord.py`, − `pytest`) |
| `requirements-dev.txt` | create | `-r requirements.txt` + `pytest` |
| `scripts/setup_local.ps1` | modify | install `requirements-dev.txt` |
| `src/config.py` | modify | Discord settings, optional log file, ID parsing |
| `src/logging_setup.py` | modify | skip file handler when `log_file_path is None` |
| `.env.example` | modify | Discord vars replace Telegram vars |
| `tests/__init__.py` | create | make `tests` a package so `tests.helpers` imports work |
| `tests/helpers.py` | create | `make_settings`, `make_listing`, `make_match`, `RecordingNotifier` |
| `tests/test_config.py` | create | config parsing + logging setup tests |
| `src/notify/base.py` | create | `Notifier` protocol + `LogNotifier` |
| `src/notify/discord_notifier.py` | create | embed builders + thread-safe `DiscordNotifier` |
| `tests/test_discord_notifier.py` | create | notifier tests |
| `src/storage/sqlite_store.py` | modify | `bot_state` table, `get_state`/`set_state`, `get_latest_source_runs` |
| `tests/test_sqlite_store_state.py` | create | storage tests |
| `src/scrapers/factories.py` | create | `SOURCE_FACTORIES`, `REGISTRY_FILE` shared by CLI and bot |
| `src/scrapers/runner.py` | modify | inject notifier, return `RunSummary`, ops dedupe |
| `src/main.py` | modify | use factories + `LogNotifier` |
| `src/notify/telegram.py`, `src/notify/formatter.py` | delete | replaced |
| `tests/test_runner.py` | create | runner tests |
| `src/bot/__init__.py` | create | package marker |
| `src/bot/checks.py` | create | `is_control_user`, `validate_bot_settings`, `parse_sources` |
| `src/bot/embeds.py` | create | `/listings`, `/status`, `/profile`, `/scrape` embed builders |
| `tests/test_bot_helpers.py` | create | checks + embeds tests |
| `src/bot/client.py` | create | `HuurBot`, scheduler loop, `run_cycle`, `CycleBusyError` |
| `src/bot/commands.py` | create | slash command handlers |
| `src/bot/__main__.py` | create | bot entrypoint |
| `tests/test_bot_client.py` | create | cycle lock / pause / failure tests |
| `Dockerfile`, `docker-compose.yml`, `.dockerignore` | create | container build + stack |
| `docs/deploy_portainer.md` | create | Discord + GitHub PAT + Portainer walkthrough |
| `README.md`, `CLAUDE.md`, `docs/first_run_windows.md` | modify | remove Telegram/Pi, document bot/Docker |
| `deploy/systemd/*`, `scripts/setup_pi.sh`, `docs/install_pi.md` | delete | Pi deployment removed |

---

### Task 1: Discord settings, optional log file, dev requirements

**Files:**
- Modify: `requirements.txt`, `scripts/setup_local.ps1`, `src/config.py`, `src/logging_setup.py`, `.env.example`
- Create: `requirements-dev.txt`, `tests/__init__.py`, `tests/helpers.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Settings` fields (removed: `telegram_bot_token`, `telegram_chat_id`; changed: `log_file_path: Path | None`; added):
    `discord_bot_token: str`, `discord_guild_id: int | None`, `discord_alert_channel_id: int | None`, `discord_ops_channel_id: int | None`, `discord_mention_user_id: int | None`, `discord_control_user_ids: list[int]`, `discord_control_role_id: int | None`, `scrape_interval_minutes: int`
  - `tests/helpers.py`: `make_settings(**overrides) -> Settings`, `make_listing(**overrides) -> Listing`, `make_match(hard: bool = True, score: int = 95) -> MatchResult`, `RecordingNotifier` (attributes `listings: list[tuple[Listing, MatchResult]]`, `ops: list[str]`)

- [ ] **Step 1: Split requirements and create the venv**

`requirements.txt` (replace whole file):
```
httpx==0.28.1
beautifulsoup4==4.13.4
pydantic==2.11.7
python-dotenv==1.1.1
PyYAML==6.0.2
discord.py==2.7.1
```

`requirements-dev.txt` (new):
```
-r requirements.txt
pytest==8.3.3
```

In `scripts/setup_local.ps1`, change the install line to:
```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run: `./scripts/setup_local.ps1`
Expected: ends with `Local environment ready (.venv only).` If `py -3.12` is not installed, create the venv with the newest available Python 3.12+ (`py -3 -m venv .venv`) and re-run the script.

Run: `.\.venv\Scripts\python.exe -m pytest tests -q`
Expected: existing `test_vbent.py` tests PASS.

- [ ] **Step 2: Create test helpers**

`tests/__init__.py`: empty file.

`tests/helpers.py`:
```python
from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.filtering.rules import MatchResult
from src.models.listing import Listing


def make_settings(**overrides) -> Settings:
    values = dict(
        database_path=Path("unused.db"),
        user_agent="test-agent",
        max_workers=1,
        request_timeout_seconds=5,
        max_rent_eur=1000,
        min_size_m2=40,
        preferred_bedrooms=2,
        allow_close_match=True,
        store_only_matches=True,
        allowed_cities=["Den Haag", "Delft"],
        log_level="INFO",
        log_file_path=None,
        log_to_console=False,
        discord_bot_token="",
        discord_guild_id=None,
        discord_alert_channel_id=None,
        discord_ops_channel_id=None,
        discord_mention_user_id=None,
        discord_control_user_ids=[],
        discord_control_role_id=None,
        scrape_interval_minutes=10,
    )
    values.update(overrides)
    return Settings(**values)


def make_listing(**overrides) -> Listing:
    values = dict(
        source_site="alpha",
        source_listing_id="1",
        source_url="https://example.com/listing/1",
        title="Teststraat 1",
        city="Delft",
        rent_price=900,
        living_area_m2=50,
        rooms_total=3,
        bedrooms=2,
        available_from=None,
        raw_features={},
    )
    values.update(overrides)
    return Listing(**values)


def make_match(hard: bool = True, score: int = 95) -> MatchResult:
    return MatchResult(is_hard_match=hard, is_close_match=not hard, score=score, reasons=[])


class RecordingNotifier:
    def __init__(self) -> None:
        self.listings: list[tuple[Listing, MatchResult]] = []
        self.ops: list[str] = []

    def notify_listing(self, listing: Listing, match: MatchResult) -> None:
        self.listings.append((listing, match))

    def notify_ops(self, text: str) -> None:
        self.ops.append(text)
```

- [ ] **Step 3: Write the failing config tests**

`tests/test_config.py`:
```python
from __future__ import annotations

import logging
import os
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

from src.config import load_settings
from src.logging_setup import configure_logging
from tests.helpers import make_settings


def _load(env: dict[str, str]):
    with patch.dict(os.environ, env, clear=True), patch("src.config.load_dotenv"):
        return load_settings()


class DiscordSettingsTests(unittest.TestCase):
    def test_defaults_when_unset(self) -> None:
        settings = _load({})
        self.assertEqual(settings.discord_bot_token, "")
        self.assertIsNone(settings.discord_guild_id)
        self.assertIsNone(settings.discord_alert_channel_id)
        self.assertIsNone(settings.discord_ops_channel_id)
        self.assertIsNone(settings.discord_mention_user_id)
        self.assertEqual(settings.discord_control_user_ids, [])
        self.assertIsNone(settings.discord_control_role_id)
        self.assertEqual(settings.scrape_interval_minutes, 10)

    def test_ids_parsed_with_sloppy_whitespace(self) -> None:
        settings = _load(
            {
                "DISCORD_BOT_TOKEN": "  tok  ",
                "DISCORD_GUILD_ID": " 123 ",
                "DISCORD_ALERT_CHANNEL_ID": "456",
                "DISCORD_OPS_CHANNEL_ID": "",
                "DISCORD_MENTION_USER_ID": "789",
                "DISCORD_CONTROL_USER_IDS": " 111 , 222,, ",
                "DISCORD_CONTROL_ROLE_ID": "333",
                "SCRAPE_INTERVAL_MINUTES": "15",
            }
        )
        self.assertEqual(settings.discord_bot_token, "tok")
        self.assertEqual(settings.discord_guild_id, 123)
        self.assertEqual(settings.discord_alert_channel_id, 456)
        self.assertIsNone(settings.discord_ops_channel_id)
        self.assertEqual(settings.discord_mention_user_id, 789)
        self.assertEqual(settings.discord_control_user_ids, [111, 222])
        self.assertEqual(settings.discord_control_role_id, 333)
        self.assertEqual(settings.scrape_interval_minutes, 15)

    def test_non_numeric_id_names_the_variable(self) -> None:
        with self.assertRaisesRegex(ValueError, "DISCORD_GUILD_ID"):
            _load({"DISCORD_GUILD_ID": "my-server"})

    def test_non_numeric_id_in_list_names_the_variable(self) -> None:
        with self.assertRaisesRegex(ValueError, "DISCORD_CONTROL_USER_IDS"):
            _load({"DISCORD_CONTROL_USER_IDS": "111,bob"})

    def test_interval_below_one_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "SCRAPE_INTERVAL_MINUTES"):
            _load({"SCRAPE_INTERVAL_MINUTES": "0"})

    def test_log_file_path_default_and_empty(self) -> None:
        self.assertEqual(_load({}).log_file_path, Path("logs/huur_scraper.log"))
        self.assertIsNone(_load({"LOG_FILE_PATH": ""}).log_file_path)
        self.assertIsNone(_load({"LOG_FILE_PATH": "   "}).log_file_path)


class LoggingSetupTests(unittest.TestCase):
    def tearDown(self) -> None:
        root = logging.getLogger()
        for handler in root.handlers:
            handler.close()
        root.handlers.clear()

    def test_no_file_handler_when_log_file_path_is_none(self) -> None:
        configure_logging(make_settings(log_file_path=None, log_to_console=True))
        handlers = logging.getLogger().handlers
        self.assertFalse(any(isinstance(h, RotatingFileHandler) for h in handlers))
        self.assertEqual(len(handlers), 1)

    def test_file_handler_when_path_set(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            configure_logging(make_settings(log_file_path=Path(tmp) / "x.log"))
            handlers = logging.getLogger().handlers
            self.assertTrue(any(isinstance(h, RotatingFileHandler) for h in handlers))
            self.tearDown()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: FAIL (`TypeError: Settings.__init__() got an unexpected keyword argument 'discord_bot_token'` or `AttributeError`).

- [ ] **Step 5: Implement config changes**

In `src/config.py`, replace the `Settings` dataclass and `load_settings`, and add the parse helpers:

```python
@dataclass(frozen=True)
class Settings:
    database_path: Path
    user_agent: str
    max_workers: int
    request_timeout_seconds: int
    max_rent_eur: int
    min_size_m2: int
    preferred_bedrooms: int
    allow_close_match: bool
    store_only_matches: bool
    allowed_cities: list[str]
    log_level: str
    log_file_path: Path | None
    log_to_console: bool
    discord_bot_token: str
    discord_guild_id: int | None
    discord_alert_channel_id: int | None
    discord_ops_channel_id: int | None
    discord_mention_user_id: int | None
    discord_control_user_ids: list[int]
    discord_control_role_id: int | None
    scrape_interval_minutes: int
```

Add below `_parse_cities`:
```python
def _parse_optional_id(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a numeric Discord ID, got {raw!r}") from error


def _parse_id_list(name: str) -> list[int]:
    ids: list[int] = []
    for item in os.getenv(name, "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ids.append(int(item))
        except ValueError as error:
            raise ValueError(
                f"{name} must be comma-separated numeric Discord IDs, got {item!r}"
            ) from error
    return ids


def _parse_log_file_path() -> Path | None:
    raw = os.getenv("LOG_FILE_PATH", "logs/huur_scraper.log").strip()
    return Path(raw) if raw else None


def _parse_interval_minutes() -> int:
    value = int(os.getenv("SCRAPE_INTERVAL_MINUTES", "10"))
    if value < 1:
        raise ValueError(f"SCRAPE_INTERVAL_MINUTES must be at least 1, got {value}")
    return value
```

In `load_settings`, delete the two `telegram_*` lines, change `log_file_path=` to `log_file_path=_parse_log_file_path(),`, and append before the closing parenthesis:
```python
        discord_bot_token=os.getenv("DISCORD_BOT_TOKEN", "").strip(),
        discord_guild_id=_parse_optional_id("DISCORD_GUILD_ID"),
        discord_alert_channel_id=_parse_optional_id("DISCORD_ALERT_CHANNEL_ID"),
        discord_ops_channel_id=_parse_optional_id("DISCORD_OPS_CHANNEL_ID"),
        discord_mention_user_id=_parse_optional_id("DISCORD_MENTION_USER_ID"),
        discord_control_user_ids=_parse_id_list("DISCORD_CONTROL_USER_IDS"),
        discord_control_role_id=_parse_optional_id("DISCORD_CONTROL_ROLE_ID"),
        scrape_interval_minutes=_parse_interval_minutes(),
```

In `src/logging_setup.py`, wrap the file-handler block:
```python
    if settings.log_file_path is not None:
        settings.log_file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            settings.log_file_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
```

Replace the Telegram block at the top of `.env.example` with:
```
# Discord bot (see docs/deploy_portainer.md for how to get these IDs)
DISCORD_BOT_TOKEN=
DISCORD_GUILD_ID=
DISCORD_ALERT_CHANNEL_ID=
# Optional: separate channel for [SOURCE_ERROR]/[SOURCE_BLOCKED]; defaults to the alert channel
DISCORD_OPS_CHANNEL_ID=
# Optional: user to @mention on hard matches
DISCORD_MENTION_USER_ID=
# Who may use /scrape, /pause, /resume (at least one of these is required)
DISCORD_CONTROL_USER_IDS=
DISCORD_CONTROL_ROLE_ID=
SCRAPE_INTERVAL_MINUTES=10
```
And under `# Runtime`, add this comment line above `LOG_FILE_PATH`: `# Leave LOG_FILE_PATH empty to log to stdout only (the Docker image does this)`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config.py -v`
Expected: all PASS.

`src/scrapers/runner.py` still references `settings.telegram_*` and gets fixed in Task 4. `pytest tests` does not import the runner yet, so the suite still passes:
Run: `.\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add requirements.txt requirements-dev.txt scripts/setup_local.ps1 src/config.py src/logging_setup.py .env.example tests/__init__.py tests/helpers.py tests/test_config.py
git commit -m "Add Discord settings and optional file logging

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Notifier protocol, LogNotifier, DiscordNotifier

**Files:**
- Create: `src/notify/base.py`, `src/notify/discord_notifier.py`, `tests/test_discord_notifier.py`

**Interfaces:**
- Consumes: `tests.helpers.make_listing`, `make_match` (Task 1).
- Produces:
  - `src.notify.base.Notifier` (Protocol): `notify_listing(listing: Listing, match: MatchResult) -> None`, `notify_ops(text: str) -> None`
  - `src.notify.base.LogNotifier()`: implements `Notifier` by logging.
  - `src.notify.discord_notifier.build_listing_embed(listing, match) -> discord.Embed`
  - `src.notify.discord_notifier.build_ops_embed(text: str) -> discord.Embed`
  - `src.notify.discord_notifier.truncate(text: str, limit: int) -> str`
  - `src.notify.discord_notifier.DiscordNotifier(loop: asyncio.AbstractEventLoop, alert_channel, ops_channel=None, mention_user_id: int | None = None)`. Implements `Notifier`. **Must be called from a thread other than `loop`'s thread**, otherwise it deadlocks.
  - Module constant `SEND_TIMEOUT_SECONDS = 30`.

- [ ] **Step 1: Write the failing tests**

`tests/test_discord_notifier.py`:
```python
from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

import discord

from src.notify.base import LogNotifier
from src.notify.discord_notifier import (
    CLOSE_MATCH_COLOR,
    HARD_MATCH_COLOR,
    DiscordNotifier,
    build_listing_embed,
    build_ops_embed,
)
from tests.helpers import make_listing, make_match


class FakeChannel:
    def __init__(self, channel_id: int, error: Exception | None = None, hang: bool = False) -> None:
        self.id = channel_id
        self.error = error
        self.hang = hang
        self.sent: list[dict] = []

    async def send(self, **kwargs):
        if self.hang:
            await asyncio.sleep(3600)
        if self.error is not None:
            raise self.error
        self.sent.append(kwargs)


def _fields(embed: discord.Embed) -> dict[str, str]:
    return {field.name: field.value for field in embed.fields}


class ListingEmbedTests(unittest.TestCase):
    def test_hard_match_fields_and_color(self) -> None:
        embed = build_listing_embed(make_listing(), make_match(hard=True, score=95))
        self.assertEqual(embed.title, "Teststraat 1")
        self.assertEqual(embed.url, "https://example.com/listing/1")
        self.assertEqual(embed.color, HARD_MATCH_COLOR)
        fields = _fields(embed)
        self.assertEqual(fields["Price"], "€900")
        self.assertEqual(fields["Area"], "50 m²")
        self.assertEqual(fields["Bedrooms"], "2")
        self.assertEqual(fields["City"], "Delft")
        self.assertEqual(fields["Site"], "alpha")
        self.assertEqual(fields["Score"], "95")
        self.assertEqual(embed.footer.text, "Hard match")

    def test_close_match_color(self) -> None:
        embed = build_listing_embed(make_listing(), make_match(hard=False))
        self.assertEqual(embed.color, CLOSE_MATCH_COLOR)
        self.assertEqual(embed.footer.text, "Close match")

    def test_missing_values_render_na(self) -> None:
        listing = make_listing(rent_price=None, living_area_m2=None, bedrooms=None, city=None)
        fields = _fields(build_listing_embed(listing, make_match()))
        for name in ("Price", "Area", "Bedrooms", "City"):
            self.assertEqual(fields[name], "n/a")

    def test_long_title_truncated_to_discord_limit(self) -> None:
        embed = build_listing_embed(make_listing(title="x" * 400), make_match())
        self.assertLessEqual(len(embed.title), 256)
        self.assertTrue(embed.title.endswith("…"))

    def test_empty_title_and_relative_url(self) -> None:
        embed = build_listing_embed(make_listing(title="", source_url="/woning/1"), make_match())
        self.assertEqual(embed.title, "(untitled)")
        self.assertIsNone(embed.url)

    def test_ops_embed_truncates_description(self) -> None:
        embed = build_ops_embed("e" * 5000)
        self.assertLessEqual(len(embed.description), 4096)


class DiscordNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()

    def test_hard_match_mentions_user(self) -> None:
        alert = FakeChannel(1)
        notifier = DiscordNotifier(self.loop, alert, mention_user_id=42)
        notifier.notify_listing(make_listing(), make_match(hard=True))
        self.assertEqual(len(alert.sent), 1)
        self.assertEqual(alert.sent[0]["content"], "<@42>")
        self.assertIsInstance(alert.sent[0]["embed"], discord.Embed)

    def test_close_match_does_not_mention(self) -> None:
        alert = FakeChannel(1)
        notifier = DiscordNotifier(self.loop, alert, mention_user_id=42)
        notifier.notify_listing(make_listing(), make_match(hard=False))
        self.assertIsNone(alert.sent[0]["content"])

    def test_hard_match_without_mention_user(self) -> None:
        alert = FakeChannel(1)
        DiscordNotifier(self.loop, alert).notify_listing(make_listing(), make_match(hard=True))
        self.assertIsNone(alert.sent[0]["content"])

    def test_ops_goes_to_ops_channel(self) -> None:
        alert, ops = FakeChannel(1), FakeChannel(2)
        DiscordNotifier(self.loop, alert, ops).notify_ops("[SOURCE_ERROR] x - boom")
        self.assertEqual(alert.sent, [])
        self.assertEqual(ops.sent[0]["embed"].description, "[SOURCE_ERROR] x - boom")

    def test_ops_falls_back_to_alert_channel(self) -> None:
        alert = FakeChannel(1)
        DiscordNotifier(self.loop, alert).notify_ops("hello")
        self.assertEqual(len(alert.sent), 1)

    def test_send_error_is_logged_not_raised_and_next_send_works(self) -> None:
        failing = FakeChannel(1, error=RuntimeError("missing access"))
        notifier = DiscordNotifier(self.loop, failing)
        with self.assertLogs("src.notify.discord_notifier", level="ERROR"):
            notifier.notify_listing(make_listing(), make_match())
        failing.error = None
        notifier.notify_listing(make_listing(), make_match())
        self.assertEqual(len(failing.sent), 1)

    def test_send_timeout_is_logged_not_raised(self) -> None:
        hanging = FakeChannel(1, hang=True)
        notifier = DiscordNotifier(self.loop, hanging)
        with patch("src.notify.discord_notifier.SEND_TIMEOUT_SECONDS", 0.2):
            with self.assertLogs("src.notify.discord_notifier", level="ERROR"):
                notifier.notify_ops("slow")


class LogNotifierTests(unittest.TestCase):
    def test_logs_listing_and_ops(self) -> None:
        notifier = LogNotifier()
        with self.assertLogs("src.notify.base", level="INFO") as captured:
            notifier.notify_listing(make_listing(), make_match())
            notifier.notify_ops("[SOURCE_ERROR] x")
        output = "\n".join(captured.output)
        self.assertIn("HARD_MATCH", output)
        self.assertIn("[SOURCE_ERROR] x", output)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_discord_notifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.notify.base'`.

- [ ] **Step 3: Implement**

`src/notify/base.py`:
```python
from __future__ import annotations

import logging
from typing import Protocol

from src.filtering.rules import MatchResult
from src.models.listing import Listing


logger = logging.getLogger(__name__)


class Notifier(Protocol):
    def notify_listing(self, listing: Listing, match: MatchResult) -> None: ...

    def notify_ops(self, text: str) -> None: ...


class LogNotifier:
    """Notifier for CLI runs: writes alerts to the log instead of sending them."""

    def notify_listing(self, listing: Listing, match: MatchResult) -> None:
        match_type = "HARD_MATCH" if match.is_hard_match else "CLOSE_MATCH"
        logger.info(
            "[%s] %s | %s | price=%s area=%s city=%s | %s",
            match_type,
            listing.source_site,
            listing.title,
            listing.rent_price,
            listing.living_area_m2,
            listing.city,
            listing.source_url,
        )

    def notify_ops(self, text: str) -> None:
        logger.warning("[OPS] %s", text)
```

`src/notify/discord_notifier.py`:
```python
from __future__ import annotations

import asyncio
import logging

import discord

from src.filtering.rules import MatchResult
from src.models.listing import Listing


logger = logging.getLogger(__name__)

HARD_MATCH_COLOR = discord.Color.green()
CLOSE_MATCH_COLOR = discord.Color.gold()
OPS_COLOR = discord.Color.red()
SEND_TIMEOUT_SECONDS = 30
TITLE_LIMIT = 256
DESCRIPTION_LIMIT = 4096


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _or_na(value: object) -> str:
    return "n/a" if value in (None, "") else str(value)


def build_listing_embed(listing: Listing, match: MatchResult) -> discord.Embed:
    label = "Hard match" if match.is_hard_match else "Close match"
    url = listing.source_url if listing.source_url.startswith(("http://", "https://")) else None
    embed = discord.Embed(
        title=truncate(listing.title or "(untitled)", TITLE_LIMIT),
        url=url,
        color=HARD_MATCH_COLOR if match.is_hard_match else CLOSE_MATCH_COLOR,
    )
    price = f"€{listing.rent_price}" if listing.rent_price is not None else "n/a"
    area = f"{listing.living_area_m2} m²" if listing.living_area_m2 is not None else "n/a"
    embed.add_field(name="Price", value=price)
    embed.add_field(name="Area", value=area)
    embed.add_field(name="Bedrooms", value=_or_na(listing.bedrooms))
    embed.add_field(name="City", value=_or_na(listing.city))
    embed.add_field(name="Site", value=_or_na(listing.source_site))
    embed.add_field(name="Score", value=str(match.score))
    embed.set_footer(text=label)
    return embed


def build_ops_embed(text: str) -> discord.Embed:
    return discord.Embed(description=truncate(text, DESCRIPTION_LIMIT), color=OPS_COLOR)


class DiscordNotifier:
    """Sends alerts to Discord from the scrape worker thread.

    Methods block until the message is sent (or fails) and must be called from a
    thread other than the bot's event loop thread.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        alert_channel,
        ops_channel=None,
        mention_user_id: int | None = None,
    ) -> None:
        self.loop = loop
        self.alert_channel = alert_channel
        self.ops_channel = ops_channel
        self.mention_user_id = mention_user_id

    def notify_listing(self, listing: Listing, match: MatchResult) -> None:
        content = None
        if match.is_hard_match and self.mention_user_id:
            content = f"<@{self.mention_user_id}>"
        self._send(self.alert_channel, content, build_listing_embed(listing, match))

    def notify_ops(self, text: str) -> None:
        self._send(self.ops_channel or self.alert_channel, None, build_ops_embed(text))

    def _send(self, channel, content: str | None, embed: discord.Embed) -> None:
        coroutine = channel.send(
            content=content,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            future.result(timeout=SEND_TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001
            future.cancel()
            logger.exception("Discord send failed channel=%s", getattr(channel, "id", "?"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_discord_notifier.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/notify/base.py src/notify/discord_notifier.py tests/test_discord_notifier.py
git commit -m "Add Notifier protocol, LogNotifier and DiscordNotifier

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Bot state and latest source runs in SQLite

**Files:**
- Modify: `src/storage/sqlite_store.py` (`_init_db` + new methods after `get_all_listings_for_prune`)
- Create: `tests/test_sqlite_store_state.py`

**Interfaces:**
- Produces:
  - `SQLiteStore.get_state(key: str, default: str | None = None) -> str | None`
  - `SQLiteStore.set_state(key: str, value: str) -> None`
  - `SQLiteStore.get_latest_source_runs() -> list[sqlite3.Row]`: one row per `source_site` (latest by `id`), columns `source_site, run_at, status, details`, ordered by `source_site`.

- [ ] **Step 1: Write the failing tests**

`tests/test_sqlite_store_state.py`:
```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite_store import SQLiteStore


class StoreStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = SQLiteStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_get_state_default_when_missing(self) -> None:
        self.assertIsNone(self.store.get_state("paused"))
        self.assertEqual(self.store.get_state("paused", "0"), "0")

    def test_set_state_overwrites_and_persists_across_instances(self) -> None:
        self.store.set_state("paused", "1")
        self.store.set_state("paused", "0")
        self.store.set_state("paused", "1")
        reopened = SQLiteStore(self.db_path)
        self.assertEqual(reopened.get_state("paused"), "1")

    def test_latest_source_runs_one_row_per_source(self) -> None:
        self.store.write_source_run("beta", "2026-10-05T10:00:00+00:00", "ok", "a")
        self.store.write_source_run("alpha", "2026-10-05T10:00:00+00:00", "error", "boom")
        self.store.write_source_run("alpha", "2026-10-05T10:10:00+00:00", "ok", "fine")
        rows = self.store.get_latest_source_runs()
        self.assertEqual([row["source_site"] for row in rows], ["alpha", "beta"])
        self.assertEqual(rows[0]["status"], "ok")
        self.assertEqual(rows[0]["details"], "fine")
        self.assertEqual(rows[0]["run_at"], "2026-10-05T10:10:00+00:00")

    def test_latest_source_runs_empty(self) -> None:
        self.assertEqual(self.store.get_latest_source_runs(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sqlite_store_state.py -v`
Expected: FAIL with `AttributeError: 'SQLiteStore' object has no attribute 'get_state'`.

- [ ] **Step 3: Implement**

In `SQLiteStore._init_db`, after the `source_runs` `CREATE TABLE` statement (same `with` block), add:
```python
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
```

Append these methods to `SQLiteStore`:
```python
    def get_state(self, key: str, default: str | None = None) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM bot_state WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row is not None else default

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bot_state (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def get_latest_source_runs(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT runs.source_site, runs.run_at, runs.status, runs.details
                FROM source_runs AS runs
                JOIN (
                    SELECT source_site, MAX(id) AS max_id
                    FROM source_runs
                    GROUP BY source_site
                ) AS latest ON runs.id = latest.max_id
                ORDER BY runs.source_site
                """
            ).fetchall()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_sqlite_store_state.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/storage/sqlite_store.py tests/test_sqlite_store_state.py
git commit -m "Add bot_state table and latest source run query

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Runner takes a notifier, returns RunSummary, dedupes ops alerts

**Files:**
- Create: `src/scrapers/factories.py`, `tests/test_runner.py`
- Modify: `src/scrapers/runner.py` (whole file), `src/main.py`
- Delete: `src/notify/telegram.py`, `src/notify/formatter.py`

**Interfaces:**
- Consumes: `Notifier`, `LogNotifier` (Task 2). `SQLiteStore.get_latest_source_runs` (Task 3). `tests.helpers` (Task 1).
- Produces:
  - `src.scrapers.factories.SOURCE_FACTORIES: dict[str, type[BaseScraper]]`, `src.scrapers.factories.REGISTRY_FILE: Path` (= `Path("src/config/sources.yaml")`)
  - `src.scrapers.runner.SourceResult` dataclass: `name: str, status: str, listings: int = 0, changed: int = 0, alerted: int = 0, details: str = ""`. `status` is one of `"ok" | "blocked" | "error"`.
  - `src.scrapers.runner.RunSummary` dataclass: `results: list[SourceResult]`, property `alerted -> int`
  - `run_all_sources(settings, store, source_factories, registry_file, notifier, selected_sources=None) -> RunSummary`. Policy-skipped and unselected sources are **not** in `results`. A missing adapter is in `results` as `status="error", details="no scraper adapter"`.
  - Ops texts: `"[SOURCE_BLOCKED] {name} - {state}"`, `"[SOURCE_ERROR] {name} - {error}"`, `"[SOURCE_RECOVERED] {name}"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_runner.py`:
```python
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from src.scrapers.base import SourceBlockedError
from src.scrapers.runner import run_all_sources
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import RecordingNotifier, make_listing, make_settings


REGISTRY = """
sources:
  - name: alpha
    mode: SCRAPE
    min_interval_seconds: 60
    max_retries: 0
    auto_disable_on_block: true
  - name: beta
    mode: SCRAPE
    min_interval_seconds: 60
    max_retries: 0
    auto_disable_on_block: true
  - name: gamma
    mode: ALERT_INGEST
    min_interval_seconds: 60
    max_retries: 0
    auto_disable_on_block: true
"""


def returning(listings):
    class _Scraper:
        def __init__(self, settings) -> None:
            pass

        def search(self, max_retries: int = 0):
            return [copy.deepcopy(listing) for listing in listings]

    return _Scraper


def raising(error: Exception):
    class _Scraper:
        def __init__(self, settings) -> None:
            pass

        def search(self, max_retries: int = 0):
            raise error

    return _Scraper


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.registry = root / "sources.yaml"
        self.registry.write_text(REGISTRY, encoding="utf-8")
        self.store = SQLiteStore(root / "test.db")
        self.settings = make_settings()
        self.notifier = RecordingNotifier()
        self.match = make_listing(source_site="alpha", source_listing_id="m1")
        self.non_match = make_listing(source_site="alpha", source_listing_id="n1", rent_price=3000)
        self.factories = {"alpha": returning([self.match, self.non_match]), "beta": returning([])}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_once(self, selected=None):
        return run_all_sources(
            settings=self.settings,
            store=self.store,
            source_factories=self.factories,
            registry_file=self.registry,
            notifier=self.notifier,
            selected_sources=selected,
        )

    def result(self, summary, name):
        return next(r for r in summary.results if r.name == name)

    def test_alerts_only_new_matches(self) -> None:
        first = self.run_once()
        alpha = self.result(first, "alpha")
        self.assertEqual((alpha.status, alpha.listings, alpha.changed, alpha.alerted), ("ok", 2, 1, 1))
        self.assertEqual(len(self.notifier.listings), 1)
        self.assertEqual(self.notifier.listings[0][0].source_listing_id, "m1")
        self.assertEqual(first.alerted, 1)

        second = self.run_once()
        self.assertEqual(self.result(second, "alpha").alerted, 0)
        self.assertEqual(len(self.notifier.listings), 1)
        self.assertEqual(self.notifier.ops, [])

    def test_policy_skipped_and_unselected_sources_not_in_summary(self) -> None:
        summary = self.run_once(selected={"alpha"})
        self.assertEqual([r.name for r in summary.results], ["alpha"])
        all_sources = self.run_once()
        self.assertEqual(sorted(r.name for r in all_sources.results), ["alpha", "beta"])

    def test_missing_adapter_is_error_result_without_ops(self) -> None:
        del self.factories["beta"]
        summary = self.run_once()
        beta = self.result(summary, "beta")
        self.assertEqual((beta.status, beta.details), ("error", "no scraper adapter"))
        self.assertEqual(self.notifier.ops, [])

    def test_blocked_source_notifies_once_until_recovered(self) -> None:
        self.factories["beta"] = raising(SourceBlockedError("403"))
        self.assertEqual(self.result(self.run_once(), "beta").status, "blocked")
        self.run_once()
        self.assertEqual(len(self.notifier.ops), 1)
        self.assertTrue(self.notifier.ops[0].startswith("[SOURCE_BLOCKED] beta"))

        self.factories["beta"] = returning([])
        self.run_once()
        self.run_once()
        self.assertEqual(self.notifier.ops[1:], ["[SOURCE_RECOVERED] beta"])

    def test_error_source_notifies_once_until_recovered(self) -> None:
        self.factories["beta"] = raising(RuntimeError("boom"))
        summary = self.run_once()
        self.run_once()
        self.run_once()
        beta = self.result(summary, "beta")
        self.assertEqual((beta.status, beta.details), ("error", "boom"))
        self.assertEqual(self.notifier.ops, ["[SOURCE_ERROR] beta - boom"])

        self.factories["beta"] = returning([])
        self.run_once()
        self.assertEqual(self.notifier.ops[-1], "[SOURCE_RECOVERED] beta")

    def test_error_then_blocked_notifies_both(self) -> None:
        self.factories["beta"] = raising(RuntimeError("boom"))
        self.run_once()
        self.factories["beta"] = raising(SourceBlockedError("429"))
        self.run_once()
        self.assertEqual(len(self.notifier.ops), 2)
        self.assertTrue(self.notifier.ops[1].startswith("[SOURCE_BLOCKED] beta"))

    def test_first_ever_ok_run_does_not_send_recovered(self) -> None:
        self.run_once()
        self.assertEqual(self.notifier.ops, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_runner.py -v`
Expected: FAIL. The import of `src.scrapers.runner` fails on the missing `src.notify.telegram` dependency, or `run_all_sources() got an unexpected keyword argument 'notifier'`.

- [ ] **Step 3: Create the shared factories module**

`src/scrapers/factories.py`:
```python
from __future__ import annotations

from pathlib import Path

from src.scrapers.base import BaseScraper
from src.scrapers.sites.nrw_wonen import NRWonenScraper
from src.scrapers.sites.thehaguerealestate import TheHagueRealEstateScraper
from src.scrapers.sites.vbent import VBentScraper
from src.scrapers.sites.verra import VerraScraper
from src.scrapers.sites.vesteda import VestedaScraper
from src.scrapers.sites.wobeco import WobecoScraper


REGISTRY_FILE = Path("src/config/sources.yaml")

SOURCE_FACTORIES: dict[str, type[BaseScraper]] = {
    "nrw_wonen": NRWonenScraper,
    "thehaguerealestate": TheHagueRealEstateScraper,
    "wobeco": WobecoScraper,
    "verra": VerraScraper,
    "vesteda": VestedaScraper,
    "vbent": VBentScraper,
}
```

- [ ] **Step 4: Rewrite `src/scrapers/runner.py`**

Replace the whole file with:
```python
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Settings
from src.filtering.rules import evaluate_listing
from src.notify.base import Notifier
from src.policy.risk_policy import is_collection_allowed
from src.scrapers.base import SourceBlockedError
from src.scrapers.registry import load_source_policies
from src.scrapers.source_health import SourceHealthState, SourceHealthTracker
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)

FAILED_STATUSES = ("blocked", "error")


@dataclass
class SourceResult:
    name: str
    status: str
    listings: int = 0
    changed: int = 0
    alerted: int = 0
    details: str = ""


@dataclass
class RunSummary:
    results: list[SourceResult] = field(default_factory=list)

    @property
    def alerted(self) -> int:
        return sum(result.alerted for result in self.results)


def _now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def run_all_sources(
    settings: Settings,
    store: SQLiteStore,
    source_factories: dict[str, callable],
    registry_file: Path,
    notifier: Notifier,
    selected_sources: set[str] | None = None,
) -> RunSummary:
    health_tracker = SourceHealthTracker()
    policies = load_source_policies(registry_file)
    # Ops alerts fire only when a source's status changes, so a source that stays
    # broken does not post on every cycle.
    previous_status = {row["source_site"]: row["status"] for row in store.get_latest_source_runs()}
    summary = RunSummary()
    logger.info("Loaded %d source policies from %s", len(policies), registry_file)

    for policy in policies:
        logger.info("Evaluating source=%s mode=%s", policy.name, policy.mode.value)
        if selected_sources and policy.name not in selected_sources:
            logger.info("Skipping source=%s because it is not selected", policy.name)
            continue

        if not is_collection_allowed(policy):
            logger.info("Skipping source=%s due to policy mode=%s", policy.name, policy.mode.value)
            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="skipped",
                details=f"mode={policy.mode.value}",
            )
            continue

        factory = source_factories.get(policy.name)
        if factory is None:
            logger.warning("No adapter for source=%s", policy.name)
            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="error",
                details="no scraper adapter",
            )
            summary.results.append(
                SourceResult(name=policy.name, status="error", details="no scraper adapter")
            )
            continue

        previous = previous_status.get(policy.name)
        scraper = factory(settings)
        try:
            logger.info("Running source=%s", policy.name)
            listings = scraper.search(max_retries=policy.max_retries)
            health_tracker.mark_success(policy.name)
            logger.info("Source=%s returned %d listings", policy.name, len(listings))

            evaluated_count = 0
            skipped_non_match_count = 0
            changed_count = 0
            alerted_count = 0

            for listing in listings:
                evaluated_count += 1
                match = evaluate_listing(listing, settings)
                is_match = match.is_hard_match or match.is_close_match

                if settings.store_only_matches and not is_match:
                    skipped_non_match_count += 1
                    continue

                upsert = store.upsert_listing(listing)
                if not upsert.changed:
                    continue
                changed_count += 1

                if not is_match:
                    continue

                notifier.notify_listing(listing, match)
                alerted_count += 1

            logger.info(
                "Source=%s changed=%d alerted=%d",
                policy.name,
                changed_count,
                alerted_count,
            )
            logger.info(
                "Source=%s evaluated=%d skipped_non_match=%d store_only_matches=%s",
                policy.name,
                evaluated_count,
                skipped_non_match_count,
                settings.store_only_matches,
            )

            details = (
                f"listings={len(listings)},evaluated={evaluated_count},"
                f"skipped_non_match={skipped_non_match_count},changed={changed_count},"
                f"alerted={alerted_count},store_only_matches={settings.store_only_matches}"
            )
            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="ok",
                details=details,
            )
            summary.results.append(
                SourceResult(
                    name=policy.name,
                    status="ok",
                    listings=len(listings),
                    changed=changed_count,
                    alerted=alerted_count,
                    details=details,
                )
            )
            if previous in FAILED_STATUSES:
                notifier.notify_ops(f"[SOURCE_RECOVERED] {policy.name}")

        except SourceBlockedError as error:
            logger.warning("Source blocked source=%s error=%s", policy.name, error)
            state = health_tracker.mark_block(policy.name)
            details = f"blocked: {error}"
            if policy.auto_disable_on_block and state == SourceHealthState.BLOCKED:
                details = f"{details}; action=disable-suggested"

            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="blocked",
                details=details,
            )
            summary.results.append(SourceResult(name=policy.name, status="blocked", details=details))
            if previous != "blocked":
                notifier.notify_ops(f"[SOURCE_BLOCKED] {policy.name} - {state.value}")

        except Exception as error:  # noqa: BLE001
            logger.exception("Source failed source=%s", policy.name)
            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="error",
                details=str(error),
            )
            summary.results.append(SourceResult(name=policy.name, status="error", details=str(error)))
            if previous != "error":
                notifier.notify_ops(f"[SOURCE_ERROR] {policy.name} - {error}")

    return summary
```

- [ ] **Step 5: Update `src/main.py` and delete Telegram files**

In `src/main.py`:
- Remove the six `from src.scrapers.sites...` imports and `from pathlib import Path`.
- Add imports:
  ```python
  from src.notify.base import LogNotifier
  from src.scrapers.factories import REGISTRY_FILE, SOURCE_FACTORIES
  ```
- In `main()`, delete the `source_factories = {...}` dict and the `registry_file = Path(...)` line, and change the call to:
  ```python
      summary = run_all_sources(
          settings=settings,
          store=store,
          source_factories=SOURCE_FACTORIES,
          registry_file=REGISTRY_FILE,
          notifier=LogNotifier(),
          selected_sources=selected_sources,
      )
      logger.info("Run completed alerted=%d", summary.alerted)
  ```
  (This replaces the existing `logger.info("Run completed")`.)

Delete the replaced files:
```bash
git rm src/notify/telegram.py src/notify/formatter.py
```

Verify nothing still references them:
Run: `git grep -n -i "telegram\|format_listing_message" -- src tests`
Expected: no output.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests -v`
Expected: all PASS (config, notifier, store, runner, vbent).

Run: `.\.venv\Scripts\python.exe -m src.main --listings --limit 1`
Expected: prints a table or `No listings found in database yet.` with no import errors.

- [ ] **Step 7: Commit**

```bash
git add src/scrapers/factories.py src/scrapers/runner.py src/main.py tests/test_runner.py
git commit -m "Inject notifier into runner, return RunSummary, dedupe ops alerts

Telegram notifier removed; CLI runs log alerts via LogNotifier.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Bot checks and embed builders

**Files:**
- Create: `src/bot/__init__.py`, `src/bot/checks.py`, `src/bot/embeds.py`, `tests/test_bot_helpers.py`

**Interfaces:**
- Consumes: `Settings` fields (Task 1). `RunSummary`, `SourceResult` (Task 4). `truncate` (Task 2).
- Produces:
  - `src.bot.checks.is_control_user(user_id: int, role_ids: Iterable[int], settings: Settings) -> bool`
  - `src.bot.checks.validate_bot_settings(settings: Settings) -> list[str]`: human-readable errors, empty when valid.
  - `src.bot.checks.parse_sources(text: str | None, known: Iterable[str]) -> tuple[set[str] | None, list[str]]`: `(selected or None for all, sorted unknown names)`.
  - `src.bot.embeds.build_listings_embed(rows: Sequence[Mapping]) -> discord.Embed`
  - `src.bot.embeds.build_status_embed(paused: bool, cycle_running: bool, next_run: datetime | None, last_cycle_at: datetime | None, source_rows: Sequence[Mapping]) -> discord.Embed`
  - `src.bot.embeds.build_profile_embed(settings: Settings) -> discord.Embed`
  - `src.bot.embeds.build_summary_embed(summary: RunSummary) -> discord.Embed`

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_helpers.py`:
```python
from __future__ import annotations

import datetime as dt
import unittest

from src.bot.checks import is_control_user, parse_sources, validate_bot_settings
from src.bot.embeds import (
    build_listings_embed,
    build_profile_embed,
    build_status_embed,
    build_summary_embed,
)
from src.scrapers.runner import RunSummary, SourceResult
from tests.helpers import make_settings


def listing_row(**overrides) -> dict:
    row = {
        "source_site": "vbent",
        "title": "Maria Stuartplein 130",
        "city": "Delft",
        "rent_price": 950,
        "living_area_m2": 55,
        "bedrooms": None,
        "source_url": "https://example.com/1",
        "is_available": 1,
        "first_seen_at": "2026-10-05T10:00:00+00:00",
        "last_seen_at": "2026-10-05T10:00:00+00:00",
        "listing_status": "available",
    }
    row.update(overrides)
    return row


class ControlUserTests(unittest.TestCase):
    def test_user_id_allowed(self) -> None:
        settings = make_settings(discord_control_user_ids=[1, 2])
        self.assertTrue(is_control_user(2, [], settings))

    def test_role_allowed(self) -> None:
        settings = make_settings(discord_control_role_id=99)
        self.assertTrue(is_control_user(5, [10, 99], settings))

    def test_other_user_denied(self) -> None:
        settings = make_settings(discord_control_user_ids=[1], discord_control_role_id=99)
        self.assertFalse(is_control_user(5, [10], settings))

    def test_nothing_configured_denies_everyone(self) -> None:
        self.assertFalse(is_control_user(1, [1], make_settings()))


class ValidateSettingsTests(unittest.TestCase):
    def test_all_missing_reports_each(self) -> None:
        errors = validate_bot_settings(make_settings())
        joined = "\n".join(errors)
        for name in ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "DISCORD_ALERT_CHANNEL_ID", "DISCORD_CONTROL_USER_IDS"):
            self.assertIn(name, joined)

    def test_valid_settings(self) -> None:
        settings = make_settings(
            discord_bot_token="t",
            discord_guild_id=1,
            discord_alert_channel_id=2,
            discord_control_role_id=3,
        )
        self.assertEqual(validate_bot_settings(settings), [])


class ParseSourcesTests(unittest.TestCase):
    known = ["vbent", "wobeco", "verra"]

    def test_empty_means_all(self) -> None:
        self.assertEqual(parse_sources(None, self.known), (None, []))
        self.assertEqual(parse_sources("  ", self.known), (None, []))

    def test_selected_with_whitespace(self) -> None:
        self.assertEqual(parse_sources(" vbent , wobeco,", self.known), ({"vbent", "wobeco"}, []))

    def test_unknown_reported_sorted(self) -> None:
        selected, unknown = parse_sources("vbent,zzz,funda", self.known)
        self.assertEqual(unknown, ["funda", "zzz"])


class ListingsEmbedTests(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(build_listings_embed([]).description, "No listings stored yet.")

    def test_lines_contain_key_info(self) -> None:
        description = build_listings_embed([listing_row()]).description
        self.assertIn("[Maria Stuartplein 130](https://example.com/1)", description)
        self.assertIn("€950", description)
        self.assertIn("55 m²", description)
        self.assertIn("Delft", description)
        self.assertIn("vbent", description)

    def test_missing_values_and_brackets_in_title(self) -> None:
        row = listing_row(title="Flat [new]", rent_price=None, living_area_m2=None, city=None)
        description = build_listings_embed([row]).description
        self.assertIn("[Flat (new)]", description)
        self.assertEqual(description.count("n/a"), 3)

    def test_long_list_stays_under_limit(self) -> None:
        rows = [listing_row(title="x" * 200, source_url="https://example.com/" + "y" * 200)] * 25
        embed = build_listings_embed(rows)
        self.assertLessEqual(len(embed.description), 4096)
        self.assertIn("of 25", embed.footer.text)


class StatusEmbedTests(unittest.TestCase):
    def test_no_runs_yet(self) -> None:
        embed = build_status_embed(False, False, None, None, [])
        fields = {f.name: f.value for f in embed.fields}
        self.assertEqual(fields["Scheduler"], "Running")
        self.assertEqual(fields["Last cycle"], "never")
        self.assertEqual(fields["Sources"], "No runs recorded yet.")

    def test_paused_hides_next_run(self) -> None:
        next_run = dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.timezone.utc)
        fields = {f.name: f.value for f in build_status_embed(True, False, next_run, None, []).fields}
        self.assertEqual(fields["Scheduler"], "Paused")
        self.assertEqual(fields["Next run"], "—")

    def test_cycle_running_and_sources(self) -> None:
        now = dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.timezone.utc)
        rows = [
            {"source_site": "vbent", "run_at": "2026-10-05T11:50:00+00:00", "status": "ok", "details": "listings=3"},
            {"source_site": "verra", "run_at": "2026-10-05T11:50:00+00:00", "status": "error", "details": "boom"},
        ]
        fields = {f.name: f.value for f in build_status_embed(False, True, now, now, rows).fields}
        self.assertEqual(fields["Scheduler"], "Running (cycle in progress)")
        self.assertIn("<t:", fields["Next run"])
        self.assertIn("vbent", fields["Sources"])
        self.assertIn("boom", fields["Sources"])
        self.assertNotIn("listings=3", fields["Sources"])


class ProfileEmbedTests(unittest.TestCase):
    def test_profile_fields(self) -> None:
        fields = {f.name: f.value for f in build_profile_embed(make_settings()).fields}
        self.assertEqual(fields["Max rent"], "€1000")
        self.assertEqual(fields["Min size"], "40 m²")
        self.assertIn("on", fields["Close match"])
        self.assertEqual(fields["Interval"], "every 10 min")
        self.assertEqual(fields["Cities"], "Den Haag, Delft")


class SummaryEmbedTests(unittest.TestCase):
    def test_summary_lines(self) -> None:
        summary = RunSummary(
            results=[
                SourceResult(name="vbent", status="ok", listings=4, changed=2, alerted=1),
                SourceResult(name="verra", status="blocked", details="blocked: 403"),
            ]
        )
        embed = build_summary_embed(summary)
        self.assertIn("vbent", embed.description)
        self.assertIn("4 listings", embed.description)
        self.assertIn("blocked: 403", embed.description)
        self.assertEqual(embed.footer.text, "1 new alert(s)")

    def test_empty_summary(self) -> None:
        self.assertEqual(build_summary_embed(RunSummary()).description, "No sources ran.")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bot'`.

- [ ] **Step 3: Implement**

`src/bot/__init__.py`: empty file.

`src/bot/checks.py`:
```python
from __future__ import annotations

from collections.abc import Iterable

from src.config import Settings


def is_control_user(user_id: int, role_ids: Iterable[int], settings: Settings) -> bool:
    if user_id in settings.discord_control_user_ids:
        return True
    role_id = settings.discord_control_role_id
    return role_id is not None and role_id in set(role_ids)


def validate_bot_settings(settings: Settings) -> list[str]:
    errors: list[str] = []
    if not settings.discord_bot_token:
        errors.append("DISCORD_BOT_TOKEN is not set")
    if settings.discord_guild_id is None:
        errors.append("DISCORD_GUILD_ID is not set")
    if settings.discord_alert_channel_id is None:
        errors.append("DISCORD_ALERT_CHANNEL_ID is not set")
    if not settings.discord_control_user_ids and settings.discord_control_role_id is None:
        errors.append(
            "Neither DISCORD_CONTROL_USER_IDS nor DISCORD_CONTROL_ROLE_ID is set; "
            "nobody could use /scrape, /pause or /resume"
        )
    return errors


def parse_sources(text: str | None, known: Iterable[str]) -> tuple[set[str] | None, list[str]]:
    names = {item.strip() for item in (text or "").split(",") if item.strip()}
    if not names:
        return None, []
    unknown = sorted(names - set(known))
    return names, unknown
```

`src/bot/embeds.py`:
```python
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import discord

from src.config import Settings
from src.notify.discord_notifier import DESCRIPTION_LIMIT, truncate
from src.scrapers.runner import RunSummary


FIELD_LIMIT = 1024
# Leave headroom below the 4096 limit for the trailing newline handling.
LIST_BUDGET = 4000
STATUS_ICONS = {"ok": "✅", "blocked": "⛔", "error": "❌", "skipped": "⏭️"}


def _money(value) -> str:
    return f"€{value}" if value is not None else "n/a"


def _area(value) -> str:
    return f"{value} m²" if value is not None else "n/a"


def _when(value: dt.datetime | None) -> str:
    return discord.utils.format_dt(value, "R") if value is not None else "never"


def _fit_lines(lines: list[str], budget: int) -> tuple[str, int]:
    text = ""
    shown = 0
    for line in lines:
        if len(text) + len(line) + 1 > budget:
            break
        text += line + "\n"
        shown += 1
    return text.rstrip("\n"), shown


def build_listings_embed(rows: Sequence[Mapping]) -> discord.Embed:
    embed = discord.Embed(title="Recent matches", color=discord.Color.blurple())
    if not rows:
        embed.description = "No listings stored yet."
        return embed

    lines = []
    for row in rows:
        title = truncate(row["title"] or "(untitled)", 80).replace("[", "(").replace("]", ")")
        lines.append(
            f"[{title}]({row['source_url']}) · {_money(row['rent_price'])} · "
            f"{_area(row['living_area_m2'])} · {row['city'] or 'n/a'} · {row['source_site']}"
        )
    embed.description, shown = _fit_lines(lines, LIST_BUDGET)
    if shown < len(rows):
        embed.set_footer(text=f"Showing {shown} of {len(rows)}")
    return embed


def build_status_embed(
    paused: bool,
    cycle_running: bool,
    next_run: dt.datetime | None,
    last_cycle_at: dt.datetime | None,
    source_rows: Sequence[Mapping],
) -> discord.Embed:
    color = discord.Color.orange() if paused else discord.Color.green()
    embed = discord.Embed(title="Scraper status", color=color)
    if paused:
        scheduler = "Paused"
    elif cycle_running:
        scheduler = "Running (cycle in progress)"
    else:
        scheduler = "Running"
    embed.add_field(name="Scheduler", value=scheduler)
    embed.add_field(name="Last cycle", value=_when(last_cycle_at))
    embed.add_field(name="Next run", value="—" if paused or next_run is None else _when(next_run))

    if not source_rows:
        embed.add_field(name="Sources", value="No runs recorded yet.", inline=False)
        return embed

    lines = []
    for row in source_rows:
        icon = STATUS_ICONS.get(row["status"], "•")
        when = str(row["run_at"])[:16].replace("T", " ")
        line = f"{icon} **{row['source_site']}** · {row['status']} · {when} UTC"
        if row["status"] in ("blocked", "error") and row["details"]:
            line += f" · {truncate(str(row['details']), 80)}"
        lines.append(line)
    value, _ = _fit_lines(lines, FIELD_LIMIT)
    embed.add_field(name="Sources", value=value, inline=False)
    return embed


def build_profile_embed(settings: Settings) -> discord.Embed:
    embed = discord.Embed(title="Search profile", color=discord.Color.blurple())
    embed.add_field(name="Max rent", value=f"€{settings.max_rent_eur}")
    embed.add_field(name="Min size", value=f"{settings.min_size_m2} m²")
    embed.add_field(name="Preferred bedrooms", value=str(settings.preferred_bedrooms))
    close = "on (+10% price, −10% area)" if settings.allow_close_match else "off"
    embed.add_field(name="Close match", value=close)
    embed.add_field(name="Interval", value=f"every {settings.scrape_interval_minutes} min")
    cities = ", ".join(settings.allowed_cities) or "n/a"
    embed.add_field(name="Cities", value=truncate(cities, FIELD_LIMIT), inline=False)
    return embed


def build_summary_embed(summary: RunSummary) -> discord.Embed:
    embed = discord.Embed(title="Scrape finished", color=discord.Color.blurple())
    if not summary.results:
        embed.description = "No sources ran."
        return embed

    lines = []
    for result in summary.results:
        icon = STATUS_ICONS.get(result.status, "•")
        if result.status == "ok":
            lines.append(
                f"{icon} **{result.name}** · {result.listings} listings · "
                f"{result.changed} changed · {result.alerted} alerted"
            )
        else:
            lines.append(f"{icon} **{result.name}** · {truncate(result.details, 100)}")
    embed.description, _ = _fit_lines(lines, DESCRIPTION_LIMIT)
    embed.set_footer(text=f"{summary.alerted} new alert(s)")
    return embed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_helpers.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/bot/__init__.py src/bot/checks.py src/bot/embeds.py tests/test_bot_helpers.py
git commit -m "Add bot permission checks, settings validation and embed builders

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: HuurBot client, scheduler, slash commands, entrypoint

**Files:**
- Create: `src/bot/client.py`, `src/bot/commands.py`, `src/bot/__main__.py`, `tests/test_bot_client.py`

**Interfaces:**
- Consumes: `DiscordNotifier` (Task 2). `get_state`/`set_state`/`get_latest_source_runs`/`get_recent_listings` (Task 3). `run_all_sources`, `RunSummary`, `SOURCE_FACTORIES`, `REGISTRY_FILE` (Task 4). Checks and embeds (Task 5).
- Produces:
  - `src.bot.client.CycleBusyError(Exception)`
  - `src.bot.client.HuurBot(settings: Settings, store: SQLiteStore)`, a `discord.Client` with attributes `settings`, `store`, `tree: app_commands.CommandTree`, `notifier: Notifier | None`, `last_cycle_at: datetime | None`, `exit_code: int`. Properties `paused: bool` and `cycle_running: bool`. Methods `set_paused(paused: bool) -> None`, `async run_cycle(selected_sources: set[str] | None = None) -> RunSummary | None` (raises `CycleBusyError` when a cycle is running, returns `None` when the cycle crashed), `async scheduled_tick() -> None`. Loop `scrape_loop: tasks.Loop`.
  - `src.bot.commands.register_commands(bot: HuurBot) -> None`
  - Entrypoint: `python -m src.bot` (exit code 2 = config error, 1 = login or channel failure).

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_client.py`:
```python
from __future__ import annotations

import asyncio
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from src.bot.client import CycleBusyError, HuurBot
from src.scrapers.runner import RunSummary, SourceResult
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import RecordingNotifier, make_settings


class HuurBotCycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "bot.db"
        self.store = SQLiteStore(self.db_path)
        self.bot = HuurBot(make_settings(discord_guild_id=1), self.store)
        self.notifier = RecordingNotifier()
        self.bot.notifier = self.notifier

    async def asyncTearDown(self) -> None:
        # The bot never logs in, so there is no connection to close.
        self.tmp.cleanup()

    async def test_run_cycle_returns_summary_and_records_time(self) -> None:
        summary = RunSummary(results=[SourceResult(name="vbent", status="ok")])
        with patch("src.bot.client.run_all_sources", return_value=summary) as runner:
            result = await self.bot.run_cycle({"vbent"})
        self.assertIs(result, summary)
        self.assertIsNotNone(self.bot.last_cycle_at)
        kwargs = runner.call_args.kwargs
        self.assertIs(kwargs["notifier"], self.notifier)
        self.assertEqual(kwargs["selected_sources"], {"vbent"})

    async def test_second_cycle_while_running_raises_busy(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow_run(**kwargs):
            started.set()
            release.wait(timeout=5)
            return RunSummary()

        with patch("src.bot.client.run_all_sources", side_effect=slow_run):
            first = asyncio.create_task(self.bot.run_cycle())
            await asyncio.to_thread(started.wait, 5)
            self.assertTrue(self.bot.cycle_running)
            with self.assertRaises(CycleBusyError):
                await self.bot.run_cycle()
            release.set()
            self.assertIsInstance(await first, RunSummary)
        self.assertFalse(self.bot.cycle_running)

    async def test_scheduled_tick_skips_silently_when_busy(self) -> None:
        release = threading.Event()
        started = threading.Event()

        def slow_run(**kwargs):
            started.set()
            release.wait(timeout=5)
            return RunSummary()

        with patch("src.bot.client.run_all_sources", side_effect=slow_run) as runner:
            first = asyncio.create_task(self.bot.run_cycle())
            await asyncio.to_thread(started.wait, 5)
            await self.bot.scheduled_tick()
            release.set()
            await first
        self.assertEqual(runner.call_count, 1)

    async def test_cycle_crash_notifies_ops_and_returns_none(self) -> None:
        with patch("src.bot.client.run_all_sources", side_effect=RuntimeError("db locked")):
            with self.assertLogs("src.bot.client", level="ERROR"):
                result = await self.bot.run_cycle()
        self.assertIsNone(result)
        self.assertEqual(self.notifier.ops, ["[CYCLE_ERROR] db locked"])
        self.assertFalse(self.bot.cycle_running)

    async def test_scheduled_tick_skips_when_paused(self) -> None:
        self.bot.set_paused(True)
        with patch("src.bot.client.run_all_sources") as runner:
            await self.bot.scheduled_tick()
        runner.assert_not_called()

    async def test_scheduled_tick_runs_when_not_paused(self) -> None:
        with patch("src.bot.client.run_all_sources", return_value=RunSummary()) as runner:
            await self.bot.scheduled_tick()
        runner.assert_called_once()

    async def test_pause_persists_across_bot_instances(self) -> None:
        self.bot.set_paused(True)
        other = HuurBot(make_settings(discord_guild_id=1), SQLiteStore(self.db_path))
        self.assertTrue(other.paused)
        other.set_paused(False)
        self.assertFalse(self.bot.paused)

    async def test_interval_comes_from_settings(self) -> None:
        bot = HuurBot(make_settings(discord_guild_id=1, scrape_interval_minutes=25), self.store)
        self.assertEqual(bot.scrape_loop.minutes, 25)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bot.client'`.

- [ ] **Step 3: Implement `src/bot/client.py`**

```python
from __future__ import annotations

import asyncio
import datetime as dt
import logging

import discord
from discord.ext import tasks

from src.config import Settings
from src.notify.base import Notifier
from src.notify.discord_notifier import DiscordNotifier
from src.scrapers.factories import REGISTRY_FILE, SOURCE_FACTORIES
from src.scrapers.runner import RunSummary, run_all_sources
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)

PAUSED_KEY = "paused"
FIRST_RUN_DELAY_SECONDS = 30


class CycleBusyError(Exception):
    """Raised when a scrape cycle is requested while another one is running."""


class HuurBot(discord.Client):
    def __init__(self, settings: Settings, store: SQLiteStore) -> None:
        super().__init__(intents=discord.Intents.default())
        self.settings = settings
        self.store = store
        self.tree = discord.app_commands.CommandTree(self)
        self.notifier: Notifier | None = None
        self.last_cycle_at: dt.datetime | None = None
        self.exit_code = 0
        self._cycle_lock = asyncio.Lock()
        self._ready_once = False
        self.scrape_loop.change_interval(minutes=settings.scrape_interval_minutes)

    @property
    def paused(self) -> bool:
        return self.store.get_state(PAUSED_KEY, "0") == "1"

    def set_paused(self, paused: bool) -> None:
        self.store.set_state(PAUSED_KEY, "1" if paused else "0")

    @property
    def cycle_running(self) -> bool:
        return self._cycle_lock.locked()

    async def setup_hook(self) -> None:
        from src.bot.commands import register_commands

        register_commands(self)
        guild = discord.Object(id=self.settings.discord_guild_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info("Synced %d slash commands to guild=%s", len(synced), guild.id)

    async def on_ready(self) -> None:
        if self._ready_once:
            logger.info("Reconnected as %s", self.user)
            return
        self._ready_once = True
        logger.info("Logged in as %s", self.user)

        alert_channel = await self._resolve_channel(self.settings.discord_alert_channel_id)
        if alert_channel is None:
            logger.error(
                "Alert channel %s not found or not accessible; shutting down",
                self.settings.discord_alert_channel_id,
            )
            self.exit_code = 1
            await self.close()
            return

        ops_channel = None
        if self.settings.discord_ops_channel_id is not None:
            ops_channel = await self._resolve_channel(self.settings.discord_ops_channel_id)
            if ops_channel is None:
                logger.warning(
                    "Ops channel %s not found; ops messages go to the alert channel",
                    self.settings.discord_ops_channel_id,
                )

        self.notifier = DiscordNotifier(
            asyncio.get_running_loop(),
            alert_channel,
            ops_channel,
            self.settings.discord_mention_user_id,
        )
        self.scrape_loop.start()
        logger.info(
            "Scheduler started (every %d min, paused=%s)",
            self.settings.scrape_interval_minutes,
            self.paused,
        )

    async def _resolve_channel(self, channel_id: int):
        channel = self.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        if not isinstance(channel, discord.abc.Messageable):
            return None
        return channel

    async def run_cycle(self, selected_sources: set[str] | None = None) -> RunSummary | None:
        if self._cycle_lock.locked():
            raise CycleBusyError()
        async with self._cycle_lock:
            self.last_cycle_at = dt.datetime.now(tz=dt.timezone.utc)
            try:
                summary = await asyncio.to_thread(
                    run_all_sources,
                    settings=self.settings,
                    store=self.store,
                    source_factories=SOURCE_FACTORIES,
                    registry_file=REGISTRY_FILE,
                    notifier=self.notifier,
                    selected_sources=selected_sources,
                )
            except Exception as error:  # noqa: BLE001
                logger.exception("Scrape cycle failed")
                # notify_ops blocks on the event loop, so it must run off-loop.
                await asyncio.to_thread(self.notifier.notify_ops, f"[CYCLE_ERROR] {error}")
                return None
            logger.info("Cycle finished alerted=%d", summary.alerted)
            return summary

    async def scheduled_tick(self) -> None:
        if self.paused:
            logger.info("Scheduler paused; skipping cycle")
            return
        try:
            await self.run_cycle()
        except CycleBusyError:
            logger.info("Previous cycle still running; skipping scheduled tick")

    @tasks.loop(minutes=10)
    async def scrape_loop(self) -> None:
        # The interval is replaced in __init__ with SCRAPE_INTERVAL_MINUTES.
        await self.scheduled_tick()

    @scrape_loop.before_loop
    async def _before_scrape_loop(self) -> None:
        await self.wait_until_ready()
        await asyncio.sleep(FIRST_RUN_DELAY_SECONDS)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_bot_client.py -v`
Expected: all PASS. (`setup_hook` and `on_ready` only run when connecting and are covered by the manual check in Step 8.)

- [ ] **Step 5: Implement `src/bot/commands.py`**

This file intentionally does **not** use `from __future__ import annotations`. discord.py inspects slash-command parameter annotations at decoration time, and these handlers are nested functions.

```python
import asyncio
import logging

import discord
from discord import app_commands

from src.bot.checks import is_control_user, parse_sources
from src.bot.client import CycleBusyError, HuurBot
from src.bot.embeds import (
    build_listings_embed,
    build_profile_embed,
    build_status_embed,
    build_summary_embed,
)
from src.scrapers.factories import SOURCE_FACTORIES


logger = logging.getLogger(__name__)


def register_commands(bot: HuurBot) -> None:
    tree = bot.tree

    async def ensure_control(interaction: discord.Interaction) -> bool:
        role_ids = [role.id for role in getattr(interaction.user, "roles", [])]
        if is_control_user(interaction.user.id, role_ids, bot.settings):
            return True
        await interaction.response.send_message(
            "You're not allowed to use this command.", ephemeral=True
        )
        return False

    @tree.command(name="listings", description="Show recently stored matching listings")
    @app_commands.describe(limit="How many listings to show (1-25)")
    async def listings(
        interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 10
    ) -> None:
        rows = await asyncio.to_thread(bot.store.get_recent_listings, limit)
        await interaction.response.send_message(embed=build_listings_embed(rows), ephemeral=True)

    @tree.command(name="status", description="Show scheduler state and the last run per source")
    async def status(interaction: discord.Interaction) -> None:
        rows = await asyncio.to_thread(bot.store.get_latest_source_runs)
        next_run = bot.scrape_loop.next_iteration if bot.scrape_loop.is_running() else None
        embed = build_status_embed(bot.paused, bot.cycle_running, next_run, bot.last_cycle_at, rows)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(name="profile", description="Show the current search profile")
    async def profile(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_profile_embed(bot.settings), ephemeral=True
        )

    @tree.command(name="scrape", description="Run a scrape cycle now")
    @app_commands.describe(sources="Comma-separated source names (default: all)")
    async def scrape(interaction: discord.Interaction, sources: str = "") -> None:
        if not await ensure_control(interaction):
            return
        selected, unknown = parse_sources(sources, SOURCE_FACTORIES.keys())
        if unknown:
            known = ", ".join(sorted(SOURCE_FACTORIES))
            await interaction.response.send_message(
                f"Unknown source(s): {', '.join(unknown)}. Known: {known}", ephemeral=True
            )
            return
        if bot.notifier is None:
            await interaction.response.send_message("Bot is still starting up.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            summary = await bot.run_cycle(selected)
        except CycleBusyError:
            await interaction.followup.send("A scrape cycle is already running. Try again shortly.")
            return
        if summary is None:
            await interaction.followup.send(
                "The scrape cycle failed. Check the ops channel or container logs."
            )
            return
        await interaction.followup.send(embed=build_summary_embed(summary))

    @tree.command(name="pause", description="Pause scheduled scraping")
    async def pause(interaction: discord.Interaction) -> None:
        if not await ensure_control(interaction):
            return
        bot.set_paused(True)
        logger.info("Paused by user=%s", interaction.user.id)
        await interaction.response.send_message(
            "Scheduled scraping paused. `/scrape` still works.", ephemeral=True
        )

    @tree.command(name="resume", description="Resume scheduled scraping")
    async def resume(interaction: discord.Interaction) -> None:
        if not await ensure_control(interaction):
            return
        bot.set_paused(False)
        logger.info("Resumed by user=%s", interaction.user.id)
        await interaction.response.send_message("Scheduled scraping resumed.", ephemeral=True)
```

- [ ] **Step 6: Implement `src/bot/__main__.py`**

```python
from __future__ import annotations

import logging
import sys

import discord

from src.bot.checks import validate_bot_settings
from src.bot.client import HuurBot
from src.config import load_settings
from src.logging_setup import configure_logging
from src.storage.sqlite_store import SQLiteStore


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as error:
        print(f"Config error: {error}", file=sys.stderr)
        return 2

    configure_logging(settings)
    logger = logging.getLogger("src.bot")

    errors = validate_bot_settings(settings)
    if errors:
        for error in errors:
            logger.error("Config error: %s", error)
        return 2

    store = SQLiteStore(settings.database_path)
    bot = HuurBot(settings, store)
    try:
        # log_handler=None keeps the logging configured by configure_logging.
        bot.run(settings.discord_bot_token, log_handler=None)
    except discord.LoginFailure:
        logger.error("Discord rejected DISCORD_BOT_TOKEN")
        return 1
    return bot.exit_code


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: Verify the commands module imports and the entrypoint fails fast**

Run: `.\.venv\Scripts\python.exe -c "import src.bot.commands; print('ok')"`
Expected: `ok`.

Run (PowerShell). If a local `.env` exists, move it aside first, because setting `$env:X=''` in PowerShell 5.1 deletes the variable and `load_dotenv` would fill it back in:
```powershell
if (Test-Path .env) { Rename-Item .env .env.bak }
.\.venv\Scripts\python.exe -m src.bot; "exit=$LASTEXITCODE"
if (Test-Path .env.bak) { Rename-Item .env.bak .env }
```
Expected: four `Config error:` log lines naming the missing variables, then `exit=2`. (If any `DISCORD_*` variables are set in your shell environment, fewer lines appear. That is fine as long as the exit code is 2.)

Run: `.\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 8: Manual smoke test against a real test server (needs the user's Discord setup)**

This step needs a bot token and IDs. Follow `docs/deploy_portainer.md` §1–2 (written in Task 8), or ask the user to provide a test server. Put the values in local `.env`, then:

Run: `.\.venv\Scripts\python.exe -m src.bot`
Expected in logs: `Synced 6 slash commands to guild=…`, `Logged in as …`, `Scheduler started (every 10 min, paused=False)`, and about 30 s later a cycle running the sources.
In Discord, check `/profile`, `/status`, `/listings`, `/pause` (then `/status` shows Paused), `/resume`, `/scrape sources:vbent`, `/scrape sources:nope` (unknown source message), and a control command from an account without permission (denied). Stop with Ctrl+C.

If no Discord credentials are available, skip this step and report it as not done. Do not mark it complete.

- [ ] **Step 9: Commit**

```bash
git add src/bot/client.py src/bot/commands.py src/bot/__main__.py tests/test_bot_client.py
git commit -m "Add Discord bot with scheduled scraping and slash commands

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Docker image and compose stack

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `.dockerignore`

**Interfaces:**
- Consumes: `python -m src.bot` (Task 6). `requirements.txt` (Task 1). Env vars from `Settings` (Task 1).
- Produces: image that runs the bot by default; compose service `huur-scraper` with volume `huur-data`.

- [ ] **Step 1: Write `.dockerignore`**

```
.git
.venv
venv
data
logs
.env
tests
docs
test.py
**/__pycache__
**/*.pyc
.pytest_cache
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/data \
    && chown app:app /app/data

COPY src ./src

USER app

# Stdout-only logging; the DB lives on the mounted volume.
ENV DATABASE_PATH=/app/data/huur_scraper.db \
    LOG_FILE_PATH= \
    LOG_TO_CONSOLE=true

VOLUME ["/app/data"]

CMD ["python", "-m", "src.bot"]
```

- [ ] **Step 3: Write `docker-compose.yml`**

```yaml
services:
  huur-scraper:
    build: .
    image: huur-scraper:latest
    container_name: huur-scraper
    restart: unless-stopped
    volumes:
      - huur-data:/app/data
    environment:
      DISCORD_BOT_TOKEN: ${DISCORD_BOT_TOKEN}
      DISCORD_GUILD_ID: ${DISCORD_GUILD_ID}
      DISCORD_ALERT_CHANNEL_ID: ${DISCORD_ALERT_CHANNEL_ID}
      DISCORD_OPS_CHANNEL_ID: ${DISCORD_OPS_CHANNEL_ID:-}
      DISCORD_MENTION_USER_ID: ${DISCORD_MENTION_USER_ID:-}
      DISCORD_CONTROL_USER_IDS: ${DISCORD_CONTROL_USER_IDS:-}
      DISCORD_CONTROL_ROLE_ID: ${DISCORD_CONTROL_ROLE_ID:-}
      SCRAPE_INTERVAL_MINUTES: ${SCRAPE_INTERVAL_MINUTES:-10}
      USER_AGENT: ${USER_AGENT:-huur-scraper/0.1}
      MAX_WORKERS: ${MAX_WORKERS:-2}
      REQUEST_TIMEOUT_SECONDS: ${REQUEST_TIMEOUT_SECONDS:-20}
      MAX_RENT_EUR: ${MAX_RENT_EUR:-1000}
      MIN_SIZE_M2: ${MIN_SIZE_M2:-40}
      PREFERRED_BEDROOMS: ${PREFERRED_BEDROOMS:-2}
      ALLOW_CLOSE_MATCH: ${ALLOW_CLOSE_MATCH:-true}
      STORE_ONLY_MATCHES: ${STORE_ONLY_MATCHES:-true}
      ALLOWED_CITIES: ${ALLOWED_CITIES:-Den Haag,Delft,Rijswijk,Voorburg,Leidschendam,Nootdorp,Ypenburg}
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  huur-data:
```

- [ ] **Step 4: Verify the build**

Docker is **not installed** on the Windows dev machine. Check first:
Run: `docker --version`

If Docker is available (here, or on the homelab after pushing the branch):
Run: `docker build -t huur-scraper:test .`
Expected: build succeeds.

Run: `docker run --rm huur-scraper:test python -m src.main --listings`
Expected: `No listings found in database yet.`

Run: `docker run --rm huur-scraper:test; echo "exit=$?"`
Expected: `Config error:` lines for the missing Discord settings and `exit=2`.

Run: `docker compose config`
Expected: the rendered config with no errors (warnings about unset `DISCORD_*` variables are fine).

If Docker is not available anywhere yet, mark this step **not verified** in the task report. The first Portainer deploy in Task 8 is then the build check.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore
git commit -m "Add Dockerfile and compose stack for the Discord bot

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Remove Pi deployment, update docs

**Files:**
- Delete: `deploy/systemd/huur-scraper.service`, `deploy/systemd/huur-scraper.timer`, `scripts/setup_pi.sh`, `docs/install_pi.md`
- Create: `docs/deploy_portainer.md`
- Modify: `README.md`, `CLAUDE.md`, `docs/first_run_windows.md`

**Interfaces:**
- Consumes: env var names (Task 1), commands (Task 6), compose file (Task 7).
- Produces: documentation only.

- [ ] **Step 1: Delete the Pi deployment**

```bash
git rm deploy/systemd/huur-scraper.service deploy/systemd/huur-scraper.timer scripts/setup_pi.sh docs/install_pi.md
```

- [ ] **Step 2: Write `docs/deploy_portainer.md`**

````markdown
# Deploy on the homelab with Portainer

The scraper runs as one long-lived container: a Discord bot that scrapes every
`SCRAPE_INTERVAL_MINUTES` and answers slash commands. Portainer builds the image
straight from this GitHub repo.

## 1) Create the Discord bot

1. Go to https://discord.com/developers/applications → **New Application**.
2. **Bot** tab → **Reset Token** → copy it. This is `DISCORD_BOT_TOKEN`.
   No privileged intents are needed.
3. **OAuth2** tab → copy the **Client ID**, then open (replace `CLIENT_ID`):
   `https://discord.com/oauth2/authorize?client_id=CLIENT_ID&scope=bot+applications.commands&permissions=19456`
   (19456 = View Channels + Send Messages + Embed Links) and add the bot to your server.

## 2) Collect the IDs

In Discord: **User Settings → Advanced → Developer Mode** on. Then right-click → **Copy ID**:

| Variable | Right-click on |
|---|---|
| `DISCORD_GUILD_ID` | the server icon |
| `DISCORD_ALERT_CHANNEL_ID` | the channel for listing alerts |
| `DISCORD_OPS_CHANNEL_ID` (optional) | a channel for source errors/blocks |
| `DISCORD_MENTION_USER_ID` (optional) | your own name, so hard matches ping you |
| `DISCORD_CONTROL_USER_IDS` | users allowed to run `/scrape`, `/pause`, `/resume` (comma-separated) |
| `DISCORD_CONTROL_ROLE_ID` (alternative) | a role allowed to run them |

Make sure the bot's role can see and post in the alert and ops channels.

## 3) GitHub access token (private repo)

GitHub → Settings → Developer settings → **Fine-grained personal access tokens** →
Generate. Repository access: **only this repo**. Permissions: **Contents: Read-only**.
Copy the token.

## 4) Create the Portainer stack

1. Portainer → your environment → **Stacks → Add stack**.
2. Name: `huur-scraper`. Build method: **Repository**.
3. Repository URL: `https://github.com/<you>/huur-scraper`, reference `refs/heads/main`,
   compose path `docker-compose.yml`.
4. **Authentication** on: username = your GitHub username, password = the token from step 3.
5. **GitOps updates** on (polling, for example every 5 minutes) so pushes to `main` redeploy.
   Enable **Re-pull image and redeploy** / force rebuild if your Portainer version offers it,
   so code changes are rebuilt.
6. **Environment variables**: add at least `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`,
   `DISCORD_ALERT_CHANNEL_ID`, and `DISCORD_CONTROL_USER_IDS` (or `DISCORD_CONTROL_ROLE_ID`).
   Optional: everything else in `.env.example` (`MAX_RENT_EUR`, `MIN_SIZE_M2`, `ALLOWED_CITIES`, …).
   Unset values use the defaults in `docker-compose.yml`.
7. **Deploy the stack**.

## 5) Check it works

- Container logs (Portainer → Containers → huur-scraper → Logs) should show
  `Synced 6 slash commands`, `Logged in as …`, `Scheduler started`, then a cycle about 30 s later.
- In Discord: `/status`, `/profile`, `/listings`.
- If the container keeps restarting, the logs start with `Config error: …` naming the
  variable to fix, or `Alert channel … not found`, meaning a wrong ID or the bot can't see the channel.

## Commands

| Command | Who | What |
|---|---|---|
| `/listings [limit]` | everyone | recent stored matches |
| `/status` | everyone | paused/running, next run, last result per source |
| `/profile` | everyone | current filters |
| `/scrape [sources]` | control | run a cycle now (also while paused) |
| `/pause`, `/resume` | control | stop/start scheduled scraping (survives redeploys) |

## CLI inside the container

Portainer → Containers → huur-scraper → **Console** (`/bin/sh`), or on the host:

```sh
docker exec huur-scraper python -m src.main --listings --limit 50
docker exec huur-scraper python -m src.main --prune-non-matches
```

`python -m src.main --once` also works there. It logs alerts instead of posting them to Discord.

## Data

The SQLite DB lives in the named volume `huur-data` (`/app/data/huur_scraper.db`).
It survives redeploys. Removing the stack **with** volumes deletes it.
````

- [ ] **Step 3: Update `README.md`**

- In **MVP features**, replace `- Telegram alerts` with `- Discord bot: alerts plus /listings, /status, /profile, /scrape, /pause, /resume`, and replace `- Raspberry Pi systemd service/timer` with `- Docker image, deployed via a Portainer Git stack`.
- In **Quick start** step 2, replace `and fill Telegram values.` with `and set your matching profile (Discord values are only needed to run the bot).`
- In **Local-first setup**, replace `- Telegram is optional for the first run` with `- Discord is optional for local CLI runs; alerts are written to the log`.
- Replace the whole `## Raspberry Pi setup` section (from that heading up to, but not including, `## Add more sources`) with:
  ```markdown
  ## Homelab deployment (Docker + Portainer)
  The bot runs as a single container that scrapes every `SCRAPE_INTERVAL_MINUTES`
  and posts matches to Discord. See [docs/deploy_portainer.md](docs/deploy_portainer.md).

  Run the bot locally (needs the Discord values in `.env`):
  - `.\.venv\Scripts\python.exe -m src.bot`
  ```
- In **View current listings** and **Clean old non-matches**, replace `./.venv/bin/python` with `.\.venv\Scripts\python.exe` and add one line under each: `In the container: docker exec huur-scraper python -m src.main ...`.

Verify: `git grep -n -i "telegram\|systemd\|raspberry\|setup_pi\|install_pi\|\.venv/bin" README.md` → no output.

- [ ] **Step 4: Update `docs/first_run_windows.md`**

- Line 3: `You can run this without Telegram first.` → `You can run this without Discord first; CLI runs write alerts to the log.`
- Section 1: `Install Python 3.11+` → `Install Python 3.12+`.
- Section 2 bullet: `You can leave TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID empty for first run` → ``You can leave the `DISCORD_*` values empty for CLI runs``.
- Replace section `## 5) Optional Telegram setup (later)` and its list with:
  ```markdown
  ## 5) Run the Discord bot locally (optional)
  1. Create the bot and collect the IDs as in [deploy_portainer.md](deploy_portainer.md) steps 1–2.
  2. Put the `DISCORD_*` values in `.env`.
  3. Run `.\.venv\Scripts\python.exe -m src.bot` and try `/status` in your server.
  ```

- [ ] **Step 5: Update `CLAUDE.md`**

- **What this is**, last two sentences: replace "sends Telegram alerts … There is no long-running process." with:
  "Posts Discord alerts for new or changed matches. In production it runs as a long-lived Discord bot (`python -m src.bot`) in Docker on a homelab, deployed through a Portainer Git stack (`docker-compose.yml`, `docs/deploy_portainer.md`). The bot's `tasks.loop` runs a scrape cycle every `SCRAPE_INTERVAL_MINUTES` in a worker thread."
- **Commands**: replace `On the Pi, use ./.venv/bin/python -m src.main ... instead.` with:
  ```
  .\.venv\Scripts\python.exe -m src.bot                      # run the Discord bot locally (needs DISCORD_* in .env)
  docker exec huur-scraper python -m src.main --listings     # CLI inside the deployed container
  ```
  and mention that `./scripts/setup_local.ps1` installs `requirements-dev.txt` (runtime deps are in `requirements.txt`, which the Docker image uses).
- **Architecture** pipeline line → `src/bot/client.py::HuurBot.run_cycle` (or `main.py --once`) → `scrapers/runner.py::run_all_sources` → per source: `scraper.search()` → `filtering/rules.py::evaluate_listing` → `storage/sqlite_store.py::upsert_listing` → `Notifier.notify_listing` (`notify/discord_notifier.py` in the bot, `notify/base.py::LogNotifier` in the CLI).
- **Source registration** bullet: replace "The `source_factories` dict in `src/main.py`" with "`SOURCE_FACTORIES` in `src/scrapers/factories.py`".
- **Scrapers** bullet: replace `plus a [SOURCE_BLOCKED] Telegram message` with `plus a [SOURCE_BLOCKED] ops message`.
- Add a bullet after **Dedupe/alerting**:
  "- **Bot:** `src/bot/client.py` (`HuurBot`: scheduler loop, `run_cycle` guarded by an `asyncio.Lock`, so a second request raises `CycleBusyError`), `commands.py` (thin slash handlers), `embeds.py`/`checks.py` (pure, unit-tested). `DiscordNotifier` is called from the worker thread and posts via `run_coroutine_threadsafe`. Never call it from the event loop thread. Ops messages (`[SOURCE_BLOCKED]`/`[SOURCE_ERROR]`/`[SOURCE_RECOVERED]`) fire only when a source's status changes, based on its previous `source_runs` row. The paused flag is in the `bot_state` table."
- **Storage** bullet: append "Key/value bot state lives in `bot_state` (`get_state`/`set_state`)."
- **Adding a source** step 2: `Register the class in source_factories in src/main.py.` → `Register the class in SOURCE_FACTORIES in src/scrapers/factories.py.`
- **Adding a source** step 4: add `or /scrape sources:<name> in Discord`.

Verify: `git grep -n -i "telegram\|systemd\|raspberry\|setup_pi\|install_pi" -- . ":!docs/superpowers"` → no output.

- [ ] **Step 6: Full test run**

Run: `.\.venv\Scripts\python.exe -m pytest tests -q`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add -A docs/deploy_portainer.md README.md CLAUDE.md docs/first_run_windows.md
git commit -m "Remove Pi deployment and document Discord bot + Portainer setup

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 8: First real deploy (with the user)**

Push the branch, merge it to `main` (or point the stack's reference at the branch), and follow `docs/deploy_portainer.md` §3–5. The first Portainer deploy is also the image build check if Task 7 Step 4 could not run. Confirm in the container logs and in Discord that `/status` shows sources running.
