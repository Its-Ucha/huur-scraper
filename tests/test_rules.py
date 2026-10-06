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
