# Multiple search profiles: design

Date: 2026-10-06
Status: approved in brainstorming, pending spec review

## Goal

Let several people on the private Discord server each keep their own search profile, edit it in Discord, and get alerts in their own channel. Scraping stays shared: every source is scraped once per cycle no matter how many profiles exist.

## Decisions (from brainstorming)

| Topic | Decision |
|---|---|
| Users | Trusted friends on the existing guild. Anyone in the guild can create **one** profile for themselves. |
| Profiles per person | Exactly one, keyed by Discord user ID |
| Alert channel | Created by the bot: a private `#huur-<username>` in a "Huur" category |
| Editable fields | The current ones: max rent, min size, preferred bedrooms (score only), close match on/off, cities |
| Cities | Pick from all Zuid-Holland municipalities (about 50) |
| Edit UX | `/profile` opens an ephemeral panel with buttons, a modal and paged selects |
| Backfill | Listings already online that newly match are posted once, after creation or after any edit |
| Delete | Profile removed; channel renamed to `archived-…` and made read-only; control users pinged in ops to delete it by hand |
| Vb&t area | Center and radius worked out from the municipalities the active profiles selected |
| Vesteda area | Drop the price bounds; the API ignores location and already returns national results |
| Matching architecture | Scrape stores everything, then a separate **dispatch** step matches per profile (approach A) |

## Architecture

```
scrape_loop tick ─┐
/scrape ──────────┼─► run_cycle (asyncio.Lock)
                  │     ├─ scope = SearchScope.from_profiles(active profiles)
                  │     ├─ run_all_sources(settings, store, factories, scope, ops_notifier)
                  │     │     scrape → upsert_listing (every listing, no matching here)
                  │     └─ dispatch(store, profiles, notifier, now)
profile save ─────┴─► run_dispatch([profile])   (same lock: waits for a running cycle)
```

The runner no longer evaluates or alerts. It scrapes, stores and tracks source health. Ops messages (`[SOURCE_*]`) stay global and go to the ops channel as they do now.

## Data model

### `profiles` table (new, created in `SQLiteStore._init_db`)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK AUTOINCREMENT | |
| `owner_user_id` | INTEGER NOT NULL UNIQUE | Discord user ID |
| `channel_id` | INTEGER NOT NULL | alert channel |
| `max_rent_eur` | INTEGER NOT NULL | |
| `min_size_m2` | INTEGER NOT NULL | |
| `preferred_bedrooms` | INTEGER NOT NULL | score only, as now |
| `allow_close_match` | INTEGER NOT NULL | 0/1 |
| `municipalities` | TEXT NOT NULL | JSON list of municipality keys |
| `paused` | INTEGER NOT NULL DEFAULT 0 | |
| `created_at` / `updated_at` | TEXT NOT NULL | ISO UTC |

In Python this is a frozen `Profile` dataclass (`src/models/profile.py`). The store gets CRUD methods: `get_profile(owner_user_id)`, `list_profiles(active_only=False)`, `create_profile`, `update_profile`, `delete_profile`.

### `profile_alerts` table (new)

`(profile_id INTEGER, dedupe_key TEXT, sent_at TEXT, PRIMARY KEY (profile_id, dedupe_key))`. Rows are deleted when their profile is deleted.

`dedupe_key` contains price and area, so a price or area change still produces a new alert per profile, as it does today. Title, status and availability changes no longer trigger a new alert: a listing that drops out and comes back as available with the same key is not posted again. That's acceptable for this use.

### Removed: `STORE_ONLY_MATCHES`

Every scraped listing is stored, because dispatch must be able to backfill a profile that gets wider later. That's a few hundred rows per cycle. `STORE_ONLY_MATCHES` and `--prune-non-matches` are removed, along with their `.env.example` and docs entries.

### Municipality data: `src/config/municipalities.yaml`

```yaml
municipalities:
  - key: den-haag
    name: Den Haag
    lat: 52.0705
    lon: 4.3007
    places: ["Den Haag", "'s-Gravenhage", "The Hague", "Ypenburg", "Scheveningen", "Loosduinen"]
  - key: leidschendam-voorburg
    name: Leidschendam-Voorburg
    lat: 52.0833
    lon: 4.3950
    places: ["Leidschendam", "Voorburg", "Stompwijk"]
  # … every Zuid-Holland municipality as of 2026, checked against the CBS list
```

