from __future__ import annotations

import logging
import sys

import discord

from src.bot.checks import validate_bot_settings
from src.bot.client import HuurBot
from src.config import load_settings
from src.logging_setup import configure_logging
from src.storage.sqlite_store import SQLiteStore


def main() -> int:
    try:
        settings = load_settings()
    except ValueError as error:
        print(f"Config error: {error}", file=sys.stderr)
        return 2

    configure_logging(settings)
    logger = logging.getLogger("src.bot")

    errors = validate_bot_settings(settings)
    if errors:
        for error in errors:
            logger.error("Config error: %s", error)
        return 2

    store = SQLiteStore(settings.database_path)
    bot = HuurBot(settings, store)
    try:
        # log_handler=None keeps the logging configured by configure_logging.
        bot.run(settings.discord_bot_token, log_handler=None)
    except discord.LoginFailure:
        logger.error("Discord rejected DISCORD_BOT_TOKEN")
        return 1
    return bot.exit_code


if __name__ == "__main__":
    sys.exit(main())
