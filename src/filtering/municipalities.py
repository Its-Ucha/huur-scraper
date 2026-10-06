from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml


MUNICIPALITIES_FILE = Path("src/config/municipalities.yaml")


@dataclass(frozen=True)
class Municipality:
    key: str
    name: str
    lat: float
    lon: float
    places: tuple[str, ...]

    @property
    def main_place(self) -> str:
        return self.places[0] if self.places else self.name


def normalize_place_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def load_municipalities(path: Path = MUNICIPALITIES_FILE) -> list[Municipality]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [
        Municipality(
            key=str(item["key"]),
            name=str(item["name"]),
            lat=float(item["lat"]),
            lon=float(item["lon"]),
            places=tuple(str(place) for place in item.get("places", [])),
        )
        for item in data.get("municipalities", [])
    ]


def build_place_index(municipalities: Iterable[Municipality]) -> dict[str, str]:
    index: dict[str, str] = {}
    for municipality in municipalities:
        for place in (municipality.name, *municipality.places):
            token = normalize_place_token(place)
            existing = index.get(token)
            if existing is not None and existing != municipality.key:
                raise ValueError(
                    f"Place {place!r} is listed under both {existing} and {municipality.key}"
                )
            index[token] = municipality.key
    return index


@lru_cache(maxsize=1)
def _loaded() -> tuple[tuple[Municipality, ...], dict[str, str]]:
    municipalities = load_municipalities()
    return tuple(municipalities), build_place_index(municipalities)


def all_municipalities() -> list[Municipality]:
    return sorted(_loaded()[0], key=lambda municipality: municipality.name.lower())


def get_municipality(key: str) -> Municipality | None:
    return next((item for item in _loaded()[0] if item.key == key), None)


def municipality_for_place(city: str | None) -> str | None:
    if not city:
        return None
    return _loaded()[1].get(normalize_place_token(city))