- `src/filtering/municipalities.py` loads the file once and exposes `municipality_for_place(city: str | None) -> str | None`, `all_municipalities() -> list[Municipality]` and `get(key)`.
- Place names are compared using today's `_normalize_city_token` (lowercase, alphanumeric only), so `'s-Gravenhage`, `s-gravenhage` and `Den Haag` all resolve to `den-haag`. The municipality's own name counts as one of its places.
- The hardcoded alias dict in `normalize_city_name` is replaced by this lookup. Ypenburg is a district of Den Haag, so it moves there.
- A listing outside Zuid-Holland resolves to `None` and matches no profile.

## Matching

`evaluate_listing(listing, profile) -> MatchResult` keeps today's scoring and its hard/close rules, but reads the thresholds from the `Profile` instead of `Settings`. The city check becomes `municipality_for_place(listing.city) in profile.municipalities`.

## Dispatch

`src/notify/dispatch.py`:

```python
def dispatch(store, profiles, notifier, now, stale_after) -> DispatchSummary
```

1. Candidates are `store.get_dispatch_candidates(seen_since=now - stale_after)`: listings with `is_available = 1` and `last_seen_at >= seen_since`. `stale_after = 3 × SCRAPE_INTERVAL_MINUTES`, so listings that disappeared, or old keys left behind by a price change, are never posted, while a source that failed one cycle doesn't drop its listings straight away.
2. For each profile that isn't paused: evaluate every candidate, keep the hard and close matches, and skip keys already in `profile_alerts` for that profile.
3. Send through `notifier.notify_listing(profile, listing, match)`. **Only after the send succeeds**, insert into `profile_alerts`. A failed send raises and stays unrecorded, so the next dispatch retries it.
4. Sort matches oldest `first_seen_at` first, so a backfill reads in chronological order.

A first backfill can mean a few dozen messages. discord.py handles the rate limits, and sends are sequential from the worker thread, so no extra throttling is needed.

### Triggers

- **Scheduled or `/scrape` cycle:** scrape, then dispatch to all active profiles.
- **Profile create or save:** `HuurBot.run_dispatch([profile])` takes the cycle lock. Unlike `run_cycle`, it **waits** for the lock rather than raising `CycleBusyError`, because the user is waiting on their own edit. It runs in `asyncio.to_thread`. The panel replies straight away ("Saved, matching listings will appear in #huur-x").
- **Paused bot (`/pause`):** stops scheduled scraping only. Dispatch after a profile edit still runs from the stored listings.

### Notifier

`DiscordNotifier` no longer holds a single alert channel. `notify_listing(profile, listing, match)` resolves `profile.channel_id` through a channel lookup provided by the bot (`bot.get_channel` with a `fetch_channel` fallback, run on the event loop through `run_coroutine_threadsafe` as now). Hard matches mention `profile.owner_user_id`. `DISCORD_MENTION_USER_ID` is used only for the one-time seeding.

The `notify_listing` signature changes in the `Notifier` protocol. `LogNotifier` logs the profile's owner ID alongside the listing.

### Errors

- **Profile channel missing or inaccessible** (`NotFound`/`Forbidden`): the profile is set to `paused = 1`, `[PROFILE_ERROR] <@owner>'s channel is unavailable; profile paused` goes to ops, and dispatch continues with the other profiles. When the owner un-pauses from their panel, the bot checks the channel again.
- **Other send failures:** logged, not recorded, retried at the next dispatch.

## Search scope (scrapers)

`src/scrapers/scope.py`:

```python
@dataclass(frozen=True)
class SearchScope:
    municipalities: frozenset[str]   # union over active profiles
```

- `BaseScraper.__init__(self, settings, scope: SearchScope | None = None)`. `run_all_sources` takes a `scope` and passes it to every factory. Every scraper except Vb&t ignores it.
- `run_cycle` builds the scope from the profiles that aren't paused.

### Vb&t

