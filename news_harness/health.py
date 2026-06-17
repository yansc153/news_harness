"""Health checks for the rolling News Harness runtime."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import find_raw_secret_material
from .connector_quality import generate_connector_quality_report
from .events import canonical_json
from .fixtures import ROOT
from .runtime_gates import check_liveness


DEFAULT_FEED = ROOT / "web" / "radar-timeline" / "timeline_feed.json"
DEFAULT_SOURCE_RUN = ROOT / "artifacts" / "manual_smoke" / "latest" / "source_run.json"
DEFAULT_DEEPSEEK = ROOT / "artifacts" / "manual_smoke" / "latest" / "deepseek_scoring.json"
DEFAULT_REVISIT = ROOT / "artifacts" / "manual_smoke" / "latest" / "revisit_schedule.json"
DEFAULT_OUTCOME = ROOT / "artifacts" / "manual_smoke" / "latest" / "outcome.json"
DEFAULT_EVAL = ROOT / "artifacts" / "manual_smoke" / "latest" / "eval.json"


def run_healthcheck(
    *,
    feed_path: Path = DEFAULT_FEED,
    source_run_path: Path = DEFAULT_SOURCE_RUN,
    deepseek_path: Path = DEFAULT_DEEPSEEK,
    revisit_path: Path = DEFAULT_REVISIT,
    outcome_path: Path = DEFAULT_OUTCOME,
    eval_path: Path = DEFAULT_EVAL,
    max_age_minutes: int = 90,
    required_sources: list[str] | None = None,
) -> dict[str, Any]:
    required_sources = required_sources or ["x_list", "reddit", "xueqiu_hot", "xueqiu_daren"]
    checks: list[dict[str, Any]] = []
    artifacts: dict[str, Any] = {}
    raw_secret_findings: list[Any] = []

    feed = _load_json(feed_path)
    source_run = _load_json(source_run_path)
    deepseek = _load_json(deepseek_path)
    revisit_direct = _load_json(revisit_path)
    outcome = _load_json(outcome_path)
    eval_result = _load_json(eval_path)
    for label, data, path in (
        ("feed", feed, feed_path),
        ("source_run", source_run, source_run_path),
        ("deepseek", deepseek, deepseek_path),
        ("revisit", revisit_direct, revisit_path),
        ("outcome", outcome, outcome_path),
        ("eval", eval_result, eval_path),
    ):
        if data is None:
            checks.append(_check(label, False, f"{path} is missing or unreadable"))
        else:
            artifacts[label] = str(path)
            findings = find_raw_secret_material(data)
            if findings:
                raw_secret_findings.extend({"artifact": label, "finding": finding} for finding in findings)

    items = feed.get("items", []) if isinstance(feed, dict) else []
    checks.append(_check("feed_has_items", bool(items), f"feed item count={len(items)}"))

    generated_at = feed.get("generated_at") if isinstance(feed, dict) else None
    age_minutes = _age_minutes(generated_at)
    checks.append(
        _check(
            "feed_fresh",
            age_minutes is not None and age_minutes <= max_age_minutes,
            f"feed age minutes={age_minutes}; max={max_age_minutes}",
        )
    )

    counts_by_source = {
        source: sum(1 for item in items if isinstance(item, dict) and item.get("source") == source)
        for source in required_sources
    }
    for source, count in counts_by_source.items():
        checks.append(_check(f"source_{source}_present", count > 0, f"{source} feed items={count}"))

    source_statuses = {
        status.get("source"): status.get("status")
        for status in source_run.get("sources", [])
        if isinstance(source_run, dict) and isinstance(status, dict)
    }
    source_counts = {
        status.get("source"): status.get("item_count")
        for status in source_run.get("sources", [])
        if isinstance(source_run, dict) and isinstance(status, dict)
    }
    for source in required_sources:
        checks.append(_check(f"source_{source}_run_ok", source_statuses.get(source) == "ok", f"{source} run status={source_statuses.get(source)}"))

    provider_status = deepseek.get("provider_status", {}) if isinstance(deepseek, dict) else {}
    scored_candidates = deepseek.get("scored_candidates", []) if isinstance(deepseek, dict) else []
    fixture_backed = isinstance(deepseek, dict) and deepseek.get("fixture_only") is True
    deepseek_ok = (
        isinstance(provider_status, dict)
        and (provider_status.get("provider_called") is True or fixture_backed)
        and isinstance(scored_candidates, list)
        and len(scored_candidates) > 0
    )
    checks.append(
        _check(
            "deepseek_scored",
            deepseek_ok,
            f"provider_called={provider_status.get('provider_called') if isinstance(provider_status, dict) else None}; fixture_backed={fixture_backed}; "
            f"fallback={provider_status.get('fallback_used') if isinstance(provider_status, dict) else None}; "
            f"scored={len(scored_candidates) if isinstance(scored_candidates, list) else 0}",
        )
    )
    visual_items = [
        item
        for item in items
        if isinstance(item, dict)
        and (item.get("image_status") == "available" or item.get("asset_refs") or item.get("visual_evidence_score"))
    ]
    checks.append(_check("visual_evidence_present", bool(visual_items), f"visual evidence items={len(visual_items)}"))
    requires_revisit = isinstance(deepseek, dict) and "prediction_contract" in deepseek
    revisit_ref = feed.get("manual_smoke", {}).get("revisit", {}).get("schedule_ref") if isinstance(feed, dict) else None
    revisit = _load_json(ROOT / revisit_ref) if isinstance(revisit_ref, str) else revisit_direct
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
    checks.append(
        _check(
            "high_score_requires_image_evidence",
            not high_missing_image,
            f"high_score_missing_image_count={len(high_missing_image)}",
        )
    )
    checks.append(_check("redaction", not raw_secret_findings, f"raw_secret_findings={len(raw_secret_findings)}"))

    failed = [check for check in checks if check["status"] != "pass"]
    return {
        "status": "ok" if not failed else "failed",
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
    required_sources = required_sources or ["x_list", "reddit", "xueqiu_hot", "xueqiu_daren"]
    if artifact_dir is None:
        artifact_dir = DEFAULT_SOURCE_RUN.parent

    checks: list[dict[str, Any]] = []
    raw_secret_findings: list[Any] = []

    feed = _load_json(feed_path)
    checks.append(_check("feed_readable", feed is not None, f"feed_path={feed_path}"))

    items = feed.get("items", []) if isinstance(feed, dict) else []
    generated_at = feed.get("generated_at") if isinstance(feed, dict) else None
    age_minutes = _age_minutes(generated_at)
    checks.append(_check("feed_fresh", age_minutes is not None and age_minutes <= max_age_minutes,
                         f"feed age minutes={age_minutes}; max={max_age_minutes}"))

    expected_artifacts = {
        "source_run.json": artifact_dir / "source_run.json",
        "deepseek_scoring.json": artifact_dir / "deepseek_scoring.json",
        "outcome.json": artifact_dir / "outcome.json",
        "eval.json": artifact_dir / "eval.json",
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
        for src_status in sources:
            if not isinstance(src_status, dict):
                continue
            src_name = src_status.get("source", "unknown")
            src_obs = [o for o in observations_all if isinstance(o, dict) and o.get("source") == src_name]
            report = generate_connector_quality_report(
                src_obs, connector_id=f"manual_smoke.{src_name}.v1", run_id=source_run.get("run_id", "unknown"),
            )
            qs = report["quality_status"]
            checks.append(_check(f"connector_quality_{src_name}", qs == "ok",
                                 f"quality_status={qs}; item_count={report['item_count']}; "
                                 f"required_field_presence={report['required_field_presence']}; "
                                 f"truncation_suspected={report['truncation_suspected']}"))

    for name, data in loaded_artifacts.items():
        if data is None:
            continue
        findings = find_raw_secret_material(data)
        if findings:
            raw_secret_findings.extend({"artifact": name, "finding": f} for f in findings)
    checks.append(_check("raw_secret_leakage", not raw_secret_findings, f"findings={len(raw_secret_findings)}"))

    outcome = loaded_artifacts.get("outcome.json")
    revisit_path = artifact_dir / "revisit_schedule.json"
    revisit = _load_json(revisit_path)
    due_task_ids = _due_task_ids(revisit)
    outcome_rows = (
        outcome.get("outcomes", [])
        if isinstance(outcome, dict) and isinstance(outcome.get("outcomes"), list)
        else []
    )
    outcome_task_ids = {row.get("task_id") for row in outcome_rows if isinstance(row, dict)}
    missing_due_outcomes = sorted(t for t in due_task_ids if t not in outcome_task_ids)
    checks.append(_check("due_outcomes_present", not missing_due_outcomes,
                         f"due_tasks={len(due_task_ids)}; outcomes={len(outcome_rows)}; missing={len(missing_due_outcomes)}"))

    liveness = check_liveness(artifact_dir, max_staleness_minutes=max_age_minutes)
    checks.append(_check("liveness", liveness["status"] == "ok",
                         f"liveness_status={liveness['status']}; staleness_minutes={liveness['staleness_minutes']}"))

    failed = [c for c in checks if c["status"] == "fail"]
    if not failed:
        status = "ok"
    elif any("blocked" in c.get("detail", "") for c in failed):
        status = "failed"
    else:
        status = "degraded"

    return {
        "status": status, "command": "healthcheck", "mode": "auto",
        "feed_path": str(feed_path), "artifact_dir": str(artifact_dir),
        "max_age_minutes": max_age_minutes, "feed_item_count": len(items),
        "feed_age_minutes": age_minutes, "checks": checks,
        "failed_checks": [c["name"] for c in failed],
        "raw_secret_findings": raw_secret_findings,
    }


def _check(name: str, passed: bool, detail: str) -> dict[str, str]:
    return {"name": name, "status": "pass" if passed else "fail", "detail": detail}


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
        max_age_minutes=args.max_age_minutes, required_sources=args.require_source,
    )
    print(canonical_json(result))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
