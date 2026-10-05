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
