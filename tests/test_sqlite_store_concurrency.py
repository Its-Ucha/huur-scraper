from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.storage.sqlite_store import SQLiteStore


class StoreConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "test.db"
        self.store = SQLiteStore(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reads_are_not_blocked_by_an_open_write_transaction(self) -> None:
        # The scrape cycle writes from a worker thread while slash commands read;
        # a blocked read makes Discord drop the interaction after 3 seconds.
        self.store.set_state("paused", "0")
        with closing(sqlite3.connect(self.db_path, timeout=0)) as writer:
            writer.execute("BEGIN EXCLUSIVE")
            writer.execute("UPDATE bot_state SET value = '1' WHERE key = 'paused'")
            self.assertEqual(self.store.get_state("paused"), "0")
            writer.rollback()
