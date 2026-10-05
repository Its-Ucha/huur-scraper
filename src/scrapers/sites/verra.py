

from __future__ import annotations

from src.models.listing import Listing
from src.scrapers.base import BaseScraper


class VerraScraper(BaseScraper):
    source_name = "verra"
    api_url = "https://www.verra.nl/nl/realtime-listings/consumer"

    def search(self, max_retries: int = 2) -> list[Listing]:
        payload = self.fetch_json(self.api_url, max_retries=max_retries)
        if not isinstance(payload, list):
            return []

        listings: list[Listing] = []

        for item in payload:
            if not isinstance(item, dict):
                continue

            listing_id_raw = item.get("_id")
            if listing_id_raw is None:
                continue
            if item.get("isRentals") is not True:
                continue

            listing_id = str(listing_id_raw)
            url = f"https://www.verra.nl{item.get('url')}"

            title = f"Verra listing {listing_id}"
            city = item.get("city")
            city = city.strip() if isinstance(city, str) else None

            status = item.get("status")
            listing_status = status.strip().lower() if isinstance(status, str) and status.strip() else "available"
            is_available = self.map_status_to_available(status, default=True)

       
            available_from = None

            raw_features = {
                "status": status if isinstance(status, str) else "",
                "postal_code": str(item.get("zipcode", "")),
                "street": " ".join(item.get("address", "").split(" ")[:-1]),
                "house_number": str(item.get("address", "").split(" ")[-1]),
                "asset_type": str(item.get("mainType") or ""),
                "furniture": "",
                "image_url": str(item.get("photo") or ""),
            }

            rent_price = item.get("rentalsPrice")
            if not isinstance(rent_price, int):
                rent_price = self.parse_int(str(rent_price) if rent_price is not None else None)

            living_area = item.get("livingSurface")
            if not isinstance(living_area, int):
                living_area = self.parse_int(str(living_area) if living_area is not None else None)

            total_rooms = item.get("rooms")
            if not isinstance(total_rooms, int):
                total_rooms = self.parse_int(str(total_rooms) if total_rooms is not None else None)

            bedrooms = item.get("bedrooms")
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
