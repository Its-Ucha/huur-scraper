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
        with self.assertRaisesRegex(ValueError, "(?i)shared"):
            build_place_index([one, two])


if __name__ == "__main__":
    unittest.main()
