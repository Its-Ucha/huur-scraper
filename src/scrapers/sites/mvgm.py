from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup, Tag

from src.scrapers.base import BaseScraper


# BPD Woningfonds and Ikwilhuren both run on MVGM's rental platform: server-rendered
# overview pages with /object/<id>/ detail links. Only the card markup differs.

DUTCH_MONTHS = {
    "januari": 1,
    "februari": 2,
    "maart": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "augustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}
UNAVAILABLE_STATUS_WORDS = ("verhuurd", "gereserveerd", "optie")
_OBJECT_ID_RE = re.compile(r"/object/(?:.*-)?([0-9a-f]{32})/?$")


class MvgmScraper(BaseScraper):
    base_url = ""

    def fetch_soup(self, path: str, *, max_retries: int, params: dict | None = None) -> BeautifulSoup:
        response = self.request_with_backoff(
            "GET",
            f"{self.base_url}{path}",
            max_retries=max_retries,
            headers={"Accept": "text/html"},
            params=params,
        )
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    def absolute_url(self, href: str) -> str:
        if href.startswith("//"):
            return f"https:{href}"
        if href.startswith("/"):
            return f"{self.base_url}{href}"
        return href

    @staticmethod
    def _text(node: Tag | None) -> str:
        return node.get_text(" ", strip=True) if node else ""

    @staticmethod
    def object_id(href: str) -> str | None:
        match = _OBJECT_ID_RE.search(href)
        return match.group(1) if match else None

    @staticmethod
    def parse_price(text: str | None) -> int | None:
        # "987,-", "€ 1.300,- /mnd": dots are thousands separators, cents come after the comma.
        match = re.search(r"\d[\d.]*", text or "")
        return int(match.group(0).replace(".", "")) if match else None

    @staticmethod
    def parse_available_from(text: str | None) -> str | None:
        """'Beschikbaar vanaf 1 november 2026' / '01-11-2026' -> ISO date; other text kept as-is."""
        cleaned = re.sub(r"(?i)^beschikbaar vanaf\s*", "", (text or "").strip())
        if not cleaned:
            return None
        numeric = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{4})", cleaned)
        written = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", cleaned.lower())
        try:
            if numeric:
                day, month, year = (int(part) for part in numeric.groups())
                return dt.date(year, month, day).isoformat()
            if written and written.group(2) in DUTCH_MONTHS:
                day, year = int(written.group(1)), int(written.group(3))
                return dt.date(year, DUTCH_MONTHS[written.group(2)], day).isoformat()
        except ValueError:
            pass
        return cleaned

    @staticmethod
    def split_address(address: str) -> tuple[str, str]:
        match = re.match(r"^(.*?)\s+(\d.*)$", address.strip())
        return (match.group(1), match.group(2)) if match else (address.strip(), "")

    def is_available_status(self, status: str) -> bool:
        lowered = status.lower()
        if any(word in lowered for word in UNAVAILABLE_STATUS_WORDS):
            return False
        return self.map_status_to_available(status, default=True)
