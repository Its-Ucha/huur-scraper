import unittest
from unittest.mock import Mock, patch

import httpx

from src.config import Settings
from src.scrapers.base import BaseScraper


def _scraper() -> BaseScraper:
	settings = Mock(spec=Settings)
	settings.user_agent = "test-agent"
	settings.request_timeout_seconds = 20
	return BaseScraper(settings)


@patch("src.scrapers.base.time.sleep")
class RequestWithBackoffTimeoutTests(unittest.TestCase):
	def test_retries_after_timeout(self, _sleep):
		ok = httpx.Response(200, request=httpx.Request("GET", "https://example.test"))
		with patch(
			"src.scrapers.base.httpx.request",
			side_effect=[httpx.ReadTimeout("The read operation timed out"), ok],
		) as request:
			response = _scraper().request_with_backoff("GET", "https://example.test", max_retries=1)

		self.assertIs(response, ok)
		self.assertEqual(request.call_count, 2)

	def test_raises_timeout_when_retries_exhausted(self, _sleep):
		with patch(
			"src.scrapers.base.httpx.request",
			side_effect=httpx.ReadTimeout("The read operation timed out"),
		) as request:
			with self.assertRaises(httpx.ReadTimeout):
				_scraper().request_with_backoff("GET", "https://example.test", max_retries=2)

		self.assertEqual(request.call_count, 3)


if __name__ == "__main__":
	unittest.main()
