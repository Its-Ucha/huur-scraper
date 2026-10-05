from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import discord

from src.config import Settings
from src.notify.discord_notifier import DESCRIPTION_LIMIT, truncate
from src.scrapers.runner import RunSummary


FIELD_LIMIT = 1024
# Leave headroom below the 4096 limit for the trailing newline handling.
LIST_BUDGET = 4000
STATUS_ICONS = {"ok": "✅", "blocked": "⛔", "error": "❌", "skipped": "⏭️"}


def _money(value) -> str:
    return f"€{value}" if value is not None else "n/a"


def _area(value) -> str:
    return f"{value} m²" if value is not None else "n/a"


def _when(value: dt.datetime | None) -> str:
    return discord.utils.format_dt(value, "R") if value is not None else "never"


def _fit_lines(lines: list[str], budget: int) -> tuple[str, int]:
    text = ""
    shown = 0
    for line in lines:
        if len(text) + len(line) + 1 > budget:
            break
        text += line + "\n"
        shown += 1
    return text.rstrip("\n"), shown


def build_listings_embed(rows: Sequence[Mapping]) -> discord.Embed:
    embed = discord.Embed(title="Recent matches", color=discord.Color.blurple())
    if not rows:
        embed.description = "No listings stored yet."
        return embed

    lines = []
    for row in rows:
        title = truncate(row["title"] or "(untitled)", 80).replace("[", "(").replace("]", ")")
        lines.append(
            f"[{title}]({row['source_url']}) · {_money(row['rent_price'])} · "
            f"{_area(row['living_area_m2'])} · {row['city'] or 'n/a'} · {row['source_site']}"
        )
    embed.description, shown = _fit_lines(lines, LIST_BUDGET)
    if shown < len(rows):
        embed.set_footer(text=f"Showing {shown} of {len(rows)}")
    return embed


def build_status_embed(
    paused: bool,
    cycle_running: bool,
    next_run: dt.datetime | None,
    last_cycle_at: dt.datetime | None,
    source_rows: Sequence[Mapping],
) -> discord.Embed:
    color = discord.Color.orange() if paused else discord.Color.green()
    embed = discord.Embed(title="Scraper status", color=color)
    if paused:
        scheduler = "Paused"
    elif cycle_running:
        scheduler = "Running (cycle in progress)"
    else:
        scheduler = "Running"
    embed.add_field(name="Scheduler", value=scheduler)
    embed.add_field(name="Last cycle", value=_when(last_cycle_at))
    embed.add_field(name="Next run", value="—" if paused or next_run is None else _when(next_run))

    if not source_rows:
        embed.add_field(name="Sources", value="No runs recorded yet.", inline=False)
        return embed

    lines = []
    for row in source_rows:
        icon = STATUS_ICONS.get(row["status"], "•")
        when = str(row["run_at"])[:16].replace("T", " ")
        line = f"{icon} **{row['source_site']}** · {row['status']} · {when} UTC"
        if row["status"] in ("blocked", "error") and row["details"]:
            line += f" · {truncate(str(row['details']), 80)}"
        lines.append(line)
    value, _ = _fit_lines(lines, FIELD_LIMIT)
    embed.add_field(name="Sources", value=value, inline=False)
    return embed


def build_profile_embed(settings: Settings) -> discord.Embed:
    embed = discord.Embed(title="Search profile", color=discord.Color.blurple())
    embed.add_field(name="Max rent", value=f"€{settings.max_rent_eur}")
    embed.add_field(name="Min size", value=f"{settings.min_size_m2} m²")
    embed.add_field(name="Preferred bedrooms", value=str(settings.preferred_bedrooms))
    close = "on (+10% price, −10% area)" if settings.allow_close_match else "off"
    embed.add_field(name="Close match", value=close)
    embed.add_field(name="Interval", value=f"every {settings.scrape_interval_minutes} min")
    cities = ", ".join(settings.allowed_cities) or "n/a"
    embed.add_field(name="Cities", value=truncate(cities, FIELD_LIMIT), inline=False)
    return embed


def build_summary_embed(summary: RunSummary) -> discord.Embed:
    embed = discord.Embed(title="Scrape finished", color=discord.Color.blurple())
    if not summary.results:
        embed.description = "No sources ran."
        return embed

    lines = []
    for result in summary.results:
        icon = STATUS_ICONS.get(result.status, "•")
        if result.status == "ok":
            lines.append(
                f"{icon} **{result.name}** · {result.listings} listings · "
                f"{result.changed} changed · {result.alerted} alerted"
            )
        else:
            lines.append(f"{icon} **{result.name}** · {truncate(result.details, 100)}")
    embed.description, _ = _fit_lines(lines, DESCRIPTION_LIMIT)
    embed.set_footer(text=f"{summary.alerted} new alert(s)")
    return embed
