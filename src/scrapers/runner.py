from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Settings
from src.filtering.rules import evaluate_listing
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
    alerted: int = 0
    details: str = ""


@dataclass
class RunSummary:
    results: list[SourceResult] = field(default_factory=list)

    @property
    def alerted(self) -> int:
        return sum(result.alerted for result in self.results)


def _now() -> str:
    return dt.datetime.now(tz=dt.timezone.utc).isoformat()


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
    # broken does not post on every cycle.
    previous_status = {row["source_site"]: row["status"] for row in store.get_latest_source_runs()}
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

        previous = previous_status.get(policy.name)
        scraper = factory(settings)
        try:
            logger.info("Running source=%s", policy.name)
            listings = scraper.search(max_retries=policy.max_retries)
            health_tracker.mark_success(policy.name)
            logger.info("Source=%s returned %d listings", policy.name, len(listings))

            evaluated_count = 0
            skipped_non_match_count = 0
            changed_count = 0
            alerted_count = 0

            for listing in listings:
                evaluated_count += 1
                match = evaluate_listing(listing, settings)
                is_match = match.is_hard_match or match.is_close_match

                if settings.store_only_matches and not is_match:
                    skipped_non_match_count += 1
                    continue

                upsert = store.upsert_listing(listing)
                if not upsert.changed:
                    continue
                changed_count += 1

                if not is_match:
                    continue

                notifier.notify_listing(listing, match)
                alerted_count += 1

            logger.info(
                "Source=%s changed=%d alerted=%d",
                policy.name,
                changed_count,
                alerted_count,
            )
            logger.info(
                "Source=%s evaluated=%d skipped_non_match=%d store_only_matches=%s",
                policy.name,
                evaluated_count,
                skipped_non_match_count,
                settings.store_only_matches,
            )

            details = (
                f"listings={len(listings)},evaluated={evaluated_count},"
                f"skipped_non_match={skipped_non_match_count},changed={changed_count},"
                f"alerted={alerted_count},store_only_matches={settings.store_only_matches}"
            )
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
                    alerted=alerted_count,
                    details=details,
                )
            )
            if previous in FAILED_STATUSES:
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
            if previous != "error":
                notifier.notify_ops(f"[SOURCE_ERROR] {policy.name} - {error}")

    return summary
