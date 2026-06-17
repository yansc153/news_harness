"""Deterministic OutcomeEvaluator — V1 formulas and outcome verdict."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .baseline import lookup_baseline

_FAILURE_LEARNING_MAP: dict[str | None, str] = {
    "missing_outcome": "not_learnable",
    "metrics_unavailable": "not_learnable",
    "auth_failed": "not_learnable",
    "source_deleted": "not_learnable",
    "window_missed": "not_learnable",
    "metric_invalid": "not_learnable",
    "leakage_detected": "not_learnable",
    "risk_blocked": "not_learnable",
    "connector_unsupported": "not_learnable",
}

_SUCCESS_GRADE_LEARNING_MAP: dict[str, str] = {
    "not_measurable": "not_learnable",
    "not_meaningful": "not_learnable",
    "weak": "learn_neutral",
    "meaningful": "learn_positive_shadow",
    "breakout": "learn_positive_shadow",
}


def evaluate_outcome(
    outcome_record: dict,
    prediction_record: dict,
    baseline_snapshot: dict | None,
    platform_weights: dict,
    thresholds: dict,
    *,
    platform: str | None = None,
) -> dict:
    if platform is None:
        platform = outcome_record.get("platform") or "xueqiu"
    platform = platform.lower()

    outcome_id = outcome_record.get("outcome_id", "unknown")
    candidate_id = outcome_record.get("candidate_id", "unknown")
    prediction_id = prediction_record.get("prediction_id", "unknown")
    window = outcome_record.get("window", "4h")
    window_role = outcome_record.get("window_role", "primary_outcome")
    source_evidence_ref = outcome_record.get("source_evidence_ref", "")
    collected_at = outcome_record.get("collected_at", "")
    failure_state = outcome_record.get("failure_state")
    observation_status = outcome_record.get("observation_status", "")
    source_availability = outcome_record.get("source_availability", "")

    platforms_cfg = platform_weights.get("platforms", {})
    platform_info = platforms_cfg.get(platform, platforms_cfg.get("xueqiu", {}))
    weights: dict[str, float] = platform_info.get("weights", {})

    platform_thresholds: list[dict] = thresholds.get("platform_thresholds", [])
    threshold_entry: dict = next(
        (t for t in platform_thresholds if t.get("platform") == platform and t.get("window") == window), {}
    )
    platform_min_denominator: float = float(threshold_entry.get("platform_min_denominator", 30))
    platform_min_delta: float = float(threshold_entry.get("platform_min_delta", 15))

    pred_thresholds: list[dict] = thresholds.get("prediction_thresholds", [])
    pred_entry: dict = next(
        (t for t in pred_thresholds if t.get("window") == window and t.get("role") == window_role),
        next((t for t in pred_thresholds if t.get("window") == window), {}),
    )
    prediction_threshold: float = float(pred_entry.get("prediction_threshold", 0.65))

    baseline_snap: dict = outcome_record.get("baseline_snapshot") or {}
    current_snap: dict = outcome_record.get("current_snapshot") or {}

    weighted_baseline = 0.0
    weighted_delta = 0.0
    for metric, weight in weights.items():
        b_val = baseline_snap.get(metric)
        c_val = current_snap.get(metric)
        b_num = float(b_val) if b_val is not None else 0.0
        c_num = float(c_val) if c_val is not None else 0.0
        weighted_baseline += b_num * weight
        weighted_delta += max(0.0, c_num - b_num) * weight

    raw_delta: dict[str, float | None] = {
        k: outcome_record.get("raw_delta", {}).get(k) for k in ("likes", "comments", "shares", "views")
    }

    if baseline_snapshot is None:
        baseline_status = "baseline_missing"
    elif baseline_snapshot.get("validation", {}).get("status") != "ok":
        baseline_status = "baseline_invalid"
    else:
        key = f"{platform}:{window}"
        pw = baseline_snapshot.get("platform_windows", {}).get(key)
        baseline_status = "baseline_ready" if pw and pw.get("sample_count", 0) >= 30 else "baseline_cold_start"

    denominator = max(weighted_baseline, platform_min_denominator)
    relative_growth = weighted_delta / denominator if denominator > 0 else 0.0

    if baseline_snapshot is not None and baseline_status == "baseline_ready":
        bl = lookup_baseline(baseline_snapshot, platform, window)
        platform_p75_delta = bl["p75_delta"]
        platform_p90_delta = bl["p90_delta"]
    else:
        platform_p75_delta = 0.0
        platform_p90_delta = 0.0

    pnorm_denom = max(platform_p75_delta, platform_min_delta)
    platform_normalized_growth = min(max(weighted_delta / pnorm_denom, 0.0), 3.0) if pnorm_denom > 0 else 0.0
    author_normalized_growth = None

    confidence = float(prediction_record.get("confidence", 0.5))
    if confidence >= 0.8:
        confidence_band = "high"
    elif confidence >= 0.5:
        confidence_band = "medium"
    else:
        confidence_band = "low"

    quality_gates: dict[str, bool] = {
        "evidence_complete": observation_status == "observed",
        "metrics_available": bool(outcome_record.get("metrics_source")),
        "source_available": source_availability == "available",
        "not_duplicate": True,
        "risk_allowed": True,
        "no_holdout_leakage": True,
    }

    outcome_labels: list[str] = []
    if source_availability == "available":
        outcome_labels.append("source_available")
    elif source_availability == "deleted":
        outcome_labels.append("source_deleted")
    if bool(outcome_record.get("metrics_source")):
        outcome_labels.append("metrics_available")
    else:
        outcome_labels.append("metrics_unavailable")
    if observation_status == "observed":
        outcome_labels.append("raw_growth_observed")

    if weighted_baseline < platform_min_denominator:
        outcome_labels.append("insufficient_denominator")
    else:
        outcome_labels.append("baseline_normal")

    if platform_normalized_growth > 0:
        if window_role == "early_momentum" and window == "1h":
            outcome_labels.append("early_momentum_1h")
        elif window_role == "primary_outcome" and window == "4h":
            outcome_labels.append("primary_spread_4h")
        if baseline_status == "baseline_ready" and weighted_delta >= platform_p75_delta:
            outcome_labels.append("platform_local_lift")

    if failure_state is not None:
        success_grade = "not_measurable"
    elif baseline_status == "baseline_invalid":
        success_grade = "not_measurable"
    elif weighted_baseline < platform_min_denominator:
        success_grade = "not_meaningful"
    elif platform_normalized_growth == 0.0:
        success_grade = "not_meaningful"
    else:
        if baseline_status == "baseline_ready":
            if weighted_delta >= platform_p90_delta and platform_p90_delta > 0:
                success_grade = "breakout"
            elif weighted_delta >= platform_p75_delta and platform_p75_delta > 0:
                success_grade = "meaningful"
            else:
                success_grade = "weak"
        else:
            success_grade = "weak"

    if success_grade in ("not_meaningful", "weak") and "weak_signal" not in outcome_labels:
        outcome_labels.append("weak_signal")

    score_key = f"{window}_score"
    prediction_score = float(prediction_record.get(score_key, 0.0))
    predicted_positive = prediction_score >= prediction_threshold
    actual_positive = success_grade in ("meaningful", "breakout")
    hit = predicted_positive == actual_positive
    calibration_error = min(abs(prediction_score - (1.0 if actual_positive else 0.0)), 1.0)

    if failure_state:
        learning_eligibility = _FAILURE_LEARNING_MAP.get(failure_state, "not_learnable")
    else:
        learning_eligibility = _SUCCESS_GRADE_LEARNING_MAP.get(success_grade, "not_learnable")

    is_false_positive = predicted_positive and not actual_positive
    if is_false_positive and learning_eligibility == "learn_neutral" and confidence_band == "high" and all(quality_gates.values()):
        learning_eligibility = "learn_negative"
    if is_false_positive:
        outcome_labels.append("false_positive")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    evaluation_id = f"outcome_eval_{uuid.uuid4().hex[:12]}"

    return {
        "object_type": "OutcomeEvaluation",
        "evaluation_id": evaluation_id,
        "evaluation_version": "outcome_evaluator.v1",
        "prediction_id": prediction_id,
        "outcome_id": outcome_id,
        "candidate_id": candidate_id,
        "source_evidence_ref": source_evidence_ref,
        "window": window,
        "window_role": window_role,
        "evaluated_at": now,
        "collected_at": collected_at,
        "platform": platform,
        "metric_map_version": platform_weights.get("metric_map_version", "platform_metrics.v1"),
        "threshold_version": thresholds.get("threshold_version", "outcome_thresholds.v1"),
        "raw_delta": raw_delta,
        "relative_growth": round(relative_growth, 6),
        "platform_normalized_growth": round(platform_normalized_growth, 6),
        "author_normalized_growth": author_normalized_growth,
        "baseline_status": baseline_status,
        "confidence_band": confidence_band,
        "outcome_labels": outcome_labels,
        "success_grade": success_grade,
        "hit": hit,
        "calibration_error": round(calibration_error, 6),
        "quality_gates": quality_gates,
        "learning_eligibility": learning_eligibility,
        "failure_state": failure_state,
        "input_refs": {
            "prediction_ref": f"artifacts/{prediction_id}.json",
            "outcome_ref": f"artifacts/{outcome_id}.json",
            "strategy_version": "strategy.v1",
            "rule_version": "rulebook.v1",
        },
    }
