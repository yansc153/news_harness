"""Inspect local replay artifacts for the fixture runtime."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .constants import REQUIRED_ARTIFACTS, REQUIRED_PATH_EVENTS
from .events import canonical_json
from .fixtures import load_json


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def inspect_replay(out_dir: Path) -> dict[str, Any]:
    manifest_path = out_dir / "latest_manifest.json"
    report_path = out_dir / "replay_report.json"
    if not manifest_path.exists():
        return {"status": "failed", "out_dir": str(out_dir), "error_code": "latest_manifest_missing"}
    if not report_path.exists():
        return {"status": "failed", "out_dir": str(out_dir), "error_code": "replay_report_missing"}

    manifest = load_json(manifest_path)
    report = load_json(report_path)
    event_log_ref = manifest.get("event_log_ref")
    if not isinstance(event_log_ref, str):
        return {"status": "failed", "out_dir": str(out_dir), "error_code": "event_log_ref_missing"}

    event_log = Path(event_log_ref)
    if not event_log.is_absolute():
        event_log = Path.cwd() / event_log
    if not event_log.exists():
        return {
            "status": "failed",
            "out_dir": str(out_dir),
            "run_id": manifest.get("run_id"),
            "error_code": "event_log_missing",
            "event_log_ref": event_log_ref,
        }

    run_dir = event_log.parent
    events = _read_jsonl(event_log)
    event_types = [event.get("event_type") for event in events]
    event_type_set = set(event_types)
    required_missing = sorted(REQUIRED_PATH_EVENTS - event_type_set)
    artifact_status = {name: (run_dir / name).exists() for name in REQUIRED_ARTIFACTS}
    missing_artifacts = sorted(name for name, exists in artifact_status.items() if not exists)

    status = "ok"
    error_code = None
    if required_missing:
        status = "failed"
        error_code = "required_events_missing"
    elif missing_artifacts:
        status = "failed"
        error_code = "required_artifacts_missing"
    elif manifest.get("replay_status") not in {"deterministic_match", "first_run_no_prior", "replay_unverifiable"}:
        status = "failed"
        error_code = "replay_status_not_accepted"

    return {
        "status": status,
        "error_code": error_code,
        "out_dir": str(out_dir),
        "run_id": manifest.get("run_id"),
        "replay_status": manifest.get("replay_status"),
        "terminal_state": "completed" if manifest.get("replay_status") != "replay_mismatch" else "failed",
        "scoring_path_hash": manifest.get("scoring_path_hash"),
        "previous_scoring_path_hash": manifest.get("previous_scoring_path_hash"),
        "event_log_ref": event_log_ref,
        "aggregate_event_log_ref": manifest.get("aggregate_event_log_ref"),
        "event_count": len(events),
        "event_types_present": sorted(event_type_set),
        "required_events_missing": required_missing,
        "missing_artifacts": missing_artifacts,
        "report_status": report.get("status"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect News Harness replay artifacts.")
    parser.add_argument("out", type=Path, help="Replay output artifact directory")
    args = parser.parse_args(argv)

    result = inspect_replay(args.out)
    print(canonical_json(result))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
