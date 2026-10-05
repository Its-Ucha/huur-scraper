from __future__ import annotations

from pathlib import Path

from src.scrapers.base import BaseScraper
from src.scrapers.sites.nrw_wonen import NRWonenScraper
from src.scrapers.sites.thehaguerealestate import TheHagueRealEstateScraper
from src.scrapers.sites.vbent import VBentScraper
from src.scrapers.sites.verra import VerraScraper
from src.scrapers.sites.vesteda import VestedaScraper
from src.scrapers.sites.wobeco import WobecoScraper


REGISTRY_FILE = Path("src/config/sources.yaml")

SOURCE_FACTORIES: dict[str, type[BaseScraper]] = {
    "nrw_wonen": NRWonenScraper,
    "thehaguerealestate": TheHagueRealEstateScraper,
    "wobeco": WobecoScraper,
    "verra": VerraScraper,
    "vesteda": VestedaScraper,
    "vbent": VBentScraper,
}
