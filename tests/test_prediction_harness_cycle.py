from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from news_harness import all_source, manual_smoke, rolling_store
from news_harness.timeline import compact_failed_timeline_items, merge_manual_timeline_items


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _observation(index: int) -> dict:
    return {
        "observation_id": f"obs-{index}",
        "evidence_ref": f"source_run.json#observations/obs-{index}",
        "source": "xueqiu_hot",
        "source_label": "雪球热门",
        "source_url": f"https://xueqiu.com/u/{index}",
        "canonical_url": f"https://xueqiu.com/u/{index}",
        "copy_text": f"雪球完整正文 {index} " * 20,
        "engagement_snapshot": {"status": "observed_at_fetch", "metrics": {"likes": index, "comments": index}},
        "image_refs": [],
        "full_text_status": "full_text_observed",
        "source_quality_status": "full_text_observed",
    }


def _candidate(index: int, evaluated_at: str) -> dict:
    ref = f"source_run.json#observations/obs-{index}"
    return {
        "candidate_id": f"manual_smoke_score_{index:03d}",
        "source_observation_ref": ref,
        "input_evidence_refs": [ref],
        "evaluated_at": evaluated_at,
        "scores": {"1h": 0.7, "4h": 0.6},
        "1h_score": 0.7,
        "4h_score": 0.6,
        "confidence": 0.5,
        "topic_or_hook": f"hook {index}",
    }


def _patch_artifact_paths(monkeypatch, tmp_path: Path) -> dict[str, Path]:
    paths = {
        "source": tmp_path / "source_run.json",
        "scoring": tmp_path / "deepseek_scoring.json",
        "schedule": tmp_path / "revisit_schedule.json",
        "outcome": tmp_path / "outcome.json",
        "eval": tmp_path / "eval.json",
    }
    for module in (all_source, manual_smoke):
        monkeypatch.setattr(module, "SOURCE_RUN_ARTIFACT", paths["source"], raising=False)
        monkeypatch.setattr(module, "SCORING_ARTIFACT", paths["scoring"], raising=False)
        monkeypatch.setattr(module, "REVISIT_SCHEDULE_ARTIFACT", paths["schedule"], raising=False)
        monkeypatch.setattr(module, "OUTCOME_ARTIFACT", paths["outcome"], raising=False)
        monkeypatch.setattr(module, "EVAL_ARTIFACT", paths["eval"], raising=False)
    return paths


def test_manual_deepseek_fallback_scores_more_than_first_five(tmp_path, monkeypatch) -> None:
    source_run = tmp_path / "source_run.json"
    scoring = tmp_path / "deepseek_scoring.json"
    schedule = tmp_path / "revisit_schedule.json"
    monkeypatch.setattr(manual_smoke, "SOURCE_RUN_ARTIFACT", source_run)
    monkeypatch.setattr(manual_smoke, "SCORING_ARTIFACT", scoring)
    monkeypatch.setattr(manual_smoke, "REVISIT_SCHEDULE_ARTIFACT", schedule)
    monkeypatch.setenv("NEWS_HARNESS_MANUAL_SMOKE_ACK", "1")
    monkeypatch.setenv("NEWS_HARNESS_REAL_SOURCE_SMOKE", "1")
    monkeypatch.setenv("NEWS_HARNESS_DEEPSEEK_SMOKE", "1")
    monkeypatch.delenv("DEEPSEEK_API_KEY_FILE", raising=False)

    _write_json(source_run, {"observations": [_observation(i) for i in range(7)]})
    config = tmp_path / "deepseek.json"
    _write_json(config, {"model_id": "deepseek-chat"})

    result = manual_smoke.score_manual_deepseek(config)
    artifact = json.loads(scoring.read_text(encoding="utf-8"))

    assert result["status"] == "blocked"
    assert result["scored_candidate_count"] == 7
    assert len(artifact["scored_candidates"]) == 7
    assert len(artifact["input_evidence_refs"]) == 7
    assert artifact["provider_status"]["fallback_used"] == "degraded_provider_unavailable"


