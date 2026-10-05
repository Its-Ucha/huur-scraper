from __future__ import annotations

import re

from bs4 import Tag

from src.models.listing import Listing
from src.scrapers.sites.mvgm import MvgmScraper


class IkwilhurenScraper(MvgmScraper):
    source_name = "ikwilhuren"
    base_url = "https://ikwilhuren.nu"
    # The national offer is 100+ pages, newest first by default. Reading only the
    # newest pages each cycle catches new listings without crawling the whole site.
    max_pages = 3

    def search(self, max_retries: int = 2) -> list[Listing]:
        unique: dict[str, Listing] = {}
        for page in range(1, self.max_pages + 1):  # page=0 and page=1 are both the first page
            soup = self.fetch_soup("/aanbod/", max_retries=max_retries, params={"page": page})
            cards = soup.select(".card-woning")
            for card in cards:
                listing = self.parse_card(card)
                if listing is not None:
                    unique.setdefault(listing.source_listing_id, listing)
            if not cards:
                break
        return list(unique.values())

    def parse_card(self, card: Tag) -> Listing | None:
        link = card.select_one(".card-title a[href]")
        listing_id = self.object_id(link["href"]) if link else None
        if listing_id is None:
            return None

        image = card.select_one(".card-img-top img")
        link_text = link.get_text(" ", strip=True)
        # The link reads "<type> <address>"; the image alt is just the address.
        address = (image.get("alt", "") if image else "").strip() or link_text
        asset_type = link_text[: -len(address)].strip() if link_text.endswith(address) else ""
        street, house_number = self.split_address(address)

        body = card.select_one(".card-body")
        # Body spans in order: title (class card-title), "<postcode> <city>" (no class), details.
        plain_spans = [span for span in body.find_all("span", recursive=False) if not span.get("class")] if body else []
        location = self._text(plain_spans[0] if plain_spans else None)
        postal_match = re.match(r"^(\d{4}\s?[A-Z]{2})\s+(.+)$", location)
        postal_code, city = postal_match.groups() if postal_match else ("", location)

        details = [self._text(span) for span in card.select(".card-body span.small > span")]
        available = next((text for text in details if text.lower().startswith("beschikbaar vanaf")), "")
        if not available and any(text.lower() == "direct beschikbaar" for text in details):
            available = "Direct beschikbaar"

        badges = [self._text(badge) for badge in card.select(".badges .badge")]
        status = next((badge for badge in badges if badge.lower() != "incl. pp"), "Te huur")

        stats = [self._text(span) for span in card.select(".dotted-spans > span")]
        price_text = next((text for text in stats if "€" in text or "/mnd" in text), "")
        area_text = next((text for text in stats if re.search(r"\bm\s*2?$", text)), "")
        bedroom_text = next((text for text in stats if "slaapkamer" in text), "")

        return Listing(
            source_site=self.source_name,
            source_listing_id=listing_id,
            source_url=self.absolute_url(link["href"]),
            title=address or f"Ikwilhuren listing {listing_id}",
            city=city or None,
            rent_price=self.parse_price(price_text),
            living_area_m2=self.parse_int(area_text.split("m")[0]),
            rooms_total=None,
            bedrooms=self.parse_int(bedroom_text),
            available_from=self.parse_available_from(available),
            raw_features={
                "status": status,
                "postal_code": postal_code.replace(" ", ""),
                "street": street,
                "house_number": house_number,
                "asset_type": asset_type,
                "parking_included": "true" if "incl. PP" in badges else "false",
                "image_url": self.absolute_url(image["src"]) if image and image.get("src") else "",
            },
            is_available=self.is_available_status(status),
            listing_status=status.lower(),
        )