`vbent_area(scope) -> tuple[str, int]` is a pure function next to `VBentScraper`:

- If `scope` is empty or `None`, return `("Delft", 15)`, today's behaviour.
- Otherwise try each municipality in the YAML as the center. Its required radius is the largest haversine distance from that center to any selected municipality's coordinates. Pick the center with the smallest required radius (ties go to the alphabetically first key), then add 5 km to cover the municipality's own extent, round up to a whole kilometre, and cap at 50 km.
- The center is passed as the municipality's `name` in the `filter_properties` cookie (`city`, `radius`). The rest of `filter_template` stays as it is.
- **To verify during implementation:** does the Vb&t API take any integer radius, or does it snap to fixed steps? Probes on 2026-10-06 accepted 15, 30 and 40 km (8, 10 and 20 pages). If it snaps, round up to the next accepted step.
- Expected cost: selecting Den Haag, Delft and Rijswijk gives roughly today's 8 pages. A profile spanning the whole province gives up to about 25 pages. `max_pages` (100) still applies as a safety limit.

### Vesteda

Remove `priceFrom`/`priceTo` from `payload_template`. A probe on 2026-10-06 found that the location fields are ignored (the same 96 units came back from all over the country with any location), while the price bounds are respected. Without them, one request returns every available unit (the most expensive was €3150). The unused `latitude`/`longitude`/`place`/`radius` fields stay, so the request looks like the website's own.

### Known limits (out of scope)

- Wobeco has `surface >= 40`, `price <= 1150`, apartment only and `bedrooms >= 1` built into its query string. A profile with a max rent above €1150 or a min size below 40 m² won't see every Wobeco listing.
- Ikwilhuren reads only the newest 3 pages of the national list.
- Several sources are local by nature (The Hague Real Estate, NRW Wonen, Verra), so picking a municipality doesn't mean every source covers it.

## Discord

### `/profile` (anyone in the guild)

Ephemeral. With no profile it shows an explanation and a **Create profile** button. With a profile it shows the panel:

```
Your search profile
Max rent      €1100
Min size      40 m²
Bedrooms      2 (preferred)
Close match   on
Cities        Delft, Den Haag, Rijswijk
Alerts →      #huur-alice  (active)
[Edit numbers] [Cities] [Close match: on] [Pause] [Delete]
```

The views are `discord.ui.View` subclasses in `src/bot/profile_views.py`. The panel embed and the validation are pure functions in `src/bot/embeds.py` / `src/bot/checks.py`, so they can be unit-tested. Views time out after 10 minutes; running `/profile` again opens a fresh panel. Every component checks that `interaction.user.id == profile.owner_user_id`.

- **Create:** reuses or creates the category named `HUUR_CATEGORY_NAME` (default `Huur`) and creates a text channel `huur-<sanitized username>` (with `-2` and so on if the name is taken) with these overwrites: `@everyone` can't view; the owner can view and read history but can't send; the bot can view, send, embed and manage messages; the control role (if set) can view. The profile starts with the defaults from `.env` (`MAX_RENT_EUR`, `MIN_SIZE_M2`, `PREFERRED_BEDROOMS`, `ALLOW_CLOSE_MATCH`) and **no municipalities**, and the reply asks the user to pick cities. Dispatch first runs after cities are saved, since an empty list matches nothing.
- **Edit numbers:** a modal with three text inputs. Validation: max rent is an integer from 100 to 10000, min size from 0 to 500, preferred bedrooms from 0 to 10. If anything is invalid, an ephemeral error lists each problem and nothing is saved.
- **Cities:** an ephemeral view with two `Select` menus, municipalities A–L and M–Z (alphabetical, split so each holds ≤25), both `min_values=0` and pre-selected from the profile, plus a **Save** button. Save stores the union of both selects.
- **Close match:** toggles and saves.
- **Pause/Resume:** sets `paused`. Resume checks the channel again (see Errors) and runs a dispatch.
- **Delete:** a confirm view ("Delete your profile? Your channel will be archived."). On confirm: delete the profile and its `profile_alerts` rows, rename the channel to `archived-<name>`, set the owner's overwrite to view-only (they already can't send), post in the channel "Profile deleted; this channel is archived", and send to ops: `[PROFILE_DELETED] <@owner> deleted their profile; <#channel> can be removed.` with mentions of `DISCORD_CONTROL_USER_IDS`.

