"""Per-source-run connector quality reporting.

Produces ConnectorQualityReport dicts matching
schemas/v1/connector_quality_report.schema.json.
"""

from __future__ import annotations

import re
from typing import Any

_EXPECTED_METRIC_FIELDS = ("likes", "comments", "reposts", "views")

_TRUNCATION_MARKERS_RE = re.compile(
    r"\u2026$|\.\.\.$|\[truncated\]|\(truncated\)|\[\u2026\]",
    re.IGNORECASE,
)


def generate_connector_quality_report(
    observations: list[dict[str, Any]],
    connector_id: str,
    run_id: str,
    rolling_medians: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Produce a ConnectorQualityReport for a single source run."""
    item_count = len(observations)

    rolling_median: float | None = None
    if rolling_medians and connector_id in rolling_medians:
        rolling_median = float(rolling_medians[connector_id])

    if rolling_median is not None and rolling_median > 0:
        item_count_vs_rolling_median = round(item_count / rolling_median, 4)
    else:
        item_count_vs_rolling_median = 1.0

    field_totals = {"source_url": 0, "copy_text": 0, "published_at": 0, "engagement_snapshot": 0}
    metric_hits = 0
    metric_possible = 0
    duplicate_urls: set[str] = set()
    duplicate_count = 0
    truncation_suspected = False
    structured_error_count = 0

    for obs in observations:
        if not isinstance(obs, dict):
            structured_error_count += 1
            continue

        if _non_empty_str(obs.get("source_url")):
            field_totals["source_url"] += 1
        if _non_empty_str(obs.get("copy_text")):
            field_totals["copy_text"] += 1
        if _non_empty(obs.get("published_at")):
            field_totals["published_at"] += 1
        if isinstance(obs.get("engagement_snapshot"), dict):
            field_totals["engagement_snapshot"] += 1

        snap = obs.get("engagement_snapshot")
        if isinstance(snap, dict):
            for field in _EXPECTED_METRIC_FIELDS:
                metric_possible += 1
                if _non_empty(snap.get(field)):
                    metric_hits += 1

        url = obs.get("source_url")
        if isinstance(url, str) and url:
            if url in duplicate_urls:
                duplicate_count += 1
            else:
                duplicate_urls.add(url)

        copy_text = obs.get("copy_text")
        if isinstance(copy_text, str) and _TRUNCATION_MARKERS_RE.search(copy_text):
            truncation_suspected = True
        if isinstance(copy_text, str) and 0 < len(copy_text.strip()) < 20:
            truncation_suspected = True

        if obs.get("structured_error") is not None:
            structured_error_count += 1

    denom = max(item_count, 1)

    required_field_presence = {
        "source_url": round(field_totals["source_url"] / denom, 4),
        "copy_text": round(field_totals["copy_text"] / denom, 4),
        "published_at": round(field_totals["published_at"] / denom, 4),
        "engagement_snapshot": round(field_totals["engagement_snapshot"] / denom, 4),
    }

    metric_completeness = round(metric_hits / max(metric_possible, 1), 4)
    duplicate_rate = round(duplicate_count / denom, 4)

    quality_status: str
    if item_count == 0:
        quality_status = "blocked"
    elif (
        item_count_vs_rolling_median < 0.5
        or any(v < 0.9 for v in required_field_presence.values())
        or metric_completeness < 0.8
        or truncation_suspected
    ):
        quality_status = "degraded"
    else:
        quality_status = "ok"

    return {
        "object_type": "ConnectorQualityReport",
        "connector_id": connector_id,
        "run_id": run_id,
        "quality_status": quality_status,
        "item_count": item_count,
        "item_count_vs_rolling_median": item_count_vs_rolling_median,
        "required_field_presence": required_field_presence,
        "metric_completeness": metric_completeness,
        "duplicate_rate": duplicate_rate,
        "truncation_suspected": truncation_suspected,
        "structured_error_count": structured_error_count,
    }


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _non_empty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    if isinstance(value, (list, dict)) and len(value) == 0:
        return False
    return True
