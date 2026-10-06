import unittest

from src.scrapers.sites.vesteda import VestedaScraper


class VestedaPayloadTests(unittest.TestCase):
    def test_no_price_bounds(self) -> None:
        self.assertNotIn("priceFrom", VestedaScraper.payload_template)
        self.assertNotIn("priceTo", VestedaScraper.payload_template)
