from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from src.config import Settings
from src.scrapers.sites.bpd_woningfonds import BpdWoningfondsScraper
from src.scrapers.sites.ikwilhuren import IkwilhurenScraper
from src.scrapers.sites.mvgm import MvgmScraper


# Card markup trimmed from the live pages (October 2026).
BPD_CARD = """
<div class="card card-result-list mb-4 w-100">
  <div class="card-image">
    <div class="card-image-label"><span>{status}</span></div>
    <div class="card-image-wrapper">
      <img alt="Vader Rijndreef 154" class="card-img-top"
           src="//d.static.nbo.nl/media/da/da949ec826e260036c6120bd078fa8bf/768x432/thumb.jpg"/>
    </div>
  </div>
  <div class="card-body flex-grow-0 pb-0">
    <h5 class="card-title fw-700 mb-1"><a class="stretched-link"
        href="/object/4506a59d16ecc5751e6cf6ae12a0cd8c/">Vader Rijndreef 154</a></h5>
    <div class="card-text">Den Haag</div>
  </div>
  <div class="object-features d-flex">
    <div class="object-price"><span class="value ml-1">987,-</span></div>
    <div class="object-rooms"><span class="value ml-1">1</span></div>
    <div class="object-area"><span class="value ml-1">55 m<sup>2</sup></span></div>
  </div>
  <div class="card-body pt-0"><div>
    <div class="mb-2"><span>Beschikbaar vanaf 1 november 2026</span></div>
    <div>709x bezichtigingen aangevraagd</div>
  </div></div>
</div>
"""

IKW_CARD = """
<div class="card card-woning shadow-sm">
  <div class="card-img-top">
    <div class="badges position-absolute">
      <span class="badge bg-white"><span class="status-dot"></span> Te huur </span>
      {extra_badge}
    </div>
    <div class="ratio"><picture>
      <img alt="Prinses Beatrixlaan 9 81 "
           src="//c.static.nbo.nl/media/6a/6a745c264ab7231bdaf72b43fcd105d6/768x510/thumb.jpg"/>
    </picture></div>
  </div>
  <div class="card-body d-flex flex-column">
    <span class="card-title h5 text-secondary mb-0">
      <a class="stretched-link"
         href="/object/den-haag-2595ak-9-prinses-beatrixlaan-c8f3f9b67afd06bc41205313df7776d7/">
        Appartement Prinses Beatrixlaan 9 81
      </a>
    </span>
    <span>2595AK Den Haag</span>
    <span class="small">
      <span class="d-flex gap-1"><span><i class="fal fa-calendar"></i></span>{available}</span>
      <span class="d-flex gap-1"><span></span>Direct inschrijven via deze site</span>
      <span title="Sinds 0.78 dagen online">Nieuw</span>
    </span>
    <div class="pt-4 dotted-spans mt-auto">
      <span class="fw-bold">&euro; 1.350,- /mnd</span>
      <span>62 m<sup>2</sup></span> <span>1  slaapkamers </span>
    </div>
  </div>
</div>
"""


def _card(html: str):
    return BeautifulSoup(html, "html.parser").find("div")


def _page(*cards: str) -> BeautifulSoup:
    return BeautifulSoup(f"<html><body>{''.join(cards)}</body></html>", "html.parser")


class MvgmHelperTests(unittest.TestCase):
    def test_parse_price(self) -> None:
        self.assertEqual(MvgmScraper.parse_price("987,-"), 987)
        self.assertEqual(MvgmScraper.parse_price("€ 1.300,- /mnd"), 1300)
        self.assertIsNone(MvgmScraper.parse_price(""))

    def test_parse_available_from(self) -> None:
        parse = MvgmScraper.parse_available_from
        self.assertEqual(parse("Beschikbaar vanaf 1 november 2026"), "2026-11-01")
        self.assertEqual(parse("Beschikbaar vanaf 16 oktober 2026"), "2026-10-16")
        self.assertEqual(parse("Beschikbaar vanaf 01-12-2026"), "2026-12-01")
        self.assertEqual(parse("Direct beschikbaar"), "Direct beschikbaar")
        self.assertIsNone(parse(""))

    def test_object_id_from_both_url_styles(self) -> None:
        self.assertEqual(
            MvgmScraper.object_id("/object/4506a59d16ecc5751e6cf6ae12a0cd8c/"),
            "4506a59d16ecc5751e6cf6ae12a0cd8c",
        )
        self.assertEqual(
            MvgmScraper.object_id("/object/arnhem-6824aa-32-schavenmolenstraat-59cc258e7afd06bc41205313df7776d7/"),
            "59cc258e7afd06bc41205313df7776d7",
        )
        self.assertIsNone(MvgmScraper.object_id("/aanbod/?page=2"))

    def test_unavailable_statuses(self) -> None:
        scraper = MvgmScraper(Mock(spec=Settings))
        self.assertTrue(scraper.is_available_status("Te huur"))
        self.assertFalse(scraper.is_available_status("Verhuurd onder voorbehoud"))
        self.assertFalse(scraper.is_available_status("Gereserveerd"))


class BpdWoningfondsParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = BpdWoningfondsScraper(Mock(spec=Settings))

    def test_parse_card(self) -> None:
        listing = self.scraper.parse_card(_card(BPD_CARD.format(status="Te huur")))
        self.assertEqual(listing.source_listing_id, "4506a59d16ecc5751e6cf6ae12a0cd8c")
        self.assertEqual(
            listing.source_url,
            "https://hurenbij.bpdwoningfonds.nl/object/4506a59d16ecc5751e6cf6ae12a0cd8c/",
        )
        self.assertEqual(listing.title, "Vader Rijndreef 154")
        self.assertEqual(listing.city, "Den Haag")
        self.assertEqual(listing.rent_price, 987)
        self.assertEqual(listing.living_area_m2, 55)
        self.assertEqual(listing.bedrooms, 1)
        self.assertIsNone(listing.rooms_total)
        self.assertEqual(listing.available_from, "2026-11-01")
        self.assertTrue(listing.is_available)
        self.assertEqual(listing.raw_features["street"], "Vader Rijndreef")
        self.assertEqual(listing.raw_features["house_number"], "154")
        self.assertTrue(listing.raw_features["image_url"].startswith("https://d.static.nbo.nl/"))

    def test_reserved_card_is_unavailable(self) -> None:
        listing = self.scraper.parse_card(_card(BPD_CARD.format(status="Gereserveerd")))
        self.assertFalse(listing.is_available)
        self.assertEqual(listing.listing_status, "gereserveerd")

    def test_search_reads_one_page_and_dedupes(self) -> None:
        card = BPD_CARD.format(status="Te huur")
        with patch.object(BpdWoningfondsScraper, "fetch_soup", return_value=_page(card, card)) as fetch:
            listings = self.scraper.search(max_retries=0)
        fetch.assert_called_once()
        self.assertEqual(len(listings), 1)


class IkwilhurenParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = IkwilhurenScraper(Mock(spec=Settings))

    def card(self, available: str = "Beschikbaar vanaf 01-11-2026", extra_badge: str = ""):
        return IKW_CARD.format(available=available, extra_badge=extra_badge)

    def test_parse_card(self) -> None:
        listing = self.scraper.parse_card(_card(self.card()))
        self.assertEqual(listing.source_listing_id, "c8f3f9b67afd06bc41205313df7776d7")
        self.assertTrue(listing.source_url.startswith("https://ikwilhuren.nu/object/den-haag-2595ak-"))
        self.assertEqual(listing.title, "Prinses Beatrixlaan 9 81")
        self.assertEqual(listing.city, "Den Haag")
        self.assertEqual(listing.rent_price, 1350)
        self.assertEqual(listing.living_area_m2, 62)
        self.assertEqual(listing.bedrooms, 1)
        self.assertEqual(listing.available_from, "2026-11-01")
        self.assertTrue(listing.is_available)
        features = listing.raw_features
        self.assertEqual(features["postal_code"], "2595AK")
        self.assertEqual(features["street"], "Prinses Beatrixlaan")
        self.assertEqual(features["house_number"], "9 81")
        self.assertEqual(features["asset_type"], "Appartement")
        self.assertEqual(features["parking_included"], "false")

    def test_direct_available_and_parking_badge(self) -> None:
        html = self.card(
            available="Direct beschikbaar",
            extra_badge='<span class="badge bg-white">incl. PP</span>',
        )
        listing = self.scraper.parse_card(_card(html))
        self.assertEqual(listing.available_from, "Direct beschikbaar")
        self.assertEqual(listing.raw_features["parking_included"], "true")
        self.assertEqual(listing.raw_features["status"], "Te huur")

    def test_search_reads_newest_pages_starting_at_one(self) -> None:
        pages = [_page(self.card()), _page(self.card()), _page()]
        with patch.object(IkwilhurenScraper, "fetch_soup", side_effect=pages) as fetch:
            listings = self.scraper.search(max_retries=0)
        self.assertEqual([call.kwargs["params"] for call in fetch.call_args_list], [{"page": 1}, {"page": 2}, {"page": 3}])
        self.assertEqual(len(listings), 1)

    def test_search_stops_at_empty_page(self) -> None:
        with patch.object(IkwilhurenScraper, "fetch_soup", side_effect=[_page()]) as fetch:
            self.assertEqual(self.scraper.search(max_retries=0), [])
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
