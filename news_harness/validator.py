#!/usr/bin/env python3
"""Validate the fixture-first News Harness MVP contract.

The validator deliberately uses only the Python standard library. It checks the
minimal schema file, cross-object references, evidence/image preservation,
version fields, failure states, context/holdout boundaries, replay manifests,
and promotion gate constraints.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import find_raw_secret_material
from .constants import (
    BLOCKED_REGISTRY_STATUSES,
    GUARDRAILS,
    LOW_RISK_SOURCE_TYPES,
    REQUIRED_CONNECTOR_PERMISSIONS,
    SMOKE_RESULT_FILES,
    SOURCE_POOL_AUTH_REQUIRED_SOURCES,
    SOURCE_POOL_READY_MARKERS,
    SOURCE_POOL_USER_SOURCES,
    X_LIST_URL,
)
from .fixtures import DEFAULT_SCHEMA, ROOT, load_fixture_set, load_json as _load_json, ref_path as _ref_path
from .gates import source_pool_intake_gate, source_readiness_gate, source_smoke_gate


REQUIRED_VPS_READ_ONLY_FORBIDDEN_ACTIONS = {
    "post",
    "like",
    "repost",
    "follow",
    "comment",
    "dm",
    "download_image",
    "cache_image",
}
REQUIRED_READINESS_GATES = {
    "auth_boundary_review",
    "legal_tos_review",
    "rate_limit_review",
    "image_reference_review",
    "evidence_contract_review",
    "connector_schema_review",
}
REQUIRED_SMOKE_SOURCES = {"fixture_source", "public_web", "xueqiu", "reddit"}
REQUIRED_SMOKE_GATE_POLICY = {
    "smoke_requires_source_registry": True,
    "smoke_requires_connector_readiness": True,
    "diagnostic_success_is_source_reach_success": False,
    "real_engagement_allowed_in_fixture": False,
    "fixture_only_not_real_source_reach": True,
}
SOURCE_POOL_ENTRY_REQUIRED_FIELDS = [
    "source_pool_entry_id",
    "source",
    "source_url",
    "source_status",
    "auth_mode",
    "auth_requirement",
    "legal_tos_status",
    "rate_limit_status",
    "image_evidence_status",
    "engagement_observability",
    "browser_requirement",
    "secret_ref_policy",
    "connector_readiness",
    "smoke_eligibility",
    "blocked_reason",
    "next_action",
]
VPS_AUTH_GATED_CONFIG = ROOT / "configs" / "vps_auth_gated_sources.example.json"
ALL_SOURCE_RUNNER_CONFIG = ROOT / "configs" / "all_source_runner.example.json"
DEEPSEEK_PROVIDER_CONFIG = ROOT / "configs" / "deepseek_provider.example.json"
SOURCE_RUNNER_RUNTIME_CONFIG = ROOT / "configs" / "source_runner_runtime.example.json"
SECRETS_ENV_EXAMPLE = ROOT / "configs" / "secrets.example.env"
VPS_PLAN_FILES = [
    "sample_vps_source_runner_plan.json",
    "sample_x_list_auth_gated_smoke_plan.json",
    "sample_xueqiu_browser_assisted_smoke_plan.json",
]
SHADOW_SOURCE_FIXTURE = "sample_shadow_source_fetch_result.json"
ROLLING_SOURCE_SCHEDULE_FIXTURE = "rolling_source_schedule.json"
TIMELINE_STORE_FIXTURE = "timeline_store.json"
REVISIT_SCHEDULE_FIXTURE = "revisit_schedule.json"
ALL_SOURCE_RUNNER_DRY_RUN_FIXTURE = "sample_all_source_runner_dry_run.json"
DEEPSEEK_SCORING_FIXTURE = "sample_deepseek_scoring_fixture.json"
REDDIT_SUBREDDITS = {
    "wallstreetbets",
    "stocks",
    "investing",
    "StockMarket",
    "options",
    "Daytrading",
    "pennystocks",
    "ValueInvesting",
    "SecurityAnalysis",
    "algotrading",
    "trading",
    "Stock_Picks",
    "dividends",
    "finance",
    "personalfinance",
    "Bogleheads",
    "dividendinvesting",
    "SPACs",
    "Shortsqueeze",
    "RobinHood",
}
SHADOW_REQUIRED_SOURCE_SECTIONS = {"x_list", "xueqiu_daren", "xueqiu_hot", "xueqiu_dispute", "reddit"}
SHADOW_REQUIRED_ITEM_FIELDS = [
    "shadow_item_id",
    "source",
    "source_label",
    "source_channel",
    "source_section",
    "source_entry_url",
    "source_url",
    "canonical_url",
    "title",
    "author",
    "published_at",
    "fetched_at",
    "evaluated_at",
    "text_snapshot_ref",
    "quoted_spans",
    "image_refs",
    "engagement_snapshot",
    "evidence_status",
    "rights_risk_flags",
    "timeline_projection",
]
SHADOW_REQUIRED_PROJECTION_FIELDS = [
    "copy_text",
    "topic_or_hook",
    "image_status",
    "hotness_score",
    "hotness_series",
    "timeline_status",
    "prediction_status",
    "outcome_status",
]
RADAR_TIMELINE_REQUIRED_ITEM_FIELDS = [
    "id",
    "source",
    "source_label",
    "source_url",
    "author",
    "published_at",
    "copy_text",
    "topic_or_hook",
    "image_refs",
    "image_status",
    "hotness_score",
    "hotness_series",
    "timeline_status",
    "prediction_status",
    "outcome_status",
    "non_investment_advice",
    "evidence_ref",
]
RADAR_TIMELINE_IMAGE_STATUSES = {"available", "no_image", "image_unavailable"}
RADAR_TIMELINE_FORBIDDEN_FIELDS = {
    "investment_advice",
    "trade_recommendation",
    "target_price",
    "position_sizing",
}
ROLLING_ALLOWED_UPDATE_FIELDS = {
    "hotness_score",
    "hotness_series",
    "llm_scoring_refs",
    "image_status",
    "image_refs",
    "outcome_status",
    "revisit_status",
    "latest_observation",
    "last_observed_at",
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    severity: str = "error"

    def format(self) -> str:
        return f"{self.severity.upper()} {self.code} {self.path}: {self.message}"


def _is_empty(value: Any) -> bool:
    return value == "" or value == [] or value == {}


def _missing_required(data: dict[str, Any], required: list[str]) -> list[str]:
    return [field for field in required if field not in data]


def _issue(code: str, path: Path | str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, path=str(path), message=message)


def validate_raw_evidence_candidate(data: dict[str, Any], path: Path | str = "<memory>") -> list[ValidationIssue]:
    """Validate that a single object is eligible to be treated as RawEvidence."""

    if data.get("object_type") != "RawEvidence":
        return [
            _issue(
                "object_type_mismatch",
                path,
                f"expected object_type 'RawEvidence', found {data.get('object_type')!r}",
            )
        ]
    return []


def validate_fixture_dir(fixtures_dir: Path, schema_path: Path = DEFAULT_SCHEMA) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    try:
        schema = _load_json(schema_path)
    except (OSError, json.JSONDecodeError) as exc:
        return [_issue("schema_unreadable", schema_path, str(exc))]

    fixtures: dict[str, Any] = {}

    expected = schema.get("expected_fixtures")
    if expected is None:
        for path in sorted(fixtures_dir.glob("*.json")):
            try:
                data = _load_json(path)
            except json.JSONDecodeError as exc:
                issues.append(_issue("json_invalid", path, str(exc)))
                continue
            except OSError as exc:
                issues.append(_issue("fixture_unreadable", path, str(exc)))
                continue
            fixtures[path.name] = data
    else:
        for filename in expected:
            path = fixtures_dir / filename
            if not path.exists():
                issues.append(_issue("fixture_missing", path, "expected fixture file is missing"))
                continue
            try:
                data = _load_json(path)
            except json.JSONDecodeError as exc:
                issues.append(_issue("json_invalid", path, str(exc)))
                continue
            except OSError as exc:
                issues.append(_issue("fixture_unreadable", path, str(exc)))
                continue
            fixtures[filename] = data

    if issues:
        return issues

    return validate_fixture_data(fixtures_dir, fixtures, schema)


def validate_fixture_data(
    fixtures_dir: Path,
    fixtures: dict[str, Any],
    schema: dict[str, Any] | None = None,
    schema_path: Path = DEFAULT_SCHEMA,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if schema is None:
        try:
            schema = _load_json(schema_path)
        except (OSError, json.JSONDecodeError) as exc:
            return [_issue("schema_unreadable", schema_path, str(exc))]

    expected = schema.get("expected_fixtures", {})
    allowed_failure_states = set(schema.get("allowed_failure_states", []))
    for filename, definition in expected.items():
        path = fixtures_dir / filename
        data = fixtures.get(filename)
        if not isinstance(data, dict):
            issues.append(_issue("fixture_missing", path, "expected fixture data is missing"))
            continue
        object_type = definition.get("object_type")
        if data.get("object_type") != object_type:
            issues.append(
                _issue(
                    "object_type_mismatch",
                    path,
                    f"expected object_type {object_type!r}, found {data.get('object_type')!r}",
                )
            )
        for field in _missing_required(data, definition.get("required", [])):
            issues.append(_issue("missing_required_field", f"{path}#{field}", "required field is absent"))

    if issues:
        return issues

    issues.extend(_validate_versions(fixtures_dir, fixtures))
    issues.extend(_validate_failure_states(fixtures_dir, fixtures, allowed_failure_states))
    issues.extend(_validate_cross_refs(fixtures_dir, fixtures))
    issues.extend(_validate_source_readiness(fixtures_dir, fixtures, allowed_failure_states))
    issues.extend(_validate_source_smoke_tests(fixtures_dir, fixtures, allowed_failure_states))
    issues.extend(_validate_source_pool_intake(fixtures_dir, fixtures, allowed_failure_states))
    issues.extend(_validate_vps_auth_gated_runner(fixtures_dir, fixtures, allowed_failure_states))
    issues.extend(_validate_all_source_deepseek_runner(fixtures_dir, fixtures))
    issues.extend(_validate_shadow_source_fetch_result(fixtures_dir, fixtures))
    issues.extend(_validate_rolling_timeline_runtime(fixtures_dir, fixtures, allowed_failure_states))
    issues.extend(_validate_radar_timeline(fixtures_dir, fixtures))
    issues.extend(_validate_connector(fixtures_dir, fixtures, allowed_failure_states))
    issues.extend(_validate_evidence(fixtures_dir, fixtures))
    issues.extend(_validate_candidate_structure_prediction(fixtures_dir, fixtures))
    issues.extend(_validate_context_and_replay(fixtures_dir, fixtures))
    issues.extend(_validate_outcome_eval_proposal(fixtures_dir, fixtures))
    issues.extend(_validate_promotion(fixtures_dir, fixtures, allowed_failure_states))
    return issues


def _validate_versions(fixtures_dir: Path, fixtures: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    version_fields_by_file = {
        "sample_run_config.json": [
            "harness_version",
            "strategy_version",
            "event_schema_version",
            "persistence_schema_version",
            "tool_contract_version",
            "promotion_gate_version",
        ],
        "sample_source_registry.json": ["registry_version"],
        "sample_source_score.json": ["score_version"],
        "sample_connector_readiness_matrix.json": ["matrix_version", "gate_policy.gate_version"],
        "sample_source_smoke_matrix.json": ["smoke_matrix_version"],
        "sample_source_pool_intake.json": ["intake_version"],
        "sample_real_smoke_candidate_plan.json": ["plan_version"],
        "sample_vps_source_runner_plan.json": ["plan_version"],
        "sample_x_list_auth_gated_smoke_plan.json": ["plan_version"],
        "sample_xueqiu_browser_assisted_smoke_plan.json": ["plan_version"],
        "sample_all_source_runner_dry_run.json": ["runner_version", "source_observation_schema_version"],
        "sample_deepseek_scoring_fixture.json": ["scoring_version", "prompt_version"],
        "sample_shadow_source_fetch_result.json": ["result_version"],
        "rolling_source_schedule.json": ["schedule_version"],
        "timeline_store.json": ["store_version"],
        "revisit_schedule.json": ["schedule_version"],
        "sample_radar_timeline_feed.json": ["feed_version"],
        "sample_connector_result.json": ["tool_version"],
        "sample_source.json": ["source_score.score_version"],
        "sample_candidate.json": ["candidate_score_record.score_version", "candidate_score_record.decision_version"],
        "sample_structure.json": ["taxonomy_version", "model_version", "prompt_version"],
        "sample_prediction.json": [
            "strategy_version",
            "harness_version",
            "scoring_version",
            "prompt_version",
            "model_version",
            "rule_version",
            "evaluator_version",
        ],
        "sample_eval.json": ["evaluator_version", "metric_set_version"],
        "sample_replay_manifest.json": ["strategy_version", "harness_version"],
    }
    for filename, fields in version_fields_by_file.items():
        data = fixtures[filename]
        for dotted in fields:
            value = data
            for part in dotted.split("."):
                if not isinstance(value, dict) or part not in value:
                    value = None
                    break
                value = value[part]
            if _is_empty(value) or value is None:
                issues.append(_issue("version_missing", f"{fixtures_dir / filename}#{dotted}", "version field is required"))
    return issues


def _validate_failure_states(
    fixtures_dir: Path, fixtures: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for filename, data in fixtures.items():
        for key in ("failure_state", "error_code"):
            value = data.get(key)
            if value is not None and value not in allowed_failure_states:
                issues.append(_issue("failure_state_invalid", fixtures_dir / filename, f"{key}={value!r} is not allowed"))
    return issues


def _validate_cross_refs(fixtures_dir: Path, fixtures: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    source = fixtures["sample_source.json"]
    connector = fixtures["sample_connector_result.json"]
    evidence = fixtures["sample_evidence.json"]
    candidate = fixtures["sample_candidate.json"]
    structure = fixtures["sample_structure.json"]
    prediction = fixtures["sample_prediction.json"]
    outcome = fixtures["sample_outcome.json"]
    evaluation = fixtures["sample_eval.json"]
    proposal = fixtures["sample_improvement_proposal.json"]
    promotion = fixtures["sample_promotion_decision.json"]
    run_config = fixtures["sample_run_config.json"]

    comparisons = [
        (run_config["source_entrance_id"], source["source_entrance_id"], "run_config.source_entrance_id"),
        (connector["source_entrance_id"], source["source_entrance_id"], "connector.source_entrance_id"),
        (candidate["source_entrance_id"], source["source_entrance_id"], "candidate.source_entrance_id"),
        (candidate["evidence_id"], evidence["evidence_id"], "candidate.evidence_id"),
        (structure["candidate_id"], candidate["candidate_id"], "structure.candidate_id"),
        (structure["evidence_id"], evidence["evidence_id"], "structure.evidence_id"),
        (prediction["candidate_id"], candidate["candidate_id"], "prediction.candidate_id"),
        (outcome["prediction_id"], prediction["prediction_id"], "outcome.prediction_id"),
        (outcome["candidate_id"], candidate["candidate_id"], "outcome.candidate_id"),
        (evaluation["prediction_id"], prediction["prediction_id"], "eval.prediction_id"),
        (evaluation["outcome_id"], outcome["outcome_id"], "eval.outcome_id"),
        (proposal["created_from_eval_run_id"], evaluation["eval_run_id"], "proposal.created_from_eval_run_id"),
        (promotion["proposal_id"], proposal["proposal_id"], "promotion.proposal_id"),
    ]
    for left, right, label in comparisons:
        if left != right:
            issues.append(_issue("cross_ref_mismatch", fixtures_dir / "fixtures", f"{label}: {left!r} != {right!r}"))

    for filename in fixtures:
        path = f"fixtures/{filename}"
        if not (fixtures_dir / filename).exists():
            issues.append(_issue("fixture_ref_missing", fixtures_dir / filename, f"{path} cannot be resolved"))

    for ref_field in ("input_ref", "output_ref"):
        ref = _ref_path(connector[ref_field])
        if ref.startswith("fixtures/") and not (fixtures_dir.parent / ref).exists():
            issues.append(_issue("fixture_ref_missing", fixtures_dir / "sample_connector_result.json", f"{ref_field}={ref!r} missing"))

    return issues


def _validate_source_readiness(
    fixtures_dir: Path, fixtures: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    registry = fixtures["sample_source_registry.json"]
    source_scores = fixtures["sample_source_score.json"]
    matrix = fixtures["sample_connector_readiness_matrix.json"]
    source = fixtures["sample_source.json"]
    candidate = fixtures["sample_candidate.json"]

    for filename, ref_field in [
        ("sample_source_score.json", "source_registry_ref"),
        ("sample_connector_readiness_matrix.json", "source_registry_ref"),
        ("sample_connector_readiness_matrix.json", "source_score_ref"),
    ]:
        ref = fixtures[filename].get(ref_field)
        if not isinstance(ref, str) or not ref.startswith("fixtures/") or not (fixtures_dir.parent / _ref_path(ref)).exists():
            issues.append(_issue("fixture_ref_missing", fixtures_dir / filename, f"{ref_field} must resolve to a fixture file"))

    entries = registry.get("entries", [])
    scores = source_scores.get("scores", [])
    decisions = matrix.get("decisions", [])
    if not isinstance(entries, list) or not entries:
        issues.append(_issue("source_registry_empty", fixtures_dir / "sample_source_registry.json", "source registry entries are required"))
        return issues
    if not isinstance(scores, list) or not scores:
        issues.append(_issue("source_score_empty", fixtures_dir / "sample_source_score.json", "source scores are required"))
        return issues
    if not isinstance(decisions, list) or not decisions:
        issues.append(_issue("connector_readiness_empty", fixtures_dir / "sample_connector_readiness_matrix.json", "readiness decisions are required"))
        return issues

    entry_by_source_id = {entry.get("source_entrance_id"): entry for entry in entries}
    score_by_score_id = {score.get("source_score_id"): score for score in scores}
    score_by_source_id = {score.get("source_entrance_id"): score for score in scores}
    decision_by_source_id = {decision.get("source_entrance_id"): decision for decision in decisions}

    required_registry_fields = [
        "source_registry_id",
        "source_entrance_id",
        "source",
        "entrance_type",
        "registry_status",
        "connector_status",
        "auth_requirement",
        "auth_status",
        "legal_tos_status",
        "rate_limit_status",
        "image_reference_policy",
        "evidence_policy",
        "real_source_reach_proven",
    ]
    for index, entry in enumerate(entries):
        path = f"{fixtures_dir / 'sample_source_registry.json'}#entries[{index}]"
        for field in required_registry_fields:
            if field not in entry or (entry[field] is not False and _is_empty(entry[field])):
                issues.append(_issue("source_registry_entry_incomplete", f"{path}.{field}", "source registry field is required"))
        if entry.get("real_source_reach_proven") is not False:
            issues.append(_issue("real_source_reach_claimed_by_fixture", path, "fixture registry must not claim real source reach"))
        if entry.get("registry_status") in BLOCKED_REGISTRY_STATUSES and entry.get("connector_status") == "production_eligible":
            issues.append(_issue("connector_readiness_invalid", path, "blocked registry status cannot have production connector status"))

    policy = source_scores.get("scoring_policy", {})
    if policy.get("can_override_readiness_gate") is not False:
        issues.append(_issue("source_score_bypass", fixtures_dir / "sample_source_score.json", "source scoring policy cannot override readiness gate"))
    for index, score in enumerate(scores):
        path = f"{fixtures_dir / 'sample_source_score.json'}#scores[{index}]"
        required_score_fields = [
            "source_score_id",
            "source_entrance_id",
            "source_registry_id",
            "source_score",
            "watch_priority",
            "scan_frequency",
            "budget_class",
            "required_auth_state",
            "min_connector_status",
            "can_override_readiness_gate",
            "candidate_discovery_intent",
            "risk_flags",
        ]
        for field in required_score_fields:
            if field not in score or (score[field] is not False and _is_empty(score[field])):
                issues.append(_issue("source_score_incomplete", f"{path}.{field}", "source score field is required"))
        value = score.get("source_score")
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            issues.append(_issue("score_invalid", f"{path}.source_score", "source score must be numeric between 0 and 1"))
        if score.get("can_override_readiness_gate") is not False:
            issues.append(_issue("source_score_bypass", path, "source score cannot override ConnectorReadinessGate"))
        registry_entry = entry_by_source_id.get(score.get("source_entrance_id"))
        if registry_entry is None:
            issues.append(_issue("cross_ref_mismatch", path, "source score references unknown source_entrance_id"))
        elif score.get("source_registry_id") != registry_entry.get("source_registry_id"):
            issues.append(_issue("cross_ref_mismatch", path, "source score registry id must match source registry entry"))

    gate_policy = matrix.get("gate_policy", {})
    if gate_policy.get("source_score_can_override") is not False:
        issues.append(_issue("source_score_bypass", fixtures_dir / "sample_connector_readiness_matrix.json", "readiness matrix must disallow source score override"))
    if gate_policy.get("candidate_discovery_requires_readiness") is not True:
        issues.append(_issue("connector_readiness_invalid", fixtures_dir / "sample_connector_readiness_matrix.json", "candidate discovery must require readiness"))
    if gate_policy.get("fixture_only_not_real_production") is not True:
        issues.append(_issue("real_source_reach_claimed_by_fixture", fixtures_dir / "sample_connector_readiness_matrix.json", "matrix must stay fixture-only"))

    for index, decision in enumerate(decisions):
        issues.extend(
            _validate_readiness_decision(
                fixtures_dir,
                index,
                decision,
                entry_by_source_id,
                score_by_score_id,
                allowed_failure_states,
            )
        )

    embedded_registry = source.get("source_registry", {})
    embedded_score = source.get("source_score", {})
    canonical_entry = entry_by_source_id.get(source.get("source_entrance_id"))
    canonical_score = score_by_source_id.get(source.get("source_entrance_id"))
    if canonical_entry is None:
        issues.append(_issue("cross_ref_mismatch", fixtures_dir / "sample_source.json", "source entrance must appear in source registry fixture"))
    elif embedded_registry.get("source_registry_id") != canonical_entry.get("source_registry_id"):
        issues.append(_issue("cross_ref_mismatch", fixtures_dir / "sample_source.json", "embedded source registry id must match source registry fixture"))
    if canonical_score is None:
        issues.append(_issue("cross_ref_mismatch", fixtures_dir / "sample_source.json", "source entrance must appear in source score fixture"))
    elif embedded_score.get("source_score_id") != canonical_score.get("source_score_id"):
        issues.append(_issue("cross_ref_mismatch", fixtures_dir / "sample_source.json", "embedded source score id must match source score fixture"))

    candidate_decision = decision_by_source_id.get(candidate.get("source_entrance_id"))
    if candidate_decision is None:
        issues.append(_issue("candidate_discovery_not_allowed", fixtures_dir / "sample_candidate.json", "candidate source has no readiness decision"))
    elif candidate_decision.get("candidate_discovery_allowed") is not True:
        issues.append(_issue("candidate_discovery_not_allowed", fixtures_dir / "sample_candidate.json", "candidate source is blocked by ConnectorReadinessGate"))

    required_statuses = {"planned", "unsupported", "auth_required", "shadow_only", "risk_blocked"}
    present_statuses = {entry.get("registry_status") for entry in entries}
    missing_statuses = sorted(required_statuses - present_statuses)
    if missing_statuses:
        issues.append(_issue("source_readiness_fixture_incomplete", fixtures_dir / "sample_source_registry.json", f"missing statuses: {missing_statuses}"))

    if not any(decision.get("production_eligible") is True for decision in decisions):
        issues.append(_issue("source_readiness_fixture_incomplete", fixtures_dir / "sample_connector_readiness_matrix.json", "fixture must include an allowed production_eligible decision"))
    if not any(decision.get("production_eligible") is False for decision in decisions):
        issues.append(_issue("source_readiness_fixture_incomplete", fixtures_dir / "sample_connector_readiness_matrix.json", "fixture must include blocked decisions"))
    gate = source_readiness_gate(fixtures)
    if not gate["passed"]:
        issues.append(_issue("source_readiness_gate_failed", fixtures_dir / "sample_connector_readiness_matrix.json", f"shared readiness gate failed: {gate}"))
    return issues


def _validate_readiness_decision(
    fixtures_dir: Path,
    index: int,
    decision: dict[str, Any],
    entry_by_source_id: dict[str, dict[str, Any]],
    score_by_score_id: dict[str, dict[str, Any]],
    allowed_failure_states: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    path = f"{fixtures_dir / 'sample_connector_readiness_matrix.json'}#decisions[{index}]"
    required_decision_fields = [
        "readiness_decision_id",
        "source_entrance_id",
        "source",
        "registry_status",
        "requested_state",
        "decision",
        "production_eligible",
        "candidate_discovery_allowed",
        "candidate_discovery_decision",
        "eligibility_scope",
        "shadow_only",
        "diagnostic_only",
        "connector_success",
        "source_score_ref",
        "source_score_can_override_gate",
        "failure_codes",
        "gate_results",
    ]
    for field in required_decision_fields:
        if field not in decision:
            issues.append(_issue("readiness_decision_incomplete", f"{path}.{field}", "readiness decision field is required"))
        elif field != "failure_codes" and decision[field] is not False and _is_empty(decision[field]):
            issues.append(_issue("readiness_decision_incomplete", f"{path}.{field}", "readiness decision field is required"))

    entry = entry_by_source_id.get(decision.get("source_entrance_id"))
    if entry is None:
        issues.append(_issue("cross_ref_mismatch", path, "readiness decision references unknown source_entrance_id"))
    else:
        if decision.get("source") != entry.get("source"):
            issues.append(_issue("cross_ref_mismatch", path, "readiness source must match registry source"))
        if decision.get("registry_status") != entry.get("registry_status"):
            issues.append(_issue("cross_ref_mismatch", path, "readiness registry_status must match registry entry"))

    score = score_by_score_id.get(decision.get("source_score_ref"))
    if score is None:
        issues.append(_issue("cross_ref_mismatch", path, "readiness decision references unknown source score"))
    elif score.get("source_entrance_id") != decision.get("source_entrance_id"):
        issues.append(_issue("cross_ref_mismatch", path, "source score must belong to readiness source"))

    if decision.get("source_score_can_override_gate") is not False:
        issues.append(_issue("source_score_bypass", path, "source score cannot override readiness decision"))

    for code in decision.get("failure_codes", []):
        if code not in allowed_failure_states:
            issues.append(_issue("failure_state_invalid", f"{path}.failure_codes", f"{code!r} is not allowed"))

    gate_results = decision.get("gate_results", [])
    gate_by_id = {gate.get("gate_id"): gate for gate in gate_results if isinstance(gate, dict)}
    missing_gates = sorted(REQUIRED_READINESS_GATES - set(gate_by_id))
    if missing_gates:
        issues.append(_issue("readiness_gate_missing", path, f"missing readiness gates: {missing_gates}"))
    for gate_index, gate in enumerate(gate_results):
        gate_path = f"{path}.gate_results[{gate_index}]"
        for field in ("gate_id", "status", "failure_code"):
            if field not in gate:
                issues.append(_issue("readiness_gate_incomplete", f"{gate_path}.{field}", "readiness gate field is required"))
        failure_code = gate.get("failure_code")
        if failure_code is not None and failure_code not in allowed_failure_states:
            issues.append(_issue("failure_state_invalid", gate_path, f"failure_code={failure_code!r} is not allowed"))

    registry_status = decision.get("registry_status")
    production_eligible = decision.get("production_eligible")
    discovery_allowed = decision.get("candidate_discovery_allowed")
    blocked = registry_status in BLOCKED_REGISTRY_STATUSES
    if blocked and (production_eligible is True or discovery_allowed is True):
        code_by_status = {
            "planned": "planned_source_not_ready",
            "unsupported": "unsupported_source_not_ready",
            "auth_required": "auth_missing",
            "risk_blocked": "risk_unapproved",
            "diagnostic_only": "diagnostic_only_not_ready",
            "shadow_only": "shadow_only_readiness_bypass",
        }
        issues.append(_issue(code_by_status.get(str(registry_status), "connector_readiness_invalid"), path, "blocked source cannot be production eligible or feed candidate discovery"))

    if production_eligible is True:
        if decision.get("eligibility_scope") != "fixture_only_not_real_source":
            issues.append(_issue("real_source_reach_claimed_by_fixture", path, "production eligibility must be marked fixture-only"))
        if decision.get("failure_codes"):
            issues.append(_issue("connector_readiness_invalid", path, "production eligible fixture decision cannot carry failure codes"))
        if decision.get("connector_success") is not True:
            issues.append(_issue("connector_readiness_invalid", path, "production eligible decision requires connector_success"))
        if discovery_allowed is not True:
            issues.append(_issue("connector_readiness_invalid", path, "production eligible decision should allow fixture candidate discovery"))
        for gate_id, gate in gate_by_id.items():
            if gate.get("status") != "passed_fixture" or gate.get("failure_code") is not None:
                issues.append(_issue("connector_readiness_invalid", f"{path}.gate_results[{gate_id}]", "production eligible decision requires passed readiness gates"))

    if registry_status == "auth_required":
        auth_gate = gate_by_id.get("auth_boundary_review", {})
        if auth_gate.get("status") != "passed_fixture":
            if production_eligible is True or discovery_allowed is True or "auth_boundary_failed" not in decision.get("failure_codes", []):
                issues.append(_issue("auth_missing", path, "auth-required source cannot be ready without auth boundary review"))

    if registry_status == "risk_blocked":
        legal_gate = gate_by_id.get("legal_tos_review", {})
        if production_eligible is True or discovery_allowed is True or legal_gate.get("status") == "passed_fixture":
            issues.append(_issue("risk_unapproved", path, "risk-blocked source cannot pass legal/ToS readiness"))

    if discovery_allowed is True and production_eligible is not True:
        issues.append(_issue("candidate_discovery_not_allowed", path, "candidate discovery requires production eligibility in fixture readiness"))
    if discovery_allowed is False and decision.get("candidate_discovery_decision") != "blocked_by_readiness":
        if production_eligible is not True:
            issues.append(_issue("candidate_discovery_blocked", path, "blocked discovery decisions must say blocked_by_readiness"))
    return issues


def _validate_source_smoke_tests(
    fixtures_dir: Path, fixtures: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    smoke_matrix = fixtures["sample_source_smoke_matrix.json"]
    registry = fixtures["sample_source_registry.json"]
    readiness_matrix = fixtures["sample_connector_readiness_matrix.json"]
    matrix_path = fixtures_dir / "sample_source_smoke_matrix.json"

    for ref_field in ("source_registry_ref", "connector_readiness_ref"):
        ref = smoke_matrix.get(ref_field)
        if not isinstance(ref, str) or not ref.startswith("fixtures/") or not (fixtures_dir.parent / _ref_path(ref)).exists():
            issues.append(_issue("fixture_ref_missing", matrix_path, f"{ref_field} must resolve to a fixture file"))

    gate_policy = smoke_matrix.get("gate_policy", {})
    for field, expected in REQUIRED_SMOKE_GATE_POLICY.items():
        if gate_policy.get(field) is not expected:
            issues.append(_issue("smoke_policy_invalid", f"{matrix_path}#gate_policy.{field}", f"must be {expected!r}"))

    decisions = smoke_matrix.get("decisions", [])
    if not isinstance(decisions, list) or not decisions:
        return [_issue("source_smoke_matrix_empty", matrix_path, "source smoke decisions are required")]

    registry_by_source_id = {
        entry.get("source_entrance_id"): entry
        for entry in registry.get("entries", [])
        if isinstance(entry, dict)
    }
    registry_by_registry_id = {
        entry.get("source_registry_id"): entry
        for entry in registry.get("entries", [])
        if isinstance(entry, dict)
    }
    readiness_by_id = {
        decision.get("readiness_decision_id"): decision
        for decision in readiness_matrix.get("decisions", [])
        if isinstance(decision, dict)
    }
    result_by_ref = {f"fixtures/{filename}": fixtures[filename] for filename in SMOKE_RESULT_FILES}
    referenced_result_refs: set[str] = set()
    present_sources = {decision.get("source") for decision in decisions if isinstance(decision, dict)}

    missing_sources = sorted(REQUIRED_SMOKE_SOURCES - present_sources)
    if missing_sources:
        issues.append(_issue("source_smoke_fixture_incomplete", matrix_path, f"missing smoke sources: {missing_sources}"))

    for index, decision in enumerate(decisions):
        path = f"{matrix_path}#decisions[{index}]"
        required_decision_fields = [
            "smoke_decision_id",
            "source_entrance_id",
            "source",
            "source_type",
            "source_url",
            "source_registry_id",
            "readiness_decision_id",
            "smoke_allowed",
            "smoke_decision",
            "smoke_result_ref",
            "diagnostic_success",
            "source_reach_success",
            "failure_codes",
            "reason",
        ]
        for field in required_decision_fields:
            if field not in decision:
                issues.append(_issue("source_smoke_decision_incomplete", f"{path}.{field}", "smoke decision field is required"))
            elif field not in {"source_registry_id", "readiness_decision_id", "failure_codes"}:
                if decision[field] is not False and _is_empty(decision[field]):
                    issues.append(_issue("source_smoke_decision_incomplete", f"{path}.{field}", "smoke decision field is required"))

        failure_codes = decision.get("failure_codes", [])
        if not isinstance(failure_codes, list):
            issues.append(_issue("failure_state_invalid", f"{path}.failure_codes", "failure_codes must be a list"))
            failure_codes = []
        for code in failure_codes:
            if code not in allowed_failure_states:
                issues.append(_issue("failure_state_invalid", f"{path}.failure_codes", f"{code!r} is not allowed"))

        result_ref = decision.get("smoke_result_ref")
        result = result_by_ref.get(result_ref)
        if not isinstance(result_ref, str) or result is None:
            issues.append(_issue("fixture_ref_missing", path, f"smoke_result_ref={result_ref!r} must resolve to a smoke result fixture"))
            continue
        referenced_result_refs.add(result_ref)

        entry = None
        source_registry_id = decision.get("source_registry_id")
        if source_registry_id is None:
            if "source_registry_missing" not in failure_codes:
                issues.append(_issue("source_registry_missing", path, "missing registry id must be explicit in failure_codes"))
        else:
            entry = registry_by_registry_id.get(source_registry_id)
            if entry is None:
                issues.append(_issue("cross_ref_mismatch", path, "smoke decision references unknown source_registry_id"))
            elif entry.get("source_entrance_id") != decision.get("source_entrance_id"):
                issues.append(_issue("cross_ref_mismatch", path, "smoke registry id must match source_entrance_id"))

        readiness_decision = None
        readiness_decision_id = decision.get("readiness_decision_id")
        if readiness_decision_id is not None:
            readiness_decision = readiness_by_id.get(readiness_decision_id)
            if readiness_decision is None:
                issues.append(_issue("cross_ref_mismatch", path, "smoke decision references unknown readiness_decision_id"))
            elif readiness_decision.get("source_entrance_id") != decision.get("source_entrance_id"):
                issues.append(_issue("cross_ref_mismatch", path, "smoke readiness decision must match source_entrance_id"))

        if decision.get("smoke_allowed") is True:
            if entry is None:
                issues.append(_issue("smoke_readiness_bypass", path, "allowed smoke requires source registry admission"))
            if readiness_decision is None:
                issues.append(_issue("smoke_readiness_bypass", path, "allowed smoke requires ConnectorReadinessGate decision"))
            elif (
                readiness_decision.get("production_eligible") is not True
                or readiness_decision.get("candidate_discovery_allowed") is not True
                or readiness_decision.get("failure_codes")
            ):
                issues.append(_issue("smoke_readiness_bypass", path, "allowed smoke requires an allowed readiness decision with no failures"))
        elif decision.get("smoke_allowed") is False:
            if source_registry_id is None and "source_registry_missing" not in failure_codes:
                issues.append(_issue("source_registry_missing", path, "blocked unregistered smoke source must carry source_registry_missing"))
        else:
            issues.append(_issue("source_smoke_decision_invalid", f"{path}.smoke_allowed", "smoke_allowed must be true or false"))

        if decision.get("diagnostic_success") is True and decision.get("source_reach_success") is True:
            issues.append(_issue("diagnostic_as_source_reach", path, "diagnostic success cannot be source reach success"))
        if decision.get("source_reach_success") is True:
            issues.append(_issue("source_reach_unverified", path, "fixture smoke decisions must not claim source reach"))

        issues.extend(
            _validate_source_smoke_result(
                fixtures_dir,
                result_ref,
                result,
                smoke_matrix,
                decision,
                entry or registry_by_source_id.get(decision.get("source_entrance_id")),
                readiness_decision,
                allowed_failure_states,
            )
        )

    unreferenced = sorted(set(result_by_ref) - referenced_result_refs)
    if unreferenced:
        issues.append(_issue("source_smoke_fixture_incomplete", matrix_path, f"unreferenced smoke results: {unreferenced}"))
    gate = source_smoke_gate(fixtures)
    if not gate["passed"]:
        issues.append(_issue("source_smoke_gate_failed", matrix_path, f"shared source smoke gate failed: {gate}"))
    return issues


def _validate_source_smoke_result(
    fixtures_dir: Path,
    result_ref: str,
    result: dict[str, Any],
    smoke_matrix: dict[str, Any],
    decision: dict[str, Any],
    registry_entry: dict[str, Any] | None,
    readiness_decision: dict[str, Any] | None,
    allowed_failure_states: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    result_path = fixtures_dir.parent / result_ref

    comparisons = [
        (result.get("smoke_matrix_id"), smoke_matrix.get("smoke_matrix_id"), "smoke_matrix_id"),
        (result.get("smoke_decision_id"), decision.get("smoke_decision_id"), "smoke_decision_id"),
        (result.get("source"), decision.get("source"), "source"),
        (result.get("source_entrance_id"), decision.get("source_entrance_id"), "source_entrance_id"),
        (result.get("source_type"), decision.get("source_type"), "source_type"),
        (result.get("source_url"), decision.get("source_url"), "source_url"),
        (result.get("smoke_allowed"), decision.get("smoke_allowed"), "smoke_allowed"),
        (result.get("diagnostic_success"), decision.get("diagnostic_success"), "diagnostic_success"),
        (result.get("source_reach_success"), decision.get("source_reach_success"), "source_reach_success"),
    ]
    for left, right, label in comparisons:
        if left != right:
            issues.append(_issue("cross_ref_mismatch", f"{result_path}#{label}", f"{left!r} != {right!r}"))

    if _is_empty(result.get("source_url")):
        issues.append(_issue("source_url_missing", f"{result_path}#source_url", "smoke result must preserve source_url"))
    if result.get("fixture_only") is not True or result.get("not_real_source_reach") is not True:
        issues.append(_issue("real_source_reach_claimed_by_fixture", result_path, "smoke result must stay fixture-only and not real source reach"))

    if result.get("diagnostic_success") is True and result.get("source_reach_success") is True:
        issues.append(_issue("diagnostic_as_source_reach", result_path, "diagnostic success cannot be source reach success"))
    if result.get("source_reach_success") is True:
        issues.append(_issue("source_reach_unverified", result_path, "fixture smoke results must not claim real source reach"))

    connector_identity = result.get("connector_identity", {})
    for field in ("connector_id", "connector_version", "connector_run_ref", "source_registry_ref", "readiness_decision_ref"):
        if field not in connector_identity:
            issues.append(_issue("connector_identity_missing", f"{result_path}#connector_identity.{field}", "smoke connector identity field is required"))
    for field in ("connector_id", "connector_version"):
        if _is_empty(connector_identity.get(field)):
            issues.append(_issue("connector_identity_missing", f"{result_path}#connector_identity.{field}", "smoke connector identity field is required"))
    if registry_entry is not None and connector_identity.get("source_registry_ref") is None:
        issues.append(_issue("connector_identity_missing", f"{result_path}#connector_identity.source_registry_ref", "registered smoke result must reference the source registry"))
    if readiness_decision is not None and connector_identity.get("readiness_decision_ref") is None:
        issues.append(_issue("connector_identity_missing", f"{result_path}#connector_identity.readiness_decision_ref", "readiness-gated smoke result must reference the readiness decision"))

    auth_status = result.get("auth_status", {})
    for field in ("auth_requirement", "auth_state", "auth_boundary_review", "session_state_ref", "secret_ref"):
        if field not in auth_status:
            issues.append(_issue("auth_status_missing", f"{result_path}#auth_status.{field}", "smoke auth status field is required"))
    for field in ("auth_requirement", "auth_state", "auth_boundary_review"):
        if _is_empty(auth_status.get(field)):
            issues.append(_issue("auth_status_missing", f"{result_path}#auth_status.{field}", "smoke auth status field is required"))

    rate_limit_status = result.get("rate_limit_status", {})
    for field in ("policy", "state", "remaining", "observed_at"):
        if field not in rate_limit_status or _is_empty(rate_limit_status[field]):
            issues.append(_issue("rate_limit_status_missing", f"{result_path}#rate_limit_status.{field}", "smoke rate-limit status field is required"))

    structured_error = result.get("structured_error")
    if result.get("smoke_allowed") is True and structured_error is not None:
        issues.append(_issue("smoke_structured_error_invalid", result_path, "allowed fixture smoke result must not contain structured_error"))
    if result.get("smoke_allowed") is False:
        if not isinstance(structured_error, dict):
            issues.append(_issue("smoke_structured_error_missing", result_path, "blocked smoke result must include structured_error"))
        elif structured_error.get("code") not in allowed_failure_states:
            issues.append(_issue("failure_state_invalid", result_path, f"structured_error.code={structured_error.get('code')!r} is not allowed"))

    real_engagement = result.get("real_engagement", {})
    metrics = real_engagement.get("metrics")
    if real_engagement.get("status") not in {"unavailable", "not_verified"} or metrics not in (None, {}, []):
        if metrics not in (None, {}, []):
            issues.append(_issue("engagement_fake_success", f"{result_path}#real_engagement", "fixture smoke cannot carry real engagement metrics"))
        elif real_engagement.get("status") not in {"unavailable", "not_verified"}:
            issues.append(_issue("engagement_fake_success", f"{result_path}#real_engagement.status", "fixture smoke engagement must be unavailable or not_verified"))

    evidence_policy = result.get("evidence_policy", {})
    if evidence_policy.get("source_url_required") is not True:
        issues.append(_issue("source_url_missing", f"{result_path}#evidence_policy.source_url_required", "smoke evidence policy must require source_url"))
    if evidence_policy.get("raw_evidence_ref_required_for_real_smoke") is not True:
        issues.append(_issue("text_evidence_missing", f"{result_path}#evidence_policy.raw_evidence_ref_required_for_real_smoke", "real smoke must require raw evidence refs"))

    failure_codes = set(decision.get("failure_codes", []))
    auth_required = (
        "auth_required" in failure_codes
        or auth_status.get("auth_requirement") == "required"
        or (registry_entry or {}).get("auth_requirement") == "required"
    )
    auth_success_claimed = (
        result.get("smoke_allowed") is True
        or result.get("source_reach_success") is True
        or str(auth_status.get("auth_state", "")).startswith("authenticated")
        or auth_status.get("auth_boundary_review") in {"passed", "passed_fixture"}
    )
    if auth_required and auth_success_claimed:
        issues.append(_issue("auth_fake_success", result_path, "auth-required smoke source cannot fake authenticated reach in fixtures"))

    if registry_entry is not None and registry_entry.get("real_source_reach_proven") is not False:
        issues.append(_issue("real_source_reach_claimed_by_fixture", result_path, "registry entry must not claim real source reach for smoke fixtures"))
    return issues


def _validate_source_pool_intake(
    fixtures_dir: Path, fixtures: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    intake = fixtures["sample_source_pool_intake.json"]
    plan = fixtures["sample_real_smoke_candidate_plan.json"]
    intake_path = fixtures_dir / "sample_source_pool_intake.json"
    plan_path = fixtures_dir / "sample_real_smoke_candidate_plan.json"

    issues.extend(_validate_no_raw_auth_material(intake_path, intake))
    issues.extend(_validate_no_raw_auth_material(plan_path, plan))

    if intake.get("fixture_only") is not True or intake.get("no_real_source_access") is not True:
        issues.append(_issue("real_source_reach_claimed_by_fixture", intake_path, "source pool intake must stay fixture-only with no real source access"))
    if plan.get("fixture_only") is not True or plan.get("no_real_source_access") is not True:
        issues.append(_issue("real_source_reach_claimed_by_fixture", plan_path, "candidate plan must stay fixture-only with no real source access"))

    ref = plan.get("source_pool_intake_ref")
    if ref != "fixtures/sample_source_pool_intake.json" or not (fixtures_dir.parent / _ref_path(str(ref))).exists():
        issues.append(_issue("fixture_ref_missing", plan_path, "source_pool_intake_ref must resolve to sample_source_pool_intake.json"))

    entries = intake.get("entries", [])
    candidates = plan.get("candidates", [])
    if not isinstance(entries, list) or not entries:
        issues.append(_issue("source_pool_intake_empty", intake_path, "source pool intake entries are required"))
        entries = []
    if not isinstance(candidates, list) or not candidates:
        issues.append(_issue("real_smoke_candidate_plan_empty", plan_path, "real smoke candidate plan entries are required"))
        candidates = []

    entry_by_id = {entry.get("source_pool_entry_id"): entry for entry in entries if isinstance(entry, dict)}
    entry_sources = {entry.get("source") for entry in entries if isinstance(entry, dict)}
    missing_sources = sorted(SOURCE_POOL_USER_SOURCES - entry_sources)
    if missing_sources:
        issues.append(_issue("source_pool_fixture_incomplete", intake_path, f"missing user-requested sources: {missing_sources}"))

    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            issues.append(_issue("source_pool_entry_incomplete", f"{intake_path}#entries[{index}]", "source pool entry must be an object"))
            continue
        issues.extend(
            _validate_source_pool_entry(
                f"{intake_path}#entries[{index}]",
                entry,
                allowed_failure_states,
            )
        )

    recommended = plan.get("recommended_next_smoke_candidate", {})
    recommended_type = recommended.get("source_type") if isinstance(recommended, dict) else None
    allowed_next_source_types = plan.get("allowed_next_source_types", [])
    if plan.get("recommended_first_smoke_source_type") not in LOW_RISK_SOURCE_TYPES:
        issues.append(_issue("source_pool_readiness_bypass", plan_path, "first smoke source type must remain low-risk"))
    if recommended_type not in LOW_RISK_SOURCE_TYPES:
        issues.append(_issue("source_pool_readiness_bypass", f"{plan_path}#recommended_next_smoke_candidate", "recommended candidate must be low-risk"))
    if not isinstance(allowed_next_source_types, list) or not set(allowed_next_source_types).issubset(LOW_RISK_SOURCE_TYPES):
        issues.append(_issue("source_pool_readiness_bypass", f"{plan_path}#allowed_next_source_types", "allowed next source types must stay low-risk"))
    if plan.get("ready_user_sources") not in ([], None):
        issues.append(_issue("source_pool_readiness_bypass", f"{plan_path}#ready_user_sources", "user-provided sources are not real-smoke ready in this fixture"))

    blocked_sources = plan.get("blocked_sources", {})
    if not isinstance(blocked_sources, dict):
        issues.append(_issue("source_pool_blocked_sources_missing", f"{plan_path}#blocked_sources", "blocked_sources must be an object"))
        blocked_sources = {}
    for source in SOURCE_POOL_USER_SOURCES:
        if source not in blocked_sources:
            issues.append(_issue("source_pool_blocked_sources_missing", f"{plan_path}#blocked_sources", f"{source!r} must be listed as blocked"))
    for source, reasons in blocked_sources.items():
        if not isinstance(reasons, list) or not reasons:
            issues.append(_issue("blocked_reason_missing", f"{plan_path}#blocked_sources.{source}", "blocked source reasons are required"))
            continue
        for reason in reasons:
            if reason not in allowed_failure_states:
                issues.append(_issue("failure_state_invalid", f"{plan_path}#blocked_sources.{source}", f"{reason!r} is not allowed"))

    candidate_sources = {candidate.get("source") for candidate in candidates if isinstance(candidate, dict)}
    missing_candidate_sources = sorted(SOURCE_POOL_USER_SOURCES - candidate_sources)
    if missing_candidate_sources:
        issues.append(_issue("real_smoke_candidate_fixture_incomplete", plan_path, f"missing user-requested candidate decisions: {missing_candidate_sources}"))

    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            issues.append(_issue("real_smoke_candidate_incomplete", f"{plan_path}#candidates[{index}]", "candidate must be an object"))
            continue
        issues.extend(
            _validate_source_pool_candidate(
                f"{plan_path}#candidates[{index}]",
                candidate,
                entry_by_id,
                allowed_failure_states,
            )
        )
    gate = source_pool_intake_gate(fixtures)
    if not gate["passed"]:
        issues.append(_issue("source_pool_gate_failed", plan_path, f"shared source pool gate failed: {gate}"))
    return issues


def _validate_vps_auth_gated_runner(
    fixtures_dir: Path, fixtures: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    runner = fixtures["sample_vps_source_runner_plan.json"]
    x_plan = fixtures["sample_x_list_auth_gated_smoke_plan.json"]
    xueqiu_plan = fixtures["sample_xueqiu_browser_assisted_smoke_plan.json"]
    runner_path = fixtures_dir / "sample_vps_source_runner_plan.json"
    x_path = fixtures_dir / "sample_x_list_auth_gated_smoke_plan.json"
    xueqiu_path = fixtures_dir / "sample_xueqiu_browser_assisted_smoke_plan.json"

    for path, data in [(runner_path, runner), (x_path, x_plan), (xueqiu_path, xueqiu_plan)]:
        issues.extend(_validate_no_raw_auth_material(path, data))

    config_ref = runner.get("config_ref")
    if config_ref != "configs/vps_auth_gated_sources.example.json":
        issues.append(_issue("fixture_ref_missing", f"{runner_path}#config_ref", "config_ref must point to the VPS auth-gated example config"))
    config_path = fixtures_dir.parent / str(config_ref)
    if not config_path.exists() and config_ref == "configs/vps_auth_gated_sources.example.json":
        config_path = VPS_AUTH_GATED_CONFIG
    if not config_path.exists():
        issues.append(_issue("fixture_ref_missing", f"{runner_path}#config_ref", "VPS auth-gated config file is missing"))
        config = {}
    else:
        try:
            config = _load_json(config_path)
            findings = find_raw_secret_material(config)
            for finding in findings:
                issues.append(_issue("raw_secret_material_present", f"{config_path}#{finding}", "VPS config may contain only redacted refs or reference names"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(_issue("fixture_unreadable", config_path, str(exc)))
            config = {}

    for path, data in [(runner_path, runner), (x_path, x_plan), (xueqiu_path, xueqiu_plan)]:
        if data.get("fixture_only") is not True or data.get("no_real_source_access") is not True:
            issues.append(_issue("real_source_reach_claimed_by_fixture", path, "VPS auth-gated plans must stay fixture-only with no real source access"))

    if runner.get("no_real_scheduler") is not True:
        issues.append(_issue("real_source_reach_claimed_by_fixture", f"{runner_path}#no_real_scheduler", "VPS runner plan must not start a real scheduler"))

    issues.extend(_validate_vps_runner_contract(runner_path, runner))
    issues.extend(_validate_vps_artifact_layout(runner_path, runner))
    issues.extend(_validate_vps_secret_policy(runner_path, runner))

    source_by_name = {source.get("source"): source for source in runner.get("sources", []) if isinstance(source, dict)}
    for source in ("x", "xueqiu"):
        if source not in source_by_name:
            issues.append(_issue("vps_runner_plan_incomplete", f"{runner_path}#sources", f"{source!r} source plan is required"))

    expected_smoke_refs = {
        "fixtures/sample_x_list_auth_gated_smoke_plan.json",
        "fixtures/sample_xueqiu_browser_assisted_smoke_plan.json",
    }
    smoke_refs = set(runner.get("smoke_plans", []))
    missing_smoke_refs = sorted(expected_smoke_refs - smoke_refs)
    if missing_smoke_refs:
        issues.append(_issue("vps_runner_plan_incomplete", f"{runner_path}#smoke_plans", f"missing smoke plan refs: {missing_smoke_refs}"))
    for ref in smoke_refs:
        if not isinstance(ref, str) or not ref.startswith("fixtures/") or not (fixtures_dir.parent / _ref_path(ref)).exists():
            issues.append(_issue("fixture_ref_missing", f"{runner_path}#smoke_plans", f"{ref!r} must resolve to a fixture file"))

    issues.extend(_validate_x_auth_gated_plan(runner_path, source_by_name.get("x", {}), x_path, x_plan, allowed_failure_states))
    issues.extend(_validate_xueqiu_browser_assisted_plan(runner_path, source_by_name.get("xueqiu", {}), xueqiu_path, xueqiu_plan, allowed_failure_states))
    issues.extend(_validate_vps_readiness_summary(runner_path, runner))

    if config:
        issues.extend(_validate_vps_config(config_path, config))
    return issues


def _validate_vps_runner_contract(path: Path, runner: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    contract = runner.get("runner_contract", {})
    if not isinstance(contract, dict):
        return [_issue("vps_runner_contract_invalid", f"{path}#runner_contract", "runner_contract must be an object")]
    if contract.get("permission_mode") != "read_only":
        issues.append(_issue("read_only_boundary_unapproved", f"{path}#runner_contract.permission_mode", "VPS runner must be read_only"))
    refresh_interval = contract.get("refresh_interval_seconds")
    if not isinstance(refresh_interval, int) or refresh_interval < 300:
        issues.append(_issue("rate_limit_unverified", f"{path}#runner_contract.refresh_interval_seconds", "refresh interval must be conservative and explicit"))
    failure_budget = contract.get("failure_budget", {})
    if not isinstance(failure_budget, dict) or failure_budget.get("max_consecutive_failures", 0) > 3:
        issues.append(_issue("gate_failed", f"{path}#runner_contract.failure_budget", "failure budget must fail closed after a small number of failures"))
    forbidden_actions = set(contract.get("forbidden_actions", []))
    missing_forbidden = sorted(REQUIRED_VPS_READ_ONLY_FORBIDDEN_ACTIONS - forbidden_actions)
    if missing_forbidden:
        issues.append(_issue("read_only_boundary_unapproved", f"{path}#runner_contract.forbidden_actions", f"missing forbidden actions: {missing_forbidden}"))
    session_policy = contract.get("session_refresh_policy", {})
    if session_policy.get("silent_relogin_allowed") is not False:
        issues.append(_issue("auth_boundary_failed", f"{path}#runner_contract.session_refresh_policy", "silent relogin must be disabled"))
    return issues


def _validate_vps_artifact_layout(path: Path, runner: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    layout = runner.get("artifact_layout", {})
    if not isinstance(layout, dict):
        return [_issue("vps_artifact_layout_invalid", f"{path}#artifact_layout", "artifact_layout must be an object")]
    if "image_reference_manifest.json" not in layout.get("allowed_artifacts", []):
        issues.append(_issue("image_refs_missing", f"{path}#artifact_layout.allowed_artifacts", "image reference manifest is required"))
    forbidden_text = " ".join(layout.get("forbidden_artifacts", []))
    if "downloaded_images" not in forbidden_text or "cached_media" not in forbidden_text:
        issues.append(_issue("image_unavailable", f"{path}#artifact_layout.forbidden_artifacts", "downloaded/cached media must be forbidden"))
    if layout.get("image_policy") != "preserve_original_image_refs_only_no_download_cache_proxy_or_rehost":
        issues.append(_issue("image_unavailable", f"{path}#artifact_layout.image_policy", "image policy must preserve refs only"))
    return issues


def _validate_vps_secret_policy(path: Path, runner: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    policy = runner.get("secret_redaction_policy", {})
    if not isinstance(policy, dict):
        return [_issue("secret_policy_invalid", f"{path}#secret_redaction_policy", "secret_redaction_policy must be an object")]
    required_false = ["raw_secret_values_allowed"]
    required_true = ["secret_refs_only", "session_state_refs_only", "structured_errors_must_not_include_session_material"]
    for field in required_false:
        if policy.get(field) is not False:
            issues.append(_issue("secret_policy_invalid", f"{path}#secret_redaction_policy.{field}", "raw secret material must be disabled"))
    for field in required_true:
        if policy.get(field) is not True:
            issues.append(_issue("secret_policy_invalid", f"{path}#secret_redaction_policy.{field}", "redacted refs must be required"))
    if policy.get("redaction_guardrail") != "fail_closed":
        issues.append(_issue("secret_policy_invalid", f"{path}#secret_redaction_policy.redaction_guardrail", "redaction guardrail must fail closed"))
    return issues


def _validate_x_auth_gated_plan(
    runner_path: Path,
    runner_source: dict[str, Any],
    smoke_path: Path,
    smoke_plan: dict[str, Any],
    allowed_failure_states: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    expected_url = X_LIST_URL
    if runner_source.get("source_url") != expected_url or smoke_plan.get("source_url") != expected_url:
        issues.append(_issue("cross_ref_mismatch", smoke_path, "X list URL must match the target list"))
    if smoke_plan.get("source") != "x" or runner_source.get("source") != "x":
        issues.append(_issue("cross_ref_mismatch", smoke_path, "X plan must be source='x'"))
    if smoke_plan.get("runner_mode") != "vps_auth_gated_read_only_plan":
        issues.append(_issue("auth_boundary_failed", f"{smoke_path}#runner_mode", "X list must use the auth-gated read-only runner plan"))
    if smoke_plan.get("vps_runner_plan_ref") != "fixtures/sample_vps_source_runner_plan.json":
        issues.append(_issue("fixture_ref_missing", f"{smoke_path}#vps_runner_plan_ref", "X smoke plan must reference the VPS runner plan fixture"))

    auth_boundary = smoke_plan.get("auth_boundary", {})
    issues.extend(_validate_ref_value(f"{smoke_path}#auth_boundary.secret_ref", auth_boundary.get("secret_ref"), "secret_ref:"))
    issues.extend(_validate_ref_value(f"{smoke_path}#auth_boundary.session_state_ref", auth_boundary.get("session_state_ref"), "session_ref:"))
    if auth_boundary.get("session_state_approval_status") == "approved":
        issues.append(_issue("session_state_unapproved", f"{smoke_path}#auth_boundary.session_state_approval_status", "fixture must not approve the X session state"))
    if auth_boundary.get("legal_tos_status") != "unreviewed":
        issues.append(_issue("legal_access_unapproved", f"{smoke_path}#auth_boundary.legal_tos_status", "fixture must not claim legal/ToS approval"))

    boundary = smoke_plan.get("read_only_boundary", {})
    if boundary.get("approved") is not False:
        issues.append(_issue("read_only_boundary_unapproved", f"{smoke_path}#read_only_boundary.approved", "fixture must not approve the X read-only boundary"))
    missing_forbidden = sorted(REQUIRED_VPS_READ_ONLY_FORBIDDEN_ACTIONS - set(boundary.get("forbidden_actions", [])))
    if missing_forbidden:
        issues.append(_issue("read_only_boundary_unapproved", f"{smoke_path}#read_only_boundary.forbidden_actions", f"missing forbidden actions: {missing_forbidden}"))

    execution = smoke_plan.get("smoke_execution", {})
    if execution.get("executed") is not False or execution.get("network_access") is not False or execution.get("scheduler_started") is not False:
        issues.append(_issue("source_reach_unverified", f"{smoke_path}#smoke_execution", "X smoke plan must not execute real source access"))

    readiness = smoke_plan.get("readiness", {})
    issues.extend(_validate_vps_blocked_reasons(f"{smoke_path}#readiness", readiness.get("blocked_reason"), allowed_failure_states))
    if readiness.get("vps_runner_plan_ready") is not True or runner_source.get("vps_runner_plan_ready") is not True:
        issues.append(_issue("gate_failed", smoke_path, "X runner contract should be plan-ready for future manual smoke"))
    if readiness.get("production_connector_ready") is not False or runner_source.get("production_connector_ready") is not False:
        issues.append(_issue("auth_fake_success", smoke_path, "X list must not be production connector ready"))
    if readiness.get("real_source_smoke_not_executed") is not True or runner_source.get("real_source_smoke_executed") is not False:
        issues.append(_issue("source_reach_unverified", smoke_path, "X list real source smoke must remain not executed"))
    for required in ["auth_required", "session_state_unapproved", "legal_access_unapproved", "rate_limit_unverified", "read_only_boundary_unapproved", "connector_not_ready"]:
        if required not in readiness.get("blocked_reason", []):
            issues.append(_issue("blocked_reason_missing", f"{smoke_path}#readiness.blocked_reason", f"X list must include {required}"))
    return issues


def _validate_xueqiu_browser_assisted_plan(
    runner_path: Path,
    runner_source: dict[str, Any],
    smoke_path: Path,
    smoke_plan: dict[str, Any],
    allowed_failure_states: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if runner_source.get("source_url") != "https://xueqiu.com/" or smoke_plan.get("source_url") != "https://xueqiu.com/":
        issues.append(_issue("cross_ref_mismatch", smoke_path, "Xueqiu URL must match the target entrance"))
    if smoke_plan.get("source") != "xueqiu" or runner_source.get("source") != "xueqiu":
        issues.append(_issue("cross_ref_mismatch", smoke_path, "Xueqiu plan must be source='xueqiu'"))
    if smoke_plan.get("vps_runner_plan_ref") != "fixtures/sample_vps_source_runner_plan.json":
        issues.append(_issue("fixture_ref_missing", f"{smoke_path}#vps_runner_plan_ref", "Xueqiu smoke plan must reference the VPS runner plan fixture"))

    browser_boundary = smoke_plan.get("browser_assisted_boundary", {})
    if browser_boundary.get("browser_assisted_required") is not True or browser_boundary.get("cannot_override_readiness") is not True:
        issues.append(_issue("browser_assisted_required", f"{smoke_path}#browser_assisted_boundary", "Xueqiu browser-assisted assumption must be explicit and non-overriding"))
    if browser_boundary.get("browser_automation_allowed_in_fixture") is not False:
        issues.append(_issue("browser_assisted_required", f"{smoke_path}#browser_assisted_boundary.browser_automation_allowed_in_fixture", "fixture must not allow browser automation"))

    session_boundary = smoke_plan.get("session_boundary", {})
    if session_boundary.get("auth_requirement") != "session_optional_unverified":
        issues.append(_issue("session_optional_unverified", f"{smoke_path}#session_boundary.auth_requirement", "Xueqiu session assumption must remain optional/unverified"))
    if session_boundary.get("secret_ref") is not None or session_boundary.get("session_state_ref") is not None:
        issues.append(_issue("source_pool_assumption_promoted", f"{smoke_path}#session_boundary", "Xueqiu must not configure session refs in this fixture"))

    execution = smoke_plan.get("smoke_execution", {})
    if execution.get("executed") is not False or execution.get("network_access") is not False or execution.get("scheduler_started") is not False:
        issues.append(_issue("source_reach_unverified", f"{smoke_path}#smoke_execution", "Xueqiu smoke plan must not execute real source access"))

    readiness = smoke_plan.get("readiness", {})
    issues.extend(_validate_vps_blocked_reasons(f"{smoke_path}#readiness", readiness.get("blocked_reason"), allowed_failure_states))
    if readiness.get("vps_runner_plan_ready") is not False or runner_source.get("vps_runner_plan_ready") is not False:
        issues.append(_issue("source_pool_assumption_promoted", smoke_path, "Xueqiu browser-assisted plan cannot be VPS runner ready"))
    if readiness.get("production_connector_ready") is not False or runner_source.get("production_connector_ready") is not False:
        issues.append(_issue("source_pool_readiness_bypass", smoke_path, "Xueqiu cannot be production connector ready"))
    for required in ["browser_assisted_required", "session_optional_unverified", "legal_access_unapproved", "rate_limit_unverified", "connector_not_ready"]:
        if required not in readiness.get("blocked_reason", []):
            issues.append(_issue("blocked_reason_missing", f"{smoke_path}#readiness.blocked_reason", f"Xueqiu must include {required}"))
    return issues


def _validate_vps_readiness_summary(path: Path, runner: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    summary = runner.get("readiness_summary", {})
    required = {
        "fixture_ready": True,
        "vps_runner_plan_ready": True,
        "real_source_smoke_not_executed": True,
        "production_connector_ready": False,
        "xueqiu_browser_assisted_does_not_override_readiness": True,
    }
    for field, expected in required.items():
        if summary.get(field) is not expected:
            issues.append(_issue("vps_runner_plan_invalid", f"{path}#readiness_summary.{field}", f"must be {expected!r}"))
    blocked_until = set(summary.get("x_list_blocked_until", []))
    required_blockers = {"approved_session_state_ref", "legal_tos_review", "rate_limit_review", "approved_read_only_boundary"}
    if not required_blockers.issubset(blocked_until):
        issues.append(_issue("auth_boundary_failed", f"{path}#readiness_summary.x_list_blocked_until", "X list production blockers must remain explicit"))
    return issues


def _validate_all_source_deepseek_runner(fixtures_dir: Path, fixtures: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    runner = fixtures[ALL_SOURCE_RUNNER_DRY_RUN_FIXTURE]
    scoring = fixtures[DEEPSEEK_SCORING_FIXTURE]
    runner_path = fixtures_dir / ALL_SOURCE_RUNNER_DRY_RUN_FIXTURE
    scoring_path = fixtures_dir / DEEPSEEK_SCORING_FIXTURE

    for path, data in [(runner_path, runner), (scoring_path, scoring)]:
        if data.get("fixture_only") is not True or data.get("no_real_source_access") is not True:
            issues.append(_issue("real_source_reach_claimed_by_fixture", path, "all-source/DeepSeek fixtures must remain fixture-only"))
        issues.extend(_validate_no_raw_auth_material(path, data))

    for config_path, expected_type in [
        (ALL_SOURCE_RUNNER_CONFIG, "AllSourceRunnerConfig"),
        (DEEPSEEK_PROVIDER_CONFIG, "DeepSeekProviderConfig"),
        (SOURCE_RUNNER_RUNTIME_CONFIG, "SourceRunnerRuntimeConfig"),
    ]:
        try:
            config = _load_json(config_path)
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(_issue("fixture_ref_missing", config_path, f"config must be readable: {exc}"))
            continue
        if config.get("object_type") != expected_type:
            issues.append(_issue("object_type_mismatch", config_path, f"config object_type must be {expected_type}"))
        if config.get("fixture_only") is not True and expected_type != "DeepSeekProviderConfig":
            issues.append(_issue("real_source_reach_claimed_by_fixture", config_path, "runner configs must remain fixture-only"))
        if config.get("no_real_source_access") is False or config.get("network_fetch_allowed") is True:
            issues.append(_issue("real_source_reach_claimed_by_fixture", config_path, "configs must not enable real network/source access"))
        issues.extend(_validate_no_raw_auth_material(config_path, config))

    if not SECRETS_ENV_EXAMPLE.exists():
        issues.append(_issue("fixture_ref_missing", SECRETS_ENV_EXAMPLE, "secrets env placeholder file must exist"))
    else:
        text = SECRETS_ENV_EXAMPLE.read_text(encoding="utf-8")
        lowered = text.lower()
        if "secret_ref:" not in text or "deepseek_api_key_v1" not in text:
            issues.append(_issue("fixture_ref_missing", SECRETS_ENV_EXAMPLE, "secrets example must contain only placeholder refs"))
        if any(marker in lowered for marker in ("auth_token=", "ct0=", "twid=", "cookie:", "set-cookie:", "authorization: bearer")):
            issues.append(_issue("raw_secret_material_present", SECRETS_ENV_EXAMPLE, "secrets example must not contain raw cookie/token markers"))

    if runner.get("real_source_smoke_executed") is not False or runner.get("production_connector_ready") is not False:
        issues.append(_issue("real_source_reach_claimed_by_fixture", runner_path, "all-source dry-run cannot claim real smoke or production connector readiness"))
    secret_boundary = runner.get("secret_boundary", {})
    for field in ("secret_refs_only", "session_refs_only"):
        if secret_boundary.get(field) is not True:
            issues.append(_issue("raw_secret_material_present", f"{runner_path}#secret_boundary.{field}", "all-source runner must use refs only"))
    for field in ("raw_cookie_detected", "raw_token_detected", "raw_api_key_detected", "repo_write_contains_raw_auth"):
        if secret_boundary.get(field) is not False:
            issues.append(_issue("raw_secret_material_present", f"{runner_path}#secret_boundary.{field}", "all-source dry-run must report no raw auth material"))

    batch_policy = runner.get("batch_policy", {})
    if batch_policy.get("batch_processing_required") is not True or batch_policy.get("per_item_full_serial_pipeline_allowed") is not False:
        issues.append(_issue("gate_failed", f"{runner_path}#batch_policy", "all-source runner must be batch based"))
    if batch_policy.get("max_items_per_source_per_run") != 10 or batch_policy.get("reddit_max_items_per_subreddit_per_run") != 10:
        issues.append(_issue("gate_failed", f"{runner_path}#batch_policy", "all-source runner must cap source and subreddit batches at 10"))

    expected_sources = {"x_list", "xueqiu_hot", "xueqiu_daren", "xueqiu_dispute", "reddit"}
    source_statuses = runner.get("source_statuses", [])
    status_sources = {status.get("source") for status in source_statuses if isinstance(status, dict)}
    if status_sources != expected_sources:
        issues.append(_issue("source_registry_missing", f"{runner_path}#source_statuses", "all-source dry-run must cover X list, Xueqiu sections, and Reddit"))
    for index, status in enumerate(source_statuses if isinstance(source_statuses, list) else []):
        if status.get("observed_item_count", 0) > 10:
            issues.append(_issue("rolling_batch_limit_invalid", f"{runner_path}#source_statuses[{index}]", "each source status must stay under 10 observed items"))
        if status.get("fetch_status") in {"success", "real_success"}:
            issues.append(_issue("real_source_reach_claimed_by_fixture", f"{runner_path}#source_statuses[{index}].fetch_status", "fixture status must not imply real source success"))

    observations = runner.get("observations", [])
    observation_sources = {item.get("source") for item in observations if isinstance(item, dict)}
    if observation_sources != expected_sources:
        issues.append(_issue("observation_incomplete", f"{runner_path}#observations", "source observations must cover all sources"))
    observation_refs = set()
    for index, observation in enumerate(observations if isinstance(observations, list) else []):
        item_path = f"{runner_path}#observations[{index}]"
        required_fields = [
            "source",
            "source_label",
            "source_url",
            "canonical_url",
            "author",
            "published_at",
            "copy_text",
            "topic_or_hook",
            "image_refs",
            "image_status",
            "engagement_snapshot",
            "content_hash",
            "connector_identity",
            "fetch_status",
            "structured_error",
            "evidence_ref",
        ]
        for field in required_fields:
            if field not in observation or (field != "structured_error" and field != "image_refs" and _is_empty(observation[field])):
                issues.append(_issue("observation_incomplete", f"{item_path}.{field}", "source observation field is required"))
        if not str(observation.get("source_url", "")).startswith("fixture://all-source/"):
            issues.append(_issue("real_source_reach_claimed_by_fixture", f"{item_path}.source_url", "all-source observation URLs must be fixture URLs"))
        if observation.get("fetch_status") != "dry_run_fixture_success":
            issues.append(_issue("source_reach_unverified", f"{item_path}.fetch_status", "dry-run observations must use fixture success status"))
        engagement = observation.get("engagement_snapshot", {})
        if engagement.get("metrics_are_fixture") is not True or "not_ground_truth" not in str(engagement.get("status", "")):
            issues.append(_issue("real_engagement_claimed_by_fixture", f"{item_path}.engagement_snapshot", "engagement metrics must be fixture-only"))
        connector = observation.get("connector_identity", {})
        for field in ("connector_id", "tool_id", "tool_version"):
            if _is_empty(connector.get(field)):
                issues.append(_issue("connector_identity_missing", f"{item_path}.connector_identity.{field}", "connector identity is required"))
        expected_ref = f"fixtures/{ALL_SOURCE_RUNNER_DRY_RUN_FIXTURE}#observations[{index}]"
        if observation.get("evidence_ref") != expected_ref:
            issues.append(_issue("fixture_ref_missing", f"{item_path}.evidence_ref", "observation evidence_ref must point to itself"))
        observation_refs.add(expected_ref)

    if scoring.get("model_provider") != "deepseek" or scoring.get("no_real_model_call") is not True:
        issues.append(_issue("external_model_not_allowed", scoring_path, "DeepSeek fixture must not call the real provider"))
    model_config = scoring.get("model_config", {})
    if model_config.get("network_calls_allowed") is not False or model_config.get("api_key_ref") != "secret_ref:deepseek_api_key_v1":
        issues.append(_issue("external_model_not_allowed", f"{scoring_path}#model_config", "DeepSeek config must remain secret-ref fixture mode"))
    provider = scoring.get("provider_status", {})
    if provider.get("api_key_present") is not False or provider.get("provider_called") is not False or provider.get("fallback_used") != "fixture_scoring":
        issues.append(_issue("llm_fixture_fallback", f"{scoring_path}#provider_status", "missing key must fall back to fixture scoring without a provider call"))
    if scoring.get("ground_truth_policy", {}).get("llm_output_is_ground_truth") is not False:
        issues.append(_issue("fixture_outcome_ground_truth_risk", f"{scoring_path}#ground_truth_policy", "LLM output cannot be ground truth"))

    scored_refs: set[str] = set()
    scored_candidates = scoring.get("scored_candidates", [])
    for index, candidate in enumerate(scored_candidates if isinstance(scored_candidates, list) else []):
        candidate_path = f"{scoring_path}#scored_candidates[{index}]"
        for field in [
            "candidate_id",
            "source_observation_ref",
            "input_evidence_refs",
            "model_provider",
            "model_id",
            "prompt_version",
            "scoring_version",
            "evaluated_at",
            "topic_or_hook",
            "hotness_score",
            "risk_flags",
            "rationale",
            "feature_contributions",
            "output_hash",
            "structured_error",
        ]:
            if field not in candidate or (field != "structured_error" and _is_empty(candidate[field])):
                issues.append(_issue("scoring_failed", f"{candidate_path}.{field}", "DeepSeek scoring field is required"))
        scored_refs.add(str(candidate.get("source_observation_ref")))
        if candidate.get("model_provider") != "deepseek":
            issues.append(_issue("scoring_failed", f"{candidate_path}.model_provider", "scoring output must record DeepSeek provider"))
        score = candidate.get("hotness_score")
        if not isinstance(score, (int, float)) or not 0 <= score <= 1:
            issues.append(_issue("score_invalid", f"{candidate_path}.hotness_score", "hotness_score must be 0..1"))
        if not str(candidate.get("output_hash", "")).startswith("sha256:"):
            issues.append(_issue("scoring_failed", f"{candidate_path}.output_hash", "output_hash must be recorded"))
        if "model_inference_not_ground_truth" not in candidate.get("risk_flags", []):
            issues.append(_issue("fixture_outcome_ground_truth_risk", f"{candidate_path}.risk_flags", "scoring must mark model inference as not ground truth"))
    if scored_refs != observation_refs:
        issues.append(_issue("scoring_failed", f"{scoring_path}#scored_candidates", "each observation must have one DeepSeek fixture score"))

    store = fixtures[TIMELINE_STORE_FIXTURE]
    active_items = [item for item in store.get("items", []) if isinstance(item, dict) and item.get("expired") is not True]
    llm_linked = [
        item for item in active_items
        if any(str(ref).startswith(f"fixtures/{DEEPSEEK_SCORING_FIXTURE}#scored_candidates") for ref in item.get("llm_scoring_refs", []))
    ]
    if not llm_linked:
        issues.append(_issue("rolling_store_incomplete", f"{fixtures_dir / TIMELINE_STORE_FIXTURE}#items", "rolling store must link at least one item to DeepSeek scoring refs"))
    return issues


def _validate_vps_config(path: Path, config: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if config.get("object_type") != "VpsAuthGatedSourceConfig":
        issues.append(_issue("object_type_mismatch", path, "VPS config object_type must be VpsAuthGatedSourceConfig"))
    if config.get("fixture_only") is not True or config.get("no_real_source_access") is not True or config.get("no_real_scheduler") is not True:
        issues.append(_issue("real_source_reach_claimed_by_fixture", path, "VPS config must stay fixture-only and non-executing"))
    defaults = config.get("runner_defaults", {})
    if defaults.get("permission_mode") != "read_only":
        issues.append(_issue("read_only_boundary_unapproved", f"{path}#runner_defaults.permission_mode", "VPS config must be read_only"))
    forbidden_actions = set(defaults.get("forbidden_actions", []))
    missing_forbidden = sorted(REQUIRED_VPS_READ_ONLY_FORBIDDEN_ACTIONS - forbidden_actions)
    if missing_forbidden:
        issues.append(_issue("read_only_boundary_unapproved", f"{path}#runner_defaults.forbidden_actions", f"missing forbidden actions: {missing_forbidden}"))
    policy = config.get("secret_reference_policy", {})
    for ref_field, prefix in [
        ("secret_file_path_ref", "secret_ref:"),
        ("session_state_file_path_ref", "session_ref:"),
    ]:
        issues.extend(_validate_ref_value(f"{path}#secret_reference_policy.{ref_field}", policy.get(ref_field), prefix))
    source_by_name = {source.get("source"): source for source in config.get("sources", []) if isinstance(source, dict)}
    x_source = source_by_name.get("x", {})
    if x_source.get("production_connector_ready") is not False or x_source.get("session_state_approval_status") == "approved":
        issues.append(_issue("auth_fake_success", f"{path}#sources.x", "X config must not claim production readiness or approved session state"))
    xueqiu_source = source_by_name.get("xueqiu", {})
    if xueqiu_source.get("browser_assisted_required") is not True or xueqiu_source.get("production_connector_ready") is not False:
        issues.append(_issue("source_pool_assumption_promoted", f"{path}#sources.xueqiu", "Xueqiu config must stay browser-assisted and not ready"))
    return issues


def _validate_vps_blocked_reasons(path: str, reasons: Any, allowed_failure_states: set[str]) -> list[ValidationIssue]:
    if not isinstance(reasons, list) or not reasons:
        return [_issue("blocked_reason_missing", f"{path}.blocked_reason", "blocked reasons are required")]
    issues: list[ValidationIssue] = []
    for reason in reasons:
        if reason not in allowed_failure_states:
            issues.append(_issue("failure_state_invalid", f"{path}.blocked_reason", f"{reason!r} is not allowed"))
    return issues


def _validate_ref_value(path: str, value: Any, prefix: str) -> list[ValidationIssue]:
    if not isinstance(value, str) or not value.startswith(prefix):
        return [_issue("raw_secret_material_present", path, f"reference value must start with {prefix!r}")]
    return []


def _validate_no_raw_auth_material(path: Path | str, data: dict[str, Any]) -> list[ValidationIssue]:
    findings = find_raw_secret_material(data)
    return [
        _issue("raw_secret_material_present", f"{path}#{finding}", "source pool fixtures may contain only empty refs or redacted reference ids")
        for finding in findings
    ]


def _validate_source_pool_secret_policy(path: str, policy: Any) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(policy, dict):
        return [_issue("secret_policy_invalid", path, "secret_ref_policy must be an object")]
    if policy.get("raw_secret_allowed") is not False:
        issues.append(_issue("secret_policy_invalid", f"{path}.raw_secret_allowed", "raw auth/session material must not be allowed"))
    for ref_field, prefix in [("secret_ref", "secret_ref:"), ("session_state_ref", "session_ref:")]:
        value = policy.get(ref_field)
        if value not in (None, "") and not (isinstance(value, str) and value.startswith(prefix)):
            issues.append(_issue("raw_secret_material_present", f"{path}.{ref_field}", f"{ref_field} must be empty or a redacted reference"))
    return issues


def _validate_source_pool_entry(
    path: str, entry: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field in SOURCE_POOL_ENTRY_REQUIRED_FIELDS:
        if field not in entry:
            issues.append(_issue("source_pool_entry_incomplete", f"{path}.{field}", "source pool entry field is required"))
        elif entry[field] is not False and _is_empty(entry[field]):
            issues.append(_issue("source_pool_entry_incomplete", f"{path}.{field}", "source pool entry field is required"))

    issues.extend(_validate_source_pool_secret_policy(f"{path}.secret_ref_policy", entry.get("secret_ref_policy")))
    issues.extend(_validate_blocked_reasons(path, entry.get("blocked_reason"), allowed_failure_states))
    issues.extend(_validate_no_fixture_reach_claims(path, entry))

    source = entry.get("source")
    smoke_eligibility = str(entry.get("smoke_eligibility", "")).lower()
    connector_readiness = str(entry.get("connector_readiness", "")).lower()
    if source in SOURCE_POOL_USER_SOURCES and (
        smoke_eligibility in SOURCE_POOL_READY_MARKERS or connector_readiness in SOURCE_POOL_READY_MARKERS
    ):
        issues.append(_issue("source_pool_readiness_bypass", path, "user-provided source cannot be promoted to ready by intake fixture"))

    if source == "x":
        if entry.get("source_status") != "auth_required":
            issues.append(_issue("auth_fake_success", f"{path}.source_status", "X list intake must remain auth_required"))
        if entry.get("auth_requirement") != "required":
            issues.append(_issue("auth_fake_success", f"{path}.auth_requirement", "X list intake must require auth boundary review"))
        if entry.get("smoke_eligibility") != "blocked_for_real_smoke":
            issues.append(_issue("auth_fake_success", f"{path}.smoke_eligibility", "auth-required X list must be blocked_for_real_smoke"))
        if "auth_required" not in entry.get("blocked_reason", []):
            issues.append(_issue("blocked_reason_missing", f"{path}.blocked_reason", "X list must carry auth_required blocked reason"))

    if source == "xueqiu":
        if entry.get("source_status") != "planned":
            issues.append(_issue("source_pool_assumption_promoted", f"{path}.source_status", "Xueqiu must remain planned until readiness is reviewed"))
        if entry.get("auth_requirement") != "unverified":
            issues.append(_issue("source_pool_assumption_promoted", f"{path}.auth_requirement", "Xueqiu auth requirement must stay unverified in this fixture"))
        if not str(entry.get("browser_requirement", "")).startswith("unverified_assumption"):
            issues.append(_issue("source_pool_assumption_promoted", f"{path}.browser_requirement", "Xueqiu browser behavior may only be an unverified assumption"))
        assumptions = entry.get("unverified_assumptions", [])
        if not isinstance(assumptions, list) or not assumptions:
            issues.append(_issue("source_pool_assumption_missing", f"{path}.unverified_assumptions", "Xueqiu assumptions must remain explicit and unverified"))
    return issues


def _validate_source_pool_candidate(
    path: str,
    candidate: dict[str, Any],
    entry_by_id: dict[str, dict[str, Any]],
    allowed_failure_states: set[str],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field in SOURCE_POOL_ENTRY_REQUIRED_FIELDS:
        if field not in candidate:
            issues.append(_issue("real_smoke_candidate_incomplete", f"{path}.{field}", "candidate field is required"))
        elif candidate[field] is not False and _is_empty(candidate[field]):
            issues.append(_issue("real_smoke_candidate_incomplete", f"{path}.{field}", "candidate field is required"))
    for field in ("recommendation", "real_smoke_candidate_ready"):
        if field not in candidate or (candidate[field] is not False and _is_empty(candidate[field])):
            issues.append(_issue("real_smoke_candidate_incomplete", f"{path}.{field}", "candidate decision field is required"))

    issues.extend(_validate_source_pool_secret_policy(f"{path}.secret_ref_policy", candidate.get("secret_ref_policy")))
    issues.extend(_validate_blocked_reasons(path, candidate.get("blocked_reason"), allowed_failure_states))
    issues.extend(_validate_no_fixture_reach_claims(path, candidate))

    entry = entry_by_id.get(candidate.get("source_pool_entry_id"))
    if entry is None:
        issues.append(_issue("cross_ref_mismatch", path, "candidate references unknown source_pool_entry_id"))
    else:
        for field in ("source", "source_url", "source_status", "auth_requirement", "connector_readiness", "smoke_eligibility"):
            if candidate.get(field) != entry.get(field):
                issues.append(_issue("cross_ref_mismatch", f"{path}.{field}", "candidate source-pool field must match intake entry"))

    source = candidate.get("source")
    auth_required = source in SOURCE_POOL_AUTH_REQUIRED_SOURCES or candidate.get("auth_requirement") == "required"
    ready_claimed = candidate.get("real_smoke_candidate_ready") is True or str(candidate.get("smoke_eligibility", "")).lower() in SOURCE_POOL_READY_MARKERS
    if auth_required and ready_claimed:
        issues.append(_issue("auth_fake_success", path, "auth-required source cannot be a real smoke candidate without auth boundary review"))
    if source in SOURCE_POOL_USER_SOURCES and candidate.get("real_smoke_candidate_ready") is not False:
        issues.append(_issue("source_pool_readiness_bypass", path, "user-provided source must remain not ready for real smoke in this fixture"))
    if source == "x" and candidate.get("smoke_eligibility") != "blocked_for_real_smoke":
        issues.append(_issue("auth_fake_success", f"{path}.smoke_eligibility", "X list candidate must remain blocked_for_real_smoke"))
    if source == "xueqiu" and str(candidate.get("browser_requirement", "")).startswith("unverified_assumption") is False:
        issues.append(_issue("source_pool_assumption_promoted", f"{path}.browser_requirement", "Xueqiu browser behavior may only be an unverified assumption"))
    return issues


def _validate_blocked_reasons(path: str, reasons: Any, allowed_failure_states: set[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if not isinstance(reasons, list) or not reasons:
        return [_issue("blocked_reason_missing", f"{path}.blocked_reason", "blocked_reason must be a non-empty list")]
    for reason in reasons:
        if reason not in allowed_failure_states:
            issues.append(_issue("failure_state_invalid", f"{path}.blocked_reason", f"{reason!r} is not allowed"))
    return issues


def _validate_no_fixture_reach_claims(path: str, data: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field in ("real_source_reach_proven", "real_engagement_proven", "production_readiness"):
        if field in data and data.get(field) is not False:
            issues.append(_issue("real_source_reach_claimed_by_fixture", f"{path}.{field}", "source-pool fixtures must not claim real reach, engagement, or production readiness"))
    return issues


def _validate_connector(
    fixtures_dir: Path, fixtures: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    issues.extend(
        _validate_connector_run(
            fixtures_dir / "sample_connector_result.json",
            fixtures["sample_connector_result.json"],
            allowed_failure_states,
        )
    )
    return issues


def _validate_connector_run(
    path: Path, connector: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    status = connector.get("status")
    structured_error = connector.get("structured_error")
    if status not in {"completed", "failed"}:
        issues.append(_issue("connector_status_invalid", path, "status must be completed or failed"))
    if status == "completed" and structured_error is not None:
        issues.append(_issue("connector_error_invalid", path, "completed connector run must not contain structured_error"))
    if status == "failed":
        if not isinstance(structured_error, dict):
            issues.append(_issue("connector_error_missing", path, "failed connector run must include structured_error"))
        elif structured_error.get("code") not in allowed_failure_states:
            issues.append(_issue("failure_state_invalid", path, f"structured_error.code={structured_error.get('code')!r} is not allowed"))
    identity = connector.get("connector_identity", {})
    required_identity = ["connector_id", "connector_version", "auth_mode", "auth_requirement", "permission"]
    for field in required_identity:
        if field not in identity or _is_empty(identity[field]):
            issues.append(_issue("connector_identity_missing", f"{path}#connector_identity.{field}", "connector identity is required"))
    permissions = set(identity.get("permission", []))
    for required in REQUIRED_CONNECTOR_PERMISSIONS:
        if required not in permissions:
            issues.append(_issue("connector_permission_missing", path, f"missing permission {required!r}"))
    return issues


def _validate_evidence(fixtures_dir: Path, fixtures: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    evidence = fixtures["sample_evidence.json"]
    path = fixtures_dir / "sample_evidence.json"
    issues.extend(validate_raw_evidence_candidate(evidence, path))

    if _is_empty(evidence.get("source_url")) or _is_empty(evidence.get("canonical_url")):
        issues.append(_issue("source_url_missing", path, "source_url and canonical_url must be preserved"))
    if _is_empty(evidence.get("text_snapshot_ref")) and _is_empty(evidence.get("quoted_spans")):
        issues.append(_issue("text_evidence_missing", path, "text evidence ref or quoted spans are required"))
    quoted_spans = evidence.get("quoted_spans")
    if not isinstance(quoted_spans, list) or not quoted_spans:
        issues.append(_issue("quoted_spans_missing", path, "quoted evidence spans are required"))
    image_refs = evidence.get("image_refs")
    if not isinstance(image_refs, list) or not image_refs:
        issues.append(_issue("image_refs_missing", path, "image_refs must preserve original image references or explicit unavailable state"))
    else:
        required_image_fields = [
            "original_image_ref",
            "page_context_ref",
            "thumbnail_ref",
            "dimensions",
            "alt",
            "caption",
            "access_status",
            "redaction_status",
        ]
        for index, image_ref in enumerate(image_refs):
            if not isinstance(image_ref, dict):
                issues.append(_issue("image_ref_invalid", f"{path}#image_refs[{index}]", "image ref must be an object"))
                continue
            for field in required_image_fields:
                if field not in image_ref or _is_empty(image_ref[field]):
                    issues.append(_issue("image_ref_field_missing", f"{path}#image_refs[{index}].{field}", "image ref field is required"))
    image_policy = str(evidence.get("content_retention_policy", {}).get("image", ""))
    if "reference_only" not in image_policy or "no_download" not in image_policy or "no_cache" not in image_policy:
        issues.append(_issue("image_policy_invalid", path, "image policy must state reference-only, no-download, and no-cache"))
    connector_identity = evidence.get("connector_identity", {})
    for field in ("connector_run_id", "tool_id", "connector_id"):
        if field not in connector_identity or _is_empty(connector_identity[field]):
            issues.append(_issue("connector_identity_missing", f"{path}#connector_identity.{field}", "raw evidence must preserve connector identity"))
    if _is_empty(evidence.get("tool_name")) or _is_empty(evidence.get("tool_version")):
        issues.append(_issue("tool_version_missing", path, "raw evidence must preserve tool name and version"))
    if evidence.get("split_role") == "holdout":
        issues.append(_issue("holdout_leakage", path, "fixture evidence used for prediction must not be holdout"))
    return issues


def _validate_shadow_source_fetch_result(fixtures_dir: Path, fixtures: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    shadow = fixtures[SHADOW_SOURCE_FIXTURE]
    path = fixtures_dir / SHADOW_SOURCE_FIXTURE

    if shadow.get("fixture_only") is not True or shadow.get("no_real_source_access") is not True:
        issues.append(_issue("real_source_reach_claimed_by_fixture", path, "shadow source result must remain fixture-only"))
    if shadow.get("real_source_smoke_executed") is not False:
        issues.append(_issue("real_source_reach_claimed_by_fixture", f"{path}#real_source_smoke_executed", "shadow fixture cannot execute real source smoke"))
    if shadow.get("production_connector_ready") is not False:
        issues.append(_issue("source_pool_assumption_promoted", f"{path}#production_connector_ready", "shadow fixture cannot mark production connector ready"))
    if shadow.get("no_cookie_session_material") is not True:
        issues.append(_issue("raw_secret_material_present", f"{path}#no_cookie_session_material", "shadow fixture must explicitly exclude cookie/session material"))
    shadow_for_secret_scan = dict(shadow)
    shadow_for_secret_scan.pop("no_cookie_session_material", None)
    raw_secret_findings = find_raw_secret_material(shadow_for_secret_scan)
    if raw_secret_findings:
        issues.append(_issue("raw_secret_material_present", path, f"shadow fixture may contain only refs and fixture URLs: {raw_secret_findings}"))

    boundary = shadow.get("source_boundary", {})
    if boundary.get("real_source_reach_proven") is not False or boundary.get("real_engagement_proven") is not False:
        issues.append(_issue("real_source_reach_claimed_by_fixture", f"{path}#source_boundary", "shadow boundary cannot claim real reach or real engagement"))
    if boundary.get("session_state_used") is not False or boundary.get("raw_cookie_used") is not False:
        issues.append(_issue("raw_secret_material_present", f"{path}#source_boundary", "shadow boundary must not use session state or raw cookies"))
    if boundary.get("image_downloaded") is not False:
        issues.append(_issue("image_ref_state_invalid", f"{path}#source_boundary.image_downloaded", "shadow fixture must not download images"))

    projection_contract = shadow.get("projection_contract", {})
    if projection_contract.get("projects_to") != "RadarTimelineItem":
        issues.append(_issue("shadow_projection_invalid", f"{path}#projection_contract.projects_to", "shadow fixture must project to RadarTimelineItem"))
    if projection_contract.get("outcome_ground_truth") != "not_ground_truth_fixture_only":
        issues.append(_issue("fixture_outcome_ground_truth_risk", f"{path}#projection_contract", "shadow fixture outcomes cannot be ground truth"))
    if projection_contract.get("non_investment_advice") is not True:
        issues.append(_issue("investment_advice_field_present", f"{path}#projection_contract.non_investment_advice", "shadow projection must be non-investment-advice"))

    items = shadow.get("items", [])
    if not isinstance(items, list) or not items:
        return issues + [_issue("shadow_source_fetch_incomplete", f"{path}#items", "shadow fixture must include source observations")]

    seen_ids: set[str] = set()
    seen_sections: set[str] = set()
    seen_sources: set[str] = set()
    for index, item in enumerate(items):
        item_path = f"{path}#items[{index}]"
        if not isinstance(item, dict):
            issues.append(_issue("shadow_source_fetch_incomplete", item_path, "shadow item must be an object"))
            continue
        for field in SHADOW_REQUIRED_ITEM_FIELDS:
            if field not in item or (item[field] is not False and _is_empty(item[field])):
                if field == "image_refs" and item.get("timeline_projection", {}).get("image_status") == "no_image" and item.get("image_refs") == []:
                    continue
                issues.append(_issue("shadow_source_fetch_incomplete", f"{item_path}.{field}", "shadow item field is required"))

        shadow_item_id = item.get("shadow_item_id")
        if isinstance(shadow_item_id, str):
            if shadow_item_id in seen_ids:
                issues.append(_issue("duplicate", f"{item_path}.shadow_item_id", "shadow_item_id must be unique"))
            seen_ids.add(shadow_item_id)
        source = item.get("source")
        section = item.get("source_section")
        if isinstance(source, str):
            seen_sources.add(source)
        if isinstance(section, str):
            seen_sections.add(section)
        if source not in {"x_list", "xueqiu_daren", "xueqiu_hot", "xueqiu_dispute", "reddit"}:
            issues.append(_issue("source_registry_missing", f"{item_path}.source", "shadow source must be X list, a known Xueqiu section, or Reddit"))
        if section not in SHADOW_REQUIRED_SOURCE_SECTIONS:
            issues.append(_issue("source_registry_missing", f"{item_path}.source_section", "shadow source section must be covered by the contract"))

        if not isinstance(item.get("source_url"), str) or not item["source_url"].startswith("fixture://shadow/"):
            issues.append(_issue("real_source_reach_claimed_by_fixture", f"{item_path}.source_url", "shadow item URLs must be fixture URLs, not fabricated real post URLs"))
        if not isinstance(item.get("canonical_url"), str) or not item["canonical_url"].startswith("fixture://shadow/"):
            issues.append(_issue("real_source_reach_claimed_by_fixture", f"{item_path}.canonical_url", "shadow canonical URLs must remain fixture URLs"))
        if not isinstance(item.get("source_entry_url"), str) or not item["source_entry_url"].startswith(("https://x.com/", "https://xueqiu.com/", "https://www.reddit.com/")):
            issues.append(_issue("source_registry_missing", f"{item_path}.source_entry_url", "shadow item must preserve the source entrance URL"))

        quoted_spans = item.get("quoted_spans", [])
        if not isinstance(quoted_spans, list) or not quoted_spans or not all(isinstance(span, dict) and span.get("text") for span in quoted_spans):
            issues.append(_issue("text_unavailable", f"{item_path}.quoted_spans", "shadow item must preserve quoted text evidence"))
        text_snapshot_ref = item.get("text_snapshot_ref")
        if not isinstance(text_snapshot_ref, str) or not text_snapshot_ref.startswith(f"fixtures/{SHADOW_SOURCE_FIXTURE}#"):
            issues.append(_issue("fixture_ref_missing", f"{item_path}.text_snapshot_ref", "shadow text snapshot ref must point to this fixture"))

        engagement = item.get("engagement_snapshot", {})
        if (
            not isinstance(engagement, dict)
            or engagement.get("status") != "shadow_fixture_metrics_not_ground_truth"
            or engagement.get("metrics_are_fixture") is not True
        ):
            issues.append(_issue("real_engagement_claimed_by_fixture", f"{item_path}.engagement_snapshot", "shadow engagement metrics must be fixture-only and not ground truth"))

        projection = item.get("timeline_projection", {})
        if not isinstance(projection, dict):
            issues.append(_issue("shadow_projection_invalid", f"{item_path}.timeline_projection", "timeline_projection must be an object"))
            continue
        for field in SHADOW_REQUIRED_PROJECTION_FIELDS:
            if field not in projection or (projection[field] is not False and _is_empty(projection[field])):
                issues.append(_issue("shadow_projection_invalid", f"{item_path}.timeline_projection.{field}", "projection field is required"))
        status = projection.get("image_status")
        image_refs = item.get("image_refs", [])
        if status not in RADAR_TIMELINE_IMAGE_STATUSES:
            issues.append(_issue("image_status_invalid", f"{item_path}.timeline_projection.image_status", "shadow projection must use a timeline image_status"))
        elif status == "no_image" and image_refs != []:
            issues.append(_issue("image_ref_state_invalid", f"{item_path}.image_refs", "no_image shadow items must not carry image refs"))
        elif status != "no_image":
            if not isinstance(image_refs, list) or not image_refs:
                issues.append(_issue("image_refs_missing", f"{item_path}.image_refs", "shadow image-bearing items must preserve image refs"))
            for ref_index, image_ref in enumerate(image_refs if isinstance(image_refs, list) else []):
                ref_path = f"{item_path}.image_refs[{ref_index}]"
                if not isinstance(image_ref, dict):
                    issues.append(_issue("image_ref_state_invalid", ref_path, "image ref must be an object"))
                    continue
                if image_ref.get("download_status") not in {"not_downloaded_reference_only", "blocked_fixture_no_real_source_access"}:
                    issues.append(_issue("image_ref_state_invalid", f"{ref_path}.download_status", "shadow image refs must not be downloaded, cached, proxied, or rehosted"))
                if not image_ref.get("original_image_ref"):
                    issues.append(_issue("image_refs_missing", f"{ref_path}.original_image_ref", "shadow image refs must preserve original_image_ref"))
                if status == "image_unavailable":
                    error_code = image_ref.get("structured_error", {}).get("code")
                    if image_ref.get("access_status") != "image_unavailable" and error_code != "image_unavailable":
                        issues.append(_issue("image_ref_state_invalid", ref_path, "unavailable shadow image refs must carry unavailable status or error"))

        score = projection.get("hotness_score")
        if not isinstance(score, (int, float)) or not 0 <= score <= 1:
            issues.append(_issue("score_invalid", f"{item_path}.timeline_projection.hotness_score", "shadow hotness_score must be numeric between 0 and 1"))
        series = projection.get("hotness_series")
        if not isinstance(series, list) or len(series) < 3:
            issues.append(_issue("hotness_series_invalid", f"{item_path}.timeline_projection.hotness_series", "shadow hotness_series must have at least 3 points"))
        else:
            for point_index, point in enumerate(series):
                if not isinstance(point, (int, float)) or not 0 <= point <= 1:
                    issues.append(_issue("hotness_series_invalid", f"{item_path}.timeline_projection.hotness_series[{point_index}]", "shadow hotness points must be numeric between 0 and 1"))

        if projection.get("prediction_status") == "real_prediction_verified":
            issues.append(_issue("real_engagement_claimed_by_fixture", f"{item_path}.timeline_projection.prediction_status", "shadow prediction cannot be real verified"))
        outcome_status = projection.get("outcome_status")
        if isinstance(outcome_status, str) and "ground_truth" in outcome_status and "not_ground_truth" not in outcome_status:
            issues.append(_issue("fixture_outcome_ground_truth_risk", f"{item_path}.timeline_projection.outcome_status", "shadow outcome cannot be ground truth"))

    missing_sections = sorted(SHADOW_REQUIRED_SOURCE_SECTIONS - seen_sections)
    if missing_sections:
        issues.append(_issue("shadow_source_fetch_incomplete", f"{path}#items", f"missing shadow source sections: {missing_sections}"))
    if "x_list" not in seen_sources or not {"xueqiu_daren", "xueqiu_hot", "xueqiu_dispute", "reddit"}.issubset(seen_sources):
        issues.append(_issue("shadow_source_fetch_incomplete", f"{path}#items", "shadow fixture must include X list, Xueqiu daren/hot/dispute, and Reddit items"))
    return issues


def _parse_utc(path: str, value: Any) -> tuple[datetime | None, list[ValidationIssue]]:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None, [_issue("timestamp_invalid", path, "timestamp must be an ISO UTC string ending in Z")]
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")), []
    except ValueError as exc:
        return None, [_issue("timestamp_invalid", path, str(exc))]


def _validate_rolling_timeline_runtime(
    fixtures_dir: Path, fixtures: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    schedule = fixtures[ROLLING_SOURCE_SCHEDULE_FIXTURE]
    store = fixtures[TIMELINE_STORE_FIXTURE]
    revisit = fixtures[REVISIT_SCHEDULE_FIXTURE]
    schedule_path = fixtures_dir / ROLLING_SOURCE_SCHEDULE_FIXTURE
    store_path = fixtures_dir / TIMELINE_STORE_FIXTURE
    revisit_path = fixtures_dir / REVISIT_SCHEDULE_FIXTURE

    for path, data in [(schedule_path, schedule), (store_path, store), (revisit_path, revisit)]:
        if data.get("fixture_only") is not True or data.get("no_real_source_access") is not True:
            issues.append(_issue("real_source_reach_claimed_by_fixture", path, "rolling runtime fixtures must not access real sources"))
        issues.extend(_validate_no_raw_auth_material(path, data))

    if schedule.get("no_real_scheduler") is not True:
        issues.append(_issue("source_reach_unverified", f"{schedule_path}#no_real_scheduler", "rolling fixture must not start a real scheduler"))
    batch_policy = schedule.get("batch_policy", {})
    if batch_policy.get("batch_processing_required") is not True:
        issues.append(_issue("gate_failed", f"{schedule_path}#batch_policy.batch_processing_required", "rolling intake must be batch based"))
    if batch_policy.get("per_item_full_serial_pipeline_allowed") is not False:
        issues.append(_issue("gate_failed", f"{schedule_path}#batch_policy.per_item_full_serial_pipeline_allowed", "per-item full serial pipeline must stay disabled"))
    if batch_policy.get("max_items_per_source_per_run") != 10:
        issues.append(_issue("gate_failed", f"{schedule_path}#batch_policy.max_items_per_source_per_run", "each source run must be capped at 10 items"))

    source_by_name = {source.get("source"): source for source in schedule.get("sources", []) if isinstance(source, dict)}
    expected_intervals = {
        "x_list": 3600,
        "xueqiu_hot": 1800,
        "xueqiu_daren": 1800,
        "xueqiu_dispute": 1800,
        "reddit": 3600,
    }
    for source, interval in expected_intervals.items():
        entry = source_by_name.get(source)
        if not entry:
            issues.append(_issue("rolling_schedule_incomplete", schedule_path, f"missing rolling source {source!r}"))
            continue
        if entry.get("refresh_interval_seconds") != interval:
            issues.append(_issue("rolling_schedule_invalid", f"{schedule_path}#sources.{source}.refresh_interval_seconds", f"must be {interval}"))
        if entry.get("batch_limit") != 10:
            issues.append(_issue("rolling_schedule_invalid", f"{schedule_path}#sources.{source}.batch_limit", "batch limit must be 10"))
    reddit = source_by_name.get("reddit", {})
    if set(reddit.get("subreddits", [])) != REDDIT_SUBREDDITS:
        issues.append(_issue("rolling_schedule_incomplete", f"{schedule_path}#sources.reddit.subreddits", "Reddit source pool must include the required subreddit list"))
    if reddit.get("max_items_per_subreddit_per_run") != 10:
        issues.append(_issue("rolling_schedule_invalid", f"{schedule_path}#sources.reddit.max_items_per_subreddit_per_run", "Reddit must cap each subreddit at 10 items per run"))

    if store.get("source_schedule_ref") != f"fixtures/{ROLLING_SOURCE_SCHEDULE_FIXTURE}":
        issues.append(_issue("fixture_ref_missing", f"{store_path}#source_schedule_ref", "timeline store must reference rolling source schedule"))
    if store.get("revisit_schedule_ref") != f"fixtures/{REVISIT_SCHEDULE_FIXTURE}":
        issues.append(_issue("fixture_ref_missing", f"{store_path}#revisit_schedule_ref", "timeline store must reference revisit schedule"))
    if revisit.get("timeline_store_ref") != f"fixtures/{TIMELINE_STORE_FIXTURE}":
        issues.append(_issue("fixture_ref_missing", f"{revisit_path}#timeline_store_ref", "revisit schedule must reference timeline store"))

    retention = store.get("retention_policy", {})
    if retention.get("export_window_hours") != 120 or retention.get("expire_after_days") != 5:
        issues.append(_issue("rolling_retention_invalid", f"{store_path}#retention_policy", "rolling store must retain/export a 120 hour / 5 day window"))
    if retention.get("expired_items_exported") is not False:
        issues.append(_issue("rolling_retention_invalid", f"{store_path}#retention_policy.expired_items_exported", "expired store items must be excluded from export"))
    reference_now, timestamp_issues = _parse_utc(f"{store_path}#retention_policy.reference_now", retention.get("reference_now"))
    issues.extend(timestamp_issues)
    cutoff = reference_now - timedelta(days=5) if reference_now else None

    dedupe = store.get("dedupe_policy", {})
    if set(dedupe.get("dedupe_keys", [])) != {"source_url", "canonical_url", "content_hash"}:
        issues.append(_issue("rolling_dedupe_invalid", f"{store_path}#dedupe_policy.dedupe_keys", "dedupe must use source_url, canonical_url, and content_hash"))
    if set(dedupe.get("update_allowed_fields", [])) != ROLLING_ALLOWED_UPDATE_FIELDS:
        issues.append(_issue("rolling_dedupe_invalid", f"{store_path}#dedupe_policy.update_allowed_fields", "duplicate updates must be limited to rolling observation fields"))
    if not store.get("duplicate_update_log"):
        issues.append(_issue("rolling_dedupe_missing", f"{store_path}#duplicate_update_log", "fixture must prove duplicate update without insertion"))
    for index, update in enumerate(store.get("duplicate_update_log", [])):
        update_path = f"{store_path}#duplicate_update_log[{index}]"
        if update.get("insertion_performed") is not False:
            issues.append(_issue("rolling_dedupe_invalid", update_path, "duplicate update must not insert a new item"))
        extra_fields = sorted(set(update.get("changed_fields", [])) - ROLLING_ALLOWED_UPDATE_FIELDS)
        if extra_fields:
            issues.append(_issue("rolling_dedupe_invalid", f"{update_path}.changed_fields", f"disallowed duplicate update fields: {extra_fields}"))

    items = store.get("items", [])
    active_items = [item for item in items if isinstance(item, dict) and item.get("expired") is not True]
    expired_items = [item for item in items if isinstance(item, dict) and item.get("expired") is True]
    active_sources = {item.get("source") for item in active_items}
    if "x_list" not in active_sources or not any(str(source).startswith("xueqiu_") for source in active_sources) or "reddit" not in active_sources:
        issues.append(_issue("rolling_store_incomplete", f"{store_path}#items", "active store must include X, Xueqiu, and Reddit shadow items"))
    if not expired_items:
        issues.append(_issue("rolling_retention_invalid", f"{store_path}#items", "store must include an expired fixture item to prove five-day exclusion"))

    seen_keys: dict[tuple[str, Any], str] = {}
    for index, item in enumerate(items):
        item_path = f"{store_path}#items[{index}]"
        required_store_fields = ["id", "source_url", "canonical_url", "content_hash", "published_at", "last_observed_at", "evidence_ref", "latest_observation"]
        if item.get("expired") is not True:
            required_store_fields.append("revisit_task_refs")
        for field in required_store_fields:
            if field not in item or (item[field] is not False and _is_empty(item[field])):
                issues.append(_issue("rolling_store_item_incomplete", f"{item_path}.{field}", "rolling store item field is required"))
        for key in ("source_url", "canonical_url", "content_hash"):
            value = item.get(key)
            if value:
                marker = (key, value)
                if marker in seen_keys and item.get("expired") is not True:
                    issues.append(_issue("rolling_dedupe_invalid", f"{item_path}.{key}", f"duplicate active dedupe key also used by {seen_keys[marker]}"))
                seen_keys[marker] = item.get("id", item_path)
        published_at, published_issues = _parse_utc(f"{item_path}.published_at", item.get("published_at"))
        issues.extend(published_issues)
        if cutoff and published_at:
            if published_at < cutoff and item.get("expired") is not True:
                issues.append(_issue("rolling_retention_invalid", item_path, "older than five days must be marked expired"))
            if published_at >= cutoff and item.get("expired") is True:
                issues.append(_issue("rolling_retention_invalid", item_path, "fresh item must not be marked expired"))
        if item.get("expired") is True:
            if item.get("timeline_status") != "expired_excluded_from_export":
                issues.append(_issue("rolling_retention_invalid", f"{item_path}.timeline_status", "expired item must be marked excluded from export"))
        else:
            if set(item.get("revisit_task_refs", [])) == set():
                issues.append(_issue("revisit_schedule_missing", f"{item_path}.revisit_task_refs", "active item must reference 12h/24h revisit tasks"))

    task_windows_by_item: dict[str, set[int]] = {}
    task_source_by_item: dict[str, set[str]] = {}
    for index, task in enumerate(revisit.get("tasks", [])):
        task_path = f"{revisit_path}#tasks[{index}]"
        for field in ["revisit_task_id", "item_id", "source_link", "evidence_ref", "revisit_window_hours", "scheduled_for", "status"]:
            if field not in task or _is_empty(task[field]):
                issues.append(_issue("revisit_task_incomplete", f"{task_path}.{field}", "revisit task field is required"))
        if task.get("revisit_window_hours") not in {12, 24}:
            issues.append(_issue("revisit_schedule_invalid", f"{task_path}.revisit_window_hours", "revisit window must be 12h or 24h"))
        if not str(task.get("source_link", "")).startswith("fixture://"):
            issues.append(_issue("source_url_missing", f"{task_path}.source_link", "fixture revisit task must preserve source link"))
        if not str(task.get("evidence_ref", "")).startswith("fixtures/"):
            issues.append(_issue("text_evidence_missing", f"{task_path}.evidence_ref", "fixture revisit task must preserve evidence ref"))
        task_windows_by_item.setdefault(str(task.get("item_id")), set()).add(int(task.get("revisit_window_hours") or 0))
        task_source_by_item.setdefault(str(task.get("item_id")), set()).add(str(task.get("source_link")))
    for item in active_items:
        if task_windows_by_item.get(str(item.get("id"))) != {12, 24}:
            issues.append(_issue("revisit_schedule_missing", f"{revisit_path}#tasks.{item.get('id')}", "each active item must register 12h and 24h revisit tasks"))
        if str(item.get("source_url")) not in task_source_by_item.get(str(item.get("id")), set()):
            issues.append(_issue("revisit_schedule_invalid", f"{revisit_path}#tasks.{item.get('id')}", "revisit must sample through the same source link"))

    for batch in store.get("shadow_batch_runs", []):
        observed = batch.get("observed_item_count")
        if not isinstance(observed, int) or observed > 10:
            issues.append(_issue("rolling_batch_limit_invalid", f"{store_path}#shadow_batch_runs.{batch.get('batch_id')}", "shadow batch runs must stay at or under 10 observed items"))
        source = batch.get("source")
        if source == "reddit" and batch.get("max_items_per_subreddit") != 10:
            issues.append(_issue("rolling_batch_limit_invalid", f"{store_path}#shadow_batch_runs.{batch.get('batch_id')}", "Reddit batch must state 10 items per subreddit"))
    return issues


def _validate_radar_timeline(fixtures_dir: Path, fixtures: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    feed = fixtures["sample_radar_timeline_feed.json"]
    store = fixtures.get(TIMELINE_STORE_FIXTURE, {})
    path = fixtures_dir / "sample_radar_timeline_feed.json"

    if feed.get("fixture_only") is not True or feed.get("no_real_source_access") is not True:
        issues.append(_issue("real_source_reach_claimed_by_fixture", path, "timeline feed must remain fixture-only"))

    for index, ref in enumerate(feed.get("source_refs", [])):
        if not isinstance(ref, str) or not ref.startswith("fixtures/") or not (fixtures_dir.parent / _ref_path(ref)).exists():
            issues.append(_issue("fixture_ref_missing", f"{path}#source_refs[{index}]", "source_refs must resolve to fixtures"))

    sorting_policy = feed.get("sorting_policy", {})
    if sorting_policy.get("outcome_ground_truth") != "not_ground_truth_fixture_only":
        issues.append(_issue("fixture_outcome_ground_truth_risk", f"{path}#sorting_policy", "timeline cannot treat fixture outcomes as ground truth"))

    rolling = feed.get("rolling_runtime", {})
    view_config = feed.get("view_config", {})
    auto_refresh = feed.get("auto_refresh", {})
    if rolling.get("exported_from_store_ref") != f"fixtures/{TIMELINE_STORE_FIXTURE}":
        issues.append(_issue("rolling_feed_contract_missing", f"{path}#rolling_runtime.exported_from_store_ref", "feed must declare the rolling timeline store source"))
    if rolling.get("expired_items_excluded") is not True:
        issues.append(_issue("rolling_retention_invalid", f"{path}#rolling_runtime.expired_items_excluded", "feed must exclude expired store items"))
    if view_config.get("default_recent_hours") != 120:
        issues.append(_issue("rolling_feed_contract_missing", f"{path}#view_config.default_recent_hours", "default recent-hours view must be 120"))
    supported_sort_ids = {
        item.get("id")
        for item in view_config.get("supported_sorts", [])
        if isinstance(item, dict)
    }
    if not {"hotness", "published_at"}.issubset(supported_sort_ids):
        issues.append(_issue("rolling_feed_contract_missing", f"{path}#view_config.supported_sorts", "feed must expose hotness and published_at sort modes"))
    if auto_refresh.get("enabled") is not True or not isinstance(auto_refresh.get("poll_interval_seconds"), int):
        issues.append(_issue("rolling_feed_contract_missing", f"{path}#auto_refresh", "feed must expose automatic refresh config"))

    items = feed.get("items", [])
    if not isinstance(items, list) or len(items) < 3:
        issues.append(_issue("radar_timeline_incomplete", f"{path}#items", "at least 3 RadarTimelineItem fixtures are required"))
        return issues

    image_statuses: set[str] = set()
    hotness_scores: list[float] = []
    shadow_item_ids: set[str] = set()
    for index, item in enumerate(items):
        item_path = f"{path}#items[{index}]"
        if item.get("object_type") != "RadarTimelineItem":
            issues.append(_issue("object_type_mismatch", item_path, "timeline items must have object_type RadarTimelineItem"))
        for field in RADAR_TIMELINE_REQUIRED_ITEM_FIELDS:
            if field not in item or (item[field] is not False and _is_empty(item[field])):
                if field == "image_refs" and item.get("image_status") == "no_image" and item.get("image_refs") == []:
                    continue
                issues.append(_issue("radar_timeline_item_incomplete", f"{item_path}.{field}", "timeline item field is required"))

        forbidden_present = sorted(RADAR_TIMELINE_FORBIDDEN_FIELDS.intersection(item))
        if forbidden_present:
            issues.append(_issue("investment_advice_field_present", item_path, f"forbidden investment fields present: {forbidden_present}"))
        if item.get("non_investment_advice") is not True:
            issues.append(_issue("investment_advice_field_present", f"{item_path}.non_investment_advice", "timeline item must explicitly be non-investment-advice"))

        evidence_ref = item.get("evidence_ref")
        if not isinstance(evidence_ref, str) or not evidence_ref.startswith("fixtures/") or not (fixtures_dir.parent / _ref_path(evidence_ref)).exists():
            issues.append(_issue("fixture_ref_missing", f"{item_path}.evidence_ref", "timeline item must preserve a resolvable evidence_ref"))

        status = item.get("image_status")
        image_statuses.add(str(status))
        image_refs = item.get("image_refs", [])
        if status not in RADAR_TIMELINE_IMAGE_STATUSES:
            issues.append(_issue("image_status_invalid", f"{item_path}.image_status", "image_status must be available, no_image, or image_unavailable"))
        elif status == "no_image" and image_refs != []:
            issues.append(_issue("image_ref_state_invalid", f"{item_path}.image_refs", "no_image items must not carry image refs"))
        elif status != "no_image":
            if not isinstance(image_refs, list) or not image_refs:
                issues.append(_issue("image_refs_missing", f"{item_path}.image_refs", "image-bearing states must preserve image refs or explicit unavailable refs"))
            for ref_index, image_ref in enumerate(image_refs if isinstance(image_refs, list) else []):
                ref_path = f"{item_path}.image_refs[{ref_index}]"
                if not isinstance(image_ref, dict):
                    issues.append(_issue("image_ref_state_invalid", ref_path, "image refs must be objects"))
                    continue
                if status == "available" and not image_ref.get("original_image_ref"):
                    issues.append(_issue("image_refs_missing", ref_path, "available images must preserve original_image_ref"))
                if status == "image_unavailable":
                    error_code = image_ref.get("structured_error", {}).get("code")
                    if image_ref.get("access_status") != "image_unavailable" and error_code != "image_unavailable":
                        issues.append(_issue("image_ref_state_invalid", ref_path, "unavailable images must carry an unavailable status or structured error"))

        score = item.get("hotness_score")
        if not isinstance(score, (int, float)) or not 0 <= score <= 1:
            issues.append(_issue("score_invalid", f"{item_path}.hotness_score", "hotness_score must be numeric between 0 and 1"))
        else:
            hotness_scores.append(float(score))
        series = item.get("hotness_series")
        if not isinstance(series, list) or len(series) < 3:
            issues.append(_issue("hotness_series_invalid", f"{item_path}.hotness_series", "hotness_series must have at least 3 points"))
        else:
            for point_index, point in enumerate(series):
                if not isinstance(point, (int, float)) or not 0 <= point <= 1:
                    issues.append(_issue("hotness_series_invalid", f"{item_path}.hotness_series[{point_index}]", "hotness series points must be numeric between 0 and 1"))

        if item.get("prediction_status") == "real_prediction_verified":
            issues.append(_issue("real_engagement_claimed_by_fixture", f"{item_path}.prediction_status", "fixture timeline cannot claim verified real prediction status"))
        outcome_status = item.get("outcome_status")
        if isinstance(outcome_status, str) and "ground_truth" in outcome_status and "not_ground_truth" not in outcome_status:
            issues.append(_issue("fixture_outcome_ground_truth_risk", f"{item_path}.outcome_status", "fixture outcome status must not become ground truth"))
        shadow_source_item_id = item.get("shadow_source_item_id")
        if isinstance(shadow_source_item_id, str):
            shadow_item_ids.add(shadow_source_item_id)

    missing_statuses = sorted(RADAR_TIMELINE_IMAGE_STATUSES - image_statuses)
    if missing_statuses:
        issues.append(_issue("radar_timeline_incomplete", f"{path}#items", f"missing image status coverage: {missing_statuses}"))
    if hotness_scores != sorted(hotness_scores, reverse=True):
        issues.append(_issue("radar_timeline_sort_invalid", f"{path}#items", "timeline items must be sorted by hotness_score descending"))
    expired_store_ids = {
        item.get("id")
        for item in store.get("items", [])
        if isinstance(item, dict) and item.get("expired") is True
    }
    exported_ids = {item.get("id") for item in items if isinstance(item, dict)}
    if exported_ids & expired_store_ids:
        issues.append(_issue("rolling_retention_invalid", f"{path}#items", "expired store items must not appear in exported feed"))
    exported_sources = {item.get("source") for item in items if isinstance(item, dict)}
    if "x_list" not in exported_sources or "reddit" not in exported_sources or not any(str(source).startswith("xueqiu_") for source in exported_sources):
        issues.append(_issue("rolling_feed_contract_missing", f"{path}#items", "exported feed must include X, Xueqiu, and Reddit shadow items"))
    if SHADOW_SOURCE_FIXTURE in fixtures:
        shadow = fixtures[SHADOW_SOURCE_FIXTURE]
        expected_shadow_item_ids = {
            item.get("shadow_item_id")
            for item in shadow.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("shadow_item_id"), str)
        }
        missing_shadow_items = sorted(expected_shadow_item_ids - shadow_item_ids)
        if missing_shadow_items:
            issues.append(_issue("shadow_projection_missing", f"{path}#items", f"missing projected shadow items: {missing_shadow_items}"))
        if f"fixtures/{SHADOW_SOURCE_FIXTURE}" not in feed.get("source_refs", []):
            issues.append(_issue("fixture_ref_missing", f"{path}#source_refs", "timeline feed must include the shadow source fixture ref"))
    return issues


def _validate_candidate_structure_prediction(fixtures_dir: Path, fixtures: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    candidate = fixtures["sample_candidate.json"]
    structure = fixtures["sample_structure.json"]
    prediction = fixtures["sample_prediction.json"]

    if candidate.get("eligibility_status") == "filtered_out" and not candidate.get("filter_reasons"):
        issues.append(_issue("filter_reason_missing", fixtures_dir / "sample_candidate.json", "filtered candidates must include reasons"))
    for score_field, data, filename in [
        ("candidate_score", candidate, "sample_candidate.json"),
        ("structure_score", structure, "sample_structure.json"),
        ("12h_score", prediction, "sample_prediction.json"),
        ("24h_score", prediction, "sample_prediction.json"),
        ("confidence", prediction, "sample_prediction.json"),
        ("uncertainty", prediction, "sample_prediction.json"),
    ]:
        value = data.get(score_field)
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            issues.append(_issue("score_invalid", f"{fixtures_dir / filename}#{score_field}", "score must be numeric between 0 and 1"))
    if prediction.get("confidence", 0) > 0.7 and "insufficient_evidence" in prediction.get("risk_flags", []):
        issues.append(_issue("insufficient_evidence_high_confidence", fixtures_dir / "sample_prediction.json", "insufficient evidence cannot be high confidence"))
    for ref_field in ("context_manifest_ref", "replay_manifest_ref", "source_evidence_ref"):
        ref = prediction.get(ref_field)
        if not isinstance(ref, str) or not ref.startswith("fixtures/") or not (fixtures_dir.parent / _ref_path(ref)).exists():
            issues.append(_issue("fixture_ref_missing", fixtures_dir / "sample_prediction.json", f"{ref_field} must resolve to a fixture file"))
    return issues


def _validate_context_and_replay(fixtures_dir: Path, fixtures: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    context = fixtures["sample_context_manifest.json"]
    replay = fixtures["sample_replay_manifest.json"]
    context_path = fixtures_dir / "sample_context_manifest.json"
    replay_path = fixtures_dir / "sample_replay_manifest.json"

    for index, ref in enumerate(context.get("included_refs", [])):
        if ref.get("split_role") == "holdout":
            issues.append(_issue("holdout_leakage", f"{context_path}#included_refs[{index}]", "holdout refs cannot enter generator/predictor context"))
        if ref.get("label_visibility") not in {"hidden", "hidden_until_eval"}:
            issues.append(_issue("label_visibility_invalid", f"{context_path}#included_refs[{index}]", "included labels must be hidden"))
    policy = context.get("holdout_access_policy", {})
    if policy.get("holdout_refs_allowed_in_generator_context") is not False:
        issues.append(_issue("holdout_policy_invalid", context_path, "holdout refs must be blocked from generator context"))

    critical_replay_fields = [
        "config_hash",
        "prompt_hash",
        "context_hash",
        "tool_output_hashes",
        "model_provider",
        "model_id",
        "model_config",
        "schema_versions",
        "replay_mode",
    ]
    for field in critical_replay_fields:
        if field not in replay or _is_empty(replay[field]):
            issues.append(_issue("replay_manifest_incomplete", f"{replay_path}#{field}", "replay manifest field is required"))
    if replay.get("replay_mode") not in {"deterministic_fixture", "replay_unverifiable"}:
        issues.append(_issue("replay_mode_invalid", replay_path, "replay_mode must be deterministic_fixture or replay_unverifiable"))
    if replay.get("model_config", {}).get("uses_external_model") not in {False, None}:
        issues.append(_issue("external_model_not_allowed", replay_path, "fixture MVP must not call external model APIs"))
    return issues


def _validate_outcome_eval_proposal(fixtures_dir: Path, fixtures: dict[str, Any]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    outcome = fixtures["sample_outcome.json"]
    evaluation = fixtures["sample_eval.json"]
    proposal = fixtures["sample_improvement_proposal.json"]

    if outcome.get("metrics_source") != "fixture_declared_outcome_not_ground_truth":
        issues.append(_issue("fixture_outcome_ground_truth_risk", fixtures_dir / "sample_outcome.json", "fixture outcome must be marked as not ground truth"))
    raw_delta = outcome.get("raw_engagement_delta", {})
    if raw_delta.get("not_ground_truth") is not True:
        issues.append(_issue("fixture_outcome_ground_truth_risk", fixtures_dir / "sample_outcome.json", "raw engagement delta must be marked not_ground_truth"))
    if evaluation.get("label_visibility") == "visible_to_predictor":
        issues.append(_issue("evaluator_generator_leakage", fixtures_dir / "sample_eval.json", "eval labels cannot be visible to predictor"))
    changed_class = proposal.get("changed_component_class")
    if isinstance(changed_class, list) or (isinstance(changed_class, str) and "," in changed_class):
        issues.append(_issue("multi_component_proposal", fixtures_dir / "sample_improvement_proposal.json", "proposal must change only one component class"))
    if not proposal.get("rollback_criteria"):
        issues.append(_issue("rollback_criteria_missing", fixtures_dir / "sample_improvement_proposal.json", "proposal must define rollback criteria"))
    return issues


def _validate_promotion(
    fixtures_dir: Path, fixtures: dict[str, Any], allowed_failure_states: set[str]
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    promotion = fixtures["sample_promotion_decision.json"]
    path = fixtures_dir / "sample_promotion_decision.json"
    decision = promotion.get("decision")
    if decision not in {"rejected", "shadow_only"}:
        issues.append(_issue("promotion_not_allowed_for_fixture_mvp", path, "fixture MVP cannot output promoted or active"))
    gate_required = [
        "gate_id",
        "gate_version",
        "threshold_version",
        "gate_command_ref",
        "artifact_schema_ref",
        "input_refs",
        "output_artifact_ref",
        "status",
        "failure_code",
        "event_id",
    ]
    for index, gate in enumerate(promotion.get("gate_results", [])):
        for field in gate_required:
            if field not in gate:
                issues.append(_issue("gate_result_incomplete", f"{path}#gate_results[{index}].{field}", "gate result field is required"))
        failure_code = gate.get("failure_code")
        if failure_code is not None and failure_code not in allowed_failure_states:
            issues.append(_issue("failure_state_invalid", f"{path}#gate_results[{index}]", f"failure_code={failure_code!r} is not allowed"))
        for version_field in ("gate_version", "threshold_version"):
            if _is_empty(gate.get(version_field)):
                issues.append(_issue("version_missing", f"{path}#gate_results[{index}].{version_field}", "gate version field is required"))
    for failure in promotion.get("hard_gate_failures", []):
        if failure not in allowed_failure_states:
            issues.append(_issue("failure_state_invalid", f"{path}#hard_gate_failures", f"{failure!r} is not allowed"))
    if decision in {"promoted", "active"} and promotion.get("hard_gate_failures"):
        issues.append(_issue("promotion_gate_violation", path, "hard gate failures cannot be promoted or active"))
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate News Harness MVP fixtures.")
    parser.add_argument("fixtures", type=Path, help="Fixture directory to validate")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema contract file")
    args = parser.parse_args(argv)

    issues = validate_fixture_dir(args.fixtures, args.schema)
    if issues:
        print("status: failed")
        print(f"issues: {len(issues)}")
        for issue in issues:
            print(issue.format())
        return 1

    schema = _load_json(args.schema)
    print("status: ok")
    print(f"schema_version: {schema['schema_version']}")
    print(f"fixtures_dir: {args.fixtures}")
    print(f"files_checked: {len(schema['expected_fixtures'])}")
    print(f"guardrails: {','.join(GUARDRAILS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
