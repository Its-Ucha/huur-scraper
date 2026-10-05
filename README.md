# huur_scraper

Compliance-first rental listing aggregator for Delft/The Hague area.

## MVP features
- Multi-source scraping with conservative rate limits
- Source policy modes: `SCRAPE`, `ALERT_INGEST`, `DISABLED`
- Hard and close-match filtering
- SQLite persistence + dedupe
- Discord bot: alerts plus /listings, /status, /profile, /scrape, /pause, /resume
- Docker image, deployed via a Portainer Git stack

## Quick start
1. Run `./scripts/setup_local.ps1`.
2. Copy `.env.example` to `.env` and set your matching profile (Discord values are only needed to run the bot).
3. Run once:
   - `./scripts/run_once.ps1`

## Local-first setup (Windows)
- Follow [docs/first_run_windows.md](docs/first_run_windows.md)
- Discord is optional for local CLI runs; alerts are written to the log
- Database is created automatically at `data/huur_scraper.db`

## Homelab deployment (Docker + Portainer)
The bot runs as a single container that scrapes every `SCRAPE_INTERVAL_MINUTES`
and posts matches to Discord. See [docs/deploy_portainer.md](docs/deploy_portainer.md).

Run the bot locally (needs the Discord values in `.env`):
- `.\.venv\Scripts\python.exe -m src.bot`

## Add more sources
- Follow [docs/source_onboarding.md](docs/source_onboarding.md)

## View current listings
- Show recent listings from SQLite:
   - `.\.venv\Scripts\python.exe -m src.main --listings`
- Show more rows:
   - `.\.venv\Scripts\python.exe -m src.main --listings --limit 100`
- In the container: `docker exec huur-scraper python -m src.main --listings`

## Clean old non-matches
- Remove previously stored rows that no longer match your current profile:
   - `.\.venv\Scripts\python.exe -m src.main --prune-non-matches`
- In the container: `docker exec huur-scraper python -m src.main --prune-non-matches`

## Current MVP sources
- `thehaguerealestate`
- `wobeco`
- `nrw_wonen`
- `vbent` (Vb&t: Delft and a 15 km radius, cookie-based API filters)

Vb&t uses the `filter_properties` cookie on each paginated API request. Its
search area is configured in `VBentScraper.filter_template` in
[src/scrapers/sites/vbent.py](src/scrapers/sites/vbent.py). Rent and size limits
remain unrestricted at the API level; your local profile filters still apply.
Rent is the base monthly rent; service and parking charges are stored separately
as metadata. Total rooms are not inferred to be bedrooms.

Run only selected sources:
- `./scripts/run_once.ps1 --sources thehaguerealestate,wobeco`

## Safety defaults
- Low concurrency
- Retry with backoff
- Auto-block handling for 403/429
- Policy-driven source enablement

## Logging
- Console logs are enabled by default
- File logs are written to `logs/huur_scraper.log` (set `LOG_FILE_PATH` empty to disable)
- Configure via `.env`: `LOG_LEVEL`, `LOG_FILE_PATH`, `LOG_TO_CONSOLE`
- The Docker image logs to stdout only: `docker logs -f huur-scraper` or the Portainer log view
