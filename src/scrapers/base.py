from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

import httpx

from src.config import Settings
from src.models.listing import Listing
from src.scrapers.scope import SearchScope


_BLOCK_STATUSES = {403, 429}
_TRANSPORT_RETRY_WAIT = 15
logger = logging.getLogger(__name__)


class SourceBlockedError(Exception):
    pass


class BaseScraper:
    source_name: str = "base"

    def __init__(self, settings: Settings, scope: SearchScope | None = None) -> None:
        self.settings = settings
        self.scope = scope or SearchScope()

    def search(self) -> list[Listing]:
        raise NotImplementedError

    def default_headers(self, headers: dict[str, str] | None = None) -> dict[str, str]:
        merged = {
            "User-Agent": self.settings.user_agent,
            "Accept": "*/*",
        }
        if headers:
            merged.update(headers)
        return merged

    def request_with_backoff(
        self,
        method: str,
        url: str,
        *,
        max_retries: int = 2,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        json_payload: Any | None = None,
        form_data: dict[str, Any] | None = None,
    ) -> httpx.Response:
        request_headers = self.default_headers(headers)
        timeout = self.settings.request_timeout_seconds
        base_wait = 2
        normalized_method = method.upper()

        for attempt in range(max_retries + 1):
            self.jitter_sleep(0.8, 1.8)
            logger.info(
                "Request source=%s method=%s url=%s attempt=%d/%d",
                self.source_name,
                normalized_method,
                url,
                attempt + 1,
                max_retries + 1,
            )

            try:
                response = httpx.request(
                    method=normalized_method,
                    url=url,
                    headers=request_headers,
                    params=params,
                    json=json_payload,
                    data=form_data,
                    timeout=timeout,
                    follow_redirects=True,
                )
            except httpx.TransportError as exc:
                # Timeouts and dropped connections are usually short slow spells on the site.
                if attempt >= max_retries:
                    raise
                wait_seconds = _TRANSPORT_RETRY_WAIT * (attempt + 1)
                logger.warning(
                    "Transport error source=%s method=%s error=%s; retrying in %ss",
                    self.source_name,
                    normalized_method,
                    exc,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
                continue
            logger.info(
                "Response source=%s method=%s status=%s url=%s",
                self.source_name,
                normalized_method,
                response.status_code,
                url,
            )

            if response.status_code in _BLOCK_STATUSES:
                if attempt >= max_retries:
                    raise SourceBlockedError(f"Blocked with status {response.status_code} for {self.source_name}")
                wait_seconds = base_wait * (attempt + 1)
                logger.warning(
                    "Blocked response source=%s method=%s status=%s; backing off for %ss",
                    self.source_name,
                    normalized_method,
                    response.status_code,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
                continue

            if response.status_code >= 500 and attempt < max_retries:
                wait_seconds = base_wait * (attempt + 1)
                logger.warning(
                    "Server error source=%s method=%s status=%s; retrying in %ss",
                    self.source_name,
                    normalized_method,
                    response.status_code,
                    wait_seconds,
                )
                time.sleep(wait_seconds)
                continue

            return response

        raise RuntimeError(f"Failed to request {url}")

    def fetch_json(
        self,
        url: str,
        *,
        max_retries: int = 2,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        method: str = "GET",
        json_payload: Any | None = None,
        form_data: dict[str, Any] | None = None,
    ) -> Any:
        merged_headers = self.default_headers(headers)
        merged_headers.setdefault("Accept", "application/json")

        response = self.request_with_backoff(
            method=method,
            url=url,
            max_retries=max_retries,
            headers=merged_headers,
            params=params,
            json_payload=json_payload,
            form_data=form_data,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def jitter_sleep(min_seconds: float, max_seconds: float) -> None:
        time.sleep(random.uniform(min_seconds, max_seconds))

    @staticmethod
    def parse_int(value: str | None) -> int | None:
        if not value:
            return None
        cleaned = re.sub(r"[^0-9]", "", value)
        if not cleaned:
            return None
        return int(cleaned)

    @staticmethod
    def map_status_to_available(status: object, default: bool = True) -> bool:
        if status is None:
            return default

        if isinstance(status, bool):
            return status

        if isinstance(status, (int, float)):
            return status > 0

        text = str(status).strip().lower()
        if not text:
            return default

        unavailable_tokens = {
            "unavailable",
            "not available",
            "rented",
            "verhuurd",
            "onder optie",
            "option",
            "sold",
            "inactive",
            "off market",
            "niet beschikbaar",
        }
        available_tokens = {
            "available",
            "te huur",
            "for rent",
            "actief",
            "active",
            "beschikbaar",
            "open",
            "1",
            "true",
            "yes",
        }

        if text in unavailable_tokens:
            return False
        if text in available_tokens:
            return True

        return default