def test_manual_deepseek_uses_configured_timeout_and_retries(monkeypatch) -> None:
    calls: list[int] = []

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            payload = {
                "model": "deepseek-test",
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "scored_candidates": [{
                                "source_observation_ref": "source_run.json#observations/obs-1",
                                "scores": {"1h": 0.4, "4h": 0.6},
                                "confidence": 0.7,
                                "uncertainty": 0.3,
                                "topic_or_hook": "hook",
                                "rationale": "why",
                                "risk_flags": [],
                                "feature_contributions": {},
                            }]
                        })
                    }
                }],
            }
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(_request: object, timeout: int) -> Response:
        calls.append(timeout)
        if len(calls) == 1:
            raise TimeoutError("timed out")
        return Response()

    monkeypatch.setattr(manual_smoke.urllib.request, "urlopen", fake_urlopen)

    candidates = manual_smoke._call_deepseek(
        {"model_id": "deepseek-chat", "timeout_ms": 90000, "max_retries": 1},
        [_observation(1)],
        "secret",
    )

    assert calls == [90, 90]
    assert candidates[0]["model_provider"] == "deepseek"
    assert candidates[0]["source_observation_ref"] == "source_run.json#observations/obs-1"


def test_manual_deepseek_chunks_large_batches(monkeypatch) -> None:
    calls: list[int] = []

    class Response:
        def __init__(self, refs: list[str]) -> None:
            self.refs = refs

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            payload = {
                "model": "deepseek-test",
                "choices": [{
                    "message": {
                        "content": json.dumps({
                            "scored_candidates": [
                                {
                                    "source_observation_ref": ref,
                                    "scores": {"1h": 0.4, "4h": 0.6},
                                    "confidence": 0.7,
                                    "uncertainty": 0.3,
                                    "topic_or_hook": "hook",
                                    "rationale": "why",
                                    "risk_flags": [],
                                    "feature_contributions": {},
                                }
                                for ref in self.refs
                            ]
                        })
                    }
                }],
            }
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request: object, timeout: int) -> Response:
        calls.append(timeout)
        body = json.loads(getattr(request, "data").decode("utf-8"))
        user_payload = json.loads(body["messages"][1]["content"])
        refs = [obs["source_observation_ref"] for obs in user_payload["observations"]]
        return Response(refs)

    monkeypatch.setattr(manual_smoke.urllib.request, "urlopen", fake_urlopen)

    candidates = manual_smoke._call_deepseek(
        {"model_id": "deepseek-chat", "timeout_ms": 90000, "max_retries": 0, "batch_size": 2},
        [_observation(i) for i in range(3)],
        "secret",
    )

    assert calls == [90, 90]
    assert [candidate["candidate_id"] for candidate in candidates] == [
        "manual_smoke_score_001",
        "manual_smoke_score_002",
        "manual_smoke_score_003",
    ]


def test_rolling_store_saves_and_loads_prediction_record(tmp_path) -> None:
    store_path = tmp_path / "rolling.json"
    evaluated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store = rolling_store.register_candidates(
        rolling_store.load(store_path),
        "cycle-a",
        [_candidate(1, evaluated_at)],
        [_observation(1)],
        manual_smoke.FAST_FEEDBACK_WINDOWS,
    )
    rolling_store.save(store, store_path)

    loaded = rolling_store.load(store_path)
    entry = next(iter(loaded["candidates"].values()))

    assert entry["prediction_record"]["source_observation_ref"] == "source_run.json#observations/obs-1"
    assert entry["prediction_record"]["1h_score"] == 0.7
    assert entry["windows"]["1h"]["status"] == "pending"
    assert entry["windows"]["4h"]["status"] == "pending"


