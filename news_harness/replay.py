#!/usr/bin/env python3
"""Replay the fixture-first News Harness MVP locally.

The runner performs no network access and calls no model APIs. It validates the
fixtures, emits an append-only JSONL event log, writes replay artifacts, and
compares a deterministic scoring-path hash against the previous run in the same
output directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


from .constants import EVENT_TYPES, SMOKE_RESULT_FILES
from .events import canonical_json as _canonical_json, make_event as _event, sha256_json as _sha256_json
from .fixtures import DEFAULT_SCHEMA, ROOT, load_fixture_set
from .paths import safe_output_path, write_json_artifact, write_text_artifact
from .timeline import build_timeline_feed
from .validator import validate_fixture_dir


def _load(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, data: Any) -> None:
    write_json_artifact(path, data)


def _next_run_index(runs_dir: Path) -> int:
    runs_dir.mkdir(parents=True, exist_ok=True)
    indexes: list[int] = []
    for path in runs_dir.glob("run_*"):
        if path.is_dir():
            suffix = path.name.removeprefix("run_")
            if suffix.isdigit():
                indexes.append(int(suffix))
    return max(indexes, default=0) + 1


def build_scoring_path(fixtures: dict[str, Any]) -> dict[str, Any]:
    source = fixtures["sample_source.json"]
    source_registry = fixtures["sample_source_registry.json"]
    source_score = fixtures["sample_source_score.json"]
    readiness_matrix = fixtures["sample_connector_readiness_matrix.json"]
    smoke_matrix = fixtures["sample_source_smoke_matrix.json"]
    connector = fixtures["sample_connector_result.json"]
    candidate = fixtures["sample_candidate.json"]
    structure = fixtures["sample_structure.json"]
    prediction = fixtures["sample_prediction.json"]
    outcome = fixtures["sample_outcome.json"]
    evaluation = fixtures["sample_eval.json"]
    proposal = fixtures["sample_improvement_proposal.json"]
    promotion = fixtures["sample_promotion_decision.json"]
    replay = fixtures["sample_replay_manifest.json"]
    vps_runner = fixtures["sample_vps_source_runner_plan.json"]
    x_list_smoke = fixtures["sample_x_list_auth_gated_smoke_plan.json"]
    xueqiu_smoke = fixtures["sample_xueqiu_browser_assisted_smoke_plan.json"]
    all_source_runner = fixtures["sample_all_source_runner_dry_run.json"]
    deepseek_scoring = fixtures["sample_deepseek_scoring_fixture.json"]
    shadow_source = fixtures["sample_shadow_source_fetch_result.json"]
    rolling_schedule = fixtures["rolling_source_schedule.json"]
    timeline_store = fixtures["timeline_store.json"]
    revisit_schedule = fixtures["revisit_schedule.json"]
    timeline = build_timeline_feed(fixtures)

    readiness_decisions = {
        decision["source_entrance_id"]: {
            "registry_status": decision["registry_status"],
            "production_eligible": decision["production_eligible"],
            "candidate_discovery_allowed": decision["candidate_discovery_allowed"],
            "candidate_discovery_decision": decision["candidate_discovery_decision"],
            "failure_codes": decision["failure_codes"],
        }
        for decision in readiness_matrix["decisions"]
    }
    source_scores = {
        score["source_entrance_id"]: {
            "source_score": score["source_score"],
            "watch_priority": score["watch_priority"],
            "scan_frequency": score["scan_frequency"],
            "budget_class": score["budget_class"],
            "can_override_readiness_gate": score["can_override_readiness_gate"],
        }
        for score in source_score["scores"]
    }
    source_smoke_decisions = {
        decision["source_entrance_id"]: {
            "smoke_decision_id": decision["smoke_decision_id"],
            "source": decision["source"],
            "source_type": decision["source_type"],
            "source_url": decision["source_url"],
            "smoke_allowed": decision["smoke_allowed"],
            "diagnostic_success": decision["diagnostic_success"],
            "source_reach_success": decision["source_reach_success"],
            "failure_codes": decision["failure_codes"],
            "smoke_result_ref": decision["smoke_result_ref"],
        }
        for decision in smoke_matrix["decisions"]
    }
    source_smoke_results = {
        fixtures[filename]["smoke_result_id"]: {
            "source": fixtures[filename]["source"],
            "source_entrance_id": fixtures[filename]["source_entrance_id"],
            "smoke_status": fixtures[filename]["smoke_status"],
            "smoke_allowed": fixtures[filename]["smoke_allowed"],
            "diagnostic_success": fixtures[filename]["diagnostic_success"],
            "source_reach_success": fixtures[filename]["source_reach_success"],
            "real_engagement_status": fixtures[filename]["real_engagement"]["status"],
            "structured_error_code": (
                fixtures[filename]["structured_error"]["code"]
                if fixtures[filename]["structured_error"] is not None
                else None
            ),
        }
        for filename in SMOKE_RESULT_FILES
    }

    return {
        "harness_version": prediction["harness_version"],
        "strategy_version": prediction["strategy_version"],
        "source_registry_version": source_registry["registry_version"],
        "source_score_version": source_score["score_version"],
        "connector_readiness_matrix_version": readiness_matrix["matrix_version"],
        "source_smoke_matrix_version": smoke_matrix["smoke_matrix_version"],
        "source_readiness_decisions": readiness_decisions,
        "source_scores": source_scores,
        "source_smoke_decisions": source_smoke_decisions,
        "source_smoke_results": source_smoke_results,
        "vps_auth_gated_runner": {
            "runner_plan_id": vps_runner["runner_plan_id"],
            "plan_version": vps_runner["plan_version"],
            "fixture_ready": vps_runner["readiness_summary"]["fixture_ready"],
            "vps_runner_plan_ready": vps_runner["readiness_summary"]["vps_runner_plan_ready"],
            "real_source_smoke_not_executed": vps_runner["readiness_summary"]["real_source_smoke_not_executed"],
            "production_connector_ready": vps_runner["readiness_summary"]["production_connector_ready"],
            "x_list_runner_mode": x_list_smoke["runner_mode"],
            "x_list_blocked_reason": x_list_smoke["readiness"]["blocked_reason"],
            "xueqiu_browser_assisted_required": xueqiu_smoke["browser_assisted_boundary"]["browser_assisted_required"],
            "xueqiu_vps_runner_plan_ready": xueqiu_smoke["readiness"]["vps_runner_plan_ready"],
        },
        "all_source_runner": {
            "runner_run_id": all_source_runner["runner_run_id"],
            "runner_version": all_source_runner["runner_version"],
            "fixture_only": all_source_runner["fixture_only"],
            "no_real_source_access": all_source_runner["no_real_source_access"],
            "real_source_smoke_executed": all_source_runner["real_source_smoke_executed"],
            "production_connector_ready": all_source_runner["production_connector_ready"],
            "sources": sorted({status["source"] for status in all_source_runner["source_statuses"]}),
            "observation_sources": sorted({observation["source"] for observation in all_source_runner["observations"]}),
            "observation_count": len(all_source_runner["observations"]),
            "structured_error_count": len(all_source_runner["structured_errors"]),
            "max_items_per_source_per_run": all_source_runner["batch_policy"]["max_items_per_source_per_run"],
            "reddit_max_items_per_subreddit_per_run": all_source_runner["batch_policy"]["reddit_max_items_per_subreddit_per_run"],
            "raw_cookie_detected": all_source_runner["secret_boundary"]["raw_cookie_detected"],
            "raw_token_detected": all_source_runner["secret_boundary"]["raw_token_detected"],
            "raw_api_key_detected": all_source_runner["secret_boundary"]["raw_api_key_detected"],
        },
        "deepseek_scoring": {
            "scoring_run_id": deepseek_scoring["scoring_run_id"],
            "model_provider": deepseek_scoring["model_provider"],
            "model_id": deepseek_scoring["model_id"],
            "prompt_version": deepseek_scoring["prompt_version"],
            "scoring_version": deepseek_scoring["scoring_version"],
            "llm_mode": deepseek_scoring["llm_mode"],
            "no_real_model_call": deepseek_scoring["no_real_model_call"],
            "api_key_present": deepseek_scoring["provider_status"]["api_key_present"],
            "provider_called": deepseek_scoring["provider_status"]["provider_called"],
            "fallback_used": deepseek_scoring["provider_status"]["fallback_used"],
            "scored_candidate_count": len(deepseek_scoring["scored_candidates"]),
            "hotness_scores": [candidate["hotness_score"] for candidate in deepseek_scoring["scored_candidates"]],
            "risk_flags": sorted({flag for candidate in deepseek_scoring["scored_candidates"] for flag in candidate["risk_flags"]}),
            "output_hashes": [candidate["output_hash"] for candidate in deepseek_scoring["scored_candidates"]],
            "llm_output_is_ground_truth": deepseek_scoring["ground_truth_policy"]["llm_output_is_ground_truth"],
        },
        "radar_timeline": {
            "feed_id": timeline["feed_id"],
            "feed_version": timeline["feed_version"],
            "item_count": len(timeline["items"]),
            "image_statuses": sorted({item["image_status"] for item in timeline["items"]}),
            "sorted_item_ids": [item["id"] for item in timeline["items"]],
            "hotness_scores": [item["hotness_score"] for item in timeline["items"]],
            "shadow_source_item_ids": [
                item["shadow_source_item_id"]
                for item in timeline["items"]
                if "shadow_source_item_id" in item
            ],
            "fixture_only": timeline["fixture_only"],
            "no_real_source_access": timeline["no_real_source_access"],
            "rolling_runtime": timeline.get("rolling_runtime", {}),
            "view_config": timeline.get("view_config", {}),
            "auto_refresh": timeline.get("auto_refresh", {}),
        },
        "rolling_timeline_runtime": {
            "schedule_id": rolling_schedule["schedule_id"],
            "store_id": timeline_store["store_id"],
            "revisit_schedule_id": revisit_schedule["schedule_id"],
            "source_refresh_intervals": {
                source["source"]: source["refresh_interval_seconds"]
                for source in rolling_schedule["sources"]
            },
            "reddit_subreddit_count": len(next(source for source in rolling_schedule["sources"] if source["source"] == "reddit")["subreddits"]),
            "reddit_max_items_per_subreddit": next(source for source in rolling_schedule["sources"] if source["source"] == "reddit")["max_items_per_subreddit_per_run"],
            "max_items_per_source_per_run": rolling_schedule["batch_policy"]["max_items_per_source_per_run"],
            "duplicate_update_count": len(timeline_store["duplicate_update_log"]),
            "active_item_ids": [
                item["id"]
                for item in timeline_store["items"]
                if item.get("expired") is not True
            ],
            "expired_item_ids": [
                item["id"]
                for item in timeline_store["items"]
                if item.get("expired") is True
            ],
            "revisit_task_count": len(revisit_schedule["tasks"]),
            "revisit_windows": sorted(set(revisit_schedule["task_policy"]["revisit_windows_hours"])),
        },
        "shadow_source_fetch_result": {
            "shadow_result_id": shadow_source["shadow_result_id"],
            "result_version": shadow_source["result_version"],
            "item_count": len(shadow_source["items"]),
            "sources": sorted({item["source"] for item in shadow_source["items"]}),
            "source_sections": sorted({item["source_section"] for item in shadow_source["items"]}),
            "fixture_only": shadow_source["fixture_only"],
            "no_real_source_access": shadow_source["no_real_source_access"],
            "real_source_smoke_executed": shadow_source["real_source_smoke_executed"],
            "production_connector_ready": shadow_source["production_connector_ready"],
        },
        "source_entrance_id": source["source_entrance_id"],
        "source_status": source["status"],
        "connector_status": connector["status"],
        "evidence_id": fixtures["sample_evidence.json"]["evidence_id"],
        "candidate_id": candidate["candidate_id"],
        "candidate_score": candidate["candidate_score"],
        "eligibility_status": candidate["eligibility_status"],
        "structure_analysis_id": structure["structure_analysis_id"],
        "structure_score": structure["structure_score"],
        "prediction_id": prediction["prediction_id"],
        "12h_score": prediction["12h_score"],
        "24h_score": prediction["24h_score"],
        "confidence": prediction["confidence"],
        "uncertainty": prediction["uncertainty"],
        "outcome_id": outcome["outcome_id"],
        "normalized_growth": outcome["normalized_growth"],
        "eval_result_id": evaluation["eval_result_id"],
        "eval_verdict": evaluation["verdict"],
        "proposal_id": proposal["proposal_id"],
        "proposal_status": proposal["status"],
        "promotion_decision_id": promotion["promotion_decision_id"],
        "promotion_decision": promotion["decision"],
        "replay_manifest_id": replay["replay_manifest_id"],
        "replay_mode": replay["replay_mode"],
    }


def build_events(fixtures: dict[str, Any], run_id: str, run_dir: Path) -> list[dict[str, Any]]:
    config = fixtures["sample_run_config.json"]
    source_registry = fixtures["sample_source_registry.json"]
    source_score = fixtures["sample_source_score.json"]
    readiness_matrix = fixtures["sample_connector_readiness_matrix.json"]
    smoke_matrix = fixtures["sample_source_smoke_matrix.json"]
    vps_runner = fixtures["sample_vps_source_runner_plan.json"]
    all_source_runner = fixtures["sample_all_source_runner_dry_run.json"]
    deepseek_scoring = fixtures["sample_deepseek_scoring_fixture.json"]
    rolling_schedule = fixtures["rolling_source_schedule.json"]
    timeline_store = fixtures["timeline_store.json"]
    revisit_schedule = fixtures["revisit_schedule.json"]
    connector = fixtures["sample_connector_result.json"]
    candidate = fixtures["sample_candidate.json"]
    structure = fixtures["sample_structure.json"]
    prediction = fixtures["sample_prediction.json"]
    outcome = fixtures["sample_outcome.json"]
    evaluation = fixtures["sample_eval.json"]
    promotion = fixtures["sample_promotion_decision.json"]
    timeline = build_timeline_feed(fixtures)

    strategy = config["strategy_version"]
    harness = config["harness_version"]
    schema = config["event_schema_version"]
    connector_event = "tool.completed" if connector["status"] == "completed" else "tool.failed"
    connector_error = None
    if connector["status"] == "failed" and connector.get("structured_error"):
        connector_error = connector["structured_error"].get("code")
    current_source_id = config["source_entrance_id"]
    current_decision = next(
        decision for decision in readiness_matrix["decisions"] if decision["source_entrance_id"] == current_source_id
    )
    allowed_smoke_decision = next(decision for decision in smoke_matrix["decisions"] if decision["smoke_allowed"] is True)
    event_specs = [
        ("run.started", config["source_entrance_id"], "2026-06-14T00:00:00Z", None, "fixtures/sample_run_config.json", str(run_dir / "run_manifest.json"), None, str(run_dir / "run_manifest.json")),
        ("source_registry.loaded", source_registry["source_registry_set_id"], "2026-06-14T00:00:01Z", "fixture.source_registry.v1", "fixtures/sample_source_registry.json", "fixtures/sample_source_registry.json", None, "fixtures/sample_source_registry.json"),
        ("source_score.computed", source_score["source_score_set_id"], "2026-06-14T00:00:02Z", "fixture.source_scorer.v1", "fixtures/sample_source_registry.json", "fixtures/sample_source_score.json", None, "fixtures/sample_source_score.json"),
        ("connector_readiness.decided", readiness_matrix["matrix_id"], "2026-06-14T00:00:03Z", "fixture.connector_readiness_gate.v1", "fixtures/sample_source_score.json", "fixtures/sample_connector_readiness_matrix.json", None, "fixtures/sample_connector_readiness_matrix.json"),
        ("source_smoke_matrix.loaded", smoke_matrix["smoke_matrix_id"], "2026-06-14T00:00:04Z", "fixture.source_smoke_planner.v1", "fixtures/sample_connector_readiness_matrix.json", "fixtures/sample_source_smoke_matrix.json", None, "fixtures/sample_source_smoke_matrix.json"),
        ("source_smoke.decided", smoke_matrix["smoke_matrix_id"], "2026-06-14T00:00:05Z", "fixture.source_smoke_planner.v1", "fixtures/sample_source_smoke_matrix.json", "fixtures/sample_source_smoke_matrix.json", None, "fixtures/sample_source_smoke_matrix.json"),
        ("vps_runner_plan.loaded", vps_runner["runner_plan_id"], "2026-06-14T00:00:06Z", "fixture.vps_auth_gated_runner_planner.v1", "fixtures/sample_vps_source_runner_plan.json", "fixtures/sample_vps_source_runner_plan.json", None, "fixtures/sample_vps_source_runner_plan.json"),
        ("vps_runner_plan.decided", vps_runner["runner_plan_id"], "2026-06-14T00:00:07Z", "fixture.vps_auth_gated_runner_planner.v1", "fixtures/sample_vps_source_runner_plan.json", "fixtures/sample_x_list_auth_gated_smoke_plan.json", "connector_not_ready", "fixtures/sample_vps_source_runner_plan.json#readiness_summary"),
        ("all_source_runner.completed", all_source_runner["runner_run_id"], "2026-06-14T00:00:10Z", "fixture.all_source_runner.v1", "configs/all_source_runner.example.json", "fixtures/sample_all_source_runner_dry_run.json", None, "fixtures/sample_all_source_runner_dry_run.json"),
        ("deepseek_scoring.completed", deepseek_scoring["scoring_run_id"], "2026-06-14T00:00:11Z", "fixture.deepseek_scoring.v1", "fixtures/sample_all_source_runner_dry_run.json#observations", "fixtures/sample_deepseek_scoring_fixture.json", None, "fixtures/sample_deepseek_scoring_fixture.json"),
        ("rolling_source_schedule.loaded", rolling_schedule["schedule_id"], "2026-06-14T00:00:12Z", "fixture.rolling_source_scheduler.v1", "fixtures/rolling_source_schedule.json", "fixtures/rolling_source_schedule.json", None, "fixtures/rolling_source_schedule.json"),
        ("shadow_batch.completed", "shadow_batch_fixture_rolling", "2026-06-14T00:00:13Z", "fixture.shadow_batch_runner.v1", "fixtures/sample_shadow_source_fetch_result.json,fixtures/sample_all_source_runner_dry_run.json", "fixtures/timeline_store.json#shadow_batch_runs", None, "fixtures/timeline_store.json#shadow_batch_runs"),
        ("timeline_store.updated", timeline_store["store_id"], "2026-06-14T00:00:14Z", "fixture.rolling_timeline_store.v1", "fixtures/sample_shadow_source_fetch_result.json,fixtures/sample_deepseek_scoring_fixture.json", "fixtures/timeline_store.json", None, "fixtures/timeline_store.json"),
        ("revisit_schedule.registered", revisit_schedule["schedule_id"], "2026-06-14T00:00:15Z", "fixture.revisit_scheduler.v1", "fixtures/timeline_store.json", "fixtures/revisit_schedule.json", None, "fixtures/revisit_schedule.json"),
        ("source_smoke.allowed", allowed_smoke_decision["source_entrance_id"], "2026-06-14T00:00:16Z", "fixture.source_smoke_planner.v1", "fixtures/sample_source_smoke_matrix.json#decisions[0]", allowed_smoke_decision["smoke_result_ref"], None, allowed_smoke_decision["smoke_result_ref"]),
        ("source_smoke.blocked", "blocked_smoke_sources_fixture", "2026-06-14T00:00:17Z", "fixture.source_smoke_planner.v1", "fixtures/sample_source_smoke_matrix.json", "fixtures/sample_source_smoke_matrix.json#blocked", "smoke_readiness_bypass", "fixtures/sample_source_smoke_matrix.json"),
        ("candidate_discovery.allowed", current_source_id, "2026-06-14T00:00:18Z", "fixture.connector_readiness_gate.v1", "fixtures/sample_connector_readiness_matrix.json", "fixtures/sample_connector_result.json", None, "fixtures/sample_connector_readiness_matrix.json#decisions[0]"),
        ("candidate_discovery.blocked", "blocked_sources_fixture", "2026-06-14T00:00:19Z", "fixture.connector_readiness_gate.v1", "fixtures/sample_connector_readiness_matrix.json", "fixtures/sample_connector_readiness_matrix.json#blocked", "candidate_discovery_blocked", "fixtures/sample_connector_readiness_matrix.json"),
        ("item.started", config["source_entrance_id"], "2026-06-14T00:00:20Z", None, "fixtures/sample_source.json", "fixtures/sample_connector_result.json", None, "fixtures/sample_source.json"),
        ("tool.requested", config["source_entrance_id"], "2026-06-14T00:00:21Z", connector["tool_id"], connector["input_ref"], connector["output_ref"], None, "fixtures/sample_connector_result.json"),
        (connector_event, config["source_entrance_id"], "2026-06-14T00:00:22Z", connector["tool_id"], connector["input_ref"], connector["output_ref"], connector_error, "fixtures/sample_connector_result.json"),
        ("observation.recorded", "evidence_fixture_001", "2026-06-14T00:00:23Z", connector["tool_id"], "fixtures/sample_connector_result.json", "fixtures/sample_evidence.json", None, "fixtures/sample_evidence.json"),
        ("candidate.filtered", candidate["candidate_id"], "2026-06-14T00:00:27Z", None, "fixtures/sample_evidence.json", "fixtures/sample_candidate.json", None, "fixtures/sample_candidate.json"),
        ("structure.completed", structure["candidate_id"], "2026-06-14T00:00:28Z", "fixture.structure_analyzer.v1", "fixtures/sample_candidate.json", "fixtures/sample_structure.json", None, "fixtures/sample_structure.json"),
        ("prediction.completed", prediction["candidate_id"], "2026-06-14T00:00:29Z", "fixture.predictor.v1", "fixtures/sample_structure.json", "fixtures/sample_prediction.json", None, "fixtures/sample_prediction.json"),
        ("outcome.collected", outcome["candidate_id"], "2026-06-15T00:15:00Z", "fixture.outcome_collector.v1", "fixtures/sample_prediction.json", "fixtures/sample_outcome.json", None, "fixtures/sample_outcome.json"),
        ("eval.completed", evaluation["prediction_id"], "2026-06-15T00:16:00Z", "fixture.evaluator.v1", "fixtures/sample_outcome.json", "fixtures/sample_eval.json", None, "fixtures/sample_eval.json"),
        ("timeline.generated", timeline["feed_id"], "2026-06-15T00:18:00Z", "fixture.timeline_projector.v1", "fixtures/sample_evidence.json,fixtures/sample_candidate.json,fixtures/sample_structure.json,fixtures/sample_prediction.json,fixtures/sample_outcome.json", str(run_dir / "timeline_feed.json"), None, "fixtures/sample_radar_timeline_feed.json"),
        ("promotion.decided", promotion["proposal_id"], "2026-06-15T00:20:00Z", "fixture.promotion_gate.v1", "fixtures/sample_improvement_proposal.json", "fixtures/sample_promotion_decision.json", None, "fixtures/sample_promotion_decision.json"),
        ("run.completed", config["source_entrance_id"], "2026-06-15T00:20:01Z", None, "fixtures/sample_promotion_decision.json", str(run_dir / "replay_manifest.json"), None, str(run_dir / "replay_manifest.json")),
    ]

    return [
        _event(
            event_type=event_type,
            ordinal=index,
            run_id=run_id,
            item_id=item_id,
            timestamp=timestamp,
            tool_id=tool_id,
            strategy_version=strategy,
            harness_version=harness,
            input_ref=input_ref,
            output_ref=output_ref,
            error_code=error_code,
            payload_ref=payload_ref,
            event_schema_version=schema,
        )
        for index, (event_type, item_id, timestamp, tool_id, input_ref, output_ref, error_code, payload_ref) in enumerate(event_specs, start=1)
    ]


def append_event_logs(out_dir: Path, run_dir: Path, events: list[dict[str, Any]]) -> None:
    run_log = run_dir / "event_log.jsonl"
    aggregate_log = out_dir / "event_log.jsonl"
    for path in (run_log, aggregate_log):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for event in events:
                handle.write(_canonical_json(event))
                handle.write("\n")


def _previous_hash(out_dir: Path) -> str | None:
    path = out_dir / "last_scoring_path_hash.txt"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8").strip() or None


def _write_previous_hash(out_dir: Path, value: str) -> None:
    write_text_artifact(out_dir / "last_scoring_path_hash.txt", value + "\n")


def replay(fixtures_dir: Path, out_dir: Path, schema_path: Path = DEFAULT_SCHEMA) -> dict[str, Any]:
    out_dir = safe_output_path(ROOT / "artifacts", out_dir)
    issues = validate_fixture_dir(fixtures_dir, schema_path)
    if issues:
        return {
            "status": "failed",
            "issues": [issue.__dict__ for issue in issues],
        }

    schema, fixtures = load_fixture_set(fixtures_dir, schema_path)
    runs_dir = out_dir / "runs"
    run_index = _next_run_index(runs_dir)
    run_id = f"run_{run_index:03d}"
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    scoring_path = build_scoring_path(fixtures)
    timeline_feed = build_timeline_feed(fixtures)
    scoring_path_hash = _sha256_json(scoring_path)
    prior_hash = _previous_hash(out_dir)
    if fixtures["sample_replay_manifest.json"].get("replay_mode") == "replay_unverifiable":
        replay_status = "replay_unverifiable"
    elif prior_hash is None:
        replay_status = "first_run_no_prior"
    elif prior_hash == scoring_path_hash:
        replay_status = "deterministic_match"
    else:
        replay_status = "replay_mismatch"

    events = build_events(fixtures, run_id, run_dir)
    append_event_logs(out_dir, run_dir, events)

    run_manifest = {
        "run_id": run_id,
        "run_index": run_index,
        "run_type": "fixture_replay",
        "fixtures_dir": str(fixtures_dir),
        "schema_version": schema["schema_version"],
        "harness_version": fixtures["sample_run_config.json"]["harness_version"],
        "strategy_version": fixtures["sample_run_config.json"]["strategy_version"],
        "terminal_state": "completed" if replay_status != "replay_mismatch" else "failed",
        "event_log_ref": str(run_dir / "event_log.jsonl"),
    }

    replay_manifest = dict(fixtures["sample_replay_manifest.json"])
    replay_manifest.update(
        {
            "run_id": run_id,
            "schema_version": schema["schema_version"],
            "scoring_path_hash": scoring_path_hash,
            "previous_scoring_path_hash": prior_hash,
            "replay_status": replay_status,
            "event_log_ref": str(run_dir / "event_log.jsonl"),
            "aggregate_event_log_ref": str(out_dir / "event_log.jsonl"),
        }
    )

    replay_report = {
        "run_id": run_id,
        "status": replay_status,
        "scoring_path_hash": scoring_path_hash,
        "previous_scoring_path_hash": prior_hash,
        "required_events_present": sorted({event["event_type"] for event in events}),
    }

    _write_json(run_dir / "run_manifest.json", run_manifest)
    _write_json(run_dir / "scoring_path.json", scoring_path)
    _write_json(run_dir / "source_registry.json", fixtures["sample_source_registry.json"])
    _write_json(run_dir / "source_score.json", fixtures["sample_source_score.json"])
    _write_json(run_dir / "connector_readiness_matrix.json", fixtures["sample_connector_readiness_matrix.json"])
    _write_json(run_dir / "source_smoke_matrix.json", fixtures["sample_source_smoke_matrix.json"])
    _write_json(run_dir / "source_pool_intake.json", fixtures["sample_source_pool_intake.json"])
    _write_json(run_dir / "real_smoke_candidate_plan.json", fixtures["sample_real_smoke_candidate_plan.json"])
    _write_json(run_dir / "vps_source_runner_plan.json", fixtures["sample_vps_source_runner_plan.json"])
    _write_json(run_dir / "x_list_auth_gated_smoke_plan.json", fixtures["sample_x_list_auth_gated_smoke_plan.json"])
    _write_json(run_dir / "xueqiu_browser_assisted_smoke_plan.json", fixtures["sample_xueqiu_browser_assisted_smoke_plan.json"])
    _write_json(run_dir / "all_source_runner_dry_run.json", fixtures["sample_all_source_runner_dry_run.json"])
    _write_json(run_dir / "deepseek_scoring_fixture.json", fixtures["sample_deepseek_scoring_fixture.json"])
    _write_json(run_dir / "shadow_source_fetch_result.json", fixtures["sample_shadow_source_fetch_result.json"])
    _write_json(run_dir / "rolling_source_schedule.json", fixtures["rolling_source_schedule.json"])
    _write_json(run_dir / "timeline_store.json", fixtures["timeline_store.json"])
    _write_json(run_dir / "revisit_schedule.json", fixtures["revisit_schedule.json"])
    _write_json(run_dir / "timeline_feed.json", timeline_feed)
    for filename in SMOKE_RESULT_FILES:
        _write_json(run_dir / filename.removeprefix("sample_"), fixtures[filename])
    _write_json(run_dir / "prediction.json", fixtures["sample_prediction.json"])
    _write_json(run_dir / "eval.json", fixtures["sample_eval.json"])
    _write_json(run_dir / "improvement_proposal.json", fixtures["sample_improvement_proposal.json"])
    _write_json(run_dir / "promotion_decision.json", fixtures["sample_promotion_decision.json"])
    _write_json(run_dir / "replay_manifest.json", replay_manifest)
    _write_json(run_dir / "replay_report.json", replay_report)
    _write_json(out_dir / "latest_manifest.json", replay_manifest)
    _write_json(out_dir / "replay_report.json", replay_report)
    _write_json(out_dir / "timeline_store.json", fixtures["timeline_store.json"])
    _write_json(out_dir / "revisit_schedule.json", fixtures["revisit_schedule.json"])
    _write_json(out_dir / "timeline_feed.json", timeline_feed)
    _write_previous_hash(out_dir, scoring_path_hash)

    return {
        "status": "ok" if replay_status != "replay_mismatch" else "failed",
        "run_id": run_id,
        "replay_status": replay_status,
        "scoring_path_hash": scoring_path_hash,
        "previous_scoring_path_hash": prior_hash,
        "run_dir": str(run_dir),
        "event_log": str(run_dir / "event_log.jsonl"),
        "aggregate_event_log": str(out_dir / "event_log.jsonl"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay News Harness MVP fixtures.")
    parser.add_argument("--fixtures", type=Path, required=True, help="Fixture directory")
    parser.add_argument("--out", type=Path, required=True, help="Output artifact directory")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema contract file")
    args = parser.parse_args(argv)

    result = replay(args.fixtures, args.out, args.schema)
    print(_canonical_json(result))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
