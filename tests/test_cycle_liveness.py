import json
from pathlib import Path

from news_harness import all_source


def test_run_cycle_writes_liveness_on_success(tmp_path, monkeypatch) -> None:
    artifact_dir = tmp_path / "latest"
    monkeypatch.setattr(all_source, "SOURCE_RUN_ARTIFACT", artifact_dir / "source_run.json")
    monkeypatch.setattr(all_source, "run_sources", lambda *args, **kwargs: {"status": "ok", "observation_count": 1})
    monkeypatch.setattr(
        all_source,
        "score",
        lambda *args, **kwargs: {"status": "ok", "scored_candidate_count": 1},
    )
    monkeypatch.setattr(
        all_source,
        "materialize_fixture_cycle_artifacts",
        lambda fixtures_dir: {"status": "ok"},
    )
    monkeypatch.setattr(
        all_source,
        "generate_timeline_feed",
        lambda fixtures_dir, timeline_out: {"status": "ok", "item_count": 1},
    )

    result = all_source.run_cycle(
        fixtures_dir=tmp_path / "fixtures",
        timeline_out=tmp_path / "timeline.json",
        dry_run=True,
    )

    liveness = json.loads((artifact_dir / "liveness.json").read_text(encoding="utf-8"))
    assert result["status"] == "ok"
    assert liveness["last_cycle_started_at"]
    assert liveness["last_cycle_completed_at"]
    assert liveness["last_success_at"] == liveness["last_cycle_completed_at"]
    assert not (artifact_dir / "run_cycle.lock").exists()


def test_run_cycle_blocks_overlap(tmp_path, monkeypatch) -> None:
    artifact_dir = tmp_path / "latest"
    monkeypatch.setattr(all_source, "SOURCE_RUN_ARTIFACT", artifact_dir / "source_run.json")
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "run_cycle.lock").write_text("active\n", encoding="utf-8")

    result = all_source.run_cycle(
        fixtures_dir=tmp_path / "fixtures",
        timeline_out=tmp_path / "timeline.json",
        dry_run=True,
    )

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "cycle_overlap_blocked"
    assert (artifact_dir / "run_cycle.lock").exists()