def test_run_cycle_keeps_old_predictions_and_collects_only_due_window(tmp_path, monkeypatch) -> None:
    paths = _patch_artifact_paths(monkeypatch, tmp_path)
    source_path = paths["source"]
    scoring_path = paths["scoring"]
    schedule_path = paths["schedule"]
    outcome_path = paths["outcome"]
    store_path = tmp_path / "rolling.json"

    calls = {"n": 0}
    first_eval = (datetime.now(timezone.utc) - timedelta(minutes=70)).strftime("%Y-%m-%dT%H:%M:%SZ")
    second_eval = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def fake_sources(*args, **kwargs):
        calls["n"] += 1
        _write_json(source_path, {"observations": [_observation(calls["n"])]})
        return {"status": "ok", "observation_count": 1, "source_statuses": {"xueqiu_hot": "ok"}}

    def fake_score(*args, **kwargs):
        evaluated_at = first_eval if calls["n"] == 1 else second_eval
        candidate = _candidate(calls["n"], evaluated_at)
        _write_json(
            scoring_path,
            {
                "object_type": "ManualSmokeDeepSeekScoring",
                "run_id": f"score-cycle-{calls['n']}",
                "scored_candidates": [candidate],
                "structured_errors": [],
            },
        )
        _write_json(schedule_path, {"tasks": []})
        return {"status": "ok", "scored_candidate_count": 1, "structured_error_count": 0}

    def fake_timeline(*args, **kwargs):
        return {"status": "ok", "item_count": 0}

    def refetch(source_url: str, source: str) -> dict:
        return {"engagement_snapshot": {"status": "refetched", "metrics": {"likes": 50, "comments": 20}}}

    monkeypatch.setattr(all_source, "run_sources", fake_sources)
    monkeypatch.setattr(all_source, "score", fake_score)
    monkeypatch.setattr(all_source, "generate_timeline_feed", fake_timeline)

    first = all_source.run_cycle(mode="manual-smoke", store_path=store_path, refetch_fn=refetch)
    second = all_source.run_cycle(mode="manual-smoke", store_path=store_path, refetch_fn=refetch)

    store = rolling_store.load(store_path)
    outcomes = json.loads(outcome_path.read_text(encoding="utf-8"))["outcomes"]

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert len(store["candidates"]) == 2
    first_entry = next(entry for entry in store["candidates"].values() if entry["source_url"].endswith("/1"))
    assert first_entry["windows"]["1h"]["status"] == "collected"
    assert first_entry["windows"]["4h"]["status"] == "pending"
    assert any(outcome["window"] == "1h" and outcome["refetch_performed"] for outcome in outcomes)


def test_run_cycle_skips_closed_loop_when_score_is_not_successful(tmp_path, monkeypatch) -> None:
    paths = _patch_artifact_paths(monkeypatch, tmp_path)
    store_path = tmp_path / "rolling.json"
    _write_json(paths["source"], {"observations": [_observation(1)]})
    _write_json(paths["scoring"], {"run_id": "stale", "scored_candidates": [_candidate(1, "2026-01-01T00:00:00Z")]})

    monkeypatch.setattr(all_source, "run_sources", lambda *args, **kwargs: {"status": "ok", "observation_count": 1})
    monkeypatch.setattr(
        all_source,
        "score",
        lambda *args, **kwargs: {"status": "blocked", "structured_error_count": 1, "scored_candidate_count": 1},
    )

    result = all_source.run_cycle(mode="manual-smoke", store_path=store_path)

    assert result["status"] == "failed"
    assert result["closed_loop_status"] == "skipped"
    assert not store_path.exists()


def test_revisit_refetch_failure_does_not_collect_pending_window(tmp_path, monkeypatch) -> None:
    paths = _patch_artifact_paths(monkeypatch, tmp_path)
    store_path = tmp_path / "rolling.json"
    evaluated_at = (datetime.now(timezone.utc) - timedelta(minutes=70)).strftime("%Y-%m-%dT%H:%M:%SZ")
    store = rolling_store.register_candidates(
        rolling_store.load(store_path),
        "cycle-a",
        [_candidate(1, evaluated_at)],
        [_observation(1)],
        manual_smoke.FAST_FEEDBACK_WINDOWS,
    )
    rolling_store.save(store, store_path)
    schedule = all_source._rolling_revisit_schedule("cycle-a", rolling_store.load(store_path))
    _write_json(paths["schedule"], schedule)
    _write_json(paths["source"], {"observations": []})

    def failing_refetch(source_url: str, source: str) -> dict:
        raise RuntimeError("network down")

    result = manual_smoke.run_revisit(
        paths["schedule"],
        paths["source"],
        paths["outcome"],
        refetch_fn=failing_refetch,
        rolling_store=store,
        preserve_existing=True,
    )

    entry = next(iter(store["candidates"].values()))
    assert result["status"] == "failed"
    assert entry["windows"]["1h"]["status"] == "pending"
    assert json.loads(paths["outcome"].read_text(encoding="utf-8"))["outcomes"] == []


