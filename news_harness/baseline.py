"""Platform/author baseline snapshot computation and validation."""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _quantile(sorted_data: list[float], q: float) -> float:
    if not sorted_data:
        return 0.0
    n = len(sorted_data)
    if n == 1:
        return sorted_data[0]
    idx = q * (n - 1)
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    return sorted_data[lo] * (1.0 - frac) + sorted_data[hi] * frac


def compute_baseline_snapshot(outcomes: list[dict], metric_weights: dict) -> dict:
    groups: dict[str, list[dict]] = {}
    for outcome in outcomes:
        platform = outcome.get("platform", "unknown")
        window = outcome.get("window", "4h")
        key = f"{platform}:{window}"
        groups.setdefault(key, []).append(outcome)

    platform_windows: dict[str, dict] = {}
    for key, group_outcomes in groups.items():
        platform = key.split(":")[0]
        platform_entry = metric_weights.get("platforms", {}).get(platform, {})
        weights: dict[str, float] = platform_entry.get("weights", {})

        deltas: list[float] = []
        missing_rates: list[float] = []
        for outcome in group_outcomes:
            baseline = outcome.get("baseline_snapshot") or {}
            current = outcome.get("current_snapshot") or {}
            wd = 0.0
            missing = 0
            total_metrics = 0
            for metric, weight in weights.items():
                total_metrics += 1
                b_val = baseline.get(metric)
                c_val = current.get(metric)
                if b_val is None or c_val is None:
                    missing += 1
                b_num = float(b_val) if b_val is not None else 0.0
                c_num = float(c_val) if c_val is not None else 0.0
                wd += max(0.0, c_num - b_num) * weight
            deltas.append(wd)
            missing_rates.append(missing / max(total_metrics, 1) if total_metrics > 0 else 0.0)

        deltas.sort()
        n = len(deltas)
        platform_windows[key] = {
            "sample_count": n,
            "p50_delta": round(_quantile(deltas, 0.50), 4) if n > 0 else 0.0,
            "p75_delta": round(_quantile(deltas, 0.75), 4) if n > 0 else 0.0,
            "p90_delta": round(_quantile(deltas, 0.90), 4) if n > 0 else 0.0,
            "missing_metric_rate": round(statistics.mean(missing_rates) if missing_rates else 0.0, 4),
        }

    now = datetime.now(timezone.utc)
    created_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    baseline_version = "baseline.v1." + now.strftime("%Y%m%dT%H%M%SZ")
    content = json.dumps(platform_windows, sort_keys=True, default=str)
    content_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()[:16]
    input_refs = list({o.get("source_evidence_ref", "") for o in outcomes if o.get("source_evidence_ref")}) or ["unknown"]

    snapshot: dict[str, Any] = {
        "object_type": "BaselineSnapshot",
        "baseline_version": baseline_version,
        "created_at": created_at,
        "input_refs": input_refs,
        "metric_map_version": metric_weights.get("metric_map_version", "platform_metrics.v1"),
        "threshold_version": "outcome_thresholds.v1",
        "platform_windows": platform_windows,
        "content_hash": content_hash,
    }
    snapshot["validation"] = _validate_platform_windows(platform_windows, created_at)
    return snapshot


def _validate_platform_windows(platform_windows: dict, created_at: str) -> dict:
    checks: list[str] = []
    all_ok = True
    for key, pw in sorted(platform_windows.items()):
        if pw["sample_count"] < 30:
            checks.append(f"sample_count:{key}={pw['sample_count']}<30")
            all_ok = False
        else:
            checks.append(f"sample_count:{key}=ok")
        if pw["p50_delta"] <= pw["p75_delta"] <= pw["p90_delta"]:
            checks.append(f"quantile_order:{key}=ok")
        else:
            checks.append(f"quantile_order:{key}=failed")
            all_ok = False
        if pw["missing_metric_rate"] <= 0.20:
            checks.append(f"missing_metric_rate:{key}=ok")
        else:
            checks.append(f"missing_metric_rate:{key}={pw['missing_metric_rate']}>0.20")
            all_ok = False
    try:
        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created_dt > datetime.now(timezone.utc):
            checks.append("future_leakage:created_at in future")
            all_ok = False
        else:
            checks.append("future_leakage:ok")
    except (ValueError, TypeError):
        checks.append("future_leakage:cannot_parse")
        all_ok = False
    return {"status": "ok" if all_ok else "invalid", "checks": checks}


def validate_baseline(snapshot: dict) -> dict:
    platform_windows = snapshot.get("platform_windows", {})
    created_at = snapshot.get("created_at", "")
    return _validate_platform_windows(platform_windows, created_at)


def load_baseline(path: Path) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            snapshot = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    validation = validate_baseline(snapshot)
    if validation.get("status") != "ok":
        return None
    return snapshot


def lookup_baseline(snapshot: dict, platform: str, window: str) -> dict:
    key = f"{platform}:{window}"
    pw = snapshot.get("platform_windows", {}).get(key)
    if pw is None:
        return {"p50_delta": 0.0, "p75_delta": 0.0, "p90_delta": 0.0, "sample_count": 0}
    return {
        "p50_delta": float(pw.get("p50_delta", 0.0)),
        "p75_delta": float(pw.get("p75_delta", 0.0)),
        "p90_delta": float(pw.get("p90_delta", 0.0)),
        "sample_count": int(pw.get("sample_count", 0)),
    }
