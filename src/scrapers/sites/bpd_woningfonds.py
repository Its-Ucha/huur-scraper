from __future__ import annotations

from bs4 import Tag

from src.models.listing import Listing
from src.scrapers.sites.mvgm import MvgmScraper


class BpdWoningfondsScraper(MvgmScraper):
    source_name = "bpd_woningfonds"
    base_url = "https://hurenbij.bpdwoningfonds.nl"

    def search(self, max_retries: int = 2) -> list[Listing]:
        # The whole national offer (a few dozen homes) is on one unpaginated page;
        # the profile filter narrows it down afterwards.
        soup = self.fetch_soup("/aanbod/", max_retries=max_retries)
        listings = [self.parse_card(card) for card in soup.select(".card-result-list")]
        unique = {item.source_listing_id: item for item in listings if item is not None}
        return list(unique.values())

    def parse_card(self, card: Tag) -> Listing | None:
        link = card.select_one(".card-title a[href]")
        listing_id = self.object_id(link["href"]) if link else None
        if listing_id is None:
            return None

        title = link.get_text(" ", strip=True) or f"BPD Woningfonds listing {listing_id}"
        street, house_number = self.split_address(title)
        city = self._text(card.select_one(".card-text")) or None
        status = self._text(card.select_one(".card-image-label")) or "Te huur"
        available = self._text(card.select_one(".card-body.pt-0 .mb-2"))
        image = card.select_one("img.card-img-top")

        return Listing(
            source_site=self.source_name,
            source_listing_id=listing_id,
            source_url=self.absolute_url(link["href"]),
            title=title,
            city=city,
            rent_price=self.parse_price(self._text(card.select_one(".object-price .value"))),
            living_area_m2=self.parse_int(self._text(card.select_one(".object-area .value")).split("m")[0]),
            rooms_total=None,
            # The card shows a bed icon, so this is the bedroom count, not total rooms.
            bedrooms=self.parse_int(self._text(card.select_one(".object-rooms .value"))),
            available_from=self.parse_available_from(available),
            raw_features={
                "status": status,
                "street": street,
                "house_number": house_number,
                "image_url": self.absolute_url(image["src"]) if image and image.get("src") else "",
            },
            is_available=self.is_available_status(status),
            listing_status=status.lower(),
        )
