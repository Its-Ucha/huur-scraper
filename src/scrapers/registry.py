from __future__ import annotations

from pathlib import Path

import yaml

from src.policy.risk_policy import SourceMode, SourcePolicy


def load_source_policies(file_path: Path) -> list[SourcePolicy]:
    data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    sources = data.get("sources", [])
    policies: list[SourcePolicy] = []

    for source in sources:
        policies.append(
            SourcePolicy(
                name=source["name"],
                mode=SourceMode(source["mode"]),
                min_interval_seconds=int(source["min_interval_seconds"]),
                max_retries=int(source["max_retries"]),
                auto_disable_on_block=bool(source["auto_disable_on_block"]),
            )
        )

    return policies


def slowest_scrape_interval_seconds(policies: list[SourcePolicy]) -> int:
    return max(
        (p.min_interval_seconds for p in policies if p.mode == SourceMode.SCRAPE),
        default=0,
    )
