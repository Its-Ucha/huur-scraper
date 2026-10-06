# Multiple Search Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let each person on the Discord server keep their own search profile, edit it from a `/profile` panel, and get alerts in a private channel the bot creates for them, while every source is still scraped only once per cycle.

**Architecture:** The scrape runner now only stores listings. A new dispatch step reads the stored, currently available listings, evaluates them against every active profile, and posts unsent matches to each profile's channel, recording what was sent in a `profile_alerts` table. Profiles live in SQLite; cities are Zuid-Holland municipalities from a YAML data file, which also gives Vb&t a search area derived from the selected municipalities.

**Tech Stack:** Python 3.12/3.13, discord.py 2.7 (`app_commands`, `discord.ui`), SQLite (`sqlite3`), PyYAML, httpx, `unittest` run through pytest.

**Spec:** `docs/superpowers/specs/2026-10-06-multi-profile-design.md`

## Global Constraints

- Run every command from the repo root. `src/config/*.yaml` paths are relative (see `CLAUDE.md`).
- Run tests with `.\.venv\Scripts\python.exe -m pytest tests` (PowerShell) or `./.venv/Scripts/python.exe -m pytest tests` (Git Bash). Tests use `unittest.TestCase` style.
- No new runtime dependencies. `requirements.txt` stays as it is.
- One profile per Discord user (`owner_user_id` UNIQUE).
- Each city select holds at most 25 options (a Discord limit). With 50 municipalities that means 2 pages.
- Vb&t radius: required distance + 5 km, rounded up, clamped to 10–50 km. With no profiles it falls back to `("Delft", 15)`.
- Dispatch window: `stale_after = 3 × SCRAPE_INTERVAL_MINUTES`.
- A row goes into `profile_alerts` only **after** the Discord send succeeds.
- Profile number limits: max rent 100–10000, min size 0–500, preferred bedrooms 0–10.
- The new channel is `huur-<sanitized display name>` in category `HUUR_CATEGORY_NAME` (default `Huur`). The owner can view and read but not send.
- Delete renames the channel to `archived-<name>`, keeps it read-only, and posts `[PROFILE_DELETED] …` to ops with mentions of `DISCORD_CONTROL_USER_IDS`.
- Never call `DiscordNotifier` methods from the event loop thread. Wrap them in `asyncio.to_thread`.
- `src/scrapers/sites/vbent.py` is indented with **tabs**. Keep tabs in that file. Every other file uses 4 spaces.
- Commit messages are short imperative sentences like the existing history ("Add …", "Post …") and end with the `Co-Authored-By` line from the session's attribution reminder.

## Review Focus

1. **A double click on "Create profile"** must end with one profile and one channel. Covered by the `IntegrityError` test in Task 2; the cleanup path in Task 9 is checked by hand.
2. **Listing city spellings from the sites** (`'s-Gravenhage`, `s-Gravenhage`, `RIJSWIJK (ZH)`, ` delft `, `The Hague`) must still resolve to the right municipality. Test in Task 1.
3. **A profile with no cities yet** must match nothing, must not crash dispatch, and must not change Vb&t's area. Tests in Task 3 and Task 6.
4. **A Discord send that fails partway through a backfill** (timeout or rate limit) must not stop the remaining sends, and the failed listing must be sent on the next dispatch. Test in Task 5.
5. **The first deploy on the existing production DB** must not post every stored listing again: seeding marks them as sent, and a second start doesn't seed again. Test in Task 7.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `src/config/municipalities.yaml` | new | Zuid-Holland municipalities: key, name, center lat/lon, place names (first one is the main place) |
| `src/filtering/municipalities.py` | new | Load the YAML and resolve a listing's place to a municipality key |
| `src/models/profile.py` | new | `Profile` frozen dataclass |
| `src/storage/sqlite_store.py` | modify | `profiles` and `profile_alerts` tables, profile CRUD, dispatch candidates; drop the prune helpers |
| `src/filtering/rules.py` | modify | `evaluate_listing(listing, profile)` |
| `src/scrapers/runner.py` | modify | Scrape and store only; pass `SearchScope` to factories |
| `src/scrapers/scope.py` | new | `SearchScope` |
| `src/scrapers/base.py` | modify | `BaseScraper(settings, scope=None)` |
| `src/scrapers/sites/vbent.py` | modify | `vbent_area()`; use it in `search()` |
| `src/scrapers/sites/vesteda.py` | modify | Drop the price bounds |
| `src/notify/base.py` | modify | `Notifier` protocol per profile, `ChannelUnavailableError`, `LogNotifier` |
| `src/notify/discord_notifier.py` | modify | Send to the profile's channel through a resolver; ops mentions |
| `src/notify/dispatch.py` | new | `dispatch()`, `recent_matches()`, `stale_window()` |
| `src/config.py` | modify | Drop `store_only_matches`; add `huur_category_name` |
| `src/main.py` | modify | Drop prune; `--once` runs a dry-run dispatch |
| `src/bot/seeding.py` | new | One-time seeding of a profile from `.env` |
| `src/bot/client.py` | modify | Cycle = scrape + dispatch; `run_dispatch`/`schedule_dispatch`; weekly clear per profile channel; ops channel fallback |
| `src/bot/checks.py` | modify | Number validation, channel names, city pages; alert channel no longer required |
| `src/bot/embeds.py` | modify | Profile panel, profiles list, status profile count, summary without per-source alerts |
| `src/bot/profile_channels.py` | new | Create and archive profile channels |
| `src/bot/profile_views.py` | new | `discord.ui` views: create, panel, numbers modal, cities, delete confirm |
| `src/bot/commands.py` | modify | `/profile`, `/profiles`, `/listings` per profile, `/status` counts |
| `tests/helpers.py` | modify | `make_profile`, new `RecordingNotifier` |
| docs (`.env.example`, `CLAUDE.md`, `README.md`, `docs/deploy_portainer.md`) | modify | Describe profiles, drop removed settings |

---

### Task 1: Municipality data and place lookup

**Files:**
- Create: `src/config/municipalities.yaml`
- Create: `src/filtering/municipalities.py`
- Test: `tests/test_municipalities.py`

**Interfaces:**
- Produces:
  - `Municipality(key: str, name: str, lat: float, lon: float, places: tuple[str, ...])` with property `main_place -> str` (first place, or `name` when there are no places)
  - `normalize_place_token(value: str) -> str`
  - `load_municipalities(path: Path = MUNICIPALITIES_FILE) -> list[Municipality]`
  - `build_place_index(municipalities: Iterable[Municipality]) -> dict[str, str]` (raises `ValueError` when one place belongs to two municipalities)
  - `all_municipalities() -> list[Municipality]` (sorted by name, case-insensitive)
  - `get_municipality(key: str) -> Municipality | None`
  - `municipality_for_place(city: str | None) -> str | None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_municipalities.py`:

```python
from __future__ import annotations

import unittest

from src.filtering.municipalities import (
    Municipality,
    all_municipalities,
    build_place_index,
    get_municipality,
    municipality_for_place,
)


class MunicipalityDataTests(unittest.TestCase):
    def test_all_zuid_holland_municipalities_loaded(self) -> None:
        municipalities = all_municipalities()
        self.assertEqual(len(municipalities), 50)
        keys = [m.key for m in municipalities]
        self.assertEqual(len(keys), len(set(keys)))
        names = [m.name for m in municipalities]
        self.assertEqual(names, sorted(names, key=str.lower))

    def test_every_municipality_has_coordinates_and_a_main_place(self) -> None:
        for municipality in all_municipalities():
            with self.subTest(municipality=municipality.key):
                self.assertTrue(51.6 <= municipality.lat <= 52.4)
                self.assertTrue(3.8 <= municipality.lon <= 5.2)
                self.assertTrue(municipality.places)
                self.assertTrue(municipality.main_place)

    def test_get_municipality(self) -> None:
        self.assertEqual(get_municipality("leidschendam-voorburg").name, "Leidschendam-Voorburg")
        self.assertEqual(get_municipality("leidschendam-voorburg").main_place, "Leidschendam")
        self.assertIsNone(get_municipality("amsterdam"))


class PlaceLookupTests(unittest.TestCase):
    def test_known_spellings(self) -> None:
        cases = {
            "Den Haag": "den-haag",
            "'s-Gravenhage": "den-haag",
            "s-Gravenhage": "den-haag",
            "'s-gravenhage": "den-haag",
            "The Hague": "den-haag",
            "Ypenburg": "den-haag",
            "Rijswijk": "rijswijk",
            "RIJSWIJK (ZH)": "rijswijk",
            " delft ": "delft",
            "Voorburg": "leidschendam-voorburg",
            "Leidschendam-Voorburg": "leidschendam-voorburg",
            "Nootdorp": "pijnacker-nootdorp",
            "Berkel en Rodenrijs": "lansingerland",
            "'s-Gravenzande": "westland",
            "Spijkenisse": "nissewaard",
        }
        for city, key in cases.items():
            with self.subTest(city=city):
                self.assertEqual(municipality_for_place(city), key)

    def test_unknown_and_empty(self) -> None:
        self.assertIsNone(municipality_for_place("Amsterdam"))
        self.assertIsNone(municipality_for_place(""))
        self.assertIsNone(municipality_for_place(None))

    def test_duplicate_place_is_rejected(self) -> None:
        one = Municipality("a", "A", 52.0, 4.0, ("Shared",))
        two = Municipality("b", "B", 52.0, 4.1, ("shared",))
        with self.assertRaisesRegex(ValueError, "Shared"):
            build_place_index([one, two])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_municipalities.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.filtering.municipalities'`

- [ ] **Step 3: Write the data file**

Create `src/config/municipalities.yaml`. The first entry of `places` is the main place that Vb&t geocodes; Task 6 probes it.

```yaml
# Zuid-Holland municipalities (2026). `places` are the place names listings use;
# the first entry is the main place, used as a search center for Vb&t.
# lat/lon is an approximate center of the municipality.
municipalities:
  - {key: alblasserdam, name: Alblasserdam, lat: 51.865, lon: 4.661, places: [Alblasserdam]}
  - {key: albrandswaard, name: Albrandswaard, lat: 51.858, lon: 4.405, places: [Rhoon, Poortugaal]}
  - {key: alphen-aan-den-rijn, name: Alphen aan den Rijn, lat: 52.129, lon: 4.656, places: [Alphen aan den Rijn, Boskoop, Hazerswoude-Dorp, Hazerswoude-Rijndijk, Koudekerk aan den Rijn, Aarlanderveen, Benthuizen, Zwammerdam]}
  - {key: barendrecht, name: Barendrecht, lat: 51.856, lon: 4.534, places: [Barendrecht]}
  - {key: bodegraven-reeuwijk, name: Bodegraven-Reeuwijk, lat: 52.075, lon: 4.749, places: [Bodegraven, Reeuwijk, Nieuwerbrug]}
  - {key: capelle-aan-den-ijssel, name: Capelle aan den IJssel, lat: 51.929, lon: 4.578, places: [Capelle aan den IJssel]}
  - {key: delft, name: Delft, lat: 52.012, lon: 4.357, places: [Delft]}
  - {key: den-haag, name: Den Haag, lat: 52.070, lon: 4.300, places: [Den Haag, "'s-Gravenhage", Gravenhage, The Hague, Scheveningen, Loosduinen, Ypenburg, Leidschenveen]}
  - {key: dordrecht, name: Dordrecht, lat: 51.813, lon: 4.690, places: [Dordrecht]}
  - {key: goeree-overflakkee, name: Goeree-Overflakkee, lat: 51.750, lon: 4.150, places: [Middelharnis, Sommelsdijk, Ouddorp, Dirksland, Oude-Tonge, Stellendam, Goedereede, Den Bommel]}
  - {key: gorinchem, name: Gorinchem, lat: 51.836, lon: 4.973, places: [Gorinchem]}
  - {key: gouda, name: Gouda, lat: 52.011, lon: 4.710, places: [Gouda]}
  - {key: hardinxveld-giessendam, name: Hardinxveld-Giessendam, lat: 51.828, lon: 4.840, places: [Hardinxveld-Giessendam]}
  - {key: hendrik-ido-ambacht, name: Hendrik-Ido-Ambacht, lat: 51.844, lon: 4.639, places: [Hendrik-Ido-Ambacht]}
  - {key: hillegom, name: Hillegom, lat: 52.291, lon: 4.583, places: [Hillegom]}
  - {key: hoeksche-waard, name: Hoeksche Waard, lat: 51.760, lon: 4.450, places: [Oud-Beijerland, "'s-Gravendeel", Strijen, Numansdorp, Puttershoek, Klaaswaal, Maasdam, Mijnsheerenland, Westmaas, Heinenoord]}
  - {key: kaag-en-braassem, name: Kaag en Braassem, lat: 52.200, lon: 4.650, places: [Roelofarendsveen, Leimuiden, Rijnsaterwoude, Hoogmade, Woubrugge, Oude Wetering, Nieuwe Wetering, Rijpwetering, Oud Ade, Buitenkaag]}
  - {key: katwijk, name: Katwijk, lat: 52.200, lon: 4.415, places: [Katwijk, Rijnsburg]}
  - {key: krimpen-aan-den-ijssel, name: Krimpen aan den IJssel, lat: 51.917, lon: 4.600, places: [Krimpen aan den IJssel]}
  - {key: krimpenerwaard, name: Krimpenerwaard, lat: 51.967, lon: 4.766, places: [Schoonhoven, Bergambacht, Ouderkerk aan den IJssel, Lekkerkerk, Stolwijk, Haastrecht, Ammerstol, Gouderak, Krimpen aan de Lek]}
  - {key: lansingerland, name: Lansingerland, lat: 52.004, lon: 4.494, places: [Berkel en Rodenrijs, Bergschenhoek, Bleiswijk]}
  - {key: leiden, name: Leiden, lat: 52.160, lon: 4.497, places: [Leiden]}
  - {key: leiderdorp, name: Leiderdorp, lat: 52.158, lon: 4.536, places: [Leiderdorp]}
  - {key: leidschendam-voorburg, name: Leidschendam-Voorburg, lat: 52.079, lon: 4.404, places: [Leidschendam, Voorburg, Stompwijk]}
  - {key: lisse, name: Lisse, lat: 52.258, lon: 4.557, places: [Lisse]}
  - {key: maassluis, name: Maassluis, lat: 51.923, lon: 4.250, places: [Maassluis]}
  - {key: midden-delfland, name: Midden-Delfland, lat: 51.965, lon: 4.290, places: [Maasland, Schipluiden, Den Hoorn]}
  - {key: molenlanden, name: Molenlanden, lat: 51.880, lon: 4.800, places: [Nieuw-Lekkerland, Groot-Ammers, Bleskensgraaf, Giessenburg, Arkel, Hoornaar, Streefkerk, Brandwijk, Goudriaan, Ottoland, Noordeloos, Kinderdijk]}
  - {key: nieuwkoop, name: Nieuwkoop, lat: 52.150, lon: 4.775, places: [Nieuwkoop, Nieuwveen, Ter Aar, Zevenhoven, Noorden, Langeraar]}
  - {key: nissewaard, name: Nissewaard, lat: 51.845, lon: 4.320, places: [Spijkenisse, Zuidland, Hekelingen, Simonshaven, Abbenbroek, Geervliet, Heenvliet]}
  - {key: noordwijk, name: Noordwijk, lat: 52.240, lon: 4.445, places: [Noordwijk, Noordwijkerhout, De Zilk]}
  - {key: oegstgeest, name: Oegstgeest, lat: 52.180, lon: 4.470, places: [Oegstgeest]}
  - {key: papendrecht, name: Papendrecht, lat: 51.832, lon: 4.688, places: [Papendrecht]}
  - {key: pijnacker-nootdorp, name: Pijnacker-Nootdorp, lat: 52.030, lon: 4.430, places: [Pijnacker, Nootdorp, Delfgauw]}
  - {key: ridderkerk, name: Ridderkerk, lat: 51.872, lon: 4.603, places: [Ridderkerk, Bolnes, Rijsoord]}
  - {key: rijswijk, name: Rijswijk, lat: 52.036, lon: 4.325, places: [Rijswijk, Rijswijk ZH]}
  - {key: rotterdam, name: Rotterdam, lat: 51.922, lon: 4.479, places: [Rotterdam, Hoogvliet, Rozenburg, Pernis, Hoek van Holland]}
  - {key: schiedam, name: Schiedam, lat: 51.919, lon: 4.400, places: [Schiedam]}
  - {key: sliedrecht, name: Sliedrecht, lat: 51.821, lon: 4.774, places: [Sliedrecht]}
  - {key: teylingen, name: Teylingen, lat: 52.220, lon: 4.510, places: [Sassenheim, Voorhout, Warmond]}
  - {key: vlaardingen, name: Vlaardingen, lat: 51.912, lon: 4.341, places: [Vlaardingen]}
  - {key: voorne-aan-zee, name: Voorne aan Zee, lat: 51.860, lon: 4.150, places: [Hellevoetsluis, Brielle, Rockanje, Oostvoorne, Zwartewaal, Vierpolders]}
  - {key: voorschoten, name: Voorschoten, lat: 52.127, lon: 4.448, places: [Voorschoten]}
  - {key: waddinxveen, name: Waddinxveen, lat: 52.045, lon: 4.650, places: [Waddinxveen]}
  - {key: wassenaar, name: Wassenaar, lat: 52.143, lon: 4.400, places: [Wassenaar]}
  - {key: westland, name: Westland, lat: 51.990, lon: 4.210, places: [Naaldwijk, Monster, "'s-Gravenzande", Wateringen, De Lier, Poeldijk, Honselersdijk, Maasdijk, Kwintsheul, Ter Heijde]}
  - {key: zoetermeer, name: Zoetermeer, lat: 52.057, lon: 4.493, places: [Zoetermeer]}
  - {key: zoeterwoude, name: Zoeterwoude, lat: 52.120, lon: 4.495, places: [Zoeterwoude]}
  - {key: zuidplas, name: Zuidplas, lat: 52.010, lon: 4.610, places: [Nieuwerkerk aan den IJssel, Moordrecht, Zevenhuizen, Moerkapelle]}
  - {key: zwijndrecht, name: Zwijndrecht, lat: 51.815, lon: 4.640, places: [Zwijndrecht, Heerjansdam]}
```

