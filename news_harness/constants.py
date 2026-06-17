"""Shared constants for the fixture-only News Harness runtime."""

from __future__ import annotations


BLOCKED_REGISTRY_STATUSES = {
    "planned",
    "unsupported",
    "auth_required",
    "risk_blocked",
    "diagnostic_only",
    "shadow_only",
}

EVENT_TYPES = [
    "run.started",
    "item.started",
    "source_registry.loaded",
    "source_score.computed",
    "connector_readiness.decided",
    "source_smoke_matrix.loaded",
    "source_smoke.decided",
    "all_source_runner.completed",
    "deepseek_scoring.completed",
    "rolling_source_schedule.loaded",
    "shadow_batch.completed",
    "timeline_store.updated",
    "revisit_schedule.registered",
    "source_smoke.allowed",
    "source_smoke.blocked",
    "candidate_discovery.allowed",
    "candidate_discovery.blocked",
    "tool.requested",
    "tool.completed",
    "tool.failed",
    "observation.recorded",
    "candidate.filtered",
    "structure.completed",
    "prediction.completed",
    "outcome.collected",
    "eval.completed",
    "timeline.generated",
    "promotion.decided",
    "run.completed",
]

GUARDRAILS = [
    "evidence",
    "image_refs",
    "versions",
    "failure_states",
    "holdout_context",
    "replay_manifest",
    "promotion_gate",
    "source_readiness",
    "source_smoke",
    "source_pool_intake",
    "vps_auth_gated_runner",
    "all_source_deepseek_runner",
    "rolling_timeline",
    "radar_timeline",
]

REQUIRED_ARTIFACTS = [
    "run_manifest.json",
    "event_log.jsonl",
    "scoring_path.json",
    "source_registry.json",
    "source_score.json",
    "connector_readiness_matrix.json",
    "source_smoke_matrix.json",
    "source_pool_intake.json",
    "real_smoke_candidate_plan.json",
    "vps_source_runner_plan.json",
    "x_list_auth_gated_smoke_plan.json",
    "xueqiu_browser_assisted_smoke_plan.json",
    "all_source_runner_dry_run.json",
    "deepseek_scoring_fixture.json",
    "shadow_source_fetch_result.json",
    "rolling_source_schedule.json",
    "timeline_store.json",
    "revisit_schedule.json",
    "timeline_feed.json",
    "source_smoke_result_rss.json",
    "source_smoke_result_public_web.json",
    "source_smoke_result_xueqiu_blocked.json",
    "source_smoke_result_reddit_auth_required.json",
    "prediction.json",
    "eval.json",
    "promotion_decision.json",
    "replay_manifest.json",
    "replay_report.json",
]

REQUIRED_CONNECTOR_PERMISSIONS = {"read-only", "no-post", "no-download", "no-cache"}

LOW_RISK_SOURCE_TYPES = {"rss", "public_web"}

REQUIRED_PATH_EVENTS = {
    "source_registry.loaded",
    "source_score.computed",
    "connector_readiness.decided",
    "source_smoke_matrix.loaded",
    "source_smoke.decided",
    "all_source_runner.completed",
    "deepseek_scoring.completed",
    "vps_runner_plan.loaded",
    "vps_runner_plan.decided",
    "rolling_source_schedule.loaded",
    "shadow_batch.completed",
    "timeline_store.updated",
    "revisit_schedule.registered",
    "source_smoke.allowed",
    "source_smoke.blocked",
    "tool.requested",
    "tool.completed",
    "observation.recorded",
    "candidate.filtered",
    "structure.completed",
    "prediction.completed",
    "outcome.collected",
    "eval.completed",
    "timeline.generated",
    "promotion.decided",
}

SMOKE_RESULT_FILES = [
    "sample_source_smoke_result_rss.json",
    "sample_source_smoke_result_public_web.json",
    "sample_source_smoke_result_xueqiu_blocked.json",
    "sample_source_smoke_result_reddit_auth_required.json",
]

SOURCE_POOL_AUTH_REQUIRED_SOURCES = {"x"}
SOURCE_POOL_READY_MARKERS = {"ready", "eligible", "production_eligible", "real_source_smoke_ready"}
SOURCE_POOL_USER_SOURCES = {"xueqiu", "x"}

X_LIST_URL = "https://x.com/i/lists/2056032482127175889?s=20"
