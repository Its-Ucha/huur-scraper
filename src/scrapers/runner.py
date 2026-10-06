from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Settings
from src.notify.base import Notifier
from src.policy.risk_policy import is_collection_allowed
from src.scrapers.base import SourceBlockedError
from src.scrapers.registry import load_source_policies
from src.scrapers.source_health import SourceHealthState, SourceHealthTracker
from src.storage.sqlite_store import SQLiteStore


logger = logging.getLogger(__name__)

FAILED_STATUSES = ("blocked", "error")


@dataclass
class SourceResult:
    name: str
    status: str
    listings: int = 0
    changed: int = 0
    details: str = ""


@dataclass
class RunSummary:
    results: list[SourceResult] = field(default_factory=list)
    # Set by the caller after dispatching; the runner itself never alerts.
    alerted: int = 0


def _now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


def _failure_was_announced(previous: str | None, before_previous: str | None) -> bool:
    """Whether the failure streak ending in the previous run produced an ops alert."""
    if previous == "blocked":
        return True
    return previous == "error" and before_previous in FAILED_STATUSES


def run_all_sources(
    settings: Settings,
    store: SQLiteStore,
    source_factories: dict[str, callable],
    registry_file: Path,
    notifier: Notifier,
    selected_sources: set[str] | None = None,
) -> RunSummary:
    health_tracker = SourceHealthTracker()
    policies = load_source_policies(registry_file)
    # Ops alerts fire only when a source's status changes, so a source that stays
    # broken does not post on every cycle. Errors are announced on the second
    # failed run in a row, so a one-off timeout stays quiet.
    recent_statuses = store.get_recent_source_statuses(per_source=2)
    summary = RunSummary()
    logger.info("Loaded %d source policies from %s", len(policies), registry_file)

    for policy in policies:
        logger.info("Evaluating source=%s mode=%s", policy.name, policy.mode.value)
        if selected_sources and policy.name not in selected_sources:
            logger.info("Skipping source=%s because it is not selected", policy.name)
            continue

        if not is_collection_allowed(policy):
            logger.info("Skipping source=%s due to policy mode=%s", policy.name, policy.mode.value)
            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="skipped",
                details=f"mode={policy.mode.value}",
            )
            continue

        factory = source_factories.get(policy.name)
        if factory is None:
            logger.warning("No adapter for source=%s", policy.name)
            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="error",
                details="no scraper adapter",
            )
            summary.results.append(
                SourceResult(name=policy.name, status="error", details="no scraper adapter")
            )
            continue

        recent = recent_statuses.get(policy.name, [])
        previous = recent[0] if recent else None
        before_previous = recent[1] if len(recent) > 1 else None
        scraper = factory(settings)
        try:
            logger.info("Running source=%s", policy.name)
            listings = scraper.search(max_retries=policy.max_retries)
            health_tracker.mark_success(policy.name)
            logger.info("Source=%s returned %d listings", policy.name, len(listings))

            changed_count = 0
            for listing in listings:
                if store.upsert_listing(listing).changed:
                    changed_count += 1
            logger.info(
                "Source=%s listings=%d changed=%d", policy.name, len(listings), changed_count
            )

            details = f"listings={len(listings)},changed={changed_count}"
            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="ok",
                details=details,
            )
            summary.results.append(
                SourceResult(
                    name=policy.name,
                    status="ok",
                    listings=len(listings),
                    changed=changed_count,
                    details=details,
                )
            )
            if _failure_was_announced(previous, before_previous):
                notifier.notify_ops(f"[SOURCE_RECOVERED] {policy.name}")

        except SourceBlockedError as error:
            logger.warning("Source blocked source=%s error=%s", policy.name, error)
            state = health_tracker.mark_block(policy.name)
            details = f"blocked: {error}"
            if policy.auto_disable_on_block and state == SourceHealthState.BLOCKED:
                details = f"{details}; action=disable-suggested"

            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="blocked",
                details=details,
            )
            summary.results.append(SourceResult(name=policy.name, status="blocked", details=details))
            if previous != "blocked":
                notifier.notify_ops(f"[SOURCE_BLOCKED] {policy.name} - {state.value}")

        except Exception as error:  # noqa: BLE001
            logger.exception("Source failed source=%s", policy.name)
            store.write_source_run(
                source_site=policy.name,
                run_at=_now(),
                status="error",
                details=str(error),
            )
            summary.results.append(SourceResult(name=policy.name, status="error", details=str(error)))
            if previous == "error" and before_previous != "error":
                notifier.notify_ops(f"[SOURCE_ERROR] {policy.name} - {error}")

    return summary
