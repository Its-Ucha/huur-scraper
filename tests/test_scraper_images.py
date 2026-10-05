from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.config import Settings
from src.scrapers.sites.thehaguerealestate import TheHagueRealEstateScraper
from src.scrapers.sites.verra import VerraScraper
from src.scrapers.sites.vesteda import VestedaScraper


def _search(scraper_class, payload):
    scraper = scraper_class(Mock(spec=Settings))
    with patch.object(scraper_class, "fetch_json", return_value=payload):
        return scraper.search(max_retries=0)


class TheHagueRealEstateImageTests(unittest.TestCase):
    def test_featured_photo_url(self) -> None:
        payload = [
            {
                "id": 1,
                "link": "https://www.thehaguerealestate.nl/1",
                "featured_photo": {"provider_link": "https://cdn.example.com/1.jpg"},
            }
        ]
        listing = _search(TheHagueRealEstateScraper, payload)[0]
        self.assertEqual(listing.raw_features["image_url"], "https://cdn.example.com/1.jpg")

    def test_missing_photo_is_empty(self) -> None:
        listing = _search(TheHagueRealEstateScraper, [{"id": 1, "link": "https://x/1"}])[0]
        self.assertEqual(listing.raw_features["image_url"], "")


class VerraImageTests(unittest.TestCase):
    def test_photo_url(self) -> None:
        payload = [
            {
                "_id": "a1",
                "isRentals": True,
                "url": "/nl/aanbod/a1",
                "address": "Teststraat 1",
                "photo": "https://media.example.com/a1.jpg",
            }
        ]
        listing = _search(VerraScraper, payload)[0]
        self.assertEqual(listing.raw_features["image_url"], "https://media.example.com/a1.jpg")

    def test_missing_photo_is_empty(self) -> None:
        payload = [{"_id": "a1", "isRentals": True, "url": "/nl/aanbod/a1", "address": "Teststraat 1"}]
        self.assertEqual(_search(VerraScraper, payload)[0].raw_features["image_url"], "")


class VestedaImageTests(unittest.TestCase):
    def test_image_big_url(self) -> None:
        payload = {
            "results": {
                "objects": [
                    {"id": 7, "url": "/nl/woning/7", "imageBig": "https://cdn.example.com/7.jpg"}
                ]
            }
        }
        listing = _search(VestedaScraper, payload)[0]
        self.assertEqual(listing.raw_features["image_url"], "https://cdn.example.com/7.jpg")

    def test_missing_image_is_empty(self) -> None:
        payload = {"results": {"objects": [{"id": 7, "url": "/nl/woning/7"}]}}
        self.assertEqual(_search(VestedaScraper, payload)[0].raw_features["image_url"], "")


if __name__ == "__main__":
    unittest.main()
