"""Pre-real-source readiness gate for the fixture-only harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import DEFAULT_PREFLIGHT_CONFIG, check_preflight_config, find_raw_secret_material, load_preflight_config
from .connectors import check_connector_runtime_boundary
from .constants import GUARDRAILS, REQUIRED_PATH_EVENTS
from .events import canonical_json
from .fixtures import DEFAULT_SCHEMA, ROOT, load_fixture_set, load_json
from .gates import source_pool_intake_gate, source_readiness_gate, source_smoke_gate
from .constants import LOW_RISK_SOURCE_TYPES
from .inspect import inspect_replay
from .paths import safe_output_path, write_json_artifact, write_text_artifact
from .validator import validate_fixture_dir


REPORT_VERSION = "preflight.fixture.v1"
BLOCKED_REAL_SOURCES = {"xueqiu", "reddit", "x", "eastmoney_guba"}
ROLLING_REQUIRED_EVENTS = {
    "all_source_runner.completed",
    "deepseek_scoring.completed",
    "rolling_source_schedule.loaded",
    "shadow_batch.completed",
    "timeline_store.updated",
    "revisit_schedule.registered",
}
ALL_SOURCE_RUNNER_CONFIG = Path("configs/all_source_runner.example.json")
DEEPSEEK_PROVIDER_CONFIG = Path("configs/deepseek_provider.example.json")
SOURCE_RUNNER_RUNTIME_CONFIG = Path("configs/source_runner_runtime.example.json")


def _status(passed: bool) -> str:
    return "pass" if passed else "fail"


def _check(passed: bool, summary: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": _status(passed),
        "summary": summary,
        "evidence": evidence or {},
    }


def _write_json(path: Path, data: dict[str, Any]) -> None:
    write_json_artifact(path, data)


def _write_text(path: Path, text: str) -> None:
    write_text_artifact(path, text)


def _load_run_artifacts(inspect_result: dict[str, Any]) -> list[tuple[str, Any]]:
    event_log_ref = inspect_result.get("event_log_ref")
    if not isinstance(event_log_ref, str):
        return []
    event_log = Path(event_log_ref)
    if not event_log.is_absolute():
        event_log = Path.cwd() / event_log
    run_dir = event_log.parent
    artifacts: list[tuple[str, Any]] = []
    for path in sorted(run_dir.glob("*.json")):
        try:
            artifacts.append((str(path), load_json(path)))
        except (OSError, json.JSONDecodeError):
            continue
    if event_log.exists():
        try:
            events = [json.loads(line) for line in event_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        except json.JSONDecodeError:
            events = []
        artifacts.append((str(event_log), events))
    return artifacts


def _secret_scan_payload(data: Any) -> Any:
    if isinstance(data, dict) and data.get("object_type") == "ShadowSourceFetchResult":
        copy = dict(data)
        copy.pop("no_cookie_session_material", None)
        return copy
    return data


def _check_source_readiness(fixtures: dict[str, Any]) -> dict[str, Any]:
    gate = source_readiness_gate(fixtures)
    return _check(
        gate["passed"],
        "ConnectorReadinessGate allows only fixture-ready source and blocks planned/auth/risk/unsupported sources.",
        {key: value for key, value in gate.items() if key != "passed"},
    )


def _check_source_smoke(fixtures: dict[str, Any]) -> dict[str, Any]:
    gate = source_smoke_gate(fixtures)
    return _check(
        gate["passed"],
        "Source smoke gate keeps fixture smoke non-executing and blocks auth/planned sources.",
        {key: value for key, value in gate.items() if key != "passed"},
    )


def _check_source_pool_intake(fixtures: dict[str, Any]) -> dict[str, Any]:
    gate = source_pool_intake_gate(fixtures)
    return _check(
        gate["passed"],
        "Source pool intake registers user-provided entrances without treating them as smoke-ready or production-ready.",
        {key: value for key, value in gate.items() if key != "passed"},
    )


def _check_vps_auth_gated_runner(fixtures: dict[str, Any]) -> dict[str, Any]:
    runner = fixtures["sample_vps_source_runner_plan.json"]
    x_plan = fixtures["sample_x_list_auth_gated_smoke_plan.json"]
    xueqiu_plan = fixtures["sample_xueqiu_browser_assisted_smoke_plan.json"]
    summary = runner.get("readiness_summary", {})
    x_readiness = x_plan.get("readiness", {})
    xueqiu_readiness = xueqiu_plan.get("readiness", {})
    x_auth = x_plan.get("auth_boundary", {})
    x_read_only = x_plan.get("read_only_boundary", {})
    x_execution = x_plan.get("smoke_execution", {})
    xueqiu_browser = xueqiu_plan.get("browser_assisted_boundary", {})
    xueqiu_session = xueqiu_plan.get("session_boundary", {})

    x_production_blockers_present = {
        "auth_required",
        "session_state_unapproved",
        "legal_access_unapproved",
        "rate_limit_unverified",
        "read_only_boundary_unapproved",
        "connector_not_ready",
    }.issubset(set(x_readiness.get("blocked_reason", [])))
    x_smoke_not_executed = (
        x_execution.get("executed") is False
        and x_execution.get("network_access") is False
        and x_execution.get("scheduler_started") is False
    )
    xueqiu_blocked = (
        xueqiu_browser.get("browser_assisted_required") is True
        and xueqiu_browser.get("cannot_override_readiness") is True
        and xueqiu_session.get("auth_requirement") == "session_optional_unverified"
        and xueqiu_readiness.get("vps_runner_plan_ready") is False
        and xueqiu_readiness.get("production_connector_ready") is False
    )
    passed = (
        summary.get("fixture_ready") is True
        and summary.get("vps_runner_plan_ready") is True
        and summary.get("real_source_smoke_not_executed") is True
        and summary.get("production_connector_ready") is False
        and x_readiness.get("vps_runner_plan_ready") is True
        and x_readiness.get("production_connector_ready") is False
        and x_auth.get("session_state_approval_status") != "approved"
        and x_read_only.get("approved") is False
        and x_production_blockers_present
        and x_smoke_not_executed
        and xueqiu_blocked
    )
    return _check(
        passed,
        "VPS auth-gated runner plans are fixture-valid, non-executing, and not production connector ready.",
        {
            "fixture_ready": summary.get("fixture_ready"),
            "vps_runner_plan_ready": summary.get("vps_runner_plan_ready"),
            "real_source_smoke_not_executed": summary.get("real_source_smoke_not_executed"),
            "production_connector_ready": summary.get("production_connector_ready"),
            "x_list": {
                "source_url": x_plan.get("source_url"),
                "session_state_approval_status": x_auth.get("session_state_approval_status"),
                "legal_tos_status": x_auth.get("legal_tos_status"),
                "rate_limit_status": x_plan.get("rate_limit_policy", {}).get("rate_limit_status"),
                "read_only_boundary_approved": x_read_only.get("approved"),
                "blocked_reason": x_readiness.get("blocked_reason", []),
                "smoke_not_executed": x_smoke_not_executed,
            },
            "xueqiu": {
                "source_url": xueqiu_plan.get("source_url"),
                "browser_assisted_required": xueqiu_browser.get("browser_assisted_required"),
                "session_boundary": xueqiu_session.get("auth_requirement"),
                "vps_runner_plan_ready": xueqiu_readiness.get("vps_runner_plan_ready"),
                "production_connector_ready": xueqiu_readiness.get("production_connector_ready"),
                "blocked_reason": xueqiu_readiness.get("blocked_reason", []),
            },
        },
    )


def _check_all_source_deepseek_runner(fixtures: dict[str, Any], inspect_result: dict[str, Any]) -> dict[str, Any]:
    runner = fixtures["sample_all_source_runner_dry_run.json"]
    scoring = fixtures["sample_deepseek_scoring_fixture.json"]
    store = fixtures["timeline_store.json"]
    event_types = set(inspect_result.get("event_types_present", []))
    missing_events = sorted({"all_source_runner.completed", "deepseek_scoring.completed"} - event_types)
    config_findings: dict[str, list[str]] = {}
    for config_path in (ALL_SOURCE_RUNNER_CONFIG, DEEPSEEK_PROVIDER_CONFIG, SOURCE_RUNNER_RUNTIME_CONFIG):
        try:
            config = load_json(config_path)
        except (OSError, json.JSONDecodeError) as exc:
            config_findings[str(config_path)] = [str(exc)]
            continue
        findings = find_raw_secret_material(config)
        if findings:
            config_findings[str(config_path)] = findings

    expected_sources = {"x_list", "xueqiu_hot", "xueqiu_daren", "xueqiu_dispute", "reddit"}
    observation_sources = {item.get("source") for item in runner.get("observations", []) if isinstance(item, dict)}
    status_sources = {item.get("source") for item in runner.get("source_statuses", []) if isinstance(item, dict)}
    scored = scoring.get("scored_candidates", [])
    llm_linked_ids = [
        item.get("id")
        for item in store.get("items", [])
        if isinstance(item, dict)
        and item.get("expired") is not True
        and any(str(ref).startswith("fixtures/sample_deepseek_scoring_fixture.json#scored_candidates") for ref in item.get("llm_scoring_refs", []))
    ]
    provider = scoring.get("provider_status", {})
    passed = (
        runner.get("fixture_only") is True
        and runner.get("no_real_source_access") is True
        and runner.get("real_source_smoke_executed") is False
        and runner.get("production_connector_ready") is False
        and status_sources == expected_sources
        and observation_sources == expected_sources
        and runner.get("batch_policy", {}).get("max_items_per_source_per_run") == 10
        and runner.get("batch_policy", {}).get("reddit_max_items_per_subreddit_per_run") == 10
        and scoring.get("model_provider") == "deepseek"
        and scoring.get("llm_mode") == "fixture"
        and scoring.get("no_real_model_call") is True
        and provider.get("api_key_present") is False
        and provider.get("provider_called") is False
        and provider.get("fallback_used") == "fixture_scoring"
        and len(scored) == len(runner.get("observations", []))
        and all(candidate.get("model_provider") == "deepseek" for candidate in scored)
        and all("model_inference_not_ground_truth" in candidate.get("risk_flags", []) for candidate in scored)
        and scoring.get("ground_truth_policy", {}).get("llm_output_is_ground_truth") is False
        and llm_linked_ids
        and not config_findings
        and not missing_events
    )
    return _check(
        passed,
        "All-source dry-run covers Reddit, X list, and Xueqiu; DeepSeek fixture scoring produces traceable model inference without real provider calls.",
        {
            "status_sources": sorted(status_sources),
            "observation_sources": sorted(observation_sources),
            "observation_count": len(runner.get("observations", [])),
            "structured_error_count": len(runner.get("structured_errors", [])),
            "max_items_per_source_per_run": runner.get("batch_policy", {}).get("max_items_per_source_per_run"),
            "reddit_max_items_per_subreddit_per_run": runner.get("batch_policy", {}).get("reddit_max_items_per_subreddit_per_run"),
            "model_provider": scoring.get("model_provider"),
            "model_id": scoring.get("model_id"),
            "prompt_version": scoring.get("prompt_version"),
            "scoring_version": scoring.get("scoring_version"),
            "api_key_present": provider.get("api_key_present"),
            "provider_called": provider.get("provider_called"),
            "fallback_used": provider.get("fallback_used"),
            "scored_candidate_count": len(scored),
            "llm_linked_timeline_item_ids": llm_linked_ids,
            "raw_secret_findings": config_findings,
            "missing_events": missing_events,
            "production_connector_ready": False,
            "real_source_smoke_not_executed": True,
        },
    )


def _check_radar_timeline(fixtures: dict[str, Any], inspect_result: dict[str, Any]) -> dict[str, Any]:
    feed = fixtures["sample_radar_timeline_feed.json"]
    shadow = fixtures["sample_shadow_source_fetch_result.json"]
    items = feed.get("items", [])
    image_statuses = sorted({item.get("image_status") for item in items if isinstance(item, dict)})
    missing_evidence_refs = [
        item.get("id")
        for item in items
        if not isinstance(item, dict) or not isinstance(item.get("evidence_ref"), str) or not item.get("evidence_ref", "").startswith("fixtures/")
    ]
    ground_truth_claims = [
        item.get("id")
        for item in items
        if isinstance(item, dict)
        and isinstance(item.get("outcome_status"), str)
        and "ground_truth" in item.get("outcome_status", "")
        and "not_ground_truth" not in item.get("outcome_status", "")
    ]
    investment_fields = []
    for item in items:
        if not isinstance(item, dict):
            continue
        present = sorted({"investment_advice", "trade_recommendation", "target_price", "position_sizing"}.intersection(item))
        if present:
            investment_fields.append({"id": item.get("id"), "fields": present})
    hotness_series_ready = [
        item.get("id")
        for item in items
        if isinstance(item.get("hotness_series"), list) and len(item.get("hotness_series", [])) >= 3
    ]
    shadow_item_ids = {
        item.get("shadow_item_id")
        for item in shadow.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("shadow_item_id"), str)
    }
    projected_shadow_item_ids = {
        item.get("shadow_source_item_id")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("shadow_source_item_id"), str)
    }
    shadow_sources = {
        item.get("source")
        for item in shadow.get("items", [])
        if isinstance(item, dict) and isinstance(item.get("source"), str)
    }
    missing_projected_shadow_items = sorted(shadow_item_ids - projected_shadow_item_ids)
    shadow_contract_passed = (
        shadow.get("fixture_only") is True
        and shadow.get("no_real_source_access") is True
        and shadow.get("real_source_smoke_executed") is False
        and shadow.get("production_connector_ready") is False
        and {"x_list", "xueqiu_daren", "xueqiu_hot", "xueqiu_dispute"}.issubset(shadow_sources)
        and not missing_projected_shadow_items
        and "fixtures/sample_shadow_source_fetch_result.json" in feed.get("source_refs", [])
    )
    passed = (
        feed.get("fixture_only") is True
        and feed.get("no_real_source_access") is True
        and len(items) >= 3
        and {"available", "no_image", "image_unavailable"}.issubset(set(image_statuses))
        and len(hotness_series_ready) == len(items)
        and not missing_evidence_refs
        and not ground_truth_claims
        and not investment_fields
        and "timeline_feed.json" not in inspect_result.get("missing_artifacts", [])
        and shadow_contract_passed
    )
    return _check(
        passed,
        "Radar timeline feed is product-readable, fixture-only, evidence-linked, and not treated as real outcome truth.",
        {
            "feed_id": feed.get("feed_id"),
            "item_count": len(items),
            "image_statuses": image_statuses,
            "hotness_series_ready_item_ids": hotness_series_ready,
            "missing_evidence_ref_item_ids": missing_evidence_refs,
            "ground_truth_claim_item_ids": ground_truth_claims,
            "investment_fields": investment_fields,
            "timeline_artifact_missing": "timeline_feed.json" in inspect_result.get("missing_artifacts", []),
            "shadow_sources": sorted(shadow_sources),
            "shadow_item_count": len(shadow_item_ids),
            "projected_shadow_item_ids": sorted(projected_shadow_item_ids),
            "missing_projected_shadow_item_ids": missing_projected_shadow_items,
            "shadow_real_source_smoke_executed": shadow.get("real_source_smoke_executed"),
            "shadow_production_connector_ready": shadow.get("production_connector_ready"),
        },
    )


def _check_rolling_timeline_runtime(fixtures: dict[str, Any], inspect_result: dict[str, Any]) -> dict[str, Any]:
    schedule = fixtures["rolling_source_schedule.json"]
    store = fixtures["timeline_store.json"]
    revisit = fixtures["revisit_schedule.json"]
    feed = fixtures["sample_radar_timeline_feed.json"]
    sources = {source.get("source"): source for source in schedule.get("sources", []) if isinstance(source, dict)}
    active_items = [item for item in store.get("items", []) if isinstance(item, dict) and item.get("expired") is not True]
    expired_items = [item for item in store.get("items", []) if isinstance(item, dict) and item.get("expired") is True]
    feed_ids = {item.get("id") for item in feed.get("items", []) if isinstance(item, dict)}
    expired_ids = {item.get("id") for item in expired_items}
    task_windows: dict[str, set[int]] = {}
    for task in revisit.get("tasks", []):
        if isinstance(task, dict):
            task_windows.setdefault(str(task.get("item_id")), set()).add(int(task.get("revisit_window_hours") or 0))
    active_revisit_complete = all(task_windows.get(str(item.get("id"))) == {12, 24} for item in active_items)
    event_types = set(inspect_result.get("event_types_present", []))
    missing_events = sorted(ROLLING_REQUIRED_EVENTS - event_types)
    reddit = sources.get("reddit", {})
    passed = (
        schedule.get("fixture_only") is True
        and store.get("fixture_only") is True
        and revisit.get("fixture_only") is True
        and schedule.get("no_real_scheduler") is True
        and sources.get("x_list", {}).get("refresh_interval_seconds") == 3600
        and sources.get("xueqiu_hot", {}).get("refresh_interval_seconds") == 1800
        and sources.get("xueqiu_daren", {}).get("refresh_interval_seconds") == 1800
        and sources.get("xueqiu_dispute", {}).get("refresh_interval_seconds") == 1800
        and reddit.get("refresh_interval_seconds") == 3600
        and reddit.get("max_items_per_subreddit_per_run") == 10
        and len(reddit.get("subreddits", [])) == 20
        and store.get("retention_policy", {}).get("export_window_hours") == 120
        and store.get("retention_policy", {}).get("expired_items_exported") is False
        and bool(store.get("duplicate_update_log"))
        and active_revisit_complete
        and not (feed_ids & expired_ids)
        and not missing_events
    )
    return _check(
        passed,
        "Rolling timeline runtime models batch refresh, dedupe updates, five-day retention, revisit registration, and feed export without real source access.",
        {
            "source_refresh_intervals": {
                name: source.get("refresh_interval_seconds")
                for name, source in sources.items()
            },
            "reddit_subreddit_count": len(reddit.get("subreddits", [])),
            "reddit_max_items_per_subreddit": reddit.get("max_items_per_subreddit_per_run"),
            "duplicate_update_count": len(store.get("duplicate_update_log", [])),
            "active_item_count": len(active_items),
            "expired_item_ids": sorted(expired_ids),
            "expired_ids_in_feed": sorted(feed_ids & expired_ids),
            "revisit_task_count": len(revisit.get("tasks", [])),
            "active_revisit_complete": active_revisit_complete,
            "missing_rolling_events": missing_events,
            "auto_refresh": feed.get("auto_refresh"),
            "view_config": feed.get("view_config"),
            "production_connector_ready": False,
            "real_source_smoke_not_executed": True,
        },
    )


def _check_promotion_gate(fixtures: dict[str, Any]) -> dict[str, Any]:
    promotion = fixtures["sample_promotion_decision.json"]
    decision = promotion.get("decision")
    promoted = decision in {"promoted", "active"}
    passed = decision in {"shadow_only", "rejected"} and not promoted and bool(promotion.get("gate_results"))
    return _check(
        passed,
        "Promotion gate remains non-production for fixture evidence.",
        {
            "decision": decision,
            "hard_gate_failures": promotion.get("hard_gate_failures", []),
            "gate_ids": [gate.get("gate_id") for gate in promotion.get("gate_results", [])],
            "production_promotion_blocked": not promoted,
        },
    )


def _check_deepseek_boundary(config: dict[str, Any], fixtures: dict[str, Any]) -> dict[str, Any]:
    boundary = config.get("deepseek_provider_boundary", {})
    scoring = fixtures["sample_deepseek_scoring_fixture.json"]
    required_fields = [
        "model_provider",
        "provider_status",
        "uses_external_model",
        "model_id",
        "model_config",
        "prompt_version",
        "scoring_version",
        "context_hash",
        "scoring_fixture_ref",
        "output_classification",
    ]
    missing = [field for field in required_fields if field not in boundary or boundary[field] in ("", {}, [])]
    passed = (
        not missing
        and boundary.get("model_provider") == "deepseek"
        and boundary.get("provider_status") == "fixture_scoring"
        and boundary.get("uses_external_model") is False
        and boundary.get("model_config", {}).get("network_calls_allowed") is False
        and boundary.get("model_config", {}).get("api_key_ref") == "secret_ref:deepseek_api_key_v1"
        and boundary.get("output_classification") == "model_inference_not_ground_truth"
        and scoring.get("no_real_model_call") is True
        and scoring.get("provider_status", {}).get("provider_called") is False
    )
    return _check(
        passed,
        "DeepSeek is configured for fixture scoring only and is not called by fixture replay.",
        {
            "missing_fields": missing,
            "configured_model_provider": boundary.get("model_provider"),
            "provider_status": boundary.get("provider_status"),
            "uses_external_model": boundary.get("uses_external_model"),
            "api_key_ref": boundary.get("model_config", {}).get("api_key_ref"),
            "scoring_fixture_model_provider": scoring.get("model_provider"),
            "provider_called": scoring.get("provider_status", {}).get("provider_called"),
            "fallback_used": scoring.get("provider_status", {}).get("fallback_used"),
        },
    )


def _check_mcp_vps_boundary(config: dict[str, Any]) -> dict[str, Any]:
    boundary = config.get("mcp_vps_boundary", {})
    passed = (
        boundary.get("mcp_server_status") == "planned_not_running"
        and boundary.get("vps_stage") == "not_deployed"
        and boundary.get("deployment_allowed") is False
        and boundary.get("tools_fixture_only") is True
        and bool(boundary.get("source_of_truth"))
    )
    return _check(
        passed,
        "MCP/VPS remains a planned boundary with no server or deployment side effects.",
        {
            "mcp_server_status": boundary.get("mcp_server_status"),
            "vps_stage": boundary.get("vps_stage"),
            "deployment_allowed": boundary.get("deployment_allowed"),
            "tools_fixture_only": boundary.get("tools_fixture_only"),
            "source_of_truth": boundary.get("source_of_truth", []),
        },
    )


def _check_secret_auth_boundary(
    config: dict[str, Any],
    fixtures: dict[str, Any],
    inspect_result: dict[str, Any],
) -> dict[str, Any]:
    findings: dict[str, list[str]] = {}
    config_findings = find_raw_secret_material(config)
    if config_findings:
        findings["config"] = config_findings
    for filename, data in fixtures.items():
        fixture_findings = find_raw_secret_material(_secret_scan_payload(data))
        if fixture_findings:
            findings[f"fixtures/{filename}"] = fixture_findings
    for artifact_path, data in _load_run_artifacts(inspect_result):
        artifact_findings = find_raw_secret_material(_secret_scan_payload(data))
        if artifact_findings:
            findings[artifact_path] = artifact_findings
    boundary = config.get("secret_auth_boundary", {})
    policy_passed = (
        boundary.get("raw_secret_values_allowed") is False
        and boundary.get("secret_refs_only") is True
        and boundary.get("session_state_refs_only") is True
        and boundary.get("raw_cookie_allowed") is False
        and boundary.get("raw_api_key_allowed") is False
        and boundary.get("raw_token_allowed") is False
    )
    return _check(
        policy_passed and not findings,
        "Auth and secret material remains absent; only redacted refs are allowed.",
        {
            "policy": boundary,
            "raw_secret_findings": findings,
        },
    )


def _check_runtime_cli_boundary(inspect_result: dict[str, Any]) -> dict[str, Any]:
    passed = inspect_result.get("status") == "ok"
    return _check(
        passed,
        "Runtime CLI inspect can read latest replay artifacts and required path evidence.",
        {
            "inspect_status": inspect_result.get("status"),
            "run_id": inspect_result.get("run_id"),
            "event_count": inspect_result.get("event_count"),
            "missing_artifacts": inspect_result.get("missing_artifacts", []),
        },
    )


def _build_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Pre-Real-Source Readiness Report",
        "",
        f"Status: {report['status']}",
        f"Fixture ready: {str(report['fixture_ready']).lower()}",
        f"Real source smoke ready: {str(report['real_source_smoke_ready']).lower()}",
        f"Real-source smoke candidate ready: {str(report['real_source_smoke_candidate_ready']).lower()}",
        f"VPS runner plan ready: {str(report['vps_runner_plan_ready']).lower()}",
        f"Real source smoke not executed: {str(report['real_source_smoke_not_executed']).lower()}",
        f"Production connector ready: {str(report['production_connector_ready']).lower()}",
        f"Recommended first smoke source type: {report['decision']['preferred_first_source_type']}",
        "Recommended next smoke candidate: "
        + f"{report['decision']['recommended_next_smoke_candidate'].get('source_type')} "
        + f"({report['decision']['recommended_next_smoke_candidate'].get('recommendation')})",
        "",
        "## Gate Results",
        "",
        "| Gate | Status | Summary |",
        "| --- | --- | --- |",
    ]
    for name, check in report["checks"].items():
        lines.append(f"| `{name}` | {check['status']} | {check['summary']} |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            report["decision"]["summary"],
            "",
            "Allowed next source types: " + ", ".join(report["decision"]["allowed_next_source_types"]),
            "",
            "Ready user sources: " + (", ".join(report["decision"]["ready_user_sources"]) or "none"),
            "",
            "Blocked user sources: "
            + ", ".join(
                f"{source}={','.join(reasons)}"
                for source, reasons in report["decision"]["blocked_user_sources"].items()
            ),
            "",
            "Blocked sources remain: "
            + ", ".join(f"{source}={reason}" for source, reason in report["decision"]["blocked_sources"].items()),
            "",
            "This report does not prove real source reach, real engagement, legal/ToS eligibility, production readiness, or model quality.",
            "",
        ]
    )
    return "\n".join(lines)


def run_preflight(
    fixtures_dir: Path,
    artifacts_dir: Path,
    out_dir: Path,
    *,
    config_path: Path = DEFAULT_PREFLIGHT_CONFIG,
    schema_path: Path = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    out_dir = safe_output_path(ROOT / "artifacts", out_dir)
    config = load_preflight_config(config_path)
    schema, fixtures = load_fixture_set(fixtures_dir, schema_path)
    validation_issues = validate_fixture_dir(fixtures_dir, schema_path)
    inspect_result = inspect_replay(artifacts_dir)

    config_issues = check_preflight_config(config, GUARDRAILS)
    expected_fixture_count = len(schema.get("expected_fixtures", {}))
    observed_fixture_count = sum(1 for filename in schema.get("expected_fixtures", {}) if (fixtures_dir / filename).exists())

    checks: dict[str, dict[str, Any]] = {}
    checks["fixture_count"] = _check(
        observed_fixture_count == expected_fixture_count and not validation_issues,
        "All expected fixtures are present and schema validation passed.",
        {
            "expected": expected_fixture_count,
            "observed": observed_fixture_count,
            "validation_issue_count": len(validation_issues),
            "validation_issues": [issue.__dict__ for issue in validation_issues],
        },
    )
    configured_guardrails = config.get("required_guardrails", [])
    checks["guardrails"] = _check(
        not config_issues and set(GUARDRAILS).issubset(set(configured_guardrails)),
        "Validator guardrails are present and required by preflight config.",
        {
            "required": GUARDRAILS,
            "configured": configured_guardrails,
            "config_issues": config_issues,
        },
    )
    missing_events = inspect_result.get("required_events_missing", sorted(REQUIRED_PATH_EVENTS))
    checks["required_event_types"] = _check(
        inspect_result.get("status") == "ok" and not missing_events,
        "Replay event log contains source readiness, smoke, wrapper, candidate, prediction, outcome, eval, and promotion path events.",
        {
            "required": sorted(REQUIRED_PATH_EVENTS),
            "present": inspect_result.get("event_types_present", []),
            "missing": missing_events,
        },
    )
    checks["deterministic_replay_hash"] = _check(
        inspect_result.get("replay_status") == "deterministic_match" and bool(inspect_result.get("scoring_path_hash")),
        "Latest replay is deterministic against the stored scoring path hash.",
        {
            "replay_status": inspect_result.get("replay_status"),
            "scoring_path_hash": inspect_result.get("scoring_path_hash"),
            "previous_scoring_path_hash": inspect_result.get("previous_scoring_path_hash"),
        },
    )
    connector_passed, connector_evidence = check_connector_runtime_boundary(config)
    checks["connector_runtime_boundary"] = _check(
        connector_passed,
        "Real connectors, upstream tools, network fetches, and browser automation are disabled.",
        connector_evidence,
    )
    checks["connector_readiness_gate"] = _check_source_readiness(fixtures)
    checks["source_smoke_gate"] = _check_source_smoke(fixtures)
    checks["source_pool_intake"] = _check_source_pool_intake(fixtures)
    checks["vps_auth_gated_runner"] = _check_vps_auth_gated_runner(fixtures)
    checks["all_source_deepseek_runner"] = _check_all_source_deepseek_runner(fixtures, inspect_result)
    checks["rolling_timeline_runtime"] = _check_rolling_timeline_runtime(fixtures, inspect_result)
    checks["radar_timeline_feed"] = _check_radar_timeline(fixtures, inspect_result)
    checks["promotion_gate"] = _check_promotion_gate(fixtures)
    checks["deepseek_provider_boundary"] = _check_deepseek_boundary(config, fixtures)
    checks["mcp_vps_boundary"] = _check_mcp_vps_boundary(config)
    checks["secret_auth_redaction_boundary"] = _check_secret_auth_boundary(config, fixtures, inspect_result)
    checks["runtime_cli_boundary"] = _check_runtime_cli_boundary(inspect_result)

    checks_before_decision_passed = all(check["status"] == "pass" for check in checks.values())
    policy = config.get("real_source_smoke_policy", {})
    source_pool_evidence = checks["source_pool_intake"]["evidence"]
    recommended_next_smoke_candidate = source_pool_evidence.get("recommended_next_smoke_candidate", {})
    recommended_source_type = recommended_next_smoke_candidate.get("source_type") or policy.get("preferred_first_source_type")
    allowed_next_source_types = policy.get("allowed_next_source_types", [])
    blocked_sources = policy.get("blocked_sources", {})
    blocked_user_sources = source_pool_evidence.get("blocked_user_sources", {})
    ready_user_sources = source_pool_evidence.get("ready_user_sources", [])
    vps_evidence = checks["vps_auth_gated_runner"]["evidence"]
    vps_runner_plan_ready = vps_evidence.get("vps_runner_plan_ready") is True
    real_source_smoke_not_executed = vps_evidence.get("real_source_smoke_not_executed") is True
    decision_passed = (
        checks_before_decision_passed
        and set(allowed_next_source_types).issubset(LOW_RISK_SOURCE_TYPES)
        and recommended_source_type in LOW_RISK_SOURCE_TYPES
        and ready_user_sources == []
        and {"xueqiu", "x"}.issubset(set(blocked_user_sources))
        and BLOCKED_REAL_SOURCES.issubset(set(blocked_sources))
        and policy.get("production_readiness_claimed") is False
        and policy.get("legal_tos_eligibility_claimed") is False
        and policy.get("real_source_reach_claimed") is False
        and policy.get("real_engagement_claimed") is False
        and vps_runner_plan_ready
        and real_source_smoke_not_executed
        and vps_evidence.get("production_connector_ready") is False
    )
    checks["real_source_ready_decision"] = _check(
        decision_passed,
        "Only low-risk read-only smoke may proceed next; production and legal/ToS readiness are not claimed.",
        {
            "allowed_next_source_types": allowed_next_source_types,
            "preferred_first_source_type": policy.get("preferred_first_source_type"),
            "recommended_next_smoke_candidate": recommended_next_smoke_candidate,
            "ready_user_sources": ready_user_sources,
            "blocked_user_sources": blocked_user_sources,
            "blocked_sources": blocked_sources,
            "production_readiness_claimed": policy.get("production_readiness_claimed"),
            "legal_tos_eligibility_claimed": policy.get("legal_tos_eligibility_claimed"),
            "real_source_reach_claimed": policy.get("real_source_reach_claimed"),
            "real_engagement_claimed": policy.get("real_engagement_claimed"),
            "vps_runner_plan_ready": vps_runner_plan_ready,
            "real_source_smoke_not_executed": real_source_smoke_not_executed,
            "vps_production_connector_ready": vps_evidence.get("production_connector_ready"),
        },
    )

    real_source_smoke_ready = checks["real_source_ready_decision"]["status"] == "pass"
    fixture_ready = checks_before_decision_passed
    real_source_smoke_candidate_ready = real_source_smoke_ready
    failed_checks = [name for name, check in checks.items() if check["status"] != "pass"]
    report = {
        "object_type": "PreRealSourceReadinessReport",
        "report_version": REPORT_VERSION,
        "status": "passed" if real_source_smoke_ready else "failed",
        "fixture_ready": fixture_ready,
        "real_source_smoke_ready": real_source_smoke_ready,
        "real_source_smoke_candidate_ready": real_source_smoke_candidate_ready,
        "vps_runner_plan_ready": vps_runner_plan_ready,
        "real_source_smoke_not_executed": real_source_smoke_not_executed,
        "production_ready": False,
        "production_connector_ready": False,
        "legal_tos_eligibility_proven": False,
        "real_source_reach_proven": False,
        "real_engagement_proven": False,
        "inputs": {
            "fixtures_dir": str(fixtures_dir),
            "artifacts_dir": str(artifacts_dir),
            "out_dir": str(out_dir),
            "schema_path": str(schema_path),
            "config_path": str(config_path),
        },
        "summary": {
            "check_count": len(checks),
            "passed_check_count": len(checks) - len(failed_checks),
            "failed_check_count": len(failed_checks),
            "failed_checks": failed_checks,
        },
        "decision": {
            "real_source_smoke_ready": real_source_smoke_ready,
            "fixture_ready": fixture_ready,
            "real_source_smoke_candidate_ready": real_source_smoke_candidate_ready,
            "vps_runner_plan_ready": vps_runner_plan_ready,
            "real_source_smoke_not_executed": real_source_smoke_not_executed,
            "production_connector_ready": False,
            "preferred_first_source_type": policy.get("preferred_first_source_type"),
            "recommended_next_smoke_candidate": recommended_next_smoke_candidate,
            "allowed_next_source_types": allowed_next_source_types,
            "ready_user_sources": ready_user_sources,
            "blocked_user_sources": blocked_user_sources,
            "blocked_sources": blocked_sources,
            "summary": (
                "Fixture harness is ready; product work should consume the radar timeline feed first, while registered Xueqiu and X list entrances remain blocked for real access."
                if real_source_smoke_ready
                else "Not ready for real-source smoke; failed checks must be fixed first."
            ),
        },
        "checks": checks,
    }

    json_path = out_dir / "readiness_report.json"
    md_path = out_dir / "readiness_report.md"
    _write_json(json_path, report)
    _write_text(md_path, _build_markdown(report))
    report["outputs"] = {
        "json_report": str(json_path),
        "markdown_report": str(md_path),
    }
    _write_json(json_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run News Harness pre-real-source readiness preflight.")
    parser.add_argument("--fixtures", type=Path, required=True, help="Fixture directory")
    parser.add_argument("--artifacts", type=Path, required=True, help="Replay artifacts directory")
    parser.add_argument("--out", type=Path, required=True, help="Preflight output directory")
    parser.add_argument("--config", type=Path, default=DEFAULT_PREFLIGHT_CONFIG, help="Preflight config")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema contract file")
    args = parser.parse_args(argv)

    report = run_preflight(
        args.fixtures,
        args.artifacts,
        args.out,
        config_path=args.config,
        schema_path=args.schema,
    )
    print(
        canonical_json(
            {
                "status": report["status"],
                "real_source_smoke_ready": report["real_source_smoke_ready"],
                "real_source_smoke_candidate_ready": report["real_source_smoke_candidate_ready"],
                "recommended_next_smoke_candidate": report["decision"]["recommended_next_smoke_candidate"],
                "blocked_user_sources": report["decision"]["blocked_user_sources"],
                "vps_runner_plan_ready": report["decision"]["vps_runner_plan_ready"],
                "real_source_smoke_not_executed": report["decision"]["real_source_smoke_not_executed"],
                "json_report": report["outputs"]["json_report"],
                "markdown_report": report["outputs"]["markdown_report"],
                "failed_checks": report["summary"]["failed_checks"],
            }
        )
    )
    return 0 if report["real_source_smoke_ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
