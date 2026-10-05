# Source onboarding guide (Pi-first)

This guide is the fastest path to make additional sources work safely.

## 1) Choose mode per source
Use one of these modes in `src/config/sources.yaml`:
- `SCRAPE`: public listing pages are readable and stable.
- `ALERT_INGEST`: source is blocked/gated/restricted; consume native alerts instead.
- `DISABLED`: temporarily off.

## 2) Onboard order (recommended)
1. `interhouse`
2. `rotsvast`
3. `verra`
4. `123wonen`
5. `expat-realestate`
6. `funda`
7. `woonnet-haaglanden` (metadata/alerts first)
8. `pararius`, `huurwoningen`, `vesteda` (alerts-first)

## 3) Per-source implementation checklist
For each source:
1. Find one stable listing overview URL for your area and budget.
2. Add a new scraper file in `src/scrapers/sites/<source>.py`.
3. Parse listing cards from overview page only first (URL + title + price + city).
4. Add scraper class to `SOURCE_FACTORIES` in `src/scrapers/factories.py`.
5. Add policy entry in `src/config/sources.yaml` with conservative interval.
6. Run single-source test:
   - `./scripts/run_once.ps1 --sources <source>` (Pi equivalent: `.venv/bin/python -m src.main --once --sources <source>`)
7. Confirm logs show:
   - `returned N listings`
   - `changed X alerted Y`
8. If blocked (403/429) or unstable selectors:
   - switch source to `ALERT_INGEST`
   - continue with next source.

## 4) What URL to find first?
Always start with a *filtered overview/search URL*, not homepages.
Good URL properties:
- includes `rent` / `huur`
- includes max price filter (~1000)
- is directly accessible without login
- paginates predictably

## 5) Alert setup strategy (important)
For risky/gated sources, set native alerts now and ingest later:
- saved-search email alerts from source
- optional RSS/webhook if offered
- parse those notifications into your local DB (future `ALERT_INGEST` pipeline)

## 6) Definition of done for a source
A source is "working" when:
- one run completes without exception
- at least one listing is parsed when listings exist
- no duplicate spam on second run
- logs are readable in both:
  - `journalctl -u huur-scraper.service -f`
  - `tail -f logs/huur_scraper.log`

## 7) Weekly maintenance
- Re-run each source individually once.
- Check for selector drift if counts suddenly drop to zero.
- Keep intervals conservative; avoid increasing request rates.
