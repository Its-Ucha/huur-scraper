# huur_scraper

Compliance-first rental listing aggregator for Delft/The Hague area.

## MVP features
- Multi-source scraping with conservative rate limits
- Source policy modes: `SCRAPE`, `ALERT_INGEST`, `DISABLED`
- Hard and close-match filtering
- SQLite persistence + dedupe
- Telegram alerts
- Raspberry Pi systemd service/timer

## Quick start
1. Run `./scripts/setup_local.ps1`.
2. Copy `.env.example` to `.env` and fill Telegram values.
3. Run once:
   - `./scripts/run_once.ps1`

## Local-first setup (Windows)
- Follow [docs/first_run_windows.md](docs/first_run_windows.md)
- Telegram is optional for the first run
- Database is created automatically at `data/huur_scraper.db`

## Raspberry Pi setup
Requires Linux with systemd, Python 3 with venv/pip support, and sudo access.

1. Copy or clone the repository to your Pi and open a terminal in its root directory.
   The checkout can live anywhere accessible to the user that will run the scraper.
2. Run `bash scripts/setup_pi.sh` **as your normal user, not with sudo**.
   The [setup script](scripts/setup_pi.sh) creates `.venv`, installs dependencies,
   and requests sudo access to install both systemd units into `/etc/systemd/system/`.
   It fills in the service's absolute checkout path and your user/group automatically,
   then reloads systemd. No hardcoded home directory or username needs editing.
3. Run `cp .env.example .env` and configure your matching profile and optional
   Telegram settings in the copied environment file.
4. Test from the repository root with `./.venv/bin/python -m src.main --once`.
5. Enable scheduled runs with `sudo systemctl enable --now huur-scraper.timer`.
   Setup does not enable the timer automatically, allowing configuration and testing
   before scheduled runs begin. The timer runs about two minutes after boot and
   every ten minutes thereafter.
6. Check scheduling with `systemctl status huur-scraper.timer` and inspect logs
   with `journalctl -u huur-scraper.service -f`.

The [service file](deploy/systemd/huur-scraper.service) is a template; do not copy
it directly into systemd's unit directory. Re-run setup after moving the checkout
or changing the unit templates. An already-enabled timer remains enabled.

See [docs/install_pi.md](docs/install_pi.md) for the detailed walkthrough.

## Add more sources
- Follow [docs/source_onboarding.md](docs/source_onboarding.md)

## View current listings
- Show recent listings from SQLite:
   - `./.venv/bin/python -m src.main --listings`
- Show more rows:
   - `./.venv/bin/python -m src.main --listings --limit 100`

## Clean old non-matches
- Remove previously stored rows that no longer match your current profile:
   - `./.venv/bin/python -m src.main --prune-non-matches`

## Current MVP sources
- `thehaguerealestate`
- `wobeco`
- `nrw_wonen`

Run only selected sources:
- `./scripts/run_once.ps1 --sources thehaguerealestate,wobeco`

## Safety defaults
- Low concurrency
- Retry with backoff
- Auto-block handling for 403/429
- Policy-driven source enablement

## Logging
- Console logs are enabled by default
- File logs are written to `logs/huur_scraper.log`
- Configure via `.env`: `LOG_LEVEL`, `LOG_FILE_PATH`, `LOG_TO_CONSOLE`

Raspberry Pi monitoring commands:
- `tail -f logs/huur_scraper.log`
- `journalctl -u huur-scraper.service -f`
