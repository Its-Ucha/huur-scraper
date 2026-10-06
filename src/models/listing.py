from __future__ import annotations

from dataclasses import dataclass, asdict
import datetime as dt


@dataclass
class Listing:
    source_site: str
    source_listing_id: str
    source_url: str
    title: str
    city: str | None
    rent_price: int | None
    living_area_m2: int | None
    rooms_total: int | None
    bedrooms: int | None
    available_from: str | None
    raw_features: dict[str, str]
    is_available: bool = True
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    last_changed_at: str | None = None
    listing_status: str = "available"

    def dedupe_key(self) -> str:
        price = self.rent_price if self.rent_price is not None else "na"
        area = self.living_area_m2 if self.living_area_m2 is not None else "na"
        city = (self.city or "na").lower().strip()
        return f"{self.source_site}:{self.source_listing_id}:{city}:{price}:{area}"

    def ref(self) -> str:
        # Unlike dedupe_key this stays the same when the price or area changes.
        return f"{self.source_site}:{self.source_listing_id}"

    def stamp_seen(self) -> None:
        now = dt.datetime.now(tz=dt.timezone.utc).isoformat()
        if not self.first_seen_at:
            self.first_seen_at = now
        self.last_seen_at = now
        if not self.last_changed_at:
            self.last_changed_at = now

    def to_record(self) -> dict:
        return asdict(self)