Every save writes `updated_at` and runs `run_dispatch([profile])` (not for Delete, and not for Pause).

### Other commands

| Command | Who | Change |
|---|---|---|
| `/profiles` (new) | control users | Lists every profile: owner, channel, cities, numbers, paused. Ephemeral. |
| `/listings` | anyone | Shows recent stored listings that match **your** profile (evaluated live), or a hint to create one |
| `/status` | anyone | Adds a "Profiles: N active / M total" line |
| `/scrape`, `/pause`, `/resume` | control users | No change |

### Weekly cleanup

`clear_loop` purges every active profile channel (bot messages that aren't pinned) instead of the single alert channel, under the same `listings_cleared_at` schedule. Archived channels aren't touched. If one channel fails, that's logged and the rest continue.

## Configuration

| Variable | Change |
|---|---|
| `DISCORD_ALERT_CHANNEL_ID` | Used only to seed the first profile. No longer required at startup. |
| `DISCORD_MENTION_USER_ID` | Used only to choose the owner of the seeded profile (first ID) |
| `MAX_RENT_EUR`, `MIN_SIZE_M2`, `PREFERRED_BEDROOMS`, `ALLOW_CLOSE_MATCH` | Defaults for new profiles and the seeded profile |
| `ALLOWED_CITIES` | Used only to seed the first profile's municipalities (each mapped through `municipality_for_place`; unknown names are logged and skipped) |
| `STORE_ONLY_MATCHES` | Removed |
| `HUUR_CATEGORY_NAME` | New, default `Huur` |

`/profile` (today's read-only `.env` view) is replaced by the panel.

## Migration and seeding

In `HuurBot.on_ready`, if the `profiles` table is empty **and** both `DISCORD_ALERT_CHANNEL_ID` and `DISCORD_MENTION_USER_ID` are set:

1. Create a profile for the first mention user ID, using the existing alert channel and the `.env` thresholds and cities.
2. Insert every existing `listings.dedupe_key` into `profile_alerts` for that profile, so the switch doesn't post everything again.
3. Log `Seeded profile for <id> from .env`.

This runs once. Afterwards the profile is edited only from Discord.

## CLI

`python -m src.main --once` scrapes and stores, then dispatches to every profile with a `LogNotifier` in **dry-run** mode: matches are logged and nothing is written to `profile_alerts`, so a CLI run never takes alerts away from the bot. `--listings` keeps showing recent stored listings, without profile filtering. `--prune-non-matches` is removed.

## Testing

All `unittest`-style tests run against a temporary SQLite file, like the existing store tests:

- `test_municipalities.py`: place resolution (aliases, `'s-Gravenhage`, districts, unknown → `None`), YAML loads with no duplicate place names across municipalities, and every municipality has coordinates.
- `test_rules.py` (update): `evaluate_listing` with a `Profile`, covering hard and close matches and municipality matching.
- `test_vbent_area.py`: empty scope → Delft 15; a single municipality → itself plus 5 km; a spread-out set → the best center, the radius and the 50 km cap.
- `test_dispatch.py`: backfill sends every current match once; a second dispatch sends nothing; a failed send isn't recorded and is retried; a paused profile is skipped; stale listings are excluded; a price change (new key) alerts again; two profiles each get their own matches.
- `test_sqlite_store.py` (update): profile CRUD, `profile_alerts` cascade on delete, seeding marks existing keys as sent, and the migration on an existing DB.
- `test_bot_*` (update): panel embed rendering, modal validation, splitting the city select into ≤25 per page, and channel name sanitizing.
- Runner tests: drop the matching and alert assertions; check that the scope reaches the factories.

Discord UI flows (create, edit, delete, archive) are tested by hand on the guild before release.

## Out of scope

- Several profiles per person, or profiles shared between users
- Per-profile source selection, min rent, hard bedroom minimum
- Widening the Wobeco/Ikwilhuren query limits
- Municipalities outside Zuid-Holland
- DM alerts
