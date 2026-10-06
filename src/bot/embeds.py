from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence

import discord

from src.filtering.municipalities import get_municipality
from src.models.mark import APPLIED, NOT_INTERESTED, ListingMark
from src.models.profile import Profile
from src.notify.discord_notifier import DESCRIPTION_LIMIT, truncate
from src.scrapers.runner import RunSummary


FIELD_LIMIT = 1024
# Leave headroom below the 4096 limit for the trailing newline handling.
LIST_BUDGET = 4000
STATUS_ICONS = {"ok": "✅", "blocked": "⛔", "error": "❌", "skipped": "⏭️"}
MARK_ICONS = {APPLIED: "✅", NOT_INTERESTED: "🚫"}
LISTINGS_TITLES = {
    "new": "New matches",
    APPLIED: "Applied",
    NOT_INTERESTED: "Not interested",
    "all": "Current matches",
}
LISTINGS_EMPTY_TEXTS = {
    "new": "No new listings match your profile.",
    APPLIED: "You haven't marked any listing as applied yet.",
    NOT_INTERESTED: "You haven't marked any listing as not interested.",
    "all": "No current listings match your profile.",
}


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


def listing_marker(mark: ListingMark | None, offline: bool) -> str:
    parts = [MARK_ICONS[mark.state]] if mark is not None else []
    if offline:
        parts.append("(offline)")
    return " ".join(parts)


def build_listings_embed(
    rows: Sequence[Mapping],
    empty_text: str = "No listings stored yet.",
    title: str = "Recent matches",
    markers: Sequence[str] = (),
) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.blurple())
    if not rows:
        embed.description = empty_text
        return embed

    lines = []
    for index, row in enumerate(rows):
        title = truncate(row["title"] or "(untitled)", 80).replace("[", "(").replace("]", ")")
        marker = markers[index] if index < len(markers) else ""
        lines.append(
            (f"{marker} " if marker else "")
            + f"[{title}]({row['source_url']}) · {_money(row['rent_price'])} · "
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
    profile_counts: tuple[int, int] | None = None,
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
    if profile_counts is not None:
        active, total = profile_counts
        embed.add_field(name="Profiles", value=f"{active} active / {total} total")

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
                f"{icon} **{result.name}** · {result.listings} listings · {result.changed} changed"
            )
        else:
            lines.append(f"{icon} **{result.name}** · {truncate(result.details, 100)}")
    embed.description, _ = _fit_lines(lines, DESCRIPTION_LIMIT)
    embed.set_footer(text=f"{summary.alerted} new alert(s)")
    return embed


def municipality_names(keys: Iterable[str]) -> list[str]:
    names = []
    for key in keys:
        municipality = get_municipality(key)
        names.append(municipality.name if municipality is not None else key)
    return sorted(names, key=str.lower)


def build_profile_panel_embed(profile: Profile) -> discord.Embed:
    color = discord.Color.orange() if profile.paused else discord.Color.blurple()
    embed = discord.Embed(title="Your search profile", color=color)
    embed.add_field(name="Max rent", value=_money(profile.max_rent_eur))
    embed.add_field(name="Min size", value=_area(profile.min_size_m2))
    embed.add_field(name="Bedrooms", value=f"{profile.preferred_bedrooms} (preferred)")
    close = "on (+10% price, −10% area)" if profile.allow_close_match else "off"
    embed.add_field(name="Close match", value=close)
    cities = ", ".join(municipality_names(profile.municipalities))
    embed.add_field(
        name="Cities",
        value=truncate(cities, FIELD_LIMIT) if cities else "None yet. Press **Cities** to pick some.",
        inline=False,
    )
    state = "paused" if profile.paused else "active"
    embed.add_field(name="Alerts", value=f"<#{profile.channel_id}> ({state})", inline=False)
    return embed


def build_no_profile_embed() -> discord.Embed:
    return discord.Embed(
        title="No search profile yet",
        description=(
            "Create one to get your own private alerts channel. "
            "You can set rent, size and cities right after."
        ),
        color=discord.Color.blurple(),
    )


def build_profiles_list_embed(profiles: Sequence[Profile]) -> discord.Embed:
    embed = discord.Embed(title="Search profiles", color=discord.Color.blurple())
    if not profiles:
        embed.description = "No profiles yet."
        return embed
    lines = []
    for profile in profiles:
        state = "paused" if profile.paused else "active"
        cities = ", ".join(municipality_names(profile.municipalities)) or "no cities"
        lines.append(
            f"<@{profile.owner_user_id}> → <#{profile.channel_id}> · {_money(profile.max_rent_eur)} · "
            f"≥{_area(profile.min_size_m2)} · {state} · {truncate(cities, 200)}"
        )
    embed.description, shown = _fit_lines(lines, LIST_BUDGET)
    if shown < len(lines):
        embed.set_footer(text=f"Showing {shown} of {len(lines)}")
    return embed
