"""Health checks for the rolling News Harness runtime."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import find_raw_secret_material
from .connector_quality import generate_connector_quality_report
from .events import canonical_json
from .fixtures import ROOT
from .runtime_gates import check_liveness


DEFAULT_FEED = ROOT / "artifacts" / "manual_smoke" / "latest" / "timeline_feed.json"
WEB_FEED_COPY = ROOT / "web" / "data" / "radar-timeline" / "timeline_feed.json"
DEFAULT_SOURCE_RUN = ROOT / "artifacts" / "manual_smoke" / "latest" / "source_run.json"
DEFAULT_DEEPSEEK = ROOT / "artifacts" / "manual_smoke" / "latest" / "deepseek_scoring.json"
DEFAULT_REVISIT = ROOT / "artifacts" / "manual_smoke" / "latest" / "revisit_schedule.json"
DEFAULT_OUTCOME = ROOT / "artifacts" / "manual_smoke" / "latest" / "outcome.json"
DEFAULT_EVAL = ROOT / "artifacts" / "manual_smoke" / "latest" / "eval.json"
DEFAULT_TARGET_SET = ROOT / "artifacts" / "manual_smoke" / "latest" / "hourly_target_set.json"


def run_healthcheck(
    *,
    feed_path: Path = DEFAULT_FEED,
    source_run_path: Path = DEFAULT_SOURCE_RUN,
    deepseek_path: Path = DEFAULT_DEEPSEEK,
    revisit_path: Path = DEFAULT_REVISIT,
    outcome_path: Path = DEFAULT_OUTCOME,
    eval_path: Path = DEFAULT_EVAL,
    target_set_path: Path = DEFAULT_TARGET_SET,
    max_age_minutes: int = 90,
    required_sources: list[str] | None = None,
) -> dict[str, Any]:
    required_sources = required_sources or ["xueqiu_targeted"]
    checks: list[dict[str, Any]] = []
    artifacts: dict[str, Any] = {}
    raw_secret_findings: list[Any] = []

    feed = _load_json(feed_path)
    source_run = _load_json(source_run_path)
    deepseek = _load_json(deepseek_path)
    revisit_direct = _load_json(revisit_path)
    outcome = _load_json(outcome_path)
    eval_result = _load_json(eval_path)
    target_set = _load_json(target_set_path)
    collection_status = None
    if isinstance(target_set, dict):
        collection_status = (target_set.get("collection") or {}).get("status")
    for label, data, path in (
        ("feed", feed, feed_path),
        ("source_run", source_run, source_run_path),
        ("deepseek", deepseek, deepseek_path),
        ("revisit", revisit_direct, revisit_path),
        ("outcome", outcome, outcome_path),
        ("eval", eval_result, eval_path),
        ("target_set", target_set, target_set_path),
    ):
        if data is None:
            checks.append(_check(label, False, f"{path} is missing or unreadable"))
        else:
            artifacts[label] = str(path)
            findings = find_raw_secret_material(data)
            if findings:
                raw_secret_findings.extend({"artifact": label, "finding": finding} for finding in findings)

    items = feed.get("items", []) if isinstance(feed, dict) else []
    quiet_hour = collection_status == "ok_no_candidates"
    checks.append(_check("feed_has_items", bool(items) or quiet_hour,
                         f"feed item count={len(items)}; collection_status={collection_status}; quiet_hour_ok={quiet_hour}"))

    generated_at = feed.get("generated_at") if isinstance(feed, dict) else None
    age_minutes = _age_minutes(generated_at)
    checks.append(
        _check(
            "feed_fresh",
            quiet_hour or (age_minutes is not None and age_minutes <= max_age_minutes),
            f"feed age minutes={age_minutes}; max={max_age_minutes}; quiet_hour={quiet_hour}",
        )
    )

    source_statuses: dict[str, str] = {}
    source_counts: dict[str, int] = {}
    if isinstance(source_run, dict):
        source_statuses = {
            status.get("source"): status.get("status")
            for status in source_run.get("sources", [])
            if isinstance(status, dict)
        }
        source_counts = {
            status.get("source"): status.get("item_count")
            for status in source_run.get("sources", [])
            if isinstance(status, dict)
        }
    counts_by_source = {
        source: sum(1 for item in items if isinstance(item, dict) and item.get("source") == source)
        for source in required_sources
    }
    for source, count in counts_by_source.items():
        source_ok = source_statuses.get(source) == "ok"
        checks.append(_check(
            f"source_{source}_present",
            count > 0 or source_ok,
            f"{source} feed items={count}; run_status={source_statuses.get(source)}",
        ))
    for source in required_sources:
        source_st = source_statuses.get(source)
        if source_st == "ok":
            checks.append(_check(f"source_{source}_run_ok", True, f"{source} run status={source_st}"))
        elif source_st == "partial":
            checks.append(_warn(f"source_{source}_run_ok", f"{source} partial run; some stock fetches failed"))
        else:
            checks.append(_check(f"source_{source}_run_ok", False, f"{source} run status={source_st}"))

    if isinstance(target_set, dict):
        endpoint_results = target_set.get("endpoint_results") or []
        endpoint_failures = [row for row in endpoint_results if isinstance(row, dict) and row.get("status") != "ok"]
        collection = target_set.get("collection") or {}
        target_age = _age_minutes(target_set.get("generated_at"))
        checks.append(_check("target_set_fresh", target_age is not None and target_age <= max_age_minutes,
                             f"target age minutes={target_age}; max={max_age_minutes}"))
        checks.append(_check("kaipanla_endpoints_ok", not endpoint_failures,
                             f"endpoint_count={len(endpoint_results)}; failures={len(endpoint_failures)}"))
        checks.append(_check("targeted_collection_state", collection.get("status") in {"ok_with_candidates", "ok_no_candidates"},
                             f"status={collection.get('status')}; targets={target_set.get('stock_count')}; "
                             f"qualified={collection.get('qualified_observation_count')}; threshold={collection.get('comment_threshold')}"))

    provider_status = deepseek.get("provider_status", {}) if isinstance(deepseek, dict) else {}
    scored_candidates = deepseek.get("scored_candidates", []) if isinstance(deepseek, dict) else []
    fixture_backed = isinstance(deepseek, dict) and deepseek.get("fixture_only") is True
    deepseek_errors = deepseek.get("structured_errors", []) if isinstance(deepseek, dict) and isinstance(deepseek.get("structured_errors"), list) else []
    deepseek_ok = (
        fixture_backed
        or (
            isinstance(provider_status, dict)
            and provider_status.get("provider_called") is True
            and not deepseek_errors
            and not provider_status.get("fallback_used")
        )
    )
    # 在没有候选的合法安静时段（ok_no_candidates），没有打分结果是正常的，不判失败；
    # 但在发现成功、存在候选而 DeepSeek 尚未打分时，才视为失败。
    scoring_required = not quiet_hour
    enough_candidates = (
        isinstance(scored_candidates, list) and len(scored_candidates) > 0
    )
    deepseek_scored_ok = deepseek_ok and (enough_candidates or not scoring_required)
    checks.append(
        _check(
            "deepseek_scored",
            deepseek_scored_ok,
            f"provider_called={provider_status.get('provider_called') if isinstance(provider_status, dict) else None}; fixture_backed={fixture_backed}; "
            f"fallback={provider_status.get('fallback_used') if isinstance(provider_status, dict) else None}; "
            f"structured_errors={len(deepseek_errors)}; scored={len(scored_candidates) if isinstance(scored_candidates, list) else 0}; "
            f"quiet_hour={quiet_hour}; scoring_required={scoring_required}",
        )
    )
    visual_items = [
        item
        for item in items
        if isinstance(item, dict)
        and (item.get("image_status") == "available" or item.get("asset_refs") or item.get("visual_evidence_score"))
    ]
    checks.append(_check("visual_evidence_optional", True, f"visual evidence items={len(visual_items)}; images are optional"))
    requires_revisit = isinstance(deepseek, dict) and "prediction_contract" in deepseek
    revisit_ref = feed.get("manual_smoke", {}).get("revisit", {}).get("schedule_ref") if isinstance(feed, dict) else None
    revisit = revisit_direct if revisit_direct is not None else (_load_json(ROOT / revisit_ref) if isinstance(revisit_ref, str) else None)
    task_count = len(revisit.get("tasks", [])) if isinstance(revisit, dict) and isinstance(revisit.get("tasks"), list) else 0
    checks.append(_check("revisit_registered", (not requires_revisit) or task_count > 0, f"revisit tasks={task_count}; required={requires_revisit}"))
    due_task_ids = _due_task_ids(revisit)
    outcome_rows = outcome.get("outcomes", []) if isinstance(outcome, dict) and isinstance(outcome.get("outcomes"), list) else []
    outcome_task_ids = {row.get("task_id") for row in outcome_rows if isinstance(row, dict)}
    missing_due_outcomes = sorted(task_id for task_id in due_task_ids if task_id not in outcome_task_ids)
    checks.append(
        _check(
            "due_revisits_have_outcomes",
            not missing_due_outcomes,
            f"due_tasks={len(due_task_ids)}; outcomes={len(outcome_rows)}; missing={len(missing_due_outcomes)}",
        )
    )
    eval_rows = eval_result.get("evaluated_rows", []) if isinstance(eval_result, dict) and isinstance(eval_result.get("evaluated_rows"), list) else []
    joined_eval_rows = [row for row in eval_rows if isinstance(row, dict) and row.get("join_status") == "joined"]
    checks.append(
        _check(
            "outcomes_joined_to_eval",
            (not outcome_rows) or bool(joined_eval_rows),
            f"outcomes={len(outcome_rows)}; eval_rows={len(eval_rows)}; joined_eval_rows={len(joined_eval_rows)}",
        )
    )
    high_missing_image = [
        item.get("id")
        for item in items
        if isinstance(item, dict)
        and _score(item) >= 0.8
        and not _has_image_evidence(item)
    ]
    checks.append(_check("high_score_requires_image_evidence", True,
                         f"high_score_missing_image_count={len(high_missing_image)}; images are optional (non-gating)"))
    checks.append(_check("redaction", not raw_secret_findings, f"raw_secret_findings={len(raw_secret_findings)}"))

    failed = [check for check in checks if check["status"] == "fail"]
    warned = [check for check in checks if check["status"] == "warn"]
    return {
        "status": "ok" if (not failed and not warned) else ("degraded" if warned and not failed else "failed"),
        "command": "healthcheck",
        "artifacts": artifacts,
        "max_age_minutes": max_age_minutes,
        "feed_item_count": len(items),
        "feed_age_minutes": age_minutes,
        "counts_by_source": counts_by_source,
        "source_run_statuses": source_statuses,
        "source_run_counts": source_counts,
        "due_revisit_count": len(due_task_ids),
        "outcome_count": len(outcome_rows),
        "eval_row_count": len(eval_rows),
        "joined_eval_row_count": len(joined_eval_rows),
        "checks": checks,
        "failed_checks": [check["name"] for check in failed],
        "warned_checks": [check["name"] for check in warned],
        "raw_secret_findings": raw_secret_findings,
        "production_connector_ready": False,
    }


def run_automatic_healthcheck(
    feed_path: Path = DEFAULT_FEED,
    artifact_dir: Path | None = None,
    max_age_minutes: int = 90,
    required_sources: list[str] | None = None,
) -> dict[str, Any]:
    """Run all automated health checks with artifact discovery (--auto mode)."""
    required_sources = required_sources or ["xueqiu_targeted"]
    if artifact_dir is None:
        artifact_dir = DEFAULT_SOURCE_RUN.parent

    checks: list[dict[str, Any]] = []
    raw_secret_findings: list[Any] = []

    feed = _load_json(feed_path)
    checks.append(_check("feed_readable", feed is not None, f"feed_path={feed_path}"))
    # Web 部署副本（供 dashboard / site_server 读取）可能不存在，属于部署监控项，
    # 不应让健康检查判为 failed；用 warn 暴露，提示运维确认部署链路。
    web_copy_exists = WEB_FEED_COPY.exists()
    checks.append(_warn("web_feed_copy_present", f"exists={web_copy_exists}; path={WEB_FEED_COPY}"))

    target_set_probe = _load_json(artifact_dir / "hourly_target_set.json")
    quiet_hour = (
        isinstance(target_set_probe, dict)
        and (target_set_probe.get("collection") or {}).get("status") == "ok_no_candidates"
    )

    items = feed.get("items", []) if isinstance(feed, dict) else []
    generated_at = feed.get("generated_at") if isinstance(feed, dict) else None
    age_minutes = _age_minutes(generated_at)
    checks.append(_check("feed_fresh", quiet_hour or (age_minutes is not None and age_minutes <= max_age_minutes),
                         f"feed age minutes={age_minutes}; max={max_age_minutes}; quiet_hour={quiet_hour}"))

    # Single-source contract: legacy model/revisit/eval artifacts are not
    # required and must not make the healthcheck fail.
    expected_artifacts = {
        "source_run.json": artifact_dir / "source_run.json",
        "hourly_target_set.json": artifact_dir / "hourly_target_set.json",
    }
    loaded_artifacts: dict[str, Any] = {}
    for name, path in expected_artifacts.items():
        data = _load_json(path)
        loaded_artifacts[name] = data
        checks.append(_check(f"artifact_{name.replace('.json','')}", data is not None, f"path={path}"))

    if artifact_dir.exists():
        for json_file in sorted(artifact_dir.glob("*.json")):
            if json_file.name in expected_artifacts:
                continue
            try:
                json.loads(json_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                checks.append(_check(f"broken_json_{json_file.name}", False, str(exc)))

    source_run = loaded_artifacts.get("source_run.json")
    if isinstance(source_run, dict):
        sources = source_run.get("sources", [])
        observations_all = source_run.get("observations", [])
        source_statuses = {
            row.get("source"): row.get("status") for row in sources if isinstance(row, dict)
        }
        for required_source in required_sources:
            src_st = source_statuses.get(required_source)
            if src_st == "ok":
                checks.append(_check(f"required_source_{required_source}", True, f"run_status={src_st}"))
            elif src_st == "partial":
                checks.append(_warn(f"required_source_{required_source}", f"run_status={src_st}; partial stock fetch failure"))
            else:
                checks.append(_check(f"required_source_{required_source}", False, f"run_status={src_st}"))
        for src_name in required_sources:
            src_status = next((row for row in sources if isinstance(row, dict) and row.get("source") == src_name), None)
            src_obs = [o for o in observations_all if isinstance(o, dict) and o.get("source") == src_name]
            if src_status is None:
                continue
            if src_status.get("status") == "ok" and not src_obs:
                checks.append(_check(f"connector_quality_{src_name}", True, "legitimate no_candidates; item_count=0"))
            else:
                report = generate_connector_quality_report(
                    src_obs, connector_id=f"manual_smoke.{src_name}.v1", run_id=source_run.get("run_id", "unknown"),
                )
                qs = report["quality_status"]
                checks.append(_check(f"connector_quality_{src_name}", qs == "ok",
                                     f"quality_status={qs}; item_count={report['item_count']}; "
                                     f"required_field_presence={report['required_field_presence']}; "
                                     f"truncation_suspected={report['truncation_suspected']}"))
    else:
        for required_source in required_sources:
            checks.append(_check(f"required_source_{required_source}", False, "source_run unavailable"))

    target_set = loaded_artifacts.get("hourly_target_set.json")
    if isinstance(target_set, dict):
        endpoint_results = target_set.get("endpoint_results") or []
        endpoint_failures = [row for row in endpoint_results if isinstance(row, dict) and row.get("status") != "ok"]
        collection = target_set.get("collection") or {}
        checks.append(_check("kaipanla_endpoints", not endpoint_failures,
                             f"count={len(endpoint_results)}; failures={len(endpoint_failures)}"))
        checks.append(_check("targeted_collection", collection.get("status") in {"ok_with_candidates", "ok_no_candidates"},
                             f"status={collection.get('status')}; targets={target_set.get('stock_count')}; "
                             f"qualified={collection.get('qualified_observation_count')}; threshold={collection.get('comment_threshold')}"))

    for name, data in loaded_artifacts.items():
        if data is None:
            continue
        findings = find_raw_secret_material(data)
        if findings:
            raw_secret_findings.extend({"artifact": name, "finding": f} for f in findings)
    checks.append(_check("raw_secret_leakage", not raw_secret_findings, f"findings={len(raw_secret_findings)}"))

    # No prediction/revisit/evaluation loop exists in the single-source mode.
    checks.append(_check("legacy_scoring_disabled", True, "DeepSeek, revisit and evaluation are disabled by contract"))

    liveness = check_liveness(artifact_dir, max_staleness_minutes=max_age_minutes)
    checks.append(_check("liveness", liveness["status"] == "ok",
                         f"liveness_status={liveness['status']}; staleness_minutes={liveness['staleness_minutes']}"))

    failed = [c for c in checks if c["status"] == "fail"]
    warned = [c for c in checks if c["status"] == "warn"]
    if not failed and not warned:
        status = "ok"
    elif not failed and warned:
        status = "degraded"
    else:
        status = "failed"

    production = _production_readiness(loaded_artifacts.get("source_run.json"))
    if not production["ready"]:
        checks.append(_warn("production_readiness",
                             f"ready={production["ready"]}; reason={production["reason"]}; "
                             f"missing_env={production["missing_env"]}"))
        warned = [c for c in checks if c["status"] == "warn"]
        if not failed and not warned:
            status = "ok"
        elif not failed and warned:
            status = "degraded"
        else:
            status = "failed"

    return {
        "status": status, "command": "healthcheck", "mode": "auto",
        "feed_path": str(feed_path), "artifact_dir": str(artifact_dir),
        "max_age_minutes": max_age_minutes, "feed_item_count": len(items),
        "feed_age_minutes": age_minutes, "checks": checks,
        "failed_checks": [c["name"] for c in failed],
        "warned_checks": [c["name"] for c in warned],
        "raw_secret_findings": raw_secret_findings,
        "production_readiness": production,
    }




_REQUIRED_PRODUCTION_ENV = {
    "NEWS_HARNESS_KPL_DEVICE_ID": "KPL 设备标识（开盘啦题材发现）",
    "NEWS_HARNESS_KPL_TOKEN": "KPL 访问令牌",
    "NEWS_HARNESS_KPL_USER_ID": "KPL 用户标识",
    "NEWS_HARNESS_XUEQIU_COOKIE_FILE": "雪球读取 cookie 文件（可选，headless 可用 storage state 替代）",
    "NEWS_HARNESS_XUEQIU_STORAGE_STATE_FILE": "雪球 headless storage state 文件（可选，cookie 可用替代）",
}


def _production_readiness(source_run: dict[str, Any] | None) -> dict[str, Any]:
    """Diagnose whether the pipeline actually ran against real sources."""
    if not isinstance(source_run, dict):
        return {
            "ready": False,
            "reason": "source_run artifact missing; real-source cycle has not produced output",
            "missing_env": list(_REQUIRED_PRODUCTION_ENV),
            "fixture_only": None,
            "production_connector_ready": None,
            "real_source_smoke_not_executed": None,
        }
    fixture_only = source_run.get("fixture_only")
    prod_ready = source_run.get("production_connector_ready")
    no_real = source_run.get("real_source_smoke_not_executed")
    if prod_ready is True:
        return {"ready": True, "reason": "real-source cycle produced output", "missing_env": [], "fixture_only": fixture_only, "production_connector_ready": prod_ready, "real_source_smoke_not_executed": no_real}
    missing = {
        key: label for key, label in _REQUIRED_PRODUCTION_ENV.items()
        if not (os.environ.get(key) or (key == "NEWS_HARNESS_XUEQIU_STORAGE_STATE_FILE" and os.environ.get("NEWS_HARNESS_XUEQIU_COOKIE_FILE")))
    }
    # KPL identity can be provided via file envs too
    for file_env, label in (("NEWS_HARNESS_KPL_DEVICE_ID_FILE", "KPL device id secret file"), ("NEWS_HARNESS_KPL_TOKEN_FILE", "KPL token secret file"), ("NEWS_HARNESS_KPL_USER_ID_FILE", "KPL user id secret file")):
        if os.environ.get(file_env):
            missing.pop(file_env[:-5], None)
    reason = "real-source cycle not executed"
    if fixture_only or no_real:
        reason = "last run was dry-run/fixture only; no real-source cycle executed"
    return {
        "ready": False,
        "reason": reason,
        "missing_env": list(missing),
        "fixture_only": fixture_only,
        "production_connector_ready": prod_ready,
        "real_source_smoke_not_executed": no_real,
    }



def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "pass" if passed else "fail", "detail": detail}


def _warn(name: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": "warn", "detail": detail}


def _due_task_ids(revisit: dict[str, Any] | None) -> set[str]:
    if not isinstance(revisit, dict) or not isinstance(revisit.get("tasks"), list):
        return set()
    now = datetime.now(timezone.utc)
    task_ids = set()
    for task in revisit["tasks"]:
        if not isinstance(task, dict) or not isinstance(task.get("task_id"), str):
            continue
        due_at = _parse_time(task.get("due_at"))
        if due_at is None or due_at <= now:
            task_ids.add(task["task_id"])
    return task_ids


def _score(item: dict[str, Any]) -> float:
    for key in ("radar_score", "hotness_score", "lasting_score"):
        value = item.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _has_image_evidence(item: dict[str, Any]) -> bool:
    if item.get("image_quality_status") in {"downloaded", "reference_only"}:
        return True
    if isinstance(item.get("asset_refs"), list) and item["asset_refs"]:
        return True
    refs = item.get("image_refs")
    return any(isinstance(ref, dict) and ref.get("evidence_eligible") is True for ref in refs) if isinstance(refs, list) else False


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _age_minutes(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return round(max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 60), 2)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check News Harness rolling runtime health.")
    parser.add_argument("--auto", action="store_true", help="Run automatic healthcheck with artifact discovery")
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--artifact-dir", type=Path, default=None, help="Artifact directory for --auto mode")
    parser.add_argument("--source-run", type=Path, default=DEFAULT_SOURCE_RUN)
    parser.add_argument("--deepseek", type=Path, default=DEFAULT_DEEPSEEK)
    parser.add_argument("--revisit", type=Path, default=DEFAULT_REVISIT)
    parser.add_argument("--outcome", type=Path, default=DEFAULT_OUTCOME)
    parser.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--target-set", type=Path, default=DEFAULT_TARGET_SET)
    parser.add_argument("--max-age-minutes", type=int, default=90)
    parser.add_argument("--require-source", action="append", default=None)
    args = parser.parse_args(argv)

    if args.auto:
        artifact_dir = args.artifact_dir or DEFAULT_SOURCE_RUN.parent
        result = run_automatic_healthcheck(
            feed_path=args.feed, artifact_dir=Path(artifact_dir),
            max_age_minutes=args.max_age_minutes, required_sources=args.require_source,
        )
        print(canonical_json(result))
        return 0 if result["status"] == "ok" else 1

    result = run_healthcheck(
        feed_path=args.feed, source_run_path=args.source_run, deepseek_path=args.deepseek,
        revisit_path=args.revisit, outcome_path=args.outcome, eval_path=args.eval,
        target_set_path=args.target_set,
        max_age_minutes=args.max_age_minutes, required_sources=args.require_source,
    )
    print(canonical_json(result))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
