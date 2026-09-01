from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from news_harness import health


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_auto_health_qc_only_required_source(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    feed = {"items": [{"source": "xueqiu_targeted", "id": "x1"}], "generated_at": now.isoformat()}
    source_run = {"run_id": "test", "sources": [{"source": "x_list", "status": "ok", "item_count": 1}], "observations": [{"source": "x_list", "source_url": "https://example.com", "copy_text": "x"}]}
    feed_path = tmp_path / "timeline_feed.json"
    _write_json(feed_path, feed)
    _write_json(tmp_path / "source_run.json", source_run)
    _write_json(tmp_path / "deepseek_scoring.json", {})
    _write_json(tmp_path / "outcome.json", {})
    _write_json(tmp_path / "eval.json", {})
    _write_json(tmp_path / "hourly_target_set.json", {})
    result = health.run_automatic_healthcheck(feed_path=feed_path, artifact_dir=tmp_path, max_age_minutes=9999)
    check_names = {c["name"] for c in result["checks"]}
    assert "connector_quality_x_list" not in check_names
    assert "connector_quality_xueqiu_targeted" not in check_names
    # A missing required source is reported by the dedicated required_source_* gate.
    assert "required_source_xueqiu_targeted" in check_names


def test_auto_health_qc_generates_for_present_required_source(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    feed = {"items": [{"source": "xueqiu_targeted", "id": "x1"}], "generated_at": now.isoformat()}
    target_obs = {"source": "xueqiu_targeted", "source_url": "https://xueqiu.com/1", "copy_text": "a long enough xueqiu post", "published_at": now.isoformat(), "engagement_snapshot": {"likes": 1, "comments": 11, "reposts": 0, "views": 0}}
    source_run = {"run_id": "test", "sources": [{"source": "xueqiu_targeted", "status": "ok", "item_count": 1}, {"source": "x_list", "status": "ok", "item_count": 1}], "observations": [target_obs, {"source": "x_list", "source_url": "https://example.com", "copy_text": "x"}]}
    feed_path = tmp_path / "timeline_feed.json"
    _write_json(feed_path, feed)
    _write_json(tmp_path / "source_run.json", source_run)
    _write_json(tmp_path / "deepseek_scoring.json", {})
    _write_json(tmp_path / "outcome.json", {})
    _write_json(tmp_path / "eval.json", {})
    _write_json(tmp_path / "hourly_target_set.json", {})
    result = health.run_automatic_healthcheck(feed_path=feed_path, artifact_dir=tmp_path, max_age_minutes=9999)
    check_names = {c["name"] for c in result["checks"]}
    assert "connector_quality_xueqiu_targeted" in check_names
    assert "connector_quality_x_list" not in check_names



def test_healthcheck_missing_artifacts_is_failed_not_degraded(tmp_path: Path) -> None:
    # Fail-fast: genuinely missing artifacts must surface as hard failures, not
    # be downgraded to degraded. This guards against the old blocked-based
    # downgrade path that hid real problems.
    result = health.run_healthcheck(
        feed_path=tmp_path / "feed.json",
        source_run_path=tmp_path / "source_run.json",
        deepseek_path=tmp_path / "deepseek.json",
        revisit_path=tmp_path / "revisit.json",
        outcome_path=tmp_path / "outcome.json",
        eval_path=tmp_path / "eval.json",
        target_set_path=tmp_path / "target.json",
    )
    assert result["status"] == "failed"
    assert "feed" in result["failed_checks"]
    assert "source_run" in result["failed_checks"]
    assert "deepseek_scored" in result["failed_checks"]
    assert result["warned_checks"] == []
