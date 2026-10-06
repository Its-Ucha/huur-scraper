from __future__ import annotations

import datetime as dt
import unittest

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
        for name in ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID", "DISCORD_CONTROL_USER_IDS"):
            self.assertIn(name, joined)
        self.assertNotIn("DISCORD_ALERT_CHANNEL_ID", joined)

    def test_valid_settings(self) -> None:
        settings = make_settings(
            discord_bot_token="t",
            discord_guild_id=1,
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
    def test_custom_empty_text(self) -> None:
        self.assertEqual(build_listings_embed([], empty_text="Nothing").description, "Nothing")

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
    def test_profile_counts(self) -> None:
        fields = {f.name: f.value for f in build_status_embed(False, False, None, None, [], (2, 3)).fields}
        self.assertEqual(fields["Profiles"], "2 active / 3 total")

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


class SummaryEmbedTests(unittest.TestCase):
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

    def test_empty_summary(self) -> None:
        self.assertEqual(build_summary_embed(RunSummary()).description, "No sources ran.")


class LastScheduledClearTests(unittest.TestCase):
    def at(self, day: int, hour: int, minute: int = 0) -> dt.datetime:
        # 2026-10-05 is a Monday.
        return dt.datetime(2026, 10, day, hour, minute, tzinfo=dt.timezone.utc)

    def test_slot_later_today_goes_back_a_week(self) -> None:
        self.assertEqual(last_scheduled_clear(self.at(5, 3, 59), 0, 4), self.at(5, 4) - dt.timedelta(days=7))

    def test_slot_exactly_now(self) -> None:
        self.assertEqual(last_scheduled_clear(self.at(5, 4), 0, 4), self.at(5, 4))

    def test_mid_week_returns_last_monday(self) -> None:
        self.assertEqual(last_scheduled_clear(self.at(8, 12), 0, 4), self.at(5, 4))

    def test_weekday_after_today(self) -> None:
        # Monday, slot on Sunday -> yesterday's Sunday.
        self.assertEqual(last_scheduled_clear(self.at(5, 12), 6, 4), self.at(4, 4))


if __name__ == "__main__":
    unittest.main()
