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
