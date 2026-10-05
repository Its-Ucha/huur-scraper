from __future__ import annotations

from src.models.listing import Listing
from src.scrapers.base import BaseScraper


class TheHagueRealEstateScraper(BaseScraper):
    source_name = "thehaguerealestate"
    api_url = "https://www.thehaguerealestate.nl/aanbod/get?object_status=AVAILABLE"

    def search(self, max_retries: int = 2) -> list[Listing]:
        payload = self.fetch_json(self.api_url, max_retries=max_retries)
        if not isinstance(payload, list):
            return []

        listings: list[Listing] = []

        for item in payload:
            if not isinstance(item, dict):
                continue

            listing_id_raw = item.get("id")
            if listing_id_raw is None:
                continue

            listing_id = str(listing_id_raw)
            url = item.get("link")

            title = item.get("formatted_address") or f"The Hague Real Estate listing {listing_id}"
            city = item.get("city")
            city = city.strip() if isinstance(city, str) else None

            status = item.get("object_status")
            listing_status = status.strip().lower() if isinstance(status, str) and status.strip() else "available"
            is_available = self.map_status_to_available(status, default=True)

            available_from = item.get("financial", {}).get("overdracht", {}).get("aanvaardingsdatum")
            if not isinstance(available_from, str) or not available_from.strip():
                available_from = None

            raw_features = {
                "status": status if isinstance(status, str) else "",
                "postal_code": str(item.get("address", {}).get("postcode") or ""),
                "street": str(item.get("address", {}).get("straat") or ""),
                "house_number": f"{item.get('address', {}).get('huisnummer', {}).get('hoofdnummer', '')}{item.get('address', {}).get('huisnummer', {}).get('toevoeging', '')}",
                "asset_type": str(item.get("object_type") or ""),
                "furniture": str(item.get("furniture_type") or ""),
                "image_url": self._featured_photo_url(item),
            }

            rent_price = item.get("price")
            if not isinstance(rent_price, int):
                rent_price = self.parse_int(str(rent_price) if rent_price is not None else None)

            living_area = item.get("area")
            if not isinstance(living_area, int):
                living_area = self.parse_int(str(living_area) if living_area is not None else None)

            total_rooms = item.get("number_of_bedrooms")
            if not isinstance(total_rooms, int):
                total_rooms = self.parse_int(str(total_rooms) if total_rooms is not None else None)

            bedrooms = item.get("number_of_bedrooms")
            if not isinstance(bedrooms, int):
                bedrooms = self.parse_int(str(bedrooms) if bedrooms is not None else None)

            listings.append(
                Listing(
                    source_site=self.source_name,
                    source_listing_id=listing_id,
                    source_url=url,
                    title=title,
                    city=city,
                    rent_price=rent_price,
                    living_area_m2=living_area,
                    rooms_total=total_rooms,
                    bedrooms=bedrooms,
                    available_from=available_from,
                    raw_features=raw_features,
                    is_available=is_available,
                    listing_status=listing_status,
                )
            )

        unique: dict[str, Listing] = {item.source_listing_id: item for item in listings}
        return list(unique.values())

    @staticmethod
    def _featured_photo_url(item: dict) -> str:
        photo = item.get("featured_photo")
        if not isinstance(photo, dict):
            return ""
        return str(photo.get("provider_link") or "")
