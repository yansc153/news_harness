"""All-source runner and DeepSeek scoring commands."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .config import find_raw_secret_material
from .events import canonical_json
from .fixtures import ROOT, load_json
from .direct_cli_backend import run_direct_cli_sources
from .manual_smoke import (
    EVAL_ARTIFACT,
    OUTCOME_ARTIFACT,
    REVISIT_SCHEDULE_ARTIFACT,
    SCORING_ARTIFACT,
    SOURCE_RUN_ARTIFACT,
    materialize_fixture_cycle_artifacts,
    run_eval,
    run_manual_sources,
    run_revisit,
    score_manual_deepseek,
)
from .timeline import generate_timeline_feed


DEFAULT_ALL_SOURCE_CONFIG = ROOT / "configs" / "all_source_runner.example.json"
DEFAULT_DEEPSEEK_CONFIG = ROOT / "configs" / "deepseek_provider.example.json"
DEFAULT_ALL_SOURCE_FIXTURE = ROOT / "fixtures" / "sample_all_source_runner_dry_run.json"
DEFAULT_DEEPSEEK_FIXTURE = ROOT / "fixtures" / "sample_deepseek_scoring_fixture.json"
DEFAULT_TIMELINE_OUT = ROOT / "web" / "data" / "radar-timeline" / "timeline_feed.json"


def _blocked_without_dry_run(command: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "command": command,
        "error_code": "read_only_boundary_unapproved",
        "message": "This MVP only supports explicit --dry-run fixture execution.",
        "production_connector_ready": False,
        "real_source_smoke_not_executed": True,
    }


def _config_redaction_status(config: dict[str, Any]) -> dict[str, Any]:
    findings = find_raw_secret_material(config)
    return {
        "raw_secret_findings": findings,
        "redaction_passed": not findings,
    }


def run_sources(config_path: Path, *, dry_run: bool = False, mode: str | None = None, backend: str = "builtin") -> dict[str, Any]:
    selected_mode = _selected_mode(dry_run, mode)
    if selected_mode == "manual-smoke":
        if backend == "direct-cli":
            return run_direct_cli_sources(config_path)
        return run_manual_sources(config_path)
    if selected_mode != "dry-run":
        return _blocked_without_dry_run("run-sources")

    config = load_json(config_path)
    fixture = load_json(DEFAULT_ALL_SOURCE_FIXTURE)
    redaction = _config_redaction_status(config)
    sources = [status.get("source") for status in fixture.get("source_statuses", [])]
    observation_sources = sorted({item.get("source") for item in fixture.get("observations", [])})

    return {
        "status": "ok" if redaction["redaction_passed"] else "failed",
        "command": "run-sources",
        "backend": "fixture",
        "mode": "dry_run",
        "config_ref": str(config_path),
        "fixture_ref": str(DEFAULT_ALL_SOURCE_FIXTURE.relative_to(ROOT)),
        "fixture_only": True,
        "no_real_source_access": True,
        "network_fetch_allowed": False,
        "real_source_smoke_not_executed": True,
        "production_connector_ready": False,
        "sources": sources,
        "observation_sources": observation_sources,
        "observation_count": len(fixture.get("observations", [])),
        "structured_error_count": len(fixture.get("structured_errors", [])),
        **redaction,
    }


def score(config_path: Path, *, dry_run: bool = False, mode: str | None = None) -> dict[str, Any]:
    selected_mode = _selected_mode(dry_run, mode)
    if selected_mode == "manual-smoke":
        return score_manual_deepseek(config_path)
    if selected_mode != "dry-run":
        return _blocked_without_dry_run("score")

    config = load_json(config_path)
    fixture = load_json(DEFAULT_DEEPSEEK_FIXTURE)
    observations = load_json(DEFAULT_ALL_SOURCE_FIXTURE).get("observations", [])
    redaction = _config_redaction_status(config)
    candidates = fixture.get("scored_candidates", [])
    source_by_ref = {
        f"fixtures/sample_all_source_runner_dry_run.json#observations[{index}]": observation.get("source")
        for index, observation in enumerate(observations)
        if isinstance(observation, dict)
    }

    return {
        "status": "ok" if redaction["redaction_passed"] else "failed",
        "command": "score",
        "mode": "dry_run",
        "config_ref": str(config_path),
        "fixture_ref": str(DEFAULT_DEEPSEEK_FIXTURE.relative_to(ROOT)),
        "fixture_only": True,
        "model_provider": fixture.get("model_provider"),
        "model_id": fixture.get("model_id"),
        "provider_called": fixture.get("provider_status", {}).get("provider_called"),
        "fallback_used": fixture.get("provider_status", {}).get("fallback_used"),
        "no_real_model_call": fixture.get("no_real_model_call"),
        "production_connector_ready": False,
        "real_source_smoke_not_executed": True,
        "scored_candidate_count": len(candidates),
        "scored_sources": sorted(
            {
                source
                for source in (source_by_ref.get(candidate.get("source_observation_ref")) for candidate in candidates)
                if isinstance(source, str)
            }
        ),
        **redaction,
    }


def run_cycle(
    *,
    source_config: Path = DEFAULT_ALL_SOURCE_CONFIG,
    score_config: Path = DEFAULT_DEEPSEEK_CONFIG,
    fixtures_dir: Path = ROOT / "fixtures",
    timeline_out: Path = DEFAULT_TIMELINE_OUT,
    dry_run: bool = False,
    mode: str | None = None,
    backend: str = "builtin",
) -> dict[str, Any]:
    """Run one source -> score -> timeline cycle for local/VPS schedulers."""

    selected_mode = _selected_mode(dry_run, mode)
    source_result = run_sources(source_config, dry_run=dry_run, mode=selected_mode, backend=backend)
    score_result: dict[str, Any] | None = None
    timeline_result: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = []

    if source_result.get("status") == "ok":
        if selected_mode == "manual-smoke" and not source_result.get("observation_count"):
            errors.append({"phase": "sources", "status": "failed", "code": "no_manual_source_observations"})
        failed_sources = [
            source
            for source, status in (source_result.get("source_statuses") or {}).items()
            if status != "ok"
        ]
        if selected_mode == "manual-smoke" and failed_sources:
            errors.append({"phase": "sources", "status": "failed", "code": "source_failed", "sources": failed_sources})

    can_score = source_result.get("status") == "ok" and (
        selected_mode != "manual-smoke" or bool(source_result.get("observation_count"))
    )
    if can_score:
        score_result = score(score_config, dry_run=dry_run, mode=selected_mode)
    else:
        if not errors:
            errors.append({"phase": "sources", "status": source_result.get("status"), "code": source_result.get("error_code")})

    if score_result is not None and score_result.get("status") != "ok":
        errors.append({"phase": "score", "status": score_result.get("status"), "code": score_result.get("error_code")})
    if selected_mode == "manual-smoke" and score_result is not None and score_result.get("structured_error_count"):
        errors.append({"phase": "score", "status": "failed", "code": "deepseek_structured_errors", "count": score_result.get("structured_error_count")})

    closed_loop_result: dict[str, Any] | None = None
    if score_result is not None and score_result.get("status") == "ok":
        if selected_mode == "dry-run":
            closed_loop_result = materialize_fixture_cycle_artifacts(fixtures_dir)
        elif selected_mode == "manual-smoke":
            revisit_result = run_revisit(REVISIT_SCHEDULE_ARTIFACT, SOURCE_RUN_ARTIFACT, OUTCOME_ARTIFACT)
            eval_result = run_eval(SCORING_ARTIFACT, OUTCOME_ARTIFACT, EVAL_ARTIFACT)
            closed_loop_result = {
                "status": "ok" if revisit_result.get("status") == "ok" and eval_result.get("status") == "ok" else "failed",
                "revisit": revisit_result,
                "eval": eval_result,
                "revisit_schedule_ref": str(REVISIT_SCHEDULE_ARTIFACT.relative_to(ROOT)),
                "outcome_ref": str(OUTCOME_ARTIFACT.relative_to(ROOT)),
                "eval_ref": str(EVAL_ARTIFACT.relative_to(ROOT)),
            }
        if closed_loop_result and closed_loop_result.get("status") != "ok":
            errors.append({"phase": "closed_loop", "status": closed_loop_result.get("status")})

    if score_result is not None:
        try:
            timeline_result = generate_timeline_feed(fixtures_dir, timeline_out)
        except Exception as exc:  # noqa: BLE001 - top-level cycle report must stay structured
            errors.append({"phase": "timeline", "status": "failed", "code": "timeline_export_failed", "message": str(exc)})

    raw_secret_findings = []
    for result in (source_result, score_result, timeline_result):
        if isinstance(result, dict):
            raw_secret_findings.extend(result.get("raw_secret_findings", []))

    return {
        "status": "ok" if not errors and not raw_secret_findings else "failed",
        "command": "run-cycle",
        "mode": selected_mode or "blocked",
        "backend": backend,
        "source_status": source_result.get("status"),
        "score_status": score_result.get("status") if score_result else "skipped",
        "timeline_status": timeline_result.get("status") if timeline_result else "skipped",
        "closed_loop_status": closed_loop_result.get("status") if closed_loop_result else "skipped",
        "closed_loop": closed_loop_result,
        "source_observation_count": source_result.get("observation_count"),
        "scored_candidate_count": score_result.get("scored_candidate_count") if score_result else None,
        "timeline_item_count": timeline_result.get("item_count") if timeline_result else None,
        "timeline_out": str(timeline_out),
        "production_connector_ready": False,
        "raw_secret_findings": raw_secret_findings,
        "errors": errors,
    }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run all-source fixture commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run-sources")
    run_parser.add_argument("--config", type=Path, default=DEFAULT_ALL_SOURCE_CONFIG)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--mode", choices=["dry-run", "manual-smoke"], default=None)
    run_parser.add_argument("--backend", choices=["fixture", "builtin", "direct-cli"], default="builtin")

    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("--config", type=Path, default=DEFAULT_DEEPSEEK_CONFIG)
    score_parser.add_argument("--dry-run", action="store_true")
    score_parser.add_argument("--mode", choices=["dry-run", "manual-smoke"], default=None)

    cycle_parser = subparsers.add_parser("run-cycle")
    cycle_parser.add_argument("--source-config", type=Path, default=DEFAULT_ALL_SOURCE_CONFIG)
    cycle_parser.add_argument("--score-config", type=Path, default=DEFAULT_DEEPSEEK_CONFIG)
    cycle_parser.add_argument("--fixtures", type=Path, default=ROOT / "fixtures")
    cycle_parser.add_argument("--out", type=Path, default=DEFAULT_TIMELINE_OUT)
    cycle_parser.add_argument("--dry-run", action="store_true")
    cycle_parser.add_argument("--mode", choices=["dry-run", "manual-smoke"], default=None)
    cycle_parser.add_argument("--backend", choices=["fixture", "builtin", "direct-cli"], default="builtin")

    args = parser.parse_args(argv)
    if args.command == "run-sources":
        result = run_sources(args.config, dry_run=args.dry_run, mode=args.mode, backend=args.backend)
    elif args.command == "score":
        result = score(args.config, dry_run=args.dry_run, mode=args.mode)
    else:
        result = run_cycle(
            source_config=args.source_config,
            score_config=args.score_config,
            fixtures_dir=args.fixtures,
            timeline_out=args.out,
            dry_run=args.dry_run,
            mode=args.mode,
            backend=args.backend,
        )

    print(canonical_json(result))
    return 0 if result.get("status") == "ok" else 1


def _selected_mode(dry_run: bool, mode: str | None) -> str | None:
    if dry_run:
        return "dry-run"
    return mode


if __name__ == "__main__":
    sys.exit(main())
