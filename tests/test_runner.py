from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from src.scrapers.base import SourceBlockedError
from src.scrapers.runner import run_all_sources
from src.scrapers.scope import SearchScope
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
        def __init__(self, settings, scope=None) -> None:
            pass

        def search(self, max_retries: int = 0):
            return [copy.deepcopy(listing) for listing in listings]

    return _Scraper


def raising(error: Exception):
    class _Scraper:
        def __init__(self, settings, scope=None) -> None:
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

    def result(self, summary, name):
        return next(r for r in summary.results if r.name == name)

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

    def test_error_source_notifies_on_second_failure_until_recovered(self) -> None:
        self.factories["beta"] = raising(RuntimeError("boom"))
        summary = self.run_once()
        beta = self.result(summary, "beta")
        self.assertEqual((beta.status, beta.details), ("error", "boom"))
        self.assertEqual(self.notifier.ops, [])

        self.run_once()
        self.run_once()
        self.assertEqual(self.notifier.ops, ["[SOURCE_ERROR] beta - boom"])

        self.factories["beta"] = returning([])
        self.run_once()
        self.assertEqual(self.notifier.ops[-1], "[SOURCE_RECOVERED] beta")

    def test_single_error_then_ok_stays_quiet(self) -> None:
        self.factories["beta"] = raising(RuntimeError("timed out"))
        self.run_once()
        self.factories["beta"] = returning([])
        self.run_once()
        self.assertEqual(self.notifier.ops, [])

    def test_error_after_block_recovers_with_one_message(self) -> None:
        self.factories["beta"] = raising(SourceBlockedError("429"))
        self.run_once()
        self.factories["beta"] = raising(RuntimeError("boom"))
        self.run_once()
        self.factories["beta"] = returning([])
        self.run_once()
        self.assertEqual(len(self.notifier.ops), 2)
        self.assertTrue(self.notifier.ops[0].startswith("[SOURCE_BLOCKED] beta"))
        self.assertEqual(self.notifier.ops[1], "[SOURCE_RECOVERED] beta")

    def test_error_then_blocked_notifies_block_immediately(self) -> None:
        self.factories["beta"] = raising(RuntimeError("boom"))
        self.run_once()
        self.factories["beta"] = raising(SourceBlockedError("429"))
        self.run_once()
        self.assertEqual(len(self.notifier.ops), 1)
        self.assertTrue(self.notifier.ops[0].startswith("[SOURCE_BLOCKED] beta"))

    def test_first_ever_ok_run_does_not_send_recovered(self) -> None:
        self.run_once()
        self.assertEqual(self.notifier.ops, [])


if __name__ == "__main__":
    unittest.main()
