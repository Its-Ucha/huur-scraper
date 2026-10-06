from __future__ import annotations

from src.models.listing import Listing
from src.scrapers.base import BaseScraper


class VestedaScraper(BaseScraper):
    source_name = "vesteda"
    api_url = "https://www.vesteda.com/api/units/search/facet"

    # Conservative status mapping. Adjust if you later confirm different enum meanings.
    status_map = {
        1: ("available", True),
        2: ("option", False),
        3: ("reserved", False),
        4: ("rented", False),
    }

    # The API ignores the location fields (results are national) but honours price
    # bounds, so none are sent: matching happens per profile after scraping.
    payload_template = {
        "filters": [6842],
        "latitude": 52.011578,
        "longitude": 4.3570676,
        "place": "Delft, Nederland",
        "radius": 10,
        "sortType": 0,
        "pageNumber": 0,
        "pageSize": 200,
    }

    def search(self, max_retries: int = 2) -> list[Listing]:
        payload = self.fetch_json(
            self.api_url,
            method="POST",
            json_payload=self.payload_template,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://www.vesteda.com",
                "Referer": "https://www.vesteda.com/nl/woning-zoeken",
            },
            max_retries=max_retries,
        )

        if not isinstance(payload, dict):
            return []

        results = payload.get("results")
        if not isinstance(results, dict):
            return []

        objects = results.get("objects")
        if not isinstance(objects, list):
            return []

        listings: list[Listing] = []

        for item in objects:
            if not isinstance(item, dict):
                continue

            listing_id_raw = item.get("id")
            if listing_id_raw is None:
                continue

            listing_id = str(listing_id_raw)
            relative_url = item.get("url")
            if not isinstance(relative_url, str) or not relative_url.strip():
                continue
            url = f"https://www.vesteda.com{relative_url.strip()}"

            title = item.get("complex") or f"Vesteda listing {listing_id}"
            city = item.get("city")
            city = city.strip() if isinstance(city, str) else None

            status_value = item.get("status")
            status_label, is_available = self._map_vesteda_status(status_value)

            available_from = item.get("upcomingeventdate")
            if not isinstance(available_from, str) or not available_from.strip():
                available_from = None

            street = str(item.get("street") or "")
            house_number = f"{item.get('houseNumber') or ''}{item.get('houseNumberAddition') or ''}"

            raw_features = {
                "status": str(status_value) if status_value is not None else "",
                "status_label": status_label,
                "postal_code": str(item.get("postalCode") or ""),
                "street": street,
                "house_number": house_number,
                "asset_type": str(item.get("entitysubtypelabel") or ""),
                "furniture": "",
                "district": str(item.get("district") or ""),
                "image_url": str(item.get("imageBig") or ""),
            }

            rent_price = item.get("priceUnformatted")
            if isinstance(rent_price, float):
                rent_price = int(round(rent_price))
            elif not isinstance(rent_price, int):
                rent_price = self.parse_int(str(rent_price) if rent_price is not None else None)

            living_area = item.get("size")
            if not isinstance(living_area, int):
                living_area = self.parse_int(str(living_area) if living_area is not None else None)

            bedrooms = item.get("numberOfBedRooms")
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
                    rooms_total=None,
                    bedrooms=bedrooms,
                    available_from=available_from,
                    raw_features=raw_features,
                    is_available=is_available,
                    listing_status=status_label,
                )
            )

        unique: dict[str, Listing] = {item.source_listing_id: item for item in listings}
        return list(unique.values())

    def _map_vesteda_status(self, status: object) -> tuple[str, bool]:
        if isinstance(status, int) and status in self.status_map:
            return self.status_map[status]

        # Fallback to generic mapper if enum changes.
        return str(status) if status is not None else "unknown", self.map_status_to_available(status, default=False)