Before committing, check the list against the CBS municipality list for Zuid-Holland (2026). If CBS shows a merger that isn't reflected here, update the entries and the `50` in the test.

- [ ] **Step 4: Write the lookup module**

Create `src/filtering/municipalities.py`:

```python
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


MUNICIPALITIES_FILE = Path("src/config/municipalities.yaml")


@dataclass(frozen=True)
class Municipality:
    key: str
    name: str
    lat: float
    lon: float
    places: tuple[str, ...]

    @property
    def main_place(self) -> str:
        return self.places[0] if self.places else self.name


def normalize_place_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def load_municipalities(path: Path = MUNICIPALITIES_FILE) -> list[Municipality]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        Municipality(
            key=str(item["key"]),
            name=str(item["name"]),
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            places=tuple(str(place) for place in item.get("places", [])),
        )
        for item in data.get("municipalities", [])
    ]


def build_place_index(municipalities: Iterable[Municipality]) -> dict[str, str]:
    index: dict[str, str] = {}
    for municipality in municipalities:
        for place in (municipality.name, *municipality.places):
            token = normalize_place_token(place)
            existing = index.get(token)
            if existing is not None and existing != municipality.key:
                raise ValueError(
                    f"Place {place!r} is listed under both {existing} and {municipality.key}"
                )
            index[token] = municipality.key
    return index


@lru_cache(maxsize=1)
def _loaded() -> tuple[tuple[Municipality, ...], dict[str, str]]:
    municipalities = load_municipalities()
    return tuple(municipalities), build_place_index(municipalities)


def all_municipalities() -> list[Municipality]:
    return sorted(_loaded()[0], key=lambda municipality: municipality.name.lower())


def get_municipality(key: str) -> Municipality | None:
    return next((item for item in _loaded()[0] if item.key == key), None)


def municipality_for_place(city: str | None) -> str | None:
    if not city:
        return None
    return _loaded()[1].get(normalize_place_token(city))
```

`"RIJSWIJK (ZH)"` normalizes to `rijswijkzh`, which matches the `Rijswijk ZH` place.

- [ ] **Step 5: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_municipalities.py -q`
Expected: PASS (7 tests)

- [ ] **Step 6: Commit**

```bash
git add src/config/municipalities.yaml src/filtering/municipalities.py tests/test_municipalities.py
git commit -m "Add Zuid-Holland municipality data and place lookup"
```

---

### Task 2: Profile model and storage

**Files:**
- Create: `src/models/profile.py`
- Modify: `src/storage/sqlite_store.py` (`_init_db`, new methods at the end of the class)
- Modify: `tests/helpers.py` (add `make_profile`)
- Test: `tests/test_sqlite_store_profiles.py`

**Interfaces:**
- Produces:
  - `Profile(id: int, owner_user_id: int, channel_id: int, max_rent_eur: int, min_size_m2: int, preferred_bedrooms: int, allow_close_match: bool, municipalities: tuple[str, ...], paused: bool = False)` (frozen; update with `dataclasses.replace`)
  - On `SQLiteStore`:
    - `create_profile(*, owner_user_id, channel_id, max_rent_eur, min_size_m2, preferred_bedrooms, allow_close_match, municipalities) -> Profile` (raises `sqlite3.IntegrityError` when the owner already has a profile)
    - `get_profile(owner_user_id: int) -> Profile | None`
    - `get_profile_by_id(profile_id: int) -> Profile | None`
    - `list_profiles(active_only: bool = False) -> list[Profile]` (ordered by id)
    - `update_profile(profile: Profile) -> None` (writes every field except id/owner)
    - `set_profile_paused(profile_id: int, paused: bool) -> None`
    - `delete_profile(profile_id: int) -> None` (also deletes its `profile_alerts`)
    - `get_alerted_keys(profile_id: int) -> set[str]`
    - `record_alert(profile_id: int, dedupe_key: str, sent_at: str) -> None` (idempotent)
    - `mark_all_listings_sent(profile_id: int, sent_at: str) -> int`
    - `get_dispatch_candidates(seen_since: str) -> list[tuple[str, Listing]]` (available listings with `last_seen_at >= seen_since`, oldest `first_seen_at` first)
  - `tests.helpers.make_profile(**overrides) -> Profile`

- [ ] **Step 1: Add the model and test helper**

Create `src/models/profile.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Profile:
    id: int
    owner_user_id: int
    channel_id: int
    max_rent_eur: int
    min_size_m2: int
    preferred_bedrooms: int
    allow_close_match: bool
    municipalities: tuple[str, ...]
    paused: bool = False
```

In `tests/helpers.py`, add `from src.models.profile import Profile` to the imports and this function after `make_listing`:

```python
def make_profile(**overrides) -> Profile:
    values = dict(
        id=1,
        owner_user_id=42,
        channel_id=100,
        max_rent_eur=1000,
        min_size_m2=40,
        preferred_bedrooms=2,
        allow_close_match=True,
        municipalities=("delft", "den-haag"),
        paused=False,
    )
    values.update(overrides)
    return Profile(**values)
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_sqlite_store_profiles.py`:

```python
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from src.storage.sqlite_store import SQLiteStore
from tests.helpers import make_listing


SENT_AT = "2026-10-06T12:00:00+00:00"


class ProfileStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = SQLiteStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def create(self, **overrides):
        values = dict(
            owner_user_id=42,
            channel_id=100,
            max_rent_eur=1000,
            min_size_m2=40,
            preferred_bedrooms=2,
            allow_close_match=True,
            municipalities=("delft", "den-haag"),
        )
        values.update(overrides)
        return self.store.create_profile(**values)

    def test_create_and_get(self) -> None:
        profile = self.create()
        self.assertEqual(self.store.get_profile(42), profile)
        self.assertEqual(self.store.get_profile_by_id(profile.id), profile)
        self.assertEqual(profile.municipalities, ("delft", "den-haag"))
        self.assertTrue(profile.allow_close_match)
        self.assertFalse(profile.paused)
        self.assertIsNone(self.store.get_profile(7))

    def test_one_profile_per_owner(self) -> None:
        self.create()
        with self.assertRaises(sqlite3.IntegrityError):
            self.create(channel_id=200)

    def test_update_and_pause(self) -> None:
        profile = self.create()
        updated = replace(profile, max_rent_eur=1200, municipalities=("leiden",), allow_close_match=False)
        self.store.update_profile(updated)
        self.assertEqual(self.store.get_profile(42), updated)

        self.store.set_profile_paused(profile.id, True)
        self.assertTrue(self.store.get_profile(42).paused)
        self.assertEqual(self.store.list_profiles(active_only=True), [])
        self.assertEqual(len(self.store.list_profiles()), 1)

    def test_empty_municipalities_round_trip(self) -> None:
        profile = self.create(municipalities=())
        self.assertEqual(self.store.get_profile(42).municipalities, ())
        self.assertEqual(profile.municipalities, ())

    def test_delete_removes_only_its_alerts(self) -> None:
        profile = self.create()
        other = self.create(owner_user_id=43, channel_id=101)
        self.store.record_alert(profile.id, "k1", SENT_AT)
        self.store.record_alert(other.id, "k1", SENT_AT)
        self.store.delete_profile(profile.id)
        self.assertIsNone(self.store.get_profile(42))
        self.assertEqual(self.store.get_alerted_keys(profile.id), set())
        self.assertEqual(self.store.get_alerted_keys(other.id), {"k1"})

    def test_record_alert_is_idempotent(self) -> None:
        profile = self.create()
        self.store.record_alert(profile.id, "k1", SENT_AT)
        self.store.record_alert(profile.id, "k1", SENT_AT)
        self.assertEqual(self.store.get_alerted_keys(profile.id), {"k1"})

    def test_mark_all_listings_sent(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="1"))
        self.store.upsert_listing(make_listing(source_listing_id="2"))
        profile = self.create()
        self.assertEqual(self.store.mark_all_listings_sent(profile.id, SENT_AT), 2)
        self.assertEqual(len(self.store.get_alerted_keys(profile.id)), 2)

    def test_dispatch_candidates_only_recent_and_available(self) -> None:
        older = make_listing(source_listing_id="older", raw_features={"street": "A"})
        newer = make_listing(source_listing_id="newer")
        gone = make_listing(source_listing_id="gone", is_available=False)
        stale = make_listing(source_listing_id="stale")
        for listing in (older, newer, gone, stale):
            self.store.upsert_listing(listing)
        connection = sqlite3.connect(self.db_path)
        with connection:
            connection.execute(
                "UPDATE listings SET last_seen_at = ? WHERE source_listing_id = 'stale'",
                ("2020-01-01T00:00:00+00:00",),
            )
            connection.execute(
                "UPDATE listings SET first_seen_at = ? WHERE source_listing_id = 'older'",
                ("2020-01-01T00:00:00+00:00",),
            )
        connection.close()

        since = (dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(hours=1)).isoformat()
        candidates = self.store.get_dispatch_candidates(seen_since=since)
        self.assertEqual([listing.source_listing_id for _, listing in candidates], ["older", "newer"])
        key, listing = candidates[0]
        self.assertEqual(key, older.dedupe_key())
        self.assertEqual(listing.raw_features, {"street": "A"})
        self.assertTrue(listing.is_available)

    def test_reopening_existing_database_keeps_profiles(self) -> None:
        self.create()
        self.assertEqual(len(SQLiteStore(self.db_path).list_profiles()), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sqlite_store_profiles.py -q`
Expected: FAIL with `AttributeError: 'SQLiteStore' object has no attribute 'create_profile'`

- [ ] **Step 4: Implement the storage**

In `src/storage/sqlite_store.py`:

1. Add `import datetime as dt` to the imports and `from src.models.profile import Profile` next to the `Listing` import.
2. Below `logger = ...`, add:

```python
PROFILE_COLUMNS = (
    "id, owner_user_id, channel_id, max_rent_eur, min_size_m2, preferred_bedrooms, "
    "allow_close_match, municipalities, paused"
)


def _utc_now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()
```

3. At the end of `_init_db`, inside the `with` block after the `bot_state` table, add:

```python
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL UNIQUE,
                    channel_id INTEGER NOT NULL,
                    max_rent_eur INTEGER NOT NULL,
                    min_size_m2 INTEGER NOT NULL,
                    preferred_bedrooms INTEGER NOT NULL,
                    allow_close_match INTEGER NOT NULL,
                    municipalities TEXT NOT NULL,
                    paused INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_alerts (
                    profile_id INTEGER NOT NULL,
                    dedupe_key TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    PRIMARY KEY (profile_id, dedupe_key)
                )
                """
            )
```

4. Add these methods at the end of the class:

```python
    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> Profile:
        return Profile(
            id=row["id"],
            owner_user_id=row["owner_user_id"],
            channel_id=row["channel_id"],
            max_rent_eur=row["max_rent_eur"],
            min_size_m2=row["min_size_m2"],
            preferred_bedrooms=row["preferred_bedrooms"],
            allow_close_match=bool(row["allow_close_match"]),
            municipalities=tuple(json.loads(row["municipalities"])),
            paused=bool(row["paused"]),
        )

    @staticmethod
    def _row_to_listing(row: sqlite3.Row) -> Listing:
        return Listing(
            source_site=row["source_site"],
            source_listing_id=row["source_listing_id"],
            source_url=row["source_url"],
            title=row["title"],
            city=row["city"],
            rent_price=row["rent_price"],
            living_area_m2=row["living_area_m2"],
            rooms_total=row["rooms_total"],
            bedrooms=row["bedrooms"],
            available_from=row["available_from"],
            raw_features=json.loads(row["raw_features"] or "{}"),
            is_available=bool(row["is_available"]),
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            last_changed_at=row["last_changed_at"],
            listing_status=row["listing_status"],
        )

    def create_profile(
        self,
        *,
        owner_user_id: int,
        channel_id: int,
        max_rent_eur: int,
        min_size_m2: int,
        preferred_bedrooms: int,
        allow_close_match: bool,
        municipalities: tuple[str, ...],
    ) -> Profile:
        now = _utc_now()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO profiles (
                    owner_user_id, channel_id, max_rent_eur, min_size_m2, preferred_bedrooms,
                    allow_close_match, municipalities, paused, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    owner_user_id,
                    channel_id,
                    max_rent_eur,
                    min_size_m2,
                    preferred_bedrooms,
                    1 if allow_close_match else 0,
                    json.dumps(list(municipalities)),
                    now,
                    now,
                ),
            )
            profile_id = cursor.lastrowid
        profile = self.get_profile_by_id(profile_id)
        assert profile is not None
        return profile

    def get_profile(self, owner_user_id: int) -> Profile | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {PROFILE_COLUMNS} FROM profiles WHERE owner_user_id = ?", (owner_user_id,)
            ).fetchone()
        return self._row_to_profile(row) if row is not None else None

    def get_profile_by_id(self, profile_id: int) -> Profile | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {PROFILE_COLUMNS} FROM profiles WHERE id = ?", (profile_id,)
            ).fetchone()
        return self._row_to_profile(row) if row is not None else None

    def list_profiles(self, active_only: bool = False) -> list[Profile]:
        query = f"SELECT {PROFILE_COLUMNS} FROM profiles"
        if active_only:
            query += " WHERE paused = 0"
        with self._connect() as connection:
            rows = connection.execute(query + " ORDER BY id").fetchall()
        return [self._row_to_profile(row) for row in rows]

    def update_profile(self, profile: Profile) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE profiles
                SET channel_id = ?, max_rent_eur = ?, min_size_m2 = ?, preferred_bedrooms = ?,
                    allow_close_match = ?, municipalities = ?, paused = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    profile.channel_id,
                    profile.max_rent_eur,
                    profile.min_size_m2,
                    profile.preferred_bedrooms,
                    1 if profile.allow_close_match else 0,
                    json.dumps(list(profile.municipalities)),
                    1 if profile.paused else 0,
                    _utc_now(),
                    profile.id,
                ),
            )

    def set_profile_paused(self, profile_id: int, paused: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE profiles SET paused = ?, updated_at = ? WHERE id = ?",
                (1 if paused else 0, _utc_now(), profile_id),
            )

    def delete_profile(self, profile_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM profile_alerts WHERE profile_id = ?", (profile_id,))
            connection.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    def get_alerted_keys(self, profile_id: int) -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT dedupe_key FROM profile_alerts WHERE profile_id = ?", (profile_id,)
            ).fetchall()
        return {row["dedupe_key"] for row in rows}

    def record_alert(self, profile_id: int, dedupe_key: str, sent_at: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO profile_alerts (profile_id, dedupe_key, sent_at) VALUES (?, ?, ?)",
                (profile_id, dedupe_key, sent_at),
            )

    def mark_all_listings_sent(self, profile_id: int, sent_at: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO profile_alerts (profile_id, dedupe_key, sent_at)
                SELECT ?, dedupe_key, ? FROM listings
                """,
                (profile_id, sent_at),
            )
            return cursor.rowcount

    def get_dispatch_candidates(self, seen_since: str) -> list[tuple[str, Listing]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM listings
                WHERE is_available = 1 AND last_seen_at >= ?
                ORDER BY first_seen_at, dedupe_key
                """,
                (seen_since,),
            ).fetchall()
        return [(row["dedupe_key"], self._row_to_listing(row)) for row in rows]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sqlite_store_profiles.py tests/test_sqlite_store_state.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/models/profile.py src/storage/sqlite_store.py tests/helpers.py tests/test_sqlite_store_profiles.py
git commit -m "Store search profiles and sent alerts in SQLite"
```

---

### Task 3: Match per profile; the runner only stores

**Files:**
- Modify: `src/filtering/rules.py` (whole file)
- Modify: `src/scrapers/runner.py` (`SourceResult`, `RunSummary`, the per-listing loop)
- Modify: `src/config.py` (drop `store_only_matches`)
- Modify: `src/main.py` (drop prune)
- Modify: `src/storage/sqlite_store.py` (delete `get_all_listings_for_prune`, `delete_listings_by_keys`)
- Modify: `src/bot/embeds.py` (`build_summary_embed`)
- Modify: `tests/helpers.py` (drop `store_only_matches`)
- Test: `tests/test_rules.py` (new), `tests/test_runner.py`, `tests/test_bot_helpers.py`

**Interfaces:**
- Consumes: `Profile` (Task 2), `municipality_for_place` (Task 1)
- Produces:
  - `evaluate_listing(listing: Listing, profile: Profile) -> MatchResult` (`MatchResult` unchanged)
  - `SourceResult(name, status, listings=0, changed=0, details="")`, with **no** `alerted`
  - `RunSummary(results: list[SourceResult] = [], alerted: int = 0)`. `alerted` is a plain field the caller sets after dispatch.

- [ ] **Step 1: Write the failing rules test**

Create `tests/test_rules.py`:

```python
from __future__ import annotations

import unittest

from src.filtering.rules import evaluate_listing
from tests.helpers import make_listing, make_profile


class EvaluateListingTests(unittest.TestCase):
    def test_hard_match(self) -> None:
        match = evaluate_listing(make_listing(city="Delft"), make_profile())
        self.assertTrue(match.is_hard_match)
        self.assertFalse(match.is_close_match)
        self.assertIn("city_ok", match.reasons)

    def test_alias_city_matches_municipality(self) -> None:
        match = evaluate_listing(make_listing(city="'s-Gravenhage"), make_profile())
        self.assertTrue(match.is_hard_match)

    def test_city_outside_profile(self) -> None:
        match = evaluate_listing(make_listing(city="Leiden"), make_profile())
        self.assertFalse(match.is_hard_match or match.is_close_match)

    def test_unknown_city(self) -> None:
        match = evaluate_listing(make_listing(city="Amsterdam"), make_profile())
        self.assertFalse(match.is_hard_match or match.is_close_match)

    def test_profile_without_cities_matches_nothing(self) -> None:
        match = evaluate_listing(make_listing(city="Delft"), make_profile(municipalities=()))
        self.assertFalse(match.is_hard_match or match.is_close_match)

    def test_close_match_within_ten_percent(self) -> None:
        listing = make_listing(rent_price=1090, living_area_m2=37)
        match = evaluate_listing(listing, make_profile())
        self.assertFalse(match.is_hard_match)
        self.assertTrue(match.is_close_match)

    def test_close_match_disabled(self) -> None:
        listing = make_listing(rent_price=1090)
        match = evaluate_listing(listing, make_profile(allow_close_match=False))
        self.assertFalse(match.is_close_match)

    def test_unavailable_never_matches(self) -> None:
        match = evaluate_listing(make_listing(is_available=False), make_profile())
        self.assertFalse(match.is_hard_match or match.is_close_match)

    def test_thresholds_come_from_profile(self) -> None:
        listing = make_listing(rent_price=1400, living_area_m2=70)
        self.assertFalse(evaluate_listing(listing, make_profile()).is_hard_match)
        roomy = make_profile(max_rent_eur=1500, min_size_m2=60)
        self.assertTrue(evaluate_listing(listing, roomy).is_hard_match)

    def test_preferred_bedrooms_only_adds_score(self) -> None:
        few = evaluate_listing(make_listing(bedrooms=1), make_profile(preferred_bedrooms=2))
        many = evaluate_listing(make_listing(bedrooms=2), make_profile(preferred_bedrooms=2))
        self.assertTrue(few.is_hard_match)
        self.assertEqual(many.score - few.score, 10)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_rules.py -q`
Expected: FAIL (`AttributeError: 'Profile' object has no attribute 'allowed_cities'`)

- [ ] **Step 3: Rewrite the rules**

Replace `src/filtering/rules.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass

from src.filtering.municipalities import municipality_for_place
from src.models.listing import Listing
from src.models.profile import Profile


@dataclass(frozen=True)
class MatchResult:
    is_hard_match: bool
    is_close_match: bool
    score: int
    reasons: list[str]


def evaluate_listing(listing: Listing, profile: Profile) -> MatchResult:
    reasons: list[str] = []
    score = 0

    municipality = municipality_for_place(listing.city)
    hard_price = listing.rent_price is not None and listing.rent_price <= profile.max_rent_eur
    hard_area = listing.living_area_m2 is not None and listing.living_area_m2 >= profile.min_size_m2
    hard_city = municipality is not None and municipality in profile.municipalities
    hard_availability = listing.is_available

    if hard_price:
        score += 40
        reasons.append("price_ok")
    if hard_area:
        score += 30
        reasons.append("size_ok")
    if hard_city:
        score += 20
        reasons.append("city_ok")
    if hard_availability:
        score += 5
        reasons.append("available")

    preferred_bedrooms = listing.bedrooms is not None and listing.bedrooms >= profile.preferred_bedrooms
    if preferred_bedrooms:
        score += 10
        reasons.append("preferred_bedrooms")

    is_hard_match = hard_price and hard_area and hard_city and hard_availability

    relaxed_price = listing.rent_price is not None and listing.rent_price <= int(profile.max_rent_eur * 1.1)
    relaxed_area = listing.living_area_m2 is not None and listing.living_area_m2 >= int(profile.min_size_m2 * 0.9)
    # Close match still requires both dimensions to stay near profile bounds.
    is_close_match = (
        profile.allow_close_match
        and hard_availability
        and hard_city
        and relaxed_price
        and relaxed_area
    )

    return MatchResult(
        is_hard_match=is_hard_match,
        is_close_match=is_close_match and not is_hard_match,
        score=score,
        reasons=reasons,
    )
```

- [ ] **Step 4: Run the rules test**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_rules.py -q`
Expected: PASS (10 tests)

- [ ] **Step 5: Update the runner test for store-only behaviour**

In `tests/test_runner.py`, replace `test_alerts_only_new_matches` with:

```python
    def test_stores_every_listing_and_counts_changes(self) -> None:
        first = self.run_once()
        alpha = self.result(first, "alpha")
        self.assertEqual((alpha.status, alpha.listings, alpha.changed), ("ok", 2, 2))
        self.assertEqual(alpha.details, "listings=2,changed=2")
        self.assertEqual(len(self.store.get_recent_listings(10)), 2)
        self.assertEqual(self.notifier.listings, [])
        self.assertEqual(first.alerted, 0)

        second = self.run_once()
        self.assertEqual(self.result(second, "alpha").changed, 0)
        self.assertEqual(self.notifier.ops, [])
```

- [ ] **Step 6: Run the runner tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_runner.py -q`
Expected: FAIL (the runner still calls `evaluate_listing(listing, settings)`)

- [ ] **Step 7: Make the runner store only**

In `src/scrapers/runner.py`:

1. Remove `from src.filtering.rules import evaluate_listing`.
2. Replace the two dataclasses:

```python
@dataclass
class SourceResult:
    name: str
    status: str
    listings: int = 0
    changed: int = 0
    details: str = ""


@dataclass
class RunSummary:
    results: list[SourceResult] = field(default_factory=list)
    # Set by the caller after dispatching; the runner itself never alerts.
    alerted: int = 0
```

3. In the `try:` block, replace everything from `evaluated_count = 0` up to and including the `store.write_source_run(... status="ok" ...)` call and the `summary.results.append(SourceResult(... status="ok" ...))` with:

```python
            changed_count = 0
            for listing in listings:
                if store.upsert_listing(listing).changed:
                    changed_count += 1
            logger.info(
                "Source=%s listings=%d changed=%d", policy.name, len(listings), changed_count
            )

            details = f"listings={len(listings)},changed={changed_count}"
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
                    details=details,
                )
            )
```

Leave the `[SOURCE_RECOVERED]` check and the two `except` blocks unchanged.

- [ ] **Step 8: Remove `STORE_ONLY_MATCHES` and prune**

- `src/config.py`: delete the `store_only_matches: bool` field and the `store_only_matches=...` line in `load_settings`.
- `tests/helpers.py`: delete `store_only_matches=True,` from `make_settings`.
- `src/storage/sqlite_store.py`: delete the `get_all_listings_for_prune` and `delete_listings_by_keys` methods.
- `src/main.py`: delete `prune_non_matching_listings`, the `--prune-non-matches` argument, the `if args.prune_non_matches:` block, and the imports of `evaluate_listing` and `Listing`. Replace the final log line with `logger.info("Run completed sources=%d", len(summary.results))`.

- [ ] **Step 9: Update the summary embed and its test**

In `src/bot/embeds.py` `build_summary_embed`, change the `ok` line to:

```python
            lines.append(
                f"{icon} **{result.name}** · {result.listings} listings · {result.changed} changed"
            )
```

In `tests/test_bot_helpers.py`, replace `SummaryEmbedTests.test_summary_lines` with:

```python
    def test_summary_lines(self) -> None:
        summary = RunSummary(
            results=[
                SourceResult(name="vbent", status="ok", listings=4, changed=2),
                SourceResult(name="verra", status="blocked", details="blocked: 403"),
            ],
            alerted=3,
        )
        embed = build_summary_embed(summary)
        self.assertIn("**vbent** · 4 listings · 2 changed", embed.description)
        self.assertIn("blocked: 403", embed.description)
        self.assertEqual(embed.footer.text, "3 new alert(s)")
```

- [ ] **Step 10: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS. `grep -rn "store_only_matches\|prune" src tests` should print nothing.

- [ ] **Step 11: Commit**

```bash
git add -A src tests
git commit -m "Match listings per profile and make the runner store every listing"
```

---

### Task 4: Notifier sends to a profile's channel

**Files:**
- Modify: `src/notify/base.py` (whole file)
- Modify: `src/notify/discord_notifier.py` (`DiscordNotifier` class)
- Modify: `src/bot/client.py` (the `DiscordNotifier(...)` construction in `on_ready` only)
- Modify: `tests/helpers.py` (`RecordingNotifier`)
- Test: `tests/test_discord_notifier.py`

**Interfaces:**
- Consumes: `Profile` (Task 2)
- Produces:
  - `src.notify.base.ChannelUnavailableError(Exception)`
  - `Notifier` protocol: `notify_listing(profile: Profile, listing: Listing, match: MatchResult) -> None` (**raises** on failure); `notify_ops(text: str, mention_user_ids: Sequence[int] = ()) -> None` (never raises)
  - `DiscordNotifier(loop, resolve_channel: Callable[[int], Awaitable[channel | None]], ops_channel=None)`
  - `RecordingNotifier` with `.listings: list[tuple[Profile, Listing, MatchResult]]`, `.ops: list[str]`, `.ops_mentions: list[list[int]]`, and `.fail_with: dict[int, Exception]` (channel_id → error raised by `notify_listing`)

- [ ] **Step 1: Update the test helper**

Replace `RecordingNotifier` in `tests/helpers.py` with:

```python
class RecordingNotifier:
    def __init__(self) -> None:
        self.listings: list[tuple[Profile, Listing, MatchResult]] = []
        self.ops: list[str] = []
        self.ops_mentions: list[list[int]] = []
        # channel_id -> exception raised by notify_listing, to simulate failed sends.
        self.fail_with: dict[int, Exception] = {}

    def notify_listing(self, profile: Profile, listing: Listing, match: MatchResult) -> None:
        error = self.fail_with.get(profile.channel_id)
        if error is not None:
            raise error
        self.listings.append((profile, listing, match))

    def notify_ops(self, text: str, mention_user_ids=()) -> None:
        self.ops.append(text)
        self.ops_mentions.append(list(mention_user_ids))
```

- [ ] **Step 2: Write the failing notifier tests**

In `tests/test_discord_notifier.py`:
- Change the imports to:

```python
from __future__ import annotations

import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

from src.notify.base import ChannelUnavailableError, LogNotifier
from src.notify.discord_notifier import (
    CLOSE_MATCH_COLOR,
    HARD_MATCH_COLOR,
    DiscordNotifier,
    build_listing_embed,
    build_ops_embed,
)
from tests.helpers import make_listing, make_match, make_profile
```

- After `FakeChannel`, add:

```python
def resolver(*channels: FakeChannel):
    by_id = {channel.id: channel for channel in channels}

    async def resolve(channel_id: int):
        return by_id.get(channel_id)

    return resolve


def http_error(cls, status: int, reason: str):
    return cls(SimpleNamespace(status=status, reason=reason), reason)
```

- Replace the whole `DiscordNotifierTests` class and `LogNotifierTests` class with:

```python
class DiscordNotifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.profile = make_profile(owner_user_id=42, channel_id=100)

    def tearDown(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        self.loop.close()

    def test_hard_match_goes_to_profile_channel_and_mentions_owner(self) -> None:
        mine, other = FakeChannel(100), FakeChannel(200)
        notifier = DiscordNotifier(self.loop, resolver(mine, other))
        notifier.notify_listing(self.profile, make_listing(), make_match(hard=True))
        self.assertEqual(len(mine.sent), 1)
        self.assertEqual(other.sent, [])
        self.assertEqual(mine.sent[0]["content"], "<@42>")
        self.assertIsInstance(mine.sent[0]["embed"], discord.Embed)

    def test_close_match_does_not_mention(self) -> None:
        mine = FakeChannel(100)
        DiscordNotifier(self.loop, resolver(mine)).notify_listing(
            self.profile, make_listing(), make_match(hard=False)
        )
        self.assertIsNone(mine.sent[0]["content"])

    def test_missing_channel_raises_unavailable(self) -> None:
        notifier = DiscordNotifier(self.loop, resolver())
        with self.assertRaises(ChannelUnavailableError):
            notifier.notify_listing(self.profile, make_listing(), make_match())

    def test_forbidden_and_not_found_raise_unavailable(self) -> None:
        for error in (
            http_error(discord.Forbidden, 403, "Missing Access"),
            http_error(discord.NotFound, 404, "Unknown Channel"),
        ):
            with self.subTest(error=type(error).__name__):
                notifier = DiscordNotifier(self.loop, resolver(FakeChannel(100, error=error)))
                with self.assertRaises(ChannelUnavailableError):
                    notifier.notify_listing(self.profile, make_listing(), make_match())

    def test_other_send_error_propagates(self) -> None:
        notifier = DiscordNotifier(self.loop, resolver(FakeChannel(100, error=RuntimeError("boom"))))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            notifier.notify_listing(self.profile, make_listing(), make_match())

    def test_send_timeout_raises(self) -> None:
        notifier = DiscordNotifier(self.loop, resolver(FakeChannel(100, hang=True)))
        with patch("src.notify.discord_notifier.SEND_TIMEOUT_SECONDS", 0.2):
            with self.assertRaises(TimeoutError):
                notifier.notify_listing(self.profile, make_listing(), make_match())

    def test_ops_goes_to_ops_channel_with_mentions(self) -> None:
        ops = FakeChannel(2)
        DiscordNotifier(self.loop, resolver(), ops).notify_ops("[PROFILE_DELETED] x", [7, 8])
        self.assertEqual(ops.sent[0]["content"], "<@7> <@8>")
        self.assertEqual(ops.sent[0]["embed"].description, "[PROFILE_DELETED] x")

    def test_ops_without_mentions_has_no_content(self) -> None:
        ops = FakeChannel(2)
        DiscordNotifier(self.loop, resolver(), ops).notify_ops("[SOURCE_ERROR] x - boom")
        self.assertIsNone(ops.sent[0]["content"])

    def test_ops_without_channel_only_logs(self) -> None:
        with self.assertLogs("src.notify.discord_notifier", level="WARNING") as captured:
            DiscordNotifier(self.loop, resolver()).notify_ops("hello")
        self.assertIn("hello", "\n".join(captured.output))

    def test_ops_send_error_is_logged_not_raised(self) -> None:
        ops = FakeChannel(2, error=RuntimeError("missing access"))
        with self.assertLogs("src.notify.discord_notifier", level="ERROR"):
            DiscordNotifier(self.loop, resolver(), ops).notify_ops("hello")


class LogNotifierTests(unittest.TestCase):
    def test_logs_listing_and_ops(self) -> None:
        notifier = LogNotifier()
        with self.assertLogs("src.notify.base", level="INFO") as captured:
            notifier.notify_listing(make_profile(owner_user_id=42), make_listing(), make_match())
            notifier.notify_ops("[SOURCE_ERROR] x")
        output = "\n".join(captured.output)
        self.assertIn("HARD_MATCH", output)
        self.assertIn("owner=42", output)
        self.assertIn("[SOURCE_ERROR] x", output)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_discord_notifier.py -q`
Expected: FAIL with `ImportError: cannot import name 'ChannelUnavailableError'`

- [ ] **Step 4: Implement the protocol and LogNotifier**

Replace `src/notify/base.py` with:

```python
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from src.filtering.rules import MatchResult
from src.models.listing import Listing
from src.models.profile import Profile


logger = logging.getLogger(__name__)


class ChannelUnavailableError(Exception):
    """The profile's alert channel no longer exists or the bot can't post in it."""


class Notifier(Protocol):
    def notify_listing(self, profile: Profile, listing: Listing, match: MatchResult) -> None:
        """Send one listing alert. Raises when the send fails, so the caller can retry later."""

    def notify_ops(self, text: str, mention_user_ids: Sequence[int] = ()) -> None:
        """Send an ops message. Never raises."""


class LogNotifier:
    """Notifier for CLI runs: writes alerts to the log instead of sending them."""

    def notify_listing(self, profile: Profile, listing: Listing, match: MatchResult) -> None:
        match_type = "HARD_MATCH" if match.is_hard_match else "CLOSE_MATCH"
        logger.info(
            "[%s] owner=%s | %s | %s | price=%s area=%s city=%s | %s",
            match_type,
            profile.owner_user_id,
            listing.source_site,
            listing.title,
            listing.rent_price,
            listing.living_area_m2,
            listing.city,
            listing.source_url,
        )

    def notify_ops(self, text: str, mention_user_ids: Sequence[int] = ()) -> None:
        logger.warning("[OPS] %s", text)
```

- [ ] **Step 5: Implement DiscordNotifier**

In `src/notify/discord_notifier.py`:
- Change `from collections.abc import Sequence` to `from collections.abc import Awaitable, Callable, Sequence`.
- Add `from src.models.profile import Profile` and `from src.notify.base import ChannelUnavailableError`.
- Under `FIELD_VALUE_LIMIT = 1024` add:

```python
ALLOWED_MENTIONS = discord.AllowedMentions(users=True, roles=False, everyone=False)
ChannelResolver = Callable[[int], Awaitable[object | None]]
```

- Replace the `DiscordNotifier` class with:

```python
class DiscordNotifier:
    """Sends alerts to Discord from the scrape worker thread.

    Methods block until the message is sent (or fails) and must be called from a
    thread other than the bot's event loop thread.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        resolve_channel: ChannelResolver,
        ops_channel=None,
    ) -> None:
        self.loop = loop
        self.resolve_channel = resolve_channel
        self.ops_channel = ops_channel

    def notify_listing(self, profile: Profile, listing: Listing, match: MatchResult) -> None:
        content = f"<@{profile.owner_user_id}>" if match.is_hard_match else None
        embed = build_listing_embed(listing, match)
        self._run(self._send_to_channel_id(profile.channel_id, content, embed))

    def notify_ops(self, text: str, mention_user_ids: Sequence[int] = ()) -> None:
        if self.ops_channel is None:
            logger.warning("[OPS] %s", text)
            return
        content = " ".join(f"<@{user_id}>" for user_id in mention_user_ids) or None
        try:
            self._run(
                self.ops_channel.send(
                    content=content, embed=build_ops_embed(text), allowed_mentions=ALLOWED_MENTIONS
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("Discord ops send failed channel=%s", getattr(self.ops_channel, "id", "?"))

    async def _send_to_channel_id(self, channel_id: int, content: str | None, embed: discord.Embed) -> None:
        channel = await self.resolve_channel(channel_id)
        if channel is None:
            raise ChannelUnavailableError(f"channel {channel_id} not found")
        try:
            await channel.send(content=content, embed=embed, allowed_mentions=ALLOWED_MENTIONS)
        except (discord.NotFound, discord.Forbidden) as error:
            raise ChannelUnavailableError(f"channel {channel_id}: {error}") from error

    def _run(self, coroutine) -> None:
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            future.result(timeout=SEND_TIMEOUT_SECONDS)
        except BaseException:
            future.cancel()
            raise
```

- [ ] **Step 6: Keep the bot constructing the notifier**

In `src/bot/client.py` `on_ready`, replace the `self.notifier = DiscordNotifier(...)` call with:

```python
        self.notifier = DiscordNotifier(
            asyncio.get_running_loop(),
            self._resolve_channel,
            ops_channel or alert_channel,
        )
```

(Task 7 rewrites `on_ready`. Until then the bot stores listings but doesn't alert. That's expected on this branch.)

- [ ] **Step 7: Run the suite**

Run: `./.venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/notify tests/helpers.py tests/test_discord_notifier.py src/bot/client.py
git commit -m "Send listing alerts to the profile's channel and mention its owner"
```

---

### Task 5: Dispatch matches to profiles

**Files:**
- Create: `src/notify/dispatch.py`
- Modify: `src/main.py` (`--once` dry-run dispatch)
- Test: `tests/test_dispatch.py`

**Interfaces:**
- Consumes: `SQLiteStore.get_dispatch_candidates/get_alerted_keys/record_alert/set_profile_paused` (Task 2), `evaluate_listing(listing, profile)` (Task 3), `Notifier`, `ChannelUnavailableError` (Task 4)
- Produces:
  - `DispatchSummary(sent: int = 0, failed: int = 0, paused_profile_ids: list[int] = [])`
  - `stale_window(interval_minutes: int) -> dt.timedelta` (3 × interval)
  - `find_matches(candidates: list[tuple[str, Listing]], profile: Profile) -> list[tuple[str, Listing, MatchResult]]`
  - `dispatch(store, profiles: Sequence[Profile], notifier: Notifier, now: dt.datetime, stale_after: dt.timedelta, record: bool = True) -> DispatchSummary`
  - `recent_matches(store, profile: Profile, now: dt.datetime, stale_after: dt.timedelta, limit: int) -> list[Listing]` (newest first)

- [ ] **Step 1: Write the failing test**

Create `tests/test_dispatch.py`:

```python
from __future__ import annotations

import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.notify.base import ChannelUnavailableError
from src.notify.dispatch import dispatch, recent_matches, stale_window
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import RecordingNotifier, make_listing


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = SQLiteStore(self.db_path)
        self.notifier = RecordingNotifier()
        self.delft = self.make_profile(owner_user_id=42, channel_id=100, municipalities=("delft",))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_profile(self, **overrides):
        values = dict(
            owner_user_id=42,
            channel_id=100,
            max_rent_eur=1000,
            min_size_m2=40,
            preferred_bedrooms=2,
            allow_close_match=True,
            municipalities=("delft",),
        )
        values.update(overrides)
        return self.store.create_profile(**values)

    def run_dispatch(self, profiles=None, record=True):
        return dispatch(
            store=self.store,
            profiles=profiles if profiles is not None else self.store.list_profiles(active_only=True),
            notifier=self.notifier,
            now=dt.datetime.now(tz=dt.timezone.utc),
            stale_after=stale_window(10),
            record=record,
        )

    def sent_ids(self):
        return [listing.source_listing_id for _, listing, _ in self.notifier.listings]

    def test_backfill_sends_each_match_once(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a", city="Delft"))
        self.store.upsert_listing(make_listing(source_listing_id="b", city="Delft", rent_price=3000))
        self.store.upsert_listing(make_listing(source_listing_id="c", city="Delft", is_available=False))
        summary = self.run_dispatch()
        self.assertEqual((summary.sent, summary.failed), (1, 0))
        self.assertEqual(self.sent_ids(), ["a"])

        self.assertEqual(self.run_dispatch().sent, 0)
        self.assertEqual(self.sent_ids(), ["a"])

    def test_two_profiles_get_their_own_matches(self) -> None:
        leiden = self.make_profile(owner_user_id=43, channel_id=101, municipalities=("leiden",))
        self.store.upsert_listing(make_listing(source_listing_id="d", city="Delft"))
        self.store.upsert_listing(make_listing(source_listing_id="l", city="Leiden"))
        self.run_dispatch()
        routed = sorted((profile.id, listing.source_listing_id) for profile, listing, _ in self.notifier.listings)
        self.assertEqual(routed, [(self.delft.id, "d"), (leiden.id, "l")])

    def test_failed_send_is_not_recorded_and_retried(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a"))
        self.store.upsert_listing(make_listing(source_listing_id="b"))
        self.notifier.fail_with[100] = TimeoutError()
        with self.assertLogs("src.notify.dispatch", level="ERROR"):
            summary = self.run_dispatch()
        self.assertEqual((summary.sent, summary.failed), (0, 2))
        self.assertEqual(self.store.get_alerted_keys(self.delft.id), set())

        del self.notifier.fail_with[100]
        self.assertEqual(self.run_dispatch().sent, 2)

    def test_unavailable_channel_pauses_profile_and_others_continue(self) -> None:
        other = self.make_profile(owner_user_id=43, channel_id=101)
        self.store.upsert_listing(make_listing(source_listing_id="a"))
        self.notifier.fail_with[100] = ChannelUnavailableError("gone")
        summary = self.run_dispatch()
        self.assertEqual(summary.paused_profile_ids, [self.delft.id])
        self.assertTrue(self.store.get_profile_by_id(self.delft.id).paused)
        self.assertEqual(self.notifier.ops, ["[PROFILE_ERROR] <@42>'s channel is unavailable; profile paused"])
        self.assertEqual([profile.id for profile, _, _ in self.notifier.listings], [other.id])

    def test_paused_profile_is_skipped(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a"))
        self.store.set_profile_paused(self.delft.id, True)
        self.assertEqual(self.run_dispatch(profiles=self.store.list_profiles()).sent, 0)

    def test_stale_listings_are_not_sent(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="old"))
        connection = sqlite3.connect(self.db_path)
        with connection:
            connection.execute("UPDATE listings SET last_seen_at = '2020-01-01T00:00:00+00:00'")
        connection.close()
        self.assertEqual(self.run_dispatch().sent, 0)

    def test_price_change_alerts_again(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a", rent_price=950))
        self.run_dispatch()
        self.store.upsert_listing(make_listing(source_listing_id="a", rent_price=900))
        self.assertEqual(self.run_dispatch().sent, 1)
        self.assertEqual([listing.rent_price for _, listing, _ in self.notifier.listings], [950, 900])

    def test_dry_run_does_not_record(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="a"))
        self.notifier.fail_with[100] = ChannelUnavailableError("gone")
        self.run_dispatch(record=False)
        self.assertFalse(self.store.get_profile_by_id(self.delft.id).paused)
        del self.notifier.fail_with[100]
        self.run_dispatch(record=False)
        self.run_dispatch(record=False)
        self.assertEqual(self.sent_ids(), ["a", "a"])
        self.assertEqual(self.store.get_alerted_keys(self.delft.id), set())

    def test_recent_matches_newest_first_with_limit(self) -> None:
        for listing_id in ("a", "b", "c"):
            self.store.upsert_listing(make_listing(source_listing_id=listing_id))
        connection = sqlite3.connect(self.db_path)
        with connection:
            for index, listing_id in enumerate(("a", "b", "c")):
                connection.execute(
                    "UPDATE listings SET first_seen_at = ? WHERE source_listing_id = ?",
                    (f"2026-10-0{index + 1}T00:00:00+00:00", listing_id),
                )
        connection.close()
        listings = recent_matches(
            self.store, self.delft, dt.datetime.now(tz=dt.timezone.utc), stale_window(10), limit=2
        )
        self.assertEqual([listing.source_listing_id for listing in listings], ["c", "b"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_dispatch.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.notify.dispatch'`

- [ ] **Step 3: Implement dispatch**

Create `src/notify/dispatch.py`:

```python
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from src.filtering.rules import MatchResult, evaluate_listing
from src.models.listing import Listing
from src.models.profile import Profile
from src.notify.base import ChannelUnavailableError, Notifier
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)


@dataclass
class DispatchSummary:
    sent: int = 0
    failed: int = 0
    paused_profile_ids: list[int] = field(default_factory=list)


def stale_window(interval_minutes: int) -> dt.timedelta:
    # Long enough that one failed scrape doesn't drop a source's listings.
    return dt.timedelta(minutes=3 * interval_minutes)


def find_matches(
    candidates: list[tuple[str, Listing]], profile: Profile
) -> list[tuple[str, Listing, MatchResult]]:
    matches = []
    for key, listing in candidates:
        match = evaluate_listing(listing, profile)
        if match.is_hard_match or match.is_close_match:
            matches.append((key, listing, match))
    return matches


def dispatch(
    store: SQLiteStore,
    profiles: Sequence[Profile],
    notifier: Notifier,
    now: dt.datetime,
    stale_after: dt.timedelta,
    record: bool = True,
) -> DispatchSummary:
    """Send every current match a profile hasn't received yet.

    With record=False (CLI dry run) nothing is written, so the bot still sends them later.
    """
    summary = DispatchSummary()
    active = [profile for profile in profiles if not profile.paused]
    if not active:
        return summary
    candidates = store.get_dispatch_candidates(seen_since=(now - stale_after).isoformat())

    for profile in active:
        already_sent = store.get_alerted_keys(profile.id) if record else set()
        for key, listing, match in find_matches(candidates, profile):
            if key in already_sent:
                continue
            try:
                notifier.notify_listing(profile, listing, match)
            except ChannelUnavailableError as error:
                logger.warning("Profile=%s channel unavailable: %s", profile.id, error)
                if record:
                    store.set_profile_paused(profile.id, True)
                    notifier.notify_ops(
                        f"[PROFILE_ERROR] <@{profile.owner_user_id}>'s channel is unavailable; profile paused"
                    )
                summary.paused_profile_ids.append(profile.id)
                break
            except Exception:  # noqa: BLE001
                # Not recorded, so the next dispatch retries it.
                logger.exception("Sending listing=%s to profile=%s failed", key, profile.id)
                summary.failed += 1
                continue
            if record:
                store.record_alert(profile.id, key, now.isoformat())
            summary.sent += 1

    logger.info(
        "Dispatch profiles=%d sent=%d failed=%d paused=%s",
        len(active),
        summary.sent,
        summary.failed,
        summary.paused_profile_ids,
    )
    return summary


def recent_matches(
    store: SQLiteStore,
    profile: Profile,
    now: dt.datetime,
    stale_after: dt.timedelta,
    limit: int,
) -> list[Listing]:
    candidates = store.get_dispatch_candidates(seen_since=(now - stale_after).isoformat())
    matches = [listing for _, listing, _ in find_matches(candidates, profile)]
    return list(reversed(matches))[:limit]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_dispatch.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Dry-run dispatch in the CLI**

In `src/main.py`, add `import datetime as dt` and `from src.notify.dispatch import dispatch, stale_window`. Replace the block from `summary = run_all_sources(` to the end of `main()` with:

```python
    notifier = LogNotifier()
    summary = run_all_sources(
        settings=settings,
        store=store,
        source_factories=SOURCE_FACTORIES,
        registry_file=REGISTRY_FILE,
        notifier=notifier,
        selected_sources=selected_sources,
    )
    # Dry run: matches are logged but not recorded, so the bot still sends them.
    result = dispatch(
        store=store,
        profiles=store.list_profiles(active_only=True),
        notifier=notifier,
        now=dt.datetime.now(tz=dt.timezone.utc),
        stale_after=stale_window(settings.scrape_interval_minutes),
        record=False,
    )
    logger.info(
        "Run completed sources=%d matches=%d (dry run, not recorded)",
        len(summary.results),
        result.sent,
    )
```

- [ ] **Step 6: Run the suite**

Run: `./.venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/notify/dispatch.py src/main.py tests/test_dispatch.py
git commit -m "Dispatch unsent matches to each profile after a scrape"
```

---

### Task 6: Search scope, Vb&t area, Vesteda without price bounds

**Files:**
- Create: `src/scrapers/scope.py`
- Modify: `src/scrapers/base.py` (`__init__`)
- Modify: `src/scrapers/runner.py` (`run_all_sources` signature, the `factory(...)` call)
- Modify: `src/scrapers/sites/vbent.py` (tabs!)
- Modify: `src/scrapers/sites/vesteda.py` (`payload_template`)
- Test: `tests/test_vbent.py`, `tests/test_runner.py`, `tests/test_scope.py` (new)

**Interfaces:**
- Consumes: `Profile` (Task 2), `Municipality`, `all_municipalities` (Task 1)
- Produces:
  - `SearchScope(municipalities: frozenset[str] = frozenset())` with `SearchScope.from_profiles(profiles: Iterable[Profile]) -> SearchScope` (unpaused profiles only)
  - `BaseScraper.__init__(self, settings, scope: SearchScope | None = None)` and `self.scope`
  - `run_all_sources(..., selected_sources=None, scope: SearchScope | None = None)`. Factories are called as `factory(settings, scope)`.
  - In `src/scrapers/sites/vbent.py`: `haversine_km(lat1, lon1, lat2, lon2) -> float`, `vbent_area(scope: SearchScope | None, municipalities: Sequence[Municipality] | None = None) -> tuple[str, int]`, constants `DEFAULT_AREA = ("Delft", 15)`, `AREA_MARGIN_KM = 5`, `MIN_RADIUS_KM = 10`, `MAX_RADIUS_KM = 50`

- [ ] **Step 1: Probe Vb&t radius handling and center names (one-off, not committed)**

Save this script as `<scratchpad>/probe_vbent.py` and run it from the repo root with `./.venv/Scripts/python.exe <scratchpad>/probe_vbent.py`. It makes about 63 requests, 3 s apart (about 3 minutes):

```python
import json, time
from urllib.parse import quote

import httpx

from src.filtering.municipalities import all_municipalities

UA = {"User-Agent": "huur-scraper/0.1"}


def page_count(city, radius):
    f = {"city": city, "radius": radius, "address": "", "priceRental": {"min": 0, "max": 0},
         "availablefrom": "", "surface": "", "rooms": 0, "typeCategory": ""}
    headers = dict(UA, Accept="application/json",
                   Cookie="language=nl; filter_properties=" + quote(json.dumps(f, separators=(",", ":")), safe=""))
    payload = httpx.get("https://vbtverhuurmakelaars.nl/api/properties/12/1",
                        params={"search": "true"}, headers=headers, timeout=20).json()
    time.sleep(3)
    return payload.get("pageCount")


national = page_count("", 0)
print("national", national)
for radius in (10, 12, 15, 18, 20, 23, 25, 30, 37, 45, 50):
    print("Delft radius", radius, page_count("Delft", radius))
for municipality in all_municipalities():
    count = page_count(municipality.main_place, 10)
    flag = "  <-- check" if count in (0, national) else ""
    print(f"{municipality.main_place:28} {count}{flag}")
```

Read the output:
- **Radii:** if the page counts change at non-standard values (12, 18, 23, 37), Vb&t accepts any radius. Continue with Step 2 as written. If the counts only change at a few fixed values (for example 10/15/20/30/40/50), add `RADIUS_STEPS = (10, 15, 20, 30, 40, 50)` next to the other constants in Step 4, and in `vbent_area` replace the `radius = ...` line with `radius = next(step for step in RADIUS_STEPS if step >= min(max(math.ceil(needed + AREA_MARGIN_KM), MIN_RADIUS_KM), MAX_RADIUS_KM))`.
- **Center names:** a row marked `<-- check` probably wasn't recognized. Try another place of that municipality (for example `Spijkenisse` instead of `Nissewaard`) by moving it to the front of `places` in `municipalities.yaml`. If no place works, leave it and note it in the commit message.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_scope.py`:

```python
from __future__ import annotations

import unittest

from src.scrapers.scope import SearchScope
from tests.helpers import make_profile


class SearchScopeTests(unittest.TestCase):
    def test_union_of_unpaused_profiles(self) -> None:
        scope = SearchScope.from_profiles(
            [
                make_profile(id=1, municipalities=("delft", "den-haag")),
                make_profile(id=2, municipalities=("leiden",)),
                make_profile(id=3, municipalities=("gouda",), paused=True),
                make_profile(id=4, municipalities=()),
            ]
        )
        self.assertEqual(scope.municipalities, frozenset({"delft", "den-haag", "leiden"}))

    def test_no_profiles(self) -> None:
        self.assertEqual(SearchScope.from_profiles([]).municipalities, frozenset())


if __name__ == "__main__":
    unittest.main()
```

Append to `tests/test_vbent.py` (before the `if __name__` line, if there is one), and add `from src.filtering.municipalities import Municipality` and `from src.scrapers.scope import SearchScope` to its imports, and `vbent_area` to the `vbent` import:

```python
GRID = [
    Municipality("a", "A", 52.0, 4.0, ("A-town",)),
    Municipality("b", "B", 52.0, 4.2, ("B-town",)),
    Municipality("c", "C", 52.0, 4.4, ("C-town",)),
]


class VBentAreaTests(unittest.TestCase):
    def test_no_scope_keeps_today_default(self) -> None:
        self.assertEqual(vbent_area(None), ("Delft", 15))
        self.assertEqual(vbent_area(SearchScope()), ("Delft", 15))

    def test_single_municipality_uses_minimum_radius(self) -> None:
        self.assertEqual(vbent_area(SearchScope(frozenset({"delft"}))), ("Delft", 10))

    def test_spread_picks_middle_center(self) -> None:
        # B is ~13.7 km from A and C; +5 km margin rounds up to 19.
        scope = SearchScope(frozenset({"a", "c"}))
        self.assertEqual(vbent_area(scope, GRID), ("B-town", 19))

    def test_radius_is_capped(self) -> None:
        far = [
            Municipality("w", "W", 52.0, 3.0, ("W-town",)),
            Municipality("m", "M", 52.0, 4.0, ("M-town",)),
            Municipality("e", "E", 52.0, 5.0, ("E-town",)),
        ]
        self.assertEqual(vbent_area(SearchScope(frozenset({"w", "e"})), far), ("M-town", 50))

    def test_unknown_keys_fall_back_to_default(self) -> None:
        self.assertEqual(vbent_area(SearchScope(frozenset({"atlantis"})), GRID), ("Delft", 15))

    def test_search_uses_area_from_scope(self) -> None:
        scraper = VBentScraper(Mock(spec=Settings), SearchScope(frozenset({"leiden"})))
        with patch.object(scraper, "fetch_json", return_value={"houses": [], "pageCount": 0}) as fetch:
            scraper.search(max_retries=0)
        cookie = fetch.call_args.kwargs["headers"]["Cookie"]
        filters = json.loads(unquote(cookie.split("filter_properties=", 1)[1]))
        self.assertEqual((filters["city"], filters["radius"]), ("Leiden", 10))
```

In `tests/test_runner.py`, change both `__init__(self, settings) -> None:` methods in `returning` and `raising` to `__init__(self, settings, scope=None) -> None:`, add `from src.scrapers.scope import SearchScope` to the imports, change `run_once` to accept and pass a scope:

```python
    def run_once(self, selected=None, scope=None):
        return run_all_sources(
            settings=self.settings,
            store=self.store,
            source_factories=self.factories,
            registry_file=self.registry,
            notifier=self.notifier,
            selected_sources=selected,
            scope=scope,
        )
```

and add this test:

```python
    def test_scope_is_passed_to_scrapers(self) -> None:
        seen = []

        class _Scraper:
            def __init__(self, settings, scope=None) -> None:
                seen.append(scope)

            def search(self, max_retries: int = 0):
                return []

        self.factories = {"alpha": _Scraper, "beta": _Scraper}
        scope = SearchScope(frozenset({"delft"}))
        self.run_once(scope=scope)
        self.assertEqual(seen, [scope, scope])
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_scope.py tests/test_vbent.py tests/test_runner.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'src.scrapers.scope'`)

- [ ] **Step 4: Implement scope plumbing and the Vb&t area**

Create `src/scrapers/scope.py`:

```python
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from src.models.profile import Profile


@dataclass(frozen=True)
class SearchScope:
    """Where the active profiles are looking; scrapers with a location filter use it."""

    municipalities: frozenset[str] = frozenset()

    @classmethod
    def from_profiles(cls, profiles: Iterable[Profile]) -> SearchScope:
        keys: set[str] = set()
        for profile in profiles:
            if not profile.paused:
                keys.update(profile.municipalities)
        return cls(frozenset(keys))
```

In `src/scrapers/base.py`, add `from src.scrapers.scope import SearchScope` and change `__init__` to:

```python
    def __init__(self, settings: Settings, scope: SearchScope | None = None) -> None:
        self.settings = settings
        self.scope = scope or SearchScope()
```

In `src/scrapers/runner.py`, add `from src.scrapers.scope import SearchScope`, add the parameter `scope: SearchScope | None = None,` after `selected_sources` in `run_all_sources`, and change `scraper = factory(settings)` to `scraper = factory(settings, scope)`.

In `src/scrapers/sites/vbent.py` (**indent with tabs**), add `from collections.abc import Sequence` and these imports:

```python
from src.filtering.municipalities import Municipality, all_municipalities
from src.scrapers.scope import SearchScope
```

Add these module-level definitions above `class VBentScraper`:

```python
DEFAULT_AREA = ("Delft", 15)
AREA_MARGIN_KM = 5
MIN_RADIUS_KM = 10
MAX_RADIUS_KM = 50
EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
	phi1, phi2 = math.radians(lat1), math.radians(lat2)
	d_phi = math.radians(lat2 - lat1)
	d_lambda = math.radians(lon2 - lon1)
	a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
	return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def vbent_area(
	scope: SearchScope | None, municipalities: Sequence[Municipality] | None = None
) -> tuple[str, int]:
	"""Smallest Vb&t search circle, centered on a municipality, covering every selected one."""
	pool = list(municipalities) if municipalities is not None else all_municipalities()
	keys = scope.municipalities if scope is not None else frozenset()
	selected = [item for item in pool if item.key in keys]
	if not selected:
		return DEFAULT_AREA
	best_center, best_needed = None, math.inf
	for center in sorted(pool, key=lambda item: item.key):
		needed = max(haversine_km(center.lat, center.lon, item.lat, item.lon) for item in selected)
		if needed < best_needed:
			best_center, best_needed = center, needed
	radius = min(max(math.ceil(best_needed + AREA_MARGIN_KM), MIN_RADIUS_KM), MAX_RADIUS_KM)
	return best_center.main_place, radius
```

In `VBentScraper.search`, replace the first line (`filters = quote(json.dumps(self.filter_template, ...`) with:

```python
		city, radius = vbent_area(self.scope)
		search_filters = dict(self.filter_template, city=city, radius=radius)
		filters = quote(json.dumps(search_filters, separators=(",", ":")), safe="")
```

The existing `test_cookie_pagination_and_deduplication` still passes, because an empty scope gives `("Delft", 15)`, which equals `filter_template`.

- [ ] **Step 5: Drop the Vesteda price bounds**

In `src/scrapers/sites/vesteda.py`, delete the `"priceFrom": 500,` and `"priceTo": 1200,` lines from `payload_template`, and add this comment above `payload_template`:

```python
    # The API ignores the location fields (results are national) but honours price
    # bounds, so none are sent: matching happens per profile after scraping.
```

Create `tests/test_vesteda.py`:

```python
import unittest

from src.scrapers.sites.vesteda import VestedaScraper


class VestedaPayloadTests(unittest.TestCase):
    def test_no_price_bounds(self) -> None:
        self.assertNotIn("priceFrom", VestedaScraper.payload_template)
        self.assertNotIn("priceTo", VestedaScraper.payload_template)
```

- [ ] **Step 6: Run the suite**

Run: `./.venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 7: Live check of both sources**

Run: `./.venv/Scripts/python.exe -m src.main --once --sources vbent,vesteda`
Expected: two `ok` source runs in the log; Vesteda reports about 96 listings. With no profiles yet, Vb&t uses Delft/15 (about 8 pages).

- [ ] **Step 8: Commit**

```bash
git add src/scrapers tests/test_scope.py tests/test_vbent.py tests/test_runner.py tests/test_vesteda.py src/config/municipalities.yaml
git commit -m "Derive the Vb&t search area from profiles and drop Vesteda price bounds"
```

---

### Task 7: Bot core: cycle with dispatch, seeding, per-profile cleanup

**Files:**
- Create: `src/bot/seeding.py`
- Modify: `src/bot/client.py` (whole file below)
- Modify: `src/bot/checks.py` (`validate_bot_settings`)
- Modify: `src/config.py` (add `huur_category_name`), `tests/helpers.py` (add it to `make_settings`)
- Test: `tests/test_seeding.py` (new), `tests/test_bot_client.py`, `tests/test_bot_helpers.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: store profile methods (Task 2), `dispatch`, `stale_window`, `DispatchSummary` (Task 5), `SearchScope` (Task 6), `DiscordNotifier(loop, resolve_channel, ops_channel)` (Task 4), `municipality_for_place` (Task 1)
- Produces:
  - `Settings.huur_category_name: str` (env `HUUR_CATEGORY_NAME`, default `"Huur"`)
  - `seed_profile_from_settings(store: SQLiteStore, settings: Settings) -> Profile | None`
  - On `HuurBot`: `resolve_channel(channel_id: int) -> Awaitable[channel | None]` (renamed from `_resolve_channel`), `stale_after: dt.timedelta`, `run_cycle(selected_sources=None) -> RunSummary | None`, `run_dispatch(profile_id: int) -> DispatchSummary | None` (waits for the lock), `schedule_dispatch(profile_id: int) -> None`, `clear_channel(channel, before) -> int`
  - `HuurBot.alert_channel` is **removed**

- [ ] **Step 1: Write the failing seeding test**

Create `tests/test_seeding.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.bot.seeding import seed_profile_from_settings
from src.storage.sqlite_store import SQLiteStore
from tests.helpers import make_listing, make_settings


class SeedingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = SQLiteStore(Path(self.tmp.name) / "test.db")
        self.settings = make_settings(
            discord_alert_channel_id=100,
            discord_mention_user_ids=[42, 43],
            max_rent_eur=1100,
            allowed_cities=["Den Haag", "'s-Gravenhage", "Delft", "Voorburg", "Atlantis"],
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_seeds_once_from_env_and_marks_existing_listings_sent(self) -> None:
        self.store.upsert_listing(make_listing(source_listing_id="1"))
        self.store.upsert_listing(make_listing(source_listing_id="2"))
        with self.assertLogs("src.bot.seeding", level="WARNING") as captured:
            profile = seed_profile_from_settings(self.store, self.settings)
        self.assertIn("Atlantis", "\n".join(captured.output))
        self.assertEqual(profile.owner_user_id, 42)
        self.assertEqual(profile.channel_id, 100)
        self.assertEqual(profile.max_rent_eur, 1100)
        self.assertEqual(profile.municipalities, ("den-haag", "delft", "leidschendam-voorburg"))
        self.assertEqual(len(self.store.get_alerted_keys(profile.id)), 2)

        self.assertIsNone(seed_profile_from_settings(self.store, self.settings))
        self.assertEqual(len(self.store.list_profiles()), 1)

    def test_no_seed_without_channel_or_owner(self) -> None:
        for overrides in ({"discord_alert_channel_id": None}, {"discord_mention_user_ids": []}):
            with self.subTest(overrides=overrides):
                settings = make_settings(**{**dict(discord_alert_channel_id=100, discord_mention_user_ids=[42]), **overrides})
                self.assertIsNone(seed_profile_from_settings(self.store, settings))
        self.assertEqual(self.store.list_profiles(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_seeding.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bot.seeding'`

- [ ] **Step 3: Implement seeding and the setting**

Create `src/bot/seeding.py`:

```python
from __future__ import annotations

import datetime as dt
import logging

from src.config import Settings
from src.filtering.municipalities import municipality_for_place
from src.models.profile import Profile
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)


def seed_profile_from_settings(store: SQLiteStore, settings: Settings) -> Profile | None:
    """Turn the single .env profile into the first stored profile, once.

    Everything already stored was alerted under the old single-profile setup, so it
    is marked as sent to avoid posting it all again.
    """
    if store.list_profiles():
        return None
    channel_id = settings.discord_alert_channel_id
    if channel_id is None or not settings.discord_mention_user_ids:
        return None

    municipalities: list[str] = []
    for city in settings.allowed_cities:
        key = municipality_for_place(city)
        if key is None:
            logger.warning("ALLOWED_CITIES entry %r is not a known Zuid-Holland place; skipped", city)
        elif key not in municipalities:
            municipalities.append(key)

    profile = store.create_profile(
        owner_user_id=settings.discord_mention_user_ids[0],
        channel_id=channel_id,
        max_rent_eur=settings.max_rent_eur,
        min_size_m2=settings.min_size_m2,
        preferred_bedrooms=settings.preferred_bedrooms,
        allow_close_match=settings.allow_close_match,
        municipalities=tuple(municipalities),
    )
    marked = store.mark_all_listings_sent(profile.id, dt.datetime.now(tz=dt.timezone.utc).isoformat())
    logger.info("Seeded profile=%s for user=%s; marked %d listings as sent", profile.id, profile.owner_user_id, marked)
    return profile
```

In `src/config.py`, add `huur_category_name: str` as the last field of `Settings` and `huur_category_name=os.getenv("HUUR_CATEGORY_NAME", "").strip() or "Huur",` as the last argument in `load_settings`. In `tests/helpers.py` `make_settings`, add `huur_category_name="Huur",`.

In `tests/test_config.py`, add to `DiscordSettingsTests`:

```python
    def test_category_name_default_and_override(self) -> None:
        self.assertEqual(_load({}).huur_category_name, "Huur")
        self.assertEqual(_load({"HUUR_CATEGORY_NAME": " Woningen "}).huur_category_name, "Woningen")
```

In `src/bot/checks.py` `validate_bot_settings`, delete the two `DISCORD_ALERT_CHANNEL_ID` lines. In `tests/test_bot_helpers.py` `ValidateSettingsTests`, change the tuple in `test_all_missing_reports_each` to `("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "DISCORD_CONTROL_USER_IDS")`, add `self.assertNotIn("DISCORD_ALERT_CHANNEL_ID", joined)`, and in `test_valid_settings` remove `discord_alert_channel_id=2,`.

Run: `./.venv/Scripts/python.exe -m pytest tests/test_seeding.py tests/test_config.py tests/test_bot_helpers.py -q`
Expected: PASS

- [ ] **Step 4: Write the failing bot client tests**

In `tests/test_bot_client.py`:
- Add `from src.notify.dispatch import DispatchSummary` and `from src.scrapers.scope import SearchScope` to the imports, and add `make_profile` to the `tests.helpers` import.
- In `HuurBotCycleTests`, replace `test_run_cycle_returns_summary_and_records_time` with the version below, and add the two new tests:

```python
    async def test_run_cycle_scrapes_then_dispatches(self) -> None:
        active = self.store.create_profile(
            owner_user_id=42, channel_id=100, max_rent_eur=1000, min_size_m2=40,
            preferred_bedrooms=2, allow_close_match=True, municipalities=("delft",),
        )
        paused = self.store.create_profile(
            owner_user_id=43, channel_id=101, max_rent_eur=1000, min_size_m2=40,
            preferred_bedrooms=2, allow_close_match=True, municipalities=("leiden",),
        )
        self.store.set_profile_paused(paused.id, True)
        summary = RunSummary(results=[SourceResult(name="vbent", status="ok")])
        with patch("src.bot.client.run_all_sources", return_value=summary) as runner, \
                patch("src.bot.client.dispatch", return_value=DispatchSummary(sent=3)) as dispatcher:
            result = await self.bot.run_cycle({"vbent"})
        self.assertIs(result, summary)
        self.assertEqual(result.alerted, 3)
        self.assertIsNotNone(self.bot.last_cycle_at)
        kwargs = runner.call_args.kwargs
        self.assertIs(kwargs["notifier"], self.notifier)
        self.assertEqual(kwargs["selected_sources"], {"vbent"})
        self.assertEqual(kwargs["scope"], SearchScope(frozenset({"delft"})))
        self.assertEqual(dispatcher.call_args.kwargs["profiles"], [active])
        self.assertEqual(dispatcher.call_args.kwargs["stale_after"], dt.timedelta(minutes=30))

    async def test_run_dispatch_waits_for_running_cycle(self) -> None:
        profile = self.store.create_profile(
            owner_user_id=42, channel_id=100, max_rent_eur=1000, min_size_m2=40,
            preferred_bedrooms=2, allow_close_match=True, municipalities=("delft",),
        )
        release = threading.Event()
        started = threading.Event()

        def slow_run(**kwargs):
            started.set()
            release.wait(timeout=5)
            return RunSummary()

        with patch("src.bot.client.run_all_sources", side_effect=slow_run), \
                patch("src.bot.client.dispatch", return_value=DispatchSummary()) as dispatcher:
            cycle = asyncio.create_task(self.bot.run_cycle())
            await asyncio.to_thread(started.wait, 5)
            single = asyncio.create_task(self.bot.run_dispatch(profile.id))
            await asyncio.sleep(0.05)
            self.assertFalse(single.done())
            release.set()
            await cycle
            self.assertIsInstance(await single, DispatchSummary)
        self.assertEqual(dispatcher.call_args.kwargs["profiles"], [profile])

    async def test_run_dispatch_skips_paused_or_missing_profile(self) -> None:
        profile = self.store.create_profile(
            owner_user_id=42, channel_id=100, max_rent_eur=1000, min_size_m2=40,
            preferred_bedrooms=2, allow_close_match=True, municipalities=("delft",),
        )
        self.store.set_profile_paused(profile.id, True)
        with patch("src.bot.client.dispatch") as dispatcher:
            self.assertIsNone(await self.bot.run_dispatch(profile.id))
            self.assertIsNone(await self.bot.run_dispatch(999))
        dispatcher.assert_not_called()
```

- In every other `HuurBotCycleTests` test that patches `src.bot.client.run_all_sources`, also patch dispatch. Wrap the existing `with patch("src.bot.client.run_all_sources", ...)` in an outer `with patch("src.bot.client.dispatch", return_value=DispatchSummary()):`.
- Replace the whole `HuurBotClearTests` class with:

```python
class HuurBotClearTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = SQLiteStore(Path(self.tmp.name) / "bot.db")
        settings = make_settings(discord_guild_id=1, listings_clear_weekday=0, listings_clear_hour_utc=4)
        self.bot = HuurBot(settings, self.store)
        self.notifier = RecordingNotifier()
        self.bot.notifier = self.notifier
        self.channels = {}
        for owner, channel_id in ((42, 100), (43, 101)):
            self.store.create_profile(
                owner_user_id=owner, channel_id=channel_id, max_rent_eur=1000, min_size_m2=40,
                preferred_bedrooms=2, allow_close_match=True, municipalities=("delft",),
            )
            channel = MagicMock()
            channel.id = channel_id
            channel.purge = AsyncMock(return_value=[object(), object()])
            channel.permissions_for.return_value = SimpleNamespace(manage_messages=False)
            self.channels[channel_id] = channel
        self.channel = self.channels[100]
        self.bot.resolve_channel = AsyncMock(side_effect=lambda channel_id: self.channels.get(channel_id))

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()

    async def test_first_start_records_time_without_clearing(self) -> None:
        await self.bot.clear_tick(utc(5, 12))
        self.channel.purge.assert_not_called()
        self.assertEqual(self.store.get_state(LISTINGS_CLEARED_AT_KEY), utc(5, 12).isoformat())

    async def test_clears_every_profile_channel_once_after_slot_passes(self) -> None:
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(4, 12).isoformat())
        await self.bot.clear_tick(utc(5, 3))
        self.channel.purge.assert_not_called()

        await self.bot.clear_tick(utc(5, 4))
        for channel in self.channels.values():
            channel.purge.assert_awaited_once()
        kwargs = self.channel.purge.call_args.kwargs
        self.assertEqual(kwargs["before"], utc(5, 4))
        self.assertFalse(kwargs["bulk"])

        await self.bot.clear_tick(utc(5, 5))
        self.channel.purge.assert_awaited_once()

    async def test_catches_up_on_missed_slot(self) -> None:
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await self.bot.clear_tick(utc(7, 9))
        self.channel.purge.assert_awaited_once()

    async def test_only_own_unpinned_messages_are_deleted(self) -> None:
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await self.bot.clear_tick(utc(5, 12))
        check = self.channel.purge.call_args.kwargs["check"]
        me = self.bot.user
        self.assertTrue(check(SimpleNamespace(author=me, pinned=False)))
        self.assertFalse(check(SimpleNamespace(author=me, pinned=True)))
        self.assertFalse(check(SimpleNamespace(author=object(), pinned=False)))

    async def test_uses_bulk_delete_with_manage_messages(self) -> None:
        self.channel.permissions_for.return_value = SimpleNamespace(manage_messages=True)
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await self.bot.clear_tick(utc(5, 12))
        self.assertTrue(self.channel.purge.call_args.kwargs["bulk"])

    async def test_one_failing_channel_does_not_stop_others_and_is_reported_once(self) -> None:
        self.channel.purge.side_effect = RuntimeError("Missing Access")
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        with self.assertLogs("src.bot.client", level="ERROR"):
            await self.bot.clear_tick(utc(5, 12))
        self.channels[101].purge.assert_awaited_once()
        await self.bot.clear_tick(utc(5, 13))
        self.assertEqual(self.notifier.ops, ["[CLEAR_ERROR] <#100>: Missing Access"])

    async def test_missing_channel_is_skipped(self) -> None:
        del self.channels[100]
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await self.bot.clear_tick(utc(5, 12))
        self.channels[101].purge.assert_awaited_once()
        self.assertEqual(self.notifier.ops, [])

    async def test_disabled_does_nothing(self) -> None:
        bot = HuurBot(make_settings(discord_guild_id=1), self.store)
        bot.resolve_channel = self.bot.resolve_channel
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, utc(1, 12).isoformat())
        await bot.clear_tick(utc(5, 12))
        self.channel.purge.assert_not_called()
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_bot_client.py -q`
Expected: FAIL (for example `AttributeError: <module 'src.bot.client'> does not have the attribute 'dispatch'`)

- [ ] **Step 6: Rewrite the client**

Replace `src/bot/client.py` with:

```python
from __future__ import annotations

import asyncio
import datetime as dt
import logging

import discord
from discord.ext import tasks

from src.bot.checks import last_scheduled_clear
from src.bot.seeding import seed_profile_from_settings
from src.config import Settings
from src.notify.base import Notifier
from src.notify.discord_notifier import DiscordNotifier
from src.notify.dispatch import DispatchSummary, dispatch, stale_window
from src.scrapers.factories import REGISTRY_FILE, SOURCE_FACTORIES
from src.scrapers.runner import RunSummary, run_all_sources
from src.scrapers.scope import SearchScope
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)

PAUSED_KEY = "paused"
LISTINGS_CLEARED_AT_KEY = "listings_cleared_at"
CLEAR_CHECK_MINUTES = 15
FIRST_RUN_DELAY_SECONDS = 30


def _utc_now() -> dt.datetime:
    return dt.datetime.now(tz=dt.timezone.utc)


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
        # Keeps fire-and-forget dispatch tasks referenced until they finish.
        self._background_tasks: set[asyncio.Task] = set()
        self.scrape_loop.change_interval(minutes=settings.scrape_interval_minutes)

    @property
    def paused(self) -> bool:
        return self.store.get_state(PAUSED_KEY, "0") == "1"

    def set_paused(self, paused: bool) -> None:
        self.store.set_state(PAUSED_KEY, "1" if paused else "0")

    @property
    def cycle_running(self) -> bool:
        return self._cycle_lock.locked()

    @property
    def stale_after(self) -> dt.timedelta:
        return stale_window(self.settings.scrape_interval_minutes)

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

        ops_channel = await self._resolve_ops_channel()
        self.notifier = DiscordNotifier(asyncio.get_running_loop(), self.resolve_channel, ops_channel)
        await asyncio.to_thread(seed_profile_from_settings, self.store, self.settings)

        self.scrape_loop.start()
        logger.info(
            "Scheduler started (every %d min, paused=%s)",
            self.settings.scrape_interval_minutes,
            self.paused,
        )
        if self.settings.listings_clear_weekday is not None:
            self.clear_loop.start()

    async def _resolve_ops_channel(self):
        # Ops messages fall back to the old alert channel, then to the log only.
        for channel_id in (self.settings.discord_ops_channel_id, self.settings.discord_alert_channel_id):
            if channel_id is None:
                continue
            channel = await self.resolve_channel(channel_id)
            if channel is not None:
                return channel
            logger.warning("Ops channel candidate %s not found or not accessible", channel_id)
        logger.warning("No ops channel available; ops messages only go to the log")
        return None

    async def resolve_channel(self, channel_id: int):
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
            self.last_cycle_at = _utc_now()
            try:
                profiles = await asyncio.to_thread(self.store.list_profiles, active_only=True)
                summary = await asyncio.to_thread(
                    run_all_sources,
                    settings=self.settings,
                    store=self.store,
                    source_factories=SOURCE_FACTORIES,
                    registry_file=REGISTRY_FILE,
                    notifier=self.notifier,
                    selected_sources=selected_sources,
                    scope=SearchScope.from_profiles(profiles),
                )
                # Re-read so edits saved during the scrape are used.
                profiles = await asyncio.to_thread(self.store.list_profiles, active_only=True)
                result = await asyncio.to_thread(
                    dispatch,
                    store=self.store,
                    profiles=profiles,
                    notifier=self.notifier,
                    now=_utc_now(),
                    stale_after=self.stale_after,
                )
            except Exception as error:  # noqa: BLE001
                logger.exception("Scrape cycle failed")
                # notify_ops blocks on the event loop, so it must run off-loop.
                await asyncio.to_thread(self.notifier.notify_ops, f"[CYCLE_ERROR] {error}")
                return None
            summary.alerted = result.sent
            logger.info("Cycle finished alerted=%d", summary.alerted)
            return summary

    async def run_dispatch(self, profile_id: int) -> DispatchSummary | None:
        # Waits for a running cycle instead of failing: the user is waiting on their own edit.
        async with self._cycle_lock:
            try:
                profile = await asyncio.to_thread(self.store.get_profile_by_id, profile_id)
                if profile is None or profile.paused or self.notifier is None:
                    return None
                return await asyncio.to_thread(
                    dispatch,
                    store=self.store,
                    profiles=[profile],
                    notifier=self.notifier,
                    now=_utc_now(),
                    stale_after=self.stale_after,
                )
            except Exception as error:  # noqa: BLE001
                logger.exception("Dispatch for profile=%s failed", profile_id)
                if self.notifier is not None:
                    await asyncio.to_thread(self.notifier.notify_ops, f"[DISPATCH_ERROR] {error}")
                return None

    def schedule_dispatch(self, profile_id: int) -> None:
        task = asyncio.create_task(self.run_dispatch(profile_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def scheduled_tick(self) -> None:
        # Any exception escaping here stops discord.py's tasks.Loop for good, so
        # everything is caught and reported instead.
        try:
            if self.paused:
                logger.info("Scheduler paused; skipping cycle")
                return
            await self.run_cycle()
        except CycleBusyError:
            logger.info("Previous cycle still running; skipping scheduled tick")
        except Exception as error:  # noqa: BLE001
            logger.exception("Scheduled tick failed")
            if self.notifier is not None:
                await asyncio.to_thread(self.notifier.notify_ops, f"[CYCLE_ERROR] {error}")

    @tasks.loop(minutes=10)
    async def scrape_loop(self) -> None:
        # The interval is replaced in __init__ with SCRAPE_INTERVAL_MINUTES.
        await self.scheduled_tick()

    @scrape_loop.before_loop
    async def _before_scrape_loop(self) -> None:
        await self.wait_until_ready()
        await asyncio.sleep(FIRST_RUN_DELAY_SECONDS)

    async def clear_tick(self, now: dt.datetime | None = None) -> None:
        # Same rule as scheduled_tick: nothing may escape, or the loop stops for good.
        try:
            await self._clear_if_due(now or _utc_now())
        except Exception as error:  # noqa: BLE001
            logger.exception("Clearing the listings channels failed")
            if self.notifier is not None:
                await asyncio.to_thread(self.notifier.notify_ops, f"[CLEAR_ERROR] {error}")

    async def _clear_if_due(self, now: dt.datetime) -> None:
        weekday = self.settings.listings_clear_weekday
        if weekday is None:
            return
        last_cleared = self.store.get_state(LISTINGS_CLEARED_AT_KEY)
        if last_cleared is None:
            # First start with this feature: keep the existing history and begin
            # counting from the next slot instead of wiping the channels right away.
            self.store.set_state(LISTINGS_CLEARED_AT_KEY, now.isoformat())
            return
        due = last_scheduled_clear(now, weekday, self.settings.listings_clear_hour_utc)
        if dt.datetime.fromisoformat(last_cleared) >= due:
            return
        # Recorded before purging so a permission error is reported once, not every tick.
        self.store.set_state(LISTINGS_CLEARED_AT_KEY, now.isoformat())

        deleted = 0
        failures: list[str] = []
        for profile in await asyncio.to_thread(self.store.list_profiles):
            channel = await self.resolve_channel(profile.channel_id)
            if channel is None:
                logger.warning("Channel %s of profile=%s not found; not cleared", profile.channel_id, profile.id)
                continue
            try:
                deleted += await self.clear_channel(channel, before=now)
            except Exception as error:  # noqa: BLE001
                logger.exception("Clearing channel %s failed", profile.channel_id)
                failures.append(f"<#{profile.channel_id}>: {error}")
        logger.info("Cleared %d messages from profile channels", deleted)
        if failures and self.notifier is not None:
            await asyncio.to_thread(self.notifier.notify_ops, "[CLEAR_ERROR] " + "; ".join(failures))

    async def clear_channel(self, channel, before: dt.datetime) -> int:
        # Bulk delete needs Manage Messages; without it the bot can still delete
        # its own messages one by one (slower, but fine for a week's worth).
        me = getattr(channel.guild, "me", None)
        bulk = me is not None and channel.permissions_for(me).manage_messages
        deleted = await channel.purge(
            limit=None,
            before=before,
            check=lambda message: message.author == self.user and not message.pinned,
            bulk=bulk,
            reason="Weekly listings channel cleanup",
        )
        return len(deleted)

    @tasks.loop(minutes=CLEAR_CHECK_MINUTES)
    async def clear_loop(self) -> None:
        # Polls instead of firing at a fixed time, so a slot missed while the bot
        # was down is caught up after the next start.
        await self.clear_tick()

    @clear_loop.before_loop
    async def _before_clear_loop(self) -> None:
        await self.wait_until_ready()
```

Note: `test_uses_bulk_delete_with_manage_messages` works because `MagicMock().guild.me` is a truthy mock.

- [ ] **Step 7: Run the suite**

Run: `./.venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/bot/seeding.py src/bot/client.py src/bot/checks.py src/config.py tests
git commit -m "Dispatch per profile after each cycle and seed the first profile from .env"
```

---

### Task 8: Pure bot helpers for the profile UI

**Files:**
- Modify: `src/bot/checks.py` (add helpers)
- Modify: `src/bot/embeds.py` (add builders; change `build_status_embed`, `build_listings_embed`; remove `build_profile_embed`)
- Test: `tests/test_bot_helpers.py`

**Interfaces:**
- Consumes: `Profile` (Task 2), `Municipality`, `get_municipality`, `all_municipalities` (Task 1)
- Produces, in `src/bot/checks.py`:
  - `validate_profile_numbers(max_rent: str, min_size: str, bedrooms: str) -> tuple[dict[str, int] | None, list[str]]`. Dict keys: `max_rent_eur`, `min_size_m2`, `preferred_bedrooms`.
  - `sanitize_channel_name(display_name: str) -> str` (`huur-<slug>`)
  - `unique_channel_name(base: str, existing: Iterable[str]) -> str`
  - `archived_channel_name(name: str) -> str`
  - `city_select_pages(municipalities: Iterable[Municipality], page_size: int = 25) -> list[list[Municipality]]`
- Produces, in `src/bot/embeds.py`:
  - `municipality_names(keys: Iterable[str]) -> list[str]`
  - `build_profile_panel_embed(profile: Profile) -> discord.Embed`
  - `build_no_profile_embed() -> discord.Embed`
  - `build_profiles_list_embed(profiles: Sequence[Profile]) -> discord.Embed`
  - `build_listings_embed(rows, empty_text: str = "No listings stored yet.")`
  - `build_status_embed(paused, cycle_running, next_run, last_cycle_at, source_rows, profile_counts: tuple[int, int] | None = None)`

- [ ] **Step 1: Write the failing tests**

In `tests/test_bot_helpers.py`:
- Change the imports to:

```python
from src.bot.checks import (
    archived_channel_name,
    city_select_pages,
    is_control_user,
    last_scheduled_clear,
    parse_sources,
    sanitize_channel_name,
    unique_channel_name,
    validate_bot_settings,
    validate_profile_numbers,
)
from src.bot.embeds import (
    build_listings_embed,
    build_no_profile_embed,
    build_profile_panel_embed,
    build_profiles_list_embed,
    build_status_embed,
    build_summary_embed,
)
from src.filtering.municipalities import Municipality, all_municipalities
from src.scrapers.runner import RunSummary, SourceResult
from tests.helpers import make_profile, make_settings
```

- Delete the `ProfileEmbedTests` class and add:

```python
class ProfileNumberValidationTests(unittest.TestCase):
    def test_valid_with_spaces_and_euro_sign(self) -> None:
        values, errors = validate_profile_numbers(" €1100 ", "45", "2")
        self.assertEqual(errors, [])
        self.assertEqual(values, {"max_rent_eur": 1100, "min_size_m2": 45, "preferred_bedrooms": 2})

    def test_every_problem_is_listed(self) -> None:
        values, errors = validate_profile_numbers("abc", "600", "1.5")
        self.assertIsNone(values)
        self.assertEqual(len(errors), 3)
        self.assertIn("Max rent must be a whole number", errors[0])
        self.assertIn("Min size must be between 0 and 500", errors[1])
        self.assertIn("Preferred bedrooms must be a whole number", errors[2])

    def test_bounds(self) -> None:
        self.assertEqual(validate_profile_numbers("100", "0", "0")[1], [])
        self.assertEqual(validate_profile_numbers("10000", "500", "10")[1], [])
        self.assertIn("between 100 and 10000", validate_profile_numbers("99", "40", "2")[1][0])


class ChannelNameTests(unittest.TestCase):
    def test_sanitize(self) -> None:
        self.assertEqual(sanitize_channel_name("Alice"), "huur-alice")
        self.assertEqual(sanitize_channel_name("Big Bob_99!"), "huur-big-bob-99")
        self.assertEqual(sanitize_channel_name("✨✨"), "huur-user")
        self.assertLessEqual(len(sanitize_channel_name("x" * 200)), 90)

    def test_unique(self) -> None:
        self.assertEqual(unique_channel_name("huur-alice", ["general"]), "huur-alice")
        self.assertEqual(unique_channel_name("huur-alice", ["huur-alice", "huur-alice-2"]), "huur-alice-3")

    def test_archived(self) -> None:
        self.assertEqual(archived_channel_name("huur-alice"), "archived-huur-alice")
        self.assertEqual(len(archived_channel_name("x" * 100)), 100)


class CitySelectPagesTests(unittest.TestCase):
    def test_real_data_splits_into_two_pages_of_25(self) -> None:
        pages = city_select_pages(all_municipalities())
        self.assertEqual([len(page) for page in pages], [25, 25])
        self.assertEqual(pages[0][0].name, "Alblasserdam")
        self.assertEqual(pages[1][-1].name, "Zwijndrecht")

    def test_even_split_and_limit(self) -> None:
        items = [Municipality(f"k{i:02}", f"N{i:02}", 52.0, 4.0, ()) for i in range(51)]
        self.assertEqual([len(page) for page in city_select_pages(items)], [17, 17, 17])
        self.assertEqual(city_select_pages([]), [])
        too_many = [Municipality(f"k{i:03}", f"N{i:03}", 52.0, 4.0, ()) for i in range(101)]
        with self.assertRaises(ValueError):
            city_select_pages(too_many)


class ProfileEmbedTests(unittest.TestCase):
    def test_panel_fields(self) -> None:
        profile = make_profile(municipalities=("leidschendam-voorburg", "delft"), channel_id=100)
        fields = {f.name: f.value for f in build_profile_panel_embed(profile).fields}
        self.assertEqual(fields["Max rent"], "€1000")
        self.assertEqual(fields["Min size"], "40 m²")
        self.assertEqual(fields["Bedrooms"], "2 (preferred)")
        self.assertIn("on", fields["Close match"])
        self.assertEqual(fields["Cities"], "Delft, Leidschendam-Voorburg")
        self.assertEqual(fields["Alerts"], "<#100> (active)")

    def test_panel_without_cities_and_paused(self) -> None:
        fields = {f.name: f.value for f in build_profile_panel_embed(make_profile(municipalities=(), paused=True)).fields}
        self.assertIn("Press **Cities**", fields["Cities"])
        self.assertIn("paused", fields["Alerts"])

    def test_no_profile_embed(self) -> None:
        self.assertIn("Create", build_no_profile_embed().description)

    def test_profiles_list(self) -> None:
        embed = build_profiles_list_embed(
            [make_profile(owner_user_id=42, channel_id=100), make_profile(id=2, owner_user_id=43, channel_id=101, paused=True)]
        )
        self.assertIn("<@42> → <#100>", embed.description)
        self.assertIn("paused", embed.description)
        self.assertEqual(build_profiles_list_embed([]).description, "No profiles yet.")
```

- In `StatusEmbedTests`, add:

```python
    def test_profile_counts(self) -> None:
        fields = {f.name: f.value for f in build_status_embed(False, False, None, None, [], (2, 3)).fields}
        self.assertEqual(fields["Profiles"], "2 active / 3 total")
```

- In `ListingsEmbedTests`, add:

```python
    def test_custom_empty_text(self) -> None:
        self.assertEqual(build_listings_embed([], empty_text="Nothing").description, "Nothing")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_bot_helpers.py -q`
Expected: FAIL with `ImportError: cannot import name 'archived_channel_name'`

- [ ] **Step 3: Implement the checks helpers**

Add to `src/bot/checks.py` (add `import math`, `import re` and `from src.filtering.municipalities import Municipality` to the imports):

```python
PROFILE_NUMBER_FIELDS = (
    ("max_rent_eur", "Max rent", 100, 10000),
    ("min_size_m2", "Min size", 0, 500),
    ("preferred_bedrooms", "Preferred bedrooms", 0, 10),
)
MAX_CITY_PAGES = 4  # A view has 5 rows; one is needed for the buttons.


def validate_profile_numbers(
    max_rent: str, min_size: str, bedrooms: str
) -> tuple[dict[str, int] | None, list[str]]:
    values: dict[str, int] = {}
    errors: list[str] = []
    for (field, label, low, high), text in zip(PROFILE_NUMBER_FIELDS, (max_rent, min_size, bedrooms)):
        cleaned = text.strip().lstrip("€").strip()
        if not cleaned.isdigit():
            errors.append(f"{label} must be a whole number, got {text.strip()!r}")
            continue
        number = int(cleaned)
        if not low <= number <= high:
            errors.append(f"{label} must be between {low} and {high}, got {number}")
            continue
        values[field] = number
    return (None, errors) if errors else (values, [])


def sanitize_channel_name(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")
    return f"huur-{slug or 'user'}"[:90]


def unique_channel_name(base: str, existing: Iterable[str]) -> str:
    taken = set(existing)
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"


def archived_channel_name(name: str) -> str:
    return f"archived-{name}"[:100]


def city_select_pages(
    municipalities: Iterable[Municipality], page_size: int = 25
) -> list[list[Municipality]]:
    ordered = sorted(municipalities, key=lambda item: item.name.lower())
    if not ordered:
        return []
    page_count = math.ceil(len(ordered) / page_size)
    if page_count > MAX_CITY_PAGES:
        raise ValueError(f"{len(ordered)} municipalities need more than {MAX_CITY_PAGES} select menus")
    per_page = math.ceil(len(ordered) / page_count)
    return [ordered[index:index + per_page] for index in range(0, len(ordered), per_page)]
```

- [ ] **Step 4: Implement the embed builders**

In `src/bot/embeds.py`:
- Replace `from src.config import Settings` with:

```python
from collections.abc import Iterable

from src.filtering.municipalities import get_municipality
from src.models.profile import Profile
```

- Delete `build_profile_embed`.
- Change `build_listings_embed`'s signature to `def build_listings_embed(rows: Sequence[Mapping], empty_text: str = "No listings stored yet.") -> discord.Embed:` and use `embed.description = empty_text` in the empty branch.
- Change `build_status_embed`'s signature to add `profile_counts: tuple[int, int] | None = None` as the last parameter, and right after the `Next run` field add:

```python
    if profile_counts is not None:
        active, total = profile_counts
        embed.add_field(name="Profiles", value=f"{active} active / {total} total")
```

- Add:

```python
def municipality_names(keys: Iterable[str]) -> list[str]:
    names = []
    for key in keys:
        municipality = get_municipality(key)
        names.append(municipality.name if municipality is not None else key)
    return sorted(names, key=str.lower)


def build_profile_panel_embed(profile: Profile) -> discord.Embed:
    color = discord.Color.orange() if profile.paused else discord.Color.blurple()
    embed = discord.Embed(title="Your search profile", color=color)
    embed.add_field(name="Max rent", value=_money(profile.max_rent_eur))
    embed.add_field(name="Min size", value=_area(profile.min_size_m2))
    embed.add_field(name="Bedrooms", value=f"{profile.preferred_bedrooms} (preferred)")
    close = "on (+10% price, −10% area)" if profile.allow_close_match else "off"
    embed.add_field(name="Close match", value=close)
    cities = ", ".join(municipality_names(profile.municipalities))
    embed.add_field(
        name="Cities",
        value=truncate(cities, FIELD_LIMIT) if cities else "None yet. Press **Cities** to pick some.",
        inline=False,
    )
    state = "paused" if profile.paused else "active"
    embed.add_field(name="Alerts", value=f"<#{profile.channel_id}> ({state})", inline=False)
    return embed


def build_no_profile_embed() -> discord.Embed:
    return discord.Embed(
        title="No search profile yet",
        description=(
            "Create one to get your own private alerts channel. "
            "You can set rent, size and cities right after."
        ),
        color=discord.Color.blurple(),
    )


def build_profiles_list_embed(profiles: Sequence[Profile]) -> discord.Embed:
    embed = discord.Embed(title="Search profiles", color=discord.Color.blurple())
    if not profiles:
        embed.description = "No profiles yet."
        return embed
    lines = []
    for profile in profiles:
        state = "paused" if profile.paused else "active"
        cities = ", ".join(municipality_names(profile.municipalities)) or "no cities"
        lines.append(
            f"<@{profile.owner_user_id}> → <#{profile.channel_id}> · {_money(profile.max_rent_eur)} · "
            f"≥{_area(profile.min_size_m2)} · {state} · {truncate(cities, 200)}"
        )
    embed.description, shown = _fit_lines(lines, LIST_BUDGET)
    if shown < len(lines):
        embed.set_footer(text=f"Showing {shown} of {len(lines)}")
    return embed
```

- [ ] **Step 5: Run the tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_bot_helpers.py -q`
Expected: PASS. (`src/bot/commands.py` still imports `build_profile_embed` until Task 9; pytest doesn't import it, so the suite passes.)

- [ ] **Step 6: Commit**

```bash
git add src/bot/checks.py src/bot/embeds.py tests/test_bot_helpers.py
git commit -m "Add profile panel embeds and validation helpers"
```

---

### Task 9: Discord UI: profile channels, views, commands

**Files:**
- Create: `src/bot/profile_channels.py`
- Create: `src/bot/profile_views.py`
- Modify: `src/bot/commands.py`
- Test: `tests/test_profile_channels.py` (new); the views are tested by hand (Step 6)

**Interfaces:**
- Consumes: `HuurBot.store/settings/notifier/resolve_channel/schedule_dispatch/stale_after` (Task 7), helpers and embeds (Task 8), `recent_matches` (Task 5), `all_municipalities` (Task 1)
- Produces:
  - `create_profile_channel(guild, member, category_name: str, control_role_id: int | None) -> discord.TextChannel`
  - `archive_profile_channel(channel, owner) -> None`
  - Views: `CreateProfileView(bot, owner_id)`, `ProfilePanel(bot, profile)`, `NumbersModal(bot, profile)`, `CitiesView(bot, profile)`, `ConfirmDeleteView(bot, profile)`, and `panel_message(bot, profile) -> dict` (kwargs `embed` and `view`)

- [ ] **Step 1: Write the failing channel test**

Create `tests/test_profile_channels.py`:

```python
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from src.bot.profile_channels import archive_profile_channel, create_profile_channel


class ProfileChannelTests(unittest.IsolatedAsyncioTestCase):
    def make_guild(self, categories=()):
        guild = MagicMock()
        guild.categories = list(categories)
        guild.text_channels = [SimpleNamespace(name="huur-alice")]
        guild.default_role = "everyone"
        guild.me = "bot"
        guild.get_role.return_value = "admins"
        guild.create_category = AsyncMock(return_value=SimpleNamespace(name="Huur"))
        guild.create_text_channel = AsyncMock(return_value="channel")
        return guild

    async def test_creates_private_channel_in_new_category(self) -> None:
        guild = self.make_guild()
        member = MagicMock(display_name="Alice")
        channel = await create_profile_channel(guild, member, "Huur", 7)
        self.assertEqual(channel, "channel")
        guild.create_category.assert_awaited_once()
        args, kwargs = guild.create_text_channel.call_args
        self.assertEqual(args[0], "huur-alice-2")
        self.assertEqual(kwargs["category"].name, "Huur")
        overwrites = kwargs["overwrites"]
        self.assertFalse(overwrites["everyone"].view_channel)
        self.assertTrue(overwrites[member].view_channel)
        self.assertFalse(overwrites[member].send_messages)
        self.assertTrue(overwrites["bot"].send_messages)
        self.assertTrue(overwrites["admins"].view_channel)
        guild.get_role.assert_called_once_with(7)

    async def test_reuses_existing_category_and_skips_missing_role(self) -> None:
        category = MagicMock()
        category.name = "Huur"
        guild = self.make_guild(categories=[category])
        member = MagicMock(display_name="Bob")
        await create_profile_channel(guild, member, "Huur", None)
        guild.create_category.assert_not_called()
        kwargs = guild.create_text_channel.call_args.kwargs
        self.assertIs(kwargs["category"], category)
        self.assertNotIn("admins", kwargs["overwrites"])

    async def test_archive_renames_and_keeps_owner_read_only(self) -> None:
        channel = MagicMock()
        channel.name = "huur-alice"
        channel.overwrites = {"bot": discord.PermissionOverwrite(send_messages=True)}
        channel.edit = AsyncMock()
        channel.send = AsyncMock()
        owner = MagicMock(id=42)  # used as an overwrites key, so it must be hashable
        await archive_profile_channel(channel, owner)
        kwargs = channel.edit.call_args.kwargs
        self.assertEqual(kwargs["name"], "archived-huur-alice")
        self.assertFalse(kwargs["overwrites"][owner].send_messages)
        self.assertTrue(kwargs["overwrites"][owner].view_channel)
        self.assertTrue(kwargs["overwrites"]["bot"].send_messages)
        channel.send.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_profile_channels.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bot.profile_channels'`

- [ ] **Step 3: Implement channel helpers**

Create `src/bot/profile_channels.py`:

```python
from __future__ import annotations

import discord

from src.bot.checks import archived_channel_name, sanitize_channel_name, unique_channel_name


CHANNEL_REASON = "Huur search profile"


async def create_profile_channel(guild, member, category_name: str, control_role_id: int | None):
    """Private alerts channel: the owner reads, only the bot posts."""
    category = discord.utils.get(guild.categories, name=category_name)
    if category is None:
        category = await guild.create_category(category_name, reason=CHANNEL_REASON)
    name = unique_channel_name(
        sanitize_channel_name(member.display_name),
        (channel.name for channel in guild.text_channels),
    )
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True, read_message_history=True, send_messages=False
        ),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            embed_links=True,
            read_message_history=True,
            manage_messages=True,
        ),
    }
    role = guild.get_role(control_role_id) if control_role_id is not None else None
    if role is not None:
        overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True)
    return await guild.create_text_channel(
        name, category=category, overwrites=overwrites, reason=CHANNEL_REASON
    )


async def archive_profile_channel(channel, owner) -> None:
    overwrites = dict(channel.overwrites)
    overwrites[owner] = discord.PermissionOverwrite(
        view_channel=True, read_message_history=True, send_messages=False
    )
    await channel.edit(
        name=archived_channel_name(channel.name),
        overwrites=overwrites,
        reason="Huur profile deleted",
    )
    await channel.send("Profile deleted; this channel is archived. An admin will remove it.")
```

Run: `./.venv/Scripts/python.exe -m pytest tests/test_profile_channels.py -q`
Expected: PASS (3 tests)

- [ ] **Step 4: Implement the views**

Create `src/bot/profile_views.py`. Like `commands.py`, it has no `from __future__ import annotations`, so discord.py sees real annotations.

```python
import asyncio
import logging
import sqlite3
from dataclasses import replace

import discord

from src.bot.checks import city_select_pages, validate_profile_numbers
from src.bot.embeds import build_profile_panel_embed
from src.bot.profile_channels import archive_profile_channel, create_profile_channel
from src.filtering.municipalities import all_municipalities
from src.models.profile import Profile


logger = logging.getLogger(__name__)

VIEW_TIMEOUT_SECONDS = 600
GONE_TEXT = "This profile no longer exists. Run /profile to start again."


def panel_message(bot, profile: Profile) -> dict:
    return {"embed": build_profile_panel_embed(profile), "view": ProfilePanel(bot, profile)}


class OwnerView(discord.ui.View):
    """Ephemeral views already reach only their owner; this guards against misuse anyway."""

    def __init__(self, bot, owner_id: int) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.bot = bot
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message("This panel belongs to someone else.", ephemeral=True)
        return False

    def current_profile(self):
        return self.bot.store.get_profile(self.owner_id)


class CreateProfileView(OwnerView):
    @discord.ui.button(label="Create profile", style=discord.ButtonStyle.primary)
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_profile() is not None:
            await interaction.response.edit_message(
                content="You already have a profile. Run /profile again.", embed=None, view=None
            )
            return
        await interaction.response.defer()
        settings = self.bot.settings
        try:
            channel = await create_profile_channel(
                interaction.guild,
                interaction.user,
                settings.huur_category_name,
                settings.discord_control_role_id,
            )
        except discord.HTTPException as error:
            logger.exception("Creating a channel for user=%s failed", interaction.user.id)
            await interaction.edit_original_response(
                content=f"Couldn't create your channel: {error}", embed=None, view=None
            )
            return
        try:
            profile = self.bot.store.create_profile(
                owner_user_id=interaction.user.id,
                channel_id=channel.id,
                max_rent_eur=settings.max_rent_eur,
                min_size_m2=settings.min_size_m2,
                preferred_bedrooms=settings.preferred_bedrooms,
                allow_close_match=settings.allow_close_match,
                municipalities=(),
            )
        except sqlite3.IntegrityError:
            # A second click raced this one; keep the profile that won and drop this channel.
            await channel.delete(reason="Duplicate huur profile")
            await interaction.edit_original_response(
                content="You already have a profile. Run /profile again.", embed=None, view=None
            )
            return
        logger.info("Created profile=%s for user=%s channel=%s", profile.id, profile.owner_user_id, channel.id)
        await interaction.edit_original_response(
            content=f"Created {channel.mention}. Press **Cities** to pick where you're looking.",
            **panel_message(self.bot, profile),
        )
        self.stop()


class ProfilePanel(OwnerView):
    def __init__(self, bot, profile: Profile) -> None:
        super().__init__(bot, profile.owner_user_id)
        self.toggle_close.label = f"Close match: {'on' if profile.allow_close_match else 'off'}"
        self.toggle_pause.label = "Resume" if profile.paused else "Pause"

    async def _gone(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content=GONE_TEXT, embed=None, view=None)

    async def _save(self, interaction: discord.Interaction, updated: Profile, content=None, dispatch=True) -> None:
        self.bot.store.update_profile(updated)
        await interaction.response.edit_message(content=content, **panel_message(self.bot, updated))
        if dispatch and not updated.paused:
            self.bot.schedule_dispatch(updated.id)

    @discord.ui.button(label="Edit numbers", style=discord.ButtonStyle.primary)
    async def edit_numbers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        await interaction.response.send_modal(NumbersModal(self.bot, profile))

    @discord.ui.button(label="Cities", style=discord.ButtonStyle.primary)
    async def cities(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        await interaction.response.edit_message(
            content=CitiesView.PROMPT, embed=None, view=CitiesView(self.bot, profile)
        )

    @discord.ui.button(label="Close match", style=discord.ButtonStyle.secondary)
    async def toggle_close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        await self._save(interaction, replace(profile, allow_close_match=not profile.allow_close_match))

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary)
    async def toggle_pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        if not profile.paused:
            await self._save(interaction, replace(profile, paused=True), dispatch=False)
            return
        if await self.bot.resolve_channel(profile.channel_id) is None:
            await interaction.response.send_message(
                "Your alert channel is gone. Delete this profile and create a new one.", ephemeral=True
            )
            return
        await self._save(interaction, replace(profile, paused=False))

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await self._gone(interaction)
            return
        await interaction.response.edit_message(
            content=(
                "Delete your profile? Your channel will be archived (read-only) "
                "and an admin will remove it."
            ),
            embed=None,
            view=ConfirmDeleteView(self.bot, profile),
        )


class NumbersModal(discord.ui.Modal, title="Edit numbers"):
    def __init__(self, bot, profile: Profile) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.bot = bot
        self.owner_id = profile.owner_user_id
        self.max_rent = discord.ui.TextInput(
            label="Max rent per month (€)", default=str(profile.max_rent_eur), max_length=6
        )
        self.min_size = discord.ui.TextInput(
            label="Min living area (m²)", default=str(profile.min_size_m2), max_length=4
        )
        self.bedrooms = discord.ui.TextInput(
            label="Preferred bedrooms (affects score only)",
            default=str(profile.preferred_bedrooms),
            max_length=2,
        )
        for item in (self.max_rent, self.min_size, self.bedrooms):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values, errors = validate_profile_numbers(
            self.max_rent.value, self.min_size.value, self.bedrooms.value
        )
        if errors:
            await interaction.response.send_message(
                "Not saved:\n" + "\n".join(f"• {error}" for error in errors), ephemeral=True
            )
            return
        profile = self.bot.store.get_profile(self.owner_id)
        if profile is None:
            await interaction.response.send_message(GONE_TEXT, ephemeral=True)
            return
        updated = replace(profile, **values)
        self.bot.store.update_profile(updated)
        await interaction.response.edit_message(
            content="Saved. New matches will appear in your channel shortly.",
            **panel_message(self.bot, updated),
        )
        if not updated.paused:
            self.bot.schedule_dispatch(updated.id)


class CitiesView(OwnerView):
    PROMPT = "Pick the municipalities you want alerts for, then press **Save cities**."

    def __init__(self, bot, profile: Profile) -> None:
        super().__init__(bot, profile.owner_user_id)
        self.pages = city_select_pages(all_municipalities())
        self.selected: list[set[str]] = []
        for index, page in enumerate(self.pages):
            chosen = {item.key for item in page if item.key in profile.municipalities}
            self.selected.append(chosen)
            select = discord.ui.Select(
                placeholder=f"Cities {page[0].name[0]}–{page[-1].name[0]}",
                min_values=0,
                max_values=len(page),
                options=[
                    discord.SelectOption(label=item.name, value=item.key, default=item.key in chosen)
                    for item in page
                ],
                row=index,
            )
            select.callback = self._select_callback(index, select)
            self.add_item(select)
        save = discord.ui.Button(label="Save cities", style=discord.ButtonStyle.success, row=len(self.pages))
        save.callback = self.save
        self.add_item(save)
        cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary, row=len(self.pages))
        cancel.callback = self.cancel
        self.add_item(cancel)

    def _select_callback(self, index: int, select: discord.ui.Select):
        async def callback(interaction: discord.Interaction) -> None:
            self.selected[index] = set(select.values)
            await interaction.response.defer()

        return callback

    async def save(self, interaction: discord.Interaction) -> None:
        profile = self.current_profile()
        if profile is None:
            await interaction.response.edit_message(content=GONE_TEXT, embed=None, view=None)
            return
        chosen = set().union(*self.selected)
        keys = tuple(item.key for page in self.pages for item in page if item.key in chosen)
        updated = replace(profile, municipalities=keys)
        self.bot.store.update_profile(updated)
        content = "Cities saved." if keys else "Cities cleared; you won't get alerts until you pick some."
        await interaction.response.edit_message(content=content, **panel_message(self.bot, updated))
        if keys and not updated.paused:
            self.bot.schedule_dispatch(updated.id)
        self.stop()

    async def cancel(self, interaction: discord.Interaction) -> None:
        profile = self.current_profile()
        if profile is None:
            await interaction.response.edit_message(content=GONE_TEXT, embed=None, view=None)
            return
        await interaction.response.edit_message(content=None, **panel_message(self.bot, profile))
        self.stop()


class ConfirmDeleteView(OwnerView):
    def __init__(self, bot, profile: Profile) -> None:
        super().__init__(bot, profile.owner_user_id)

    @discord.ui.button(label="Yes, delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        profile = self.current_profile()
        if profile is None:
            await interaction.edit_original_response(content=GONE_TEXT, embed=None, view=None)
            return
        self.bot.store.delete_profile(profile.id)
        logger.info("Deleted profile=%s of user=%s", profile.id, profile.owner_user_id)
        channel = await self.bot.resolve_channel(profile.channel_id)
        if channel is not None:
            try:
                await archive_profile_channel(channel, interaction.user)
            except discord.HTTPException:
                logger.exception("Archiving channel %s failed", profile.channel_id)
        if self.bot.notifier is not None:
            text = (
                f"[PROFILE_DELETED] <@{profile.owner_user_id}> deleted their profile; "
                f"<#{profile.channel_id}> can be removed."
            )
            # notify_ops blocks on the event loop, so it must run off-loop.
            await asyncio.to_thread(
                self.bot.notifier.notify_ops, text, self.bot.settings.discord_control_user_ids
            )
        await interaction.edit_original_response(
            content="Profile deleted. Your channel is archived.", embed=None, view=None
        )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        profile = self.current_profile()
        if profile is None:
            await interaction.response.edit_message(content=GONE_TEXT, embed=None, view=None)
            return
        await interaction.response.edit_message(content=None, **panel_message(self.bot, profile))
        self.stop()
```

- [ ] **Step 5: Update the commands**

In `src/bot/commands.py`:
- Replace the embeds import with:

```python
from src.bot.embeds import (
    build_listings_embed,
    build_no_profile_embed,
    build_profiles_list_embed,
    build_status_embed,
    build_summary_embed,
)
from src.bot.profile_views import CreateProfileView, panel_message
from src.notify.dispatch import recent_matches
```

and add `import datetime as dt` at the top.
- Replace the `listings`, `status` and `profile` commands with:

```python
    @tree.command(name="listings", description="Show current listings that match your profile")
    @app_commands.describe(limit="How many listings to show (1-25)")
    async def listings(
        interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 10
    ) -> None:
        profile = await asyncio.to_thread(bot.store.get_profile, interaction.user.id)
        if profile is None:
            await interaction.response.send_message(
                "You don't have a profile yet. Run /profile to create one.", ephemeral=True
            )
            return
        now = dt.datetime.now(tz=dt.timezone.utc)
        matches = await asyncio.to_thread(
            recent_matches, bot.store, profile, now, bot.stale_after, limit
        )
        embed = build_listings_embed(
            [listing.to_record() for listing in matches],
            empty_text="No current listings match your profile.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(name="status", description="Show scheduler state and the last run per source")
    async def status(interaction: discord.Interaction) -> None:
        rows = await asyncio.to_thread(bot.store.get_latest_source_runs)
        profiles = await asyncio.to_thread(bot.store.list_profiles)
        active = sum(1 for profile in profiles if not profile.paused)
        next_run = bot.scrape_loop.next_iteration if bot.scrape_loop.is_running() else None
        embed = build_status_embed(
            bot.paused, bot.cycle_running, next_run, bot.last_cycle_at, rows, (active, len(profiles))
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tree.command(name="profile", description="Create or edit your search profile")
    async def profile(interaction: discord.Interaction) -> None:
        existing = await asyncio.to_thread(bot.store.get_profile, interaction.user.id)
        if existing is None:
            await interaction.response.send_message(
                embed=build_no_profile_embed(),
                view=CreateProfileView(bot, interaction.user.id),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(ephemeral=True, **panel_message(bot, existing))

    @tree.command(name="profiles", description="List everyone's search profiles")
    async def profiles(interaction: discord.Interaction) -> None:
        if not await ensure_control(interaction):
            return
        rows = await asyncio.to_thread(bot.store.list_profiles)
        await interaction.response.send_message(embed=build_profiles_list_embed(rows), ephemeral=True)
```

Leave `/scrape`, `/pause` and `/resume` as they are.

Run: `./.venv/Scripts/python.exe -c "import src.bot.commands, src.bot.profile_views"` and `./.venv/Scripts/python.exe -m pytest tests -q`
Expected: no import errors; suite PASS.

- [ ] **Step 6: Manual test on the guild**

Run the bot locally (`./.venv/Scripts/python.exe -m src.bot`) against your server, using a copy of the production DB in `DATABASE_PATH` so seeding is exercised. Check each item:

1. On start, the log shows `Seeded profile=… for user=<your id>; marked N listings as sent`, and nothing is posted again to the old alert channel.
2. `/profile` as yourself shows the panel with your `.env` values and cities; **Alerts** points at the old alert channel.
3. As a second account: `/profile` → **Create profile**. Check that a `huur-<name>` channel appears under **Huur**, the second account can read it but not post, and other members can't see it.
4. Double-click **Create profile** quickly on a third account: only one channel and one profile exist afterwards.
5. **Cities**: select some municipalities on both pages → **Save cities**. Matching listings appear in the channel within a few seconds (backfill). Run `/listings` and confirm the same listings show up.
6. **Edit numbers** with `abc` → an ephemeral error lists the problem and nothing changes. With `€1200` → saved, and the panel shows €1200.
7. **Close match** toggles the label and the field.
8. **Pause** → the panel shows paused; the next `/scrape` posts nothing to that channel. **Resume** → posts any missed matches.
9. Delete the second account's channel by hand in Discord, run `/scrape`: ops shows `[PROFILE_ERROR] <@…>'s channel is unavailable; profile paused`, and **Resume** then says the channel is gone.
10. **Delete** → **Yes, delete**: the channel is renamed `archived-huur-…` and read-only, it contains "Profile deleted…", and ops shows `[PROFILE_DELETED]`, pinging the control users. `/profile` now offers **Create profile** again.
11. `/profiles` as a control user lists every profile; as a non-control user it's refused.
12. `/status` shows the `Profiles` line.

- [ ] **Step 7: Commit**

```bash
git add src/bot/profile_channels.py src/bot/profile_views.py src/bot/commands.py tests/test_profile_channels.py
git commit -m "Add the /profile panel, per-user alert channels and /profiles"
```

---

### Task 10: Documentation and configuration

**Files:**
- Modify: `.env.example`, `CLAUDE.md`, `README.md`, `docs/deploy_portainer.md`

- [ ] **Step 1: Update `.env.example`**

- Replace the `DISCORD_ALERT_CHANNEL_ID=` line and the comment block around it with:

```
# Only used once, to turn the old single profile into the first stored profile
# (owned by the first DISCORD_MENTION_USER_ID). Also the ops fallback channel.
DISCORD_ALERT_CHANNEL_ID=
```

- Replace the `DISCORD_MENTION_USER_ID` comment with `# Only used once: owner of the seeded profile (first ID)`.
- Add after `DISCORD_CONTROL_ROLE_ID=`:

```
# Category the bot creates per-user alert channels in
HUUR_CATEGORY_NAME=Huur
```

- Replace the `# Matching` header with `# Defaults for new profiles (and the seeded profile); each user edits theirs with /profile`, delete `STORE_ONLY_MATCHES=true`, and change the `# Geography` comment to `# Only used to seed the first profile's cities (comma-separated Zuid-Holland places)`.

- [ ] **Step 2: Update `CLAUDE.md`**

- In "What this is", replace "filters listings against a profile set in `.env`, stores them in SQLite with dedupe, and posts Discord alerts for new or changed matches" with "stores every listing in SQLite with dedupe, then matches them against each user's search profile and posts new matches to that user's private Discord channel".
- In **Commands**, delete the `--prune-non-matches` line.
- Replace the **Pipeline** line with: `HuurBot.run_cycle` → `runner.run_all_sources` (scrape + `upsert_listing`, no matching) → `notify/dispatch.py::dispatch` (per active profile: `evaluate_listing(listing, profile)`, skip keys already in `profile_alerts`, `notifier.notify_listing(profile, …)`, record after a successful send).
- Replace the **Matching** bullet with: hard match needs price ≤ `profile.max_rent_eur`, area ≥ `profile.min_size_m2`, a listing place in one of `profile.municipalities`, and availability; close match +10%/−10%. Places resolve through `src/config/municipalities.yaml` (`filtering/municipalities.py::municipality_for_place`), so add new spellings there as `places`.
- Replace the **Dedupe/alerting** bullet with: `dedupe_key()` is `source:id:city:price:area`; a profile gets each key once (`profile_alerts`), so a price or area change alerts again. Saving a profile triggers `HuurBot.schedule_dispatch` → `run_dispatch`, which waits for the cycle lock.
- Add a **Profiles** bullet: one per Discord user (`profiles` table); `/profile` panel in `bot/profile_views.py`; channels are created and archived in `bot/profile_channels.py`; a channel the bot can't reach auto-pauses the profile (`[PROFILE_ERROR]`); the first start seeds a profile from `.env` (`bot/seeding.py`).
- In the **Scrapers** bullet add: scrapers receive a `SearchScope` (union of active profiles' municipalities); only Vb&t uses it (`vbent_area`). Vesteda's API ignores location.
- In the **Bot** bullet, change "deletes the bot's own unpinned messages from the alert channel" to "from every profile channel".
- In **Config**, note that the `.env` matching values are defaults for new profiles.

- [ ] **Step 3: Update `README.md` and `docs/deploy_portainer.md`**

- `README.md`: delete the two `--prune-non-matches` lines. In the usage section, add a short "Profiles" paragraph: each user runs `/profile` → **Create profile**, picks cities and numbers, and gets alerts in their own `#huur-<name>` channel; admins see everyone's with `/profiles`.
- `docs/deploy_portainer.md`: in the env table change the `DISCORD_ALERT_CHANNEL_ID` row to "only used on first start to seed your profile; also the ops fallback", change `DISCORD_MENTION_USER_ID` to "owner of the seeded profile", add a row `HUUR_CATEGORY_NAME` (optional, default `Huur`), remove `DISCORD_ALERT_CHANNEL_ID` from the required list on line 54, and delete the `--prune-non-matches` line. Add a note under the upgrade section: "Upgrading from the single-profile version: the first start creates your profile from `.env` and marks every stored listing as sent, so nothing is reposted."

- [ ] **Step 4: Final verification**

Run: `./.venv/Scripts/python.exe -m pytest tests -q`
Expected: PASS

Run: `grep -rn "STORE_ONLY\|prune-non\|build_profile_embed\|alert_channel\b" src docs README.md CLAUDE.md .env.example`
Expected: only the intended `DISCORD_ALERT_CHANNEL_ID`/`discord_alert_channel_id` mentions (seeding, ops fallback, docs).

- [ ] **Step 5: Commit**

```bash
git add .env.example CLAUDE.md README.md docs/deploy_portainer.md
git commit -m "Document multi-profile setup"
```
