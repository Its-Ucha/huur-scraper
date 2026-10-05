from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.config import Settings
from src.scrapers.sites.verra import VerraScraper


def _first_listing(item: dict):
    base = {"_id": "a1", "isRentals": True, "url": "/nl/aanbod/a1"}
    with patch.object(VerraScraper, "fetch_json", return_value=[dict(base, **item)]):
        return VerraScraper(Mock(spec=Settings)).search(max_retries=0)[0]


class VerraTitleTests(unittest.TestCase):
    def test_title_is_street_address(self) -> None:
        self.assertEqual(_first_listing({"address": " Raaphorstlaan 17 B "}).title, "Raaphorstlaan 17 B")

    def test_title_falls_back_to_id_without_address(self) -> None:
        self.assertEqual(_first_listing({}).title, "Verra listing a1")
        self.assertEqual(_first_listing({"address": "   "}).title, "Verra listing a1")


if __name__ == "__main__":
    unittest.main()
