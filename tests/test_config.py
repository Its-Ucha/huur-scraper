from __future__ import annotations

import logging
import os
import tempfile
import unittest
from logging.handlers import RotatingFileHandler
from pathlib import Path
from unittest.mock import patch

from src.config import load_settings
from src.logging_setup import configure_logging
from tests.helpers import make_settings


def _load(env: dict[str, str]):
    with patch.dict(os.environ, env, clear=True), patch("src.config.load_dotenv"):
        return load_settings()


class DiscordSettingsTests(unittest.TestCase):
    def test_defaults_when_unset(self) -> None:
        settings = _load({})
        self.assertEqual(settings.discord_bot_token, "")
        self.assertIsNone(settings.discord_guild_id)
        self.assertIsNone(settings.discord_alert_channel_id)
        self.assertIsNone(settings.discord_ops_channel_id)
        self.assertIsNone(settings.discord_mention_user_id)
        self.assertEqual(settings.discord_control_user_ids, [])
        self.assertIsNone(settings.discord_control_role_id)
        self.assertEqual(settings.scrape_interval_minutes, 10)

    def test_ids_parsed_with_sloppy_whitespace(self) -> None:
        settings = _load(
            {
                "DISCORD_BOT_TOKEN": "  tok  ",
                "DISCORD_GUILD_ID": " 123 ",
                "DISCORD_ALERT_CHANNEL_ID": "456",
                "DISCORD_OPS_CHANNEL_ID": "",
                "DISCORD_MENTION_USER_ID": "789",
                "DISCORD_CONTROL_USER_IDS": " 111 , 222,, ",
                "DISCORD_CONTROL_ROLE_ID": "333",
                "SCRAPE_INTERVAL_MINUTES": "15",
            }
        )
        self.assertEqual(settings.discord_bot_token, "tok")
        self.assertEqual(settings.discord_guild_id, 123)
        self.assertEqual(settings.discord_alert_channel_id, 456)
        self.assertIsNone(settings.discord_ops_channel_id)
        self.assertEqual(settings.discord_mention_user_id, 789)
        self.assertEqual(settings.discord_control_user_ids, [111, 222])
        self.assertEqual(settings.discord_control_role_id, 333)
        self.assertEqual(settings.scrape_interval_minutes, 15)

    def test_non_numeric_id_names_the_variable(self) -> None:
        with self.assertRaisesRegex(ValueError, "DISCORD_GUILD_ID"):
            _load({"DISCORD_GUILD_ID": "my-server"})

    def test_non_numeric_id_in_list_names_the_variable(self) -> None:
        with self.assertRaisesRegex(ValueError, "DISCORD_CONTROL_USER_IDS"):
            _load({"DISCORD_CONTROL_USER_IDS": "111,bob"})

    def test_interval_below_one_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "SCRAPE_INTERVAL_MINUTES"):
            _load({"SCRAPE_INTERVAL_MINUTES": "0"})

    def test_log_file_path_default_and_empty(self) -> None:
        self.assertEqual(_load({}).log_file_path, Path("logs/huur_scraper.log"))
        self.assertIsNone(_load({"LOG_FILE_PATH": ""}).log_file_path)
        self.assertIsNone(_load({"LOG_FILE_PATH": "   "}).log_file_path)


class LoggingSetupTests(unittest.TestCase):
    def tearDown(self) -> None:
        root = logging.getLogger()
        for handler in root.handlers:
            handler.close()
        root.handlers.clear()

    def test_no_file_handler_when_log_file_path_is_none(self) -> None:
        configure_logging(make_settings(log_file_path=None, log_to_console=True))
        handlers = logging.getLogger().handlers
        self.assertFalse(any(isinstance(h, RotatingFileHandler) for h in handlers))
        self.assertEqual(len(handlers), 1)

    def test_file_handler_when_path_set(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            configure_logging(make_settings(log_file_path=Path(tmp) / "x.log"))
            handlers = logging.getLogger().handlers
            self.assertTrue(any(isinstance(h, RotatingFileHandler) for h in handlers))
            self.tearDown()


if __name__ == "__main__":
    unittest.main()
