"""Rolling candidate store for cross-cycle persistence.

Separates per-cycle snapshot artifacts (source_run, schedule, outcome) from the
cross-cycle rolling store that keeps baseline engagement snapshots alive for
true delayed-outcome measurement.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .fixtures import load_json

STORE_VERSION = "rolling_candidate_store.v1"
DEFAULT_STORE_PATH = Path("artifacts/manual_smoke/rolling_candidate_store.json")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def load(path: Path = DEFAULT_STORE_PATH) -> dict[str, Any]:
    """Load the rolling candidate store, returning an empty store if missing.

    Returns an empty store for missing files. Warns (via stderr) if the file
    exists but is corrupted or has the wrong object_type.
    """
    if not path.exists():
        return _empty_store()
    try:
        data = load_json(path)
        if isinstance(data, dict) and data.get("object_type") == "RollingCandidateStore":
            return data
        import sys
        print(
            f"rolling_store: {path} has unexpected object_type={data.get('object_type')!r}, "
            f"starting with empty store",
            file=sys.stderr,
        )
    except (OSError, Exception) as exc:
        import sys
        print(
            f"rolling_store: failed to load {path}: {exc}, starting with empty store",
            file=sys.stderr,
        )
    return _empty_store()


def _empty_store() -> dict[str, Any]:
    return {
        "object_type": "RollingCandidateStore",
        "store_version": STORE_VERSION,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "cycles": [],
        "candidates": {},
    }


def save(store: dict[str, Any], path: Path = DEFAULT_STORE_PATH) -> None:
    """Persist the rolling store to disk."""
    store["updated_at"] = _utc_now()
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def register_candidates(
    store: dict[str, Any],
    cycle_id: str,
    candidates: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    windows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Register a new cycle's candidates in the rolling store.

    For each candidate, record the baseline engagement snapshot and schedule
    due_at timestamps for every feedback window. Deduplicate by candidate_id.

    Returns the updated store.
    """
    obs_by_ref = {
        obs.get("evidence_ref"): obs for obs in observations if isinstance(obs, dict)
    }
    now = _utc_now()
    new_ids = []

    for candidate in candidates:
        cid = candidate.get("candidate_id")
        if not cid:
            continue
        ref = candidate.get("source_observation_ref")
        observation = obs_by_ref.get(ref, {})
        evaluated_at = _parse_utc(candidate.get("evaluated_at")) or datetime.now(timezone.utc)

        entry = store["candidates"].get(cid, {})
        if not entry:
            entry = {
                "candidate_id": cid,
                "registered_at": now,
                "dedupe_key": candidate.get("dedupe_key", cid),
                "source_observation_ref": ref,
                "source_url": observation.get("source_url"),
                "canonical_url": observation.get("canonical_url"),
                "source": observation.get("source"),
                "baseline_engagement_snapshot": deepcopy(observation.get("engagement_snapshot", {})),
                "windows": {},
                "outcomes_collected": [],
            }
            new_ids.append(cid)

        for window in windows:
            wname = window["window"]
            if wname not in entry["windows"]:
                due_at = evaluated_at + timedelta(minutes=int(window["minutes"]))
                entry["windows"][wname] = {
                    "window": wname,
                    "window_minutes": window["minutes"],
                    "role": window.get("role"),
                    "due_at": due_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "evaluated_at": candidate.get("evaluated_at"),
                    "prediction_score": candidate.get("scores", {}).get(wname),
                    "status": "pending",
                    "outcome_id": None,
                }

        store["candidates"][cid] = entry

    cycle_record = {
        "cycle_id": cycle_id,
        "started_at": now,
        "candidate_count": len(candidates),
        "new_candidate_ids": new_ids,
        "windows_configured": [w["window"] for w in windows],
    }
    store["cycles"].append(cycle_record)
    store["updated_at"] = now
    return store


def get_due_revisits(
    store: dict[str, Any], now: datetime | None = None
) -> list[dict[str, Any]]:
    """Return all pending revisit tasks whose due_at has passed.

    Each result includes the candidate_id, window, due_at, observation_ref,
    source_url, and the stored baseline snapshot for diffing.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    due = []
    for cid, entry in store["candidates"].items():
        for wname, wdata in entry.get("windows", {}).items():
            if wdata.get("status") != "pending":
                continue
            due_at = _parse_utc(wdata.get("due_at"))
            if due_at and due_at <= now:
                due.append({
                    "candidate_id": cid,
                    "window": wname,
                    "due_at": wdata["due_at"],
                    "source_observation_ref": entry.get("source_observation_ref"),
                    "source_url": entry.get("source_url"),
                    "source": entry.get("source"),
                    "prediction_score": wdata.get("prediction_score"),
                    "baseline_engagement_snapshot": deepcopy(entry.get("baseline_engagement_snapshot", {})),
                })
    return due


def record_outcome(
    store: dict[str, Any],
    candidate_id: str,
    window: str,
    outcome_id: str,
    engagement_growth: dict[str, Any],
) -> dict[str, Any]:
    """Mark a revisit window as completed and record the outcome reference."""
    entry = store["candidates"].get(candidate_id, {})
    wdata = entry.get("windows", {}).get(window, {})
    if wdata:
        wdata["status"] = "collected"
        wdata["outcome_id"] = outcome_id
        entry["windows"][window] = wdata
    entry.setdefault("outcomes_collected", []).append({
        "window": window,
        "outcome_id": outcome_id,
        "collected_at": _utc_now(),
        "engagement_growth_summary": {
            k: v for k, v in engagement_growth.items()
            if k in ("total_growth", "growth_score", "metric_deltas")
        },
    })
    store["candidates"][candidate_id] = entry
    store["updated_at"] = _utc_now()
    return store


def earliest_due_at(store: dict[str, Any]) -> datetime | None:
    """Return the earliest pending due_at across all candidates, or None."""
    now = datetime.now(timezone.utc)
    earliest = None
    for entry in store["candidates"].values():
        for wdata in entry.get("windows", {}).values():
            if wdata.get("status") != "pending":
                continue
            due_at = _parse_utc(wdata.get("due_at"))
            if due_at and due_at > now:
                if earliest is None or due_at < earliest:
                    earliest = due_at
    return earliest
