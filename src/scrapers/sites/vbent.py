from __future__ import annotations

import json
import math
from urllib.parse import quote, urljoin

from src.models.listing import Listing
from src.scrapers.base import BaseScraper


class VBentScraper(BaseScraper):
	source_name = "vbent"
	base_url = "https://vbtverhuurmakelaars.nl"
	page_size = 12
	max_pages = 100

	# The website reads its search filters from this cookie, not query params.
	# Keep price/size unrestricted so the local close-match rules still apply.
	filter_template = {
		"city": "Delft",
		"radius": 15,
		"address": "",
		"priceRental": {"min": 0, "max": 0},
		"availablefrom": "",
		"surface": "",
		"rooms": 0,
		"typeCategory": "",
	}

	def search(self, max_retries: int = 2) -> list[Listing]:
		filters = quote(json.dumps(self.filter_template, separators=(",", ":")), safe="")
		headers = {
			"Accept": "application/json",
			"Cookie": f"language=nl; filter_properties={filters}",
		}
		unique: dict[str, Listing] = {}
		page = 1
		page_count = 1
		while page <= page_count:
			payload = self.fetch_json(
				f"{self.base_url}/api/properties/{self.page_size}/{page}",
				params={"search": "true"},
				headers=headers,
				max_retries=max_retries,
			)
			if not isinstance(payload, dict) or not isinstance(payload.get("houses"), list):
				raise ValueError("Vb&t API response must contain a houses list")
			count = payload.get("pageCount")
			if isinstance(count, bool) or not isinstance(count, int) or count < 0:
				raise ValueError("Vb&t API response has an invalid pageCount")
			if count > self.max_pages:
				raise ValueError(f"Vb&t pagination exceeds safety limit of {self.max_pages} pages")
			page_count = count
			for item in payload["houses"]:
				if not isinstance(item, dict):
					continue
				listing = self._parse_house(item)
				if listing is not None:
					unique[listing.source_listing_id] = listing
			page += 1
		return list(unique.values())

	def _parse_house(self, item: dict) -> Listing | None:
		listing_id = item.get("id") or item.get("sourceId")
		relative_url = item.get("url")
		if not listing_id or not isinstance(relative_url, str) or not relative_url.strip():
			return None
		source_url = urljoin(self.base_url, relative_url.strip())
		if not source_url.startswith(f"{self.base_url}/woning/"):
			return None

		address = self._as_dict(item.get("address"))
		prices = self._as_dict(item.get("prices"))
		if prices.get("category") not in (None, "rent"):
			return None
		rental = self._as_dict(prices.get("rental"))
		status = self._as_dict(item.get("status"))
		property_type = self._as_dict(self._as_dict(item.get("attributes")).get("type"))
		status_label = self._text(status.get("name")) or "unknown"
		# Only code 1 is confirmed available; do not infer other numeric codes.
		is_available = self.map_status_to_available(
			status.get("name"), default=status.get("code") == 1
		)
		return Listing(
			source_site=self.source_name,
			source_listing_id=str(listing_id),
			source_url=source_url,
			title=self._text(address.get("house")) or f"Vb&t listing {listing_id}",
			city=self._text(address.get("city")),
			rent_price=self._number(rental.get("price")),
			living_area_m2=self._number(item.get("plot")),
			rooms_total=self._number(item.get("rooms")),
			bedrooms=None,  # Total rooms are not a verified bedroom count.
			available_from=self._text(item.get("acceptance")),
			raw_features={
				"status": status_label,
				"status_code": str(status.get("code", "")),
				"source_id": str(item.get("sourceId") or ""),
				"property_type": str(property_type.get("category") or ""),
				"service_charges": str(rental.get("serviceCharges", "")),
				"parking_charges": str(prices.get("parkingCharges", "")),
				"parking_service_charges": str(prices.get("parkingServiceCharges", "")),
			},
			is_available=is_available,
			listing_status=status_label,
		)

	@staticmethod
	def _as_dict(value: object) -> dict:
		return value if isinstance(value, dict) else {}

	@staticmethod
	def _text(value: object) -> str | None:
		return value.strip() or None if isinstance(value, str) else None

	@staticmethod
	def _number(value: object) -> int | None:
		if isinstance(value, bool) or not isinstance(value, (int, float, str)):
			return None
		try:
			number = float(value)
		except ValueError:
			return None
		return int(round(number)) if math.isfinite(number) and number >= 0 else None