def test_eval_joins_outcomes_by_candidate_id_not_shared_source_ref(tmp_path) -> None:
    scoring = tmp_path / "deepseek_scoring.json"
    outcome = tmp_path / "outcome.json"
    eval_path = tmp_path / "eval.json"
    ref = "source_run.json#observations/shared"
    old_candidate = {**_candidate(1, "2026-01-01T00:00:00Z"), "candidate_id": "c-old", "source_observation_ref": ref}
    new_candidate = {**_candidate(1, "2026-01-01T01:00:00Z"), "candidate_id": "c-new", "source_observation_ref": ref}
    _write_json(scoring, {"scored_candidates": [old_candidate, new_candidate]})
    _write_json(
        outcome,
        {
            "outcomes": [
                {
                    "outcome_id": "out-c-new-1h",
                    "candidate_id": "c-new",
                    "source_observation_ref": ref,
                    "source_evidence_ref": ref,
                    "window": "1h",
                    "window_role": "early_momentum",
                    "observation_status": "observed",
                    "metrics_source": "same_connector_same_url",
                    "source_availability": "available",
                    "source": "xueqiu_hot",
                    "baseline_snapshot": {"likes": 10, "comments": 1},
                    "current_snapshot": {"likes": 80, "comments": 9},
                    "raw_delta": {"likes": 70, "comments": 8, "shares": None, "views": None},
                }
            ]
        },
    )

    manual_smoke.run_eval(scoring, outcome, eval_path)
    rows = json.loads(eval_path.read_text(encoding="utf-8"))["evaluated_rows"]

    assert [row["join_status"] for row in rows if row["candidate_id"] == "c-new" and row["window"] == "1h"] == ["joined"]
    assert [row["join_status"] for row in rows if row["candidate_id"] == "c-old" and row["window"] == "1h"] == ["missing_outcome"]


def test_rolling_store_prediction_id_reuse_does_not_overwrite_old_prediction(tmp_path) -> None:
    evaluated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    first = {**_candidate(1, evaluated_at), "prediction_id": "provider-reused-id"}
    second = {**_candidate(2, evaluated_at), "prediction_id": "provider-reused-id"}
    store = rolling_store.register_candidates(rolling_store.load(tmp_path / "rolling.json"), "cycle-a", [first], [_observation(1)], manual_smoke.FAST_FEEDBACK_WINDOWS)
    store = rolling_store.register_candidates(store, "cycle-b", [second], [_observation(2)], manual_smoke.FAST_FEEDBACK_WINDOWS)

    assert len(store["candidates"]) == 2
    assert sorted(entry["source_url"] for entry in store["candidates"].values()) == ["https://xueqiu.com/u/1", "https://xueqiu.com/u/2"]


def test_retention_removes_failed_evaluated_items_and_keeps_pending_or_passed() -> None:
    failed = {
        "id": "failed",
        "source_url": "https://example.com/failed",
        "copy_text": "failed copy",
        "image_refs": [{"original_image_ref": "https://example.com/a.png"}],
        "hotness_score": 0.9,
        "published_at": "2026-06-20T00:00:00Z",
        "retention_status": "failed_1h_4h",
    }
    pending = {
        "id": "pending",
        "source_url": "https://example.com/pending",
        "copy_text": "pending copy",
        "hotness_score": 0.8,
        "published_at": "2026-06-20T00:00:00Z",
        "retention_status": "pending",
    }
    passed = {
        "id": "passed",
        "source_url": "https://example.com/passed",
        "copy_text": "passed copy",
        "hotness_score": 0.7,
        "published_at": "2026-06-20T00:00:00Z",
        "retention_status": "passed",
    }

    merged = merge_manual_timeline_items([failed, pending, passed], [])

    assert [item["id"] for item in merged] == ["pending", "passed"]


def test_retention_compacts_failed_copy_after_three_days() -> None:
    item = {
        "id": "failed-old",
        "source": "xueqiu_hot",
        "source_url": "https://example.com/failed-old",
        "copy_text": "old failed copy should not remain",
        "image_refs": [{"original_image_ref": "https://example.com/a.png"}],
        "retention_status": "failed_1h_4h",
        "retention_failed_at": "2026-06-17T00:00:00Z",
        "eval_status": "eval_joined_2_windows",
        "eval_success_grades": ["not_meaningful", "weak"],
    }

    compacted = compact_failed_timeline_items([item], now="2026-06-21T00:00:00Z")

    assert compacted == [
        {
            "id": "failed-old",
            "source": "xueqiu_hot",
            "source_url": "https://example.com/failed-old",
            "retention_status": "compacted_failed_1h_4h",
            "copy_text_hash": compacted[0]["copy_text_hash"],
            "eval_summary": {
                "eval_status": "eval_joined_2_windows",
                "eval_success_grades": ["not_meaningful", "weak"],
            },
            "compacted_at": "2026-06-21T00:00:00Z",
        }
    ]
    assert "old failed copy should not remain" not in json.dumps(compacted)
    assert "image_refs" not in compacted[0]
