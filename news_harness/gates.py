"""Shared admission gate calculations used by validation and preflight."""

from __future__ import annotations

from typing import Any

from .constants import BLOCKED_REGISTRY_STATUSES, LOW_RISK_SOURCE_TYPES, SMOKE_RESULT_FILES, SOURCE_POOL_USER_SOURCES


def source_readiness_gate(fixtures: dict[str, Any]) -> dict[str, Any]:
    matrix = fixtures["sample_connector_readiness_matrix.json"]
    source_score = fixtures["sample_source_score.json"]
    decisions = matrix.get("decisions", [])
    allowed = [decision for decision in decisions if decision.get("production_eligible") is True]
    blocked_violations = [
        decision.get("source_entrance_id")
        for decision in decisions
        if decision.get("registry_status") in BLOCKED_REGISTRY_STATUSES
        and (decision.get("production_eligible") is True or decision.get("candidate_discovery_allowed") is True)
    ]
    score_override = source_score.get("scoring_policy", {}).get("can_override_readiness_gate") is not False or any(
        score.get("can_override_readiness_gate") is not False for score in source_score.get("scores", [])
    )
    passed = bool(allowed) and not blocked_violations and not score_override
    return {
        "passed": passed,
        "allowed_source_entrance_ids": [decision.get("source_entrance_id") for decision in allowed],
        "blocked_violation_source_entrance_ids": blocked_violations,
        "source_score_override_detected": score_override,
    }


def source_smoke_gate(fixtures: dict[str, Any]) -> dict[str, Any]:
    smoke_matrix = fixtures["sample_source_smoke_matrix.json"]
    decisions = smoke_matrix.get("decisions", [])
    allowed_types = sorted({decision.get("source_type") for decision in decisions if decision.get("smoke_allowed") is True})
    source_reach_claims = [decision.get("smoke_decision_id") for decision in decisions if decision.get("source_reach_success") is True]
    blocked_sources = {
        decision.get("source"): decision.get("failure_codes", [])
        for decision in decisions
        if decision.get("smoke_allowed") is False
    }
    engagement_violations = []
    result_source_reach_claims = []
    auth_fake_success_claims = []
    for filename in SMOKE_RESULT_FILES:
        result = fixtures[filename]
        engagement = result.get("real_engagement", {})
        if engagement.get("status") not in {"unavailable", "not_verified"} or engagement.get("metrics") not in (None, {}, []):
            engagement_violations.append(filename)
        if result.get("source_reach_success") is True:
            result_source_reach_claims.append(filename)
        auth_status = result.get("auth_status", {})
        if auth_status.get("auth_requirement") == "required":
            if (
                result.get("smoke_allowed") is True
                or result.get("source_reach_success") is True
                or str(auth_status.get("auth_state", "")).startswith("authenticated")
                or auth_status.get("auth_boundary_review") in {"passed", "passed_fixture"}
            ):
                auth_fake_success_claims.append(filename)
    passed = (
        "rss" in allowed_types
        and not source_reach_claims
        and not result_source_reach_claims
        and not auth_fake_success_claims
        and not engagement_violations
        and "xueqiu" in blocked_sources
        and "reddit" in blocked_sources
    )
    return {
        "passed": passed,
        "allowed_source_types": allowed_types,
        "blocked_sources": blocked_sources,
        "source_reach_claims": source_reach_claims,
        "result_source_reach_claims": result_source_reach_claims,
        "auth_fake_success_claims": auth_fake_success_claims,
        "engagement_violations": engagement_violations,
    }


def source_pool_intake_gate(fixtures: dict[str, Any]) -> dict[str, Any]:
    intake = fixtures["sample_source_pool_intake.json"]
    plan = fixtures["sample_real_smoke_candidate_plan.json"]
    entries = intake.get("entries", [])
    candidates = plan.get("candidates", [])
    entry_by_source = {entry.get("source"): entry for entry in entries if isinstance(entry, dict)}
    candidate_by_source = {candidate.get("source"): candidate for candidate in candidates if isinstance(candidate, dict)}
    ready_user_sources = plan.get("ready_user_sources", [])
    blocked_sources = plan.get("blocked_sources", {})
    recommended = plan.get("recommended_next_smoke_candidate", {})
    recommended_type = recommended.get("source_type") if isinstance(recommended, dict) else None

    auth_ready_violations = []
    unverified_assumption_promotions = []
    real_claim_violations = []
    missing_user_sources = sorted(SOURCE_POOL_USER_SOURCES - set(entry_by_source))
    missing_candidate_decisions = sorted(SOURCE_POOL_USER_SOURCES - set(candidate_by_source))

    for source in SOURCE_POOL_USER_SOURCES:
        entry = entry_by_source.get(source, {})
        candidate = candidate_by_source.get(source, {})
        for data in (entry, candidate):
            if not isinstance(data, dict):
                continue
            if (
                data.get("real_source_reach_proven") is True
                or data.get("real_engagement_proven") is True
                or data.get("production_readiness") is True
            ):
                real_claim_violations.append(source)
        if source == "x":
            if (
                entry.get("source_status") != "auth_required"
                or entry.get("auth_requirement") != "required"
                or entry.get("smoke_eligibility") != "blocked_for_real_smoke"
                or candidate.get("real_smoke_candidate_ready") is True
                or candidate.get("smoke_eligibility") != "blocked_for_real_smoke"
            ):
                auth_ready_violations.append(source)
        if source == "xueqiu":
            if (
                entry.get("auth_requirement") != "unverified"
                or entry.get("source_status") != "planned"
                or not str(entry.get("browser_requirement", "")).startswith("unverified_assumption")
                or candidate.get("real_smoke_candidate_ready") is True
            ):
                unverified_assumption_promotions.append(source)

    passed = (
        intake.get("fixture_only") is True
        and intake.get("no_real_source_access") is True
        and plan.get("fixture_only") is True
        and plan.get("no_real_source_access") is True
        and not missing_user_sources
        and not missing_candidate_decisions
        and ready_user_sources == []
        and recommended_type in LOW_RISK_SOURCE_TYPES
        and set(plan.get("allowed_next_source_types", [])).issubset(LOW_RISK_SOURCE_TYPES)
        and SOURCE_POOL_USER_SOURCES.issubset(set(blocked_sources))
        and not auth_ready_violations
        and not unverified_assumption_promotions
        and not real_claim_violations
    )
    return {
        "passed": passed,
        "user_sources": sorted(entry_by_source),
        "ready_user_sources": ready_user_sources,
        "blocked_user_sources": blocked_sources,
        "recommended_next_smoke_candidate": recommended,
        "missing_user_sources": missing_user_sources,
        "missing_candidate_decisions": missing_candidate_decisions,
        "auth_ready_violations": auth_ready_violations,
        "unverified_assumption_promotions": unverified_assumption_promotions,
        "real_claim_violations": sorted(set(real_claim_violations)),
    }
