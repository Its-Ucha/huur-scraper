from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import unquote

from src.config import Settings
from src.scrapers.base import SourceBlockedError
from src.filtering.municipalities import Municipality
from src.scrapers.scope import SearchScope
from src.scrapers.sites.vbent import VBentScraper, vbent_area


HOUSE = {
    "id": "8f4dec",
    "sourceId": "34703962",
    "url": "/woning/s-gravenhage-maria-stuartplein-130",
    "address": {"city": "'s-Gravenhage", "house": "Maria Stuartplein 130"},
    "prices": {
        "category": "rent",
        "rental": {"price": 2025, "serviceCharges": 60},
        "parkingCharges": 0,
        "parkingServiceCharges": 0,
    },
    "status": {"name": "available", "code": 1},
    "attributes": {"type": {"category": "apartment"}},
    "plot": 111,
    "rooms": 4,
    "acceptance": "2026-11-01T00:00:00.000Z",
}


class VBentScraperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = VBentScraper(Mock(spec=Settings))

    def test_example_fields(self) -> None:
        listing = self.scraper._parse_house(HOUSE)
        assert listing is not None
        self.assertEqual(listing.source_site, "vbent")
        self.assertEqual(listing.source_listing_id, "8f4dec")
        self.assertEqual(listing.title, "Maria Stuartplein 130")
        self.assertEqual(listing.city, "'s-Gravenhage")
        self.assertEqual(listing.rent_price, 2025)
        self.assertEqual(listing.living_area_m2, 111)
        self.assertEqual(listing.rooms_total, 4)
        self.assertIsNone(listing.bedrooms)
        self.assertEqual(listing.available_from, HOUSE["acceptance"])
        self.assertTrue(listing.is_available)
        self.assertEqual(listing.raw_features["service_charges"], "60")
        self.assertEqual(listing.raw_features["parking_charges"], "0")
        self.assertEqual(listing.source_url, self.scraper.base_url + HOUSE["url"])

    def test_image_path_made_absolute(self) -> None:
        house = dict(HOUSE, image="/images/a3b16a-w300-s-fwj/stationsplein-6-g3")
        listing = self.scraper._parse_house(house)
        assert listing is not None
        self.assertEqual(
            listing.raw_features["image_url"],
            "https://vbtverhuurmakelaars.nl/images/a3b16a-w300-s-fwj/stationsplein-6-g3",
        )

    def test_missing_image_is_empty(self) -> None:
        listing = self.scraper._parse_house(HOUSE)
        assert listing is not None
        self.assertEqual(listing.raw_features["image_url"], "")

    def test_cookie_pagination_and_deduplication(self) -> None:
        second = dict(HOUSE, id="second")
        with patch.object(self.scraper, "fetch_json", side_effect=[
            {"houses": [HOUSE], "pageCount": 2},
            {"houses": [HOUSE, second], "pageCount": 2},
        ]) as fetch:
            listings = self.scraper.search(max_retries=0)
        self.assertEqual([item.source_listing_id for item in listings], ["8f4dec", "second"])
        self.assertEqual(fetch.call_count, 2)
        for page, call in enumerate(fetch.call_args_list, start=1):
            self.assertEqual(call.args[0], f"{self.scraper.base_url}/api/properties/12/{page}")
            self.assertEqual(call.kwargs["params"], {"search": "true"})
            self.assertEqual(call.kwargs["max_retries"], 0)
            cookie = call.kwargs["headers"]["Cookie"]
            filters = json.loads(unquote(cookie.split("filter_properties=", 1)[1]))
            self.assertEqual(filters, self.scraper.filter_template)
            self.assertEqual(filters["city"], "Delft")
            self.assertEqual(filters["radius"], 15)

    def test_empty_results(self) -> None:
        with patch.object(self.scraper, "fetch_json", return_value={"houses": [], "pageCount": 0}):
            self.assertEqual(self.scraper.search(), [])

    def test_bad_responses_fail_instead_of_returning_partial_results(self) -> None:
        for payload in [None, {}, {"houses": None}, {"houses": [], "pageCount": True},
                        {"houses": [], "pageCount": -1}, {"houses": [], "pageCount": 101}]:
            with self.subTest(payload=payload), patch.object(self.scraper, "fetch_json", return_value=payload):
                with self.assertRaises(ValueError):
                    self.scraper.search()

    def test_block_on_later_page_propagates(self) -> None:
        with patch.object(self.scraper, "fetch_json", side_effect=[
            {"houses": [HOUSE], "pageCount": 2}, SourceBlockedError("blocked"),
        ]):
            with self.assertRaises(SourceBlockedError):
                self.scraper.search()

    def test_status_is_conservative(self) -> None:
        for status, expected in [
            ({"name": "rented", "code": 1}, False),
            ({"name": "option", "code": 2}, False),
            ({"code": 1}, True),
            ({"code": 2}, False),
            ({}, False),
        ]:
            with self.subTest(status=status):
                listing = self.scraper._parse_house(dict(HOUSE, status=status))
                assert listing is not None
                self.assertEqual(listing.is_available, expected)

    def test_missing_fields_and_invalid_entries(self) -> None:
        listing = self.scraper._parse_house({"id": "minimal", "url": "/woning/minimal"})
        assert listing is not None
        self.assertIsNone(listing.city)
        self.assertIsNone(listing.rent_price)
        self.assertFalse(listing.is_available)
        for item in [{}, dict(HOUSE, url=""), dict(HOUSE, id=None, sourceId=None),
                     dict(HOUSE, url="https://example.com/woning/test"),
                     dict(HOUSE, prices={"category": "purchase"})]:
            with self.subTest(item=item):
                self.assertIsNone(self.scraper._parse_house(item))
        fallback = self.scraper._parse_house(dict(HOUSE, id=None))
        assert fallback is not None
        self.assertEqual(fallback.source_listing_id, "34703962")

    def test_numeric_conversion(self) -> None:
        item = copy.deepcopy(HOUSE)
        item["prices"]["rental"]["price"] = "2025.5"
        item["plot"] = 111.0
        item["rooms"] = "4"
        listing = self.scraper._parse_house(item)
        assert listing is not None
        self.assertEqual(listing.rent_price, 2026)
        self.assertEqual(listing.living_area_m2, 111)
        self.assertEqual(listing.rooms_total, 4)
        for value in [None, True, {}, "bad", "NaN", float("inf"), -1]:
            with self.subTest(value=value):
                self.assertIsNone(self.scraper._number(value))


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


if __name__ == "__main__":
    unittest.main()