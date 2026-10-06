from __future__ import annotations

from dataclasses import dataclass

from src.filtering.municipalities import municipality_for_place
from src.models.listing import Listing
from src.models.profile import Profile


@dataclass(frozen=True)
class MatchResult:
    is_hard_match: bool
    is_close_match: bool
    score: int
    reasons: list[str]


def evaluate_listing(listing: Listing, profile: Profile) -> MatchResult:
    reasons: list[str] = []
    score = 0

    municipality = municipality_for_place(listing.city)
    hard_price = listing.rent_price is not None and listing.rent_price <= profile.max_rent_eur
    hard_area = listing.living_area_m2 is not None and listing.living_area_m2 >= profile.min_size_m2
    hard_city = municipality is not None and municipality in profile.municipalities
    hard_availability = listing.is_available

    if hard_price:
        score += 40
        reasons.append("price_ok")
    if hard_area:
        score += 30
        reasons.append("size_ok")
    if hard_city:
        score += 20
        reasons.append("city_ok")
    if hard_availability:
        score += 5
        reasons.append("available")

    preferred_bedrooms = listing.bedrooms is not None and listing.bedrooms >= profile.preferred_bedrooms
    if preferred_bedrooms:
        score += 10
        reasons.append("preferred_bedrooms")

    is_hard_match = hard_price and hard_area and hard_city and hard_availability

    relaxed_price = listing.rent_price is not None and listing.rent_price <= int(profile.max_rent_eur * 1.1)
    relaxed_area = listing.living_area_m2 is not None and listing.living_area_m2 >= int(profile.min_size_m2 * 0.9)
    # Close match still requires both dimensions to stay near profile bounds.
    is_close_match = (
        profile.allow_close_match
        and hard_availability
        and hard_city
        and relaxed_price
        and relaxed_area
    )

    return MatchResult(
        is_hard_match=is_hard_match,
        is_close_match=is_close_match and not is_hard_match,
        score=score,
        reasons=reasons,
    )
