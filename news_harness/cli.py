"""Command line entrypoint for the local News Harness runtime."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import all_source as all_source_module
from . import health as health_module
from . import inspect as inspect_module
from . import manual_smoke as manual_smoke_module
from . import mcp_server as mcp_server_module
from . import preflight as preflight_module
from . import replay as replay_module
from . import site_server as site_server_module
from . import timeline as timeline_module
from . import validator
from .config import DEFAULT_PREFLIGHT_CONFIG
from .fixtures import DEFAULT_SCHEMA


QUICKSTART_TEXT = """News Harness v2 — financial content repack tool

Local candidate, safe fixture-only path:
  python3 -m news_harness validate fixtures
  python3 -m news_harness run-sources --config configs/all_source_runner.example.json --dry-run
  python3 -m news_harness serve --host 127.0.0.1 --port 8765
  open http://127.0.0.1:8765/

Read-only integration surfaces:
  Website/API: python3 -m news_harness serve --host 0.0.0.0 --port 8765
  MCP stdio:   python3 -m news_harness mcp --feed web/data/radar-timeline/timeline_feed.json
"""


class HarnessArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(
            2,
            f"{self.prog}: error: {message}\n\n"
            "Next: run `python3 -m news_harness quickstart` for the safe local path and VPS healthcheck commands.\n",
        )


def main(argv: list[str] | None = None) -> int:
    parser = HarnessArgumentParser(
        prog="news_harness",
        description="News Harness v2 — financial content repack tool.",
        epilog="Start here: python3 -m news_harness quickstart",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=HarnessArgumentParser)

    subparsers.add_parser("quickstart", help="Print local and VPS candidate commands")

    validate_parser = subparsers.add_parser("validate", help="Validate fixture contracts")
    validate_parser.add_argument("fixtures", type=Path, help="Fixture directory to validate")
    validate_parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema contract file")

    replay_parser = subparsers.add_parser("replay", help="Replay fixture contracts")
    replay_parser.add_argument("--fixtures", type=Path, required=True, help="Fixture directory")
    replay_parser.add_argument("--out", type=Path, required=True, help="Output artifact directory")
    replay_parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema contract file")

    inspect_parser = subparsers.add_parser("inspect", help="Inspect replay artifacts")
    inspect_parser.add_argument("out", type=Path, help="Replay output artifact directory")

    preflight_parser = subparsers.add_parser("preflight", help="Run pre-real-source readiness preflight")
    preflight_parser.add_argument("--fixtures", type=Path, required=True, help="Fixture directory")
    preflight_parser.add_argument("--artifacts", type=Path, required=True, help="Replay artifact directory")
    preflight_parser.add_argument("--out", type=Path, required=True, help="Preflight output directory")
    preflight_parser.add_argument("--config", type=Path, default=DEFAULT_PREFLIGHT_CONFIG, help="Preflight config")
    preflight_parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema contract file")

    timeline_parser = subparsers.add_parser("timeline", help="Generate product-facing radar timeline feed")
    timeline_parser.add_argument("--fixtures", type=Path, required=True, help="Fixture directory")
    timeline_parser.add_argument("--out", type=Path, required=True, help="Output timeline_feed.json path")
    timeline_parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema contract file")

    run_sources_parser = subparsers.add_parser("run-sources", help="Run all-source source runner")
    run_sources_parser.add_argument("--config", type=Path, required=True, help="All-source runner config")
    run_sources_parser.add_argument("--dry-run", action="store_true", help="Use fixture-only dry-run mode")
    run_sources_parser.add_argument("--mode", choices=["dry-run", "manual-smoke"], default=None, help="Runtime mode")
    run_sources_parser.add_argument("--backend", choices=["fixture", "builtin", "direct-cli"], default="builtin", help="Source runner backend")

    cycle_parser = subparsers.add_parser("run-cycle", help="Run one source, scoring, and timeline cycle")
    cycle_parser.add_argument("--source-config", type=Path, default=all_source_module.DEFAULT_ALL_SOURCE_CONFIG)
    cycle_parser.add_argument("--score-config", type=Path, default=all_source_module.DEFAULT_DEEPSEEK_CONFIG)
    cycle_parser.add_argument("--fixtures", type=Path, default=all_source_module.ROOT / "fixtures")
    cycle_parser.add_argument("--out", type=Path, default=all_source_module.DEFAULT_TIMELINE_OUT)
    cycle_parser.add_argument("--dry-run", action="store_true", help="Use fixture-only dry-run mode")
    cycle_parser.add_argument("--mode", choices=["dry-run", "manual-smoke"], default=None, help="Runtime mode")
    cycle_parser.add_argument("--backend", choices=["fixture", "builtin", "direct-cli"], default="builtin", help="Source runner backend")

    health_parser = subparsers.add_parser("healthcheck", help="Check rolling runtime health")
    health_parser.add_argument("--auto", action="store_true", help="Run automatic healthcheck with artifact discovery")
    health_parser.add_argument("--feed", type=Path, default=health_module.DEFAULT_FEED, help="Timeline feed path")
    health_parser.add_argument("--artifact-dir", type=Path, default=None, help="Artifact directory for --auto mode")
    health_parser.add_argument("--source-run", type=Path, default=health_module.DEFAULT_SOURCE_RUN, help="Manual source run artifact")
    health_parser.add_argument("--deepseek", type=Path, default=health_module.DEFAULT_DEEPSEEK, help="DeepSeek scoring artifact")
    health_parser.add_argument("--revisit", type=Path, default=health_module.DEFAULT_REVISIT, help="Revisit schedule artifact")
    health_parser.add_argument("--outcome", type=Path, default=health_module.DEFAULT_OUTCOME, help="Outcome artifact")
    health_parser.add_argument("--eval", type=Path, default=health_module.DEFAULT_EVAL, help="Eval artifact")
    health_parser.add_argument("--max-age-minutes", type=int, default=90, help="Maximum feed age")
    health_parser.add_argument("--require-source", action="append", default=None, help="Source that must have feed items and ok source status")

    serve_parser = subparsers.add_parser("serve", help="Serve the production website and read-only API")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    serve_parser.add_argument("--port", type=int, default=8765, help="Bind port")
    serve_parser.add_argument("--root", type=Path, default=site_server_module.STATIC_ROOT, help="Static root")
    serve_parser.add_argument("--feed", type=Path, default=site_server_module.artifact_api.DEFAULT_FEED, help="Timeline feed path")
    serve_parser.add_argument("--artifact-dir", type=Path, default=site_server_module.artifact_api.DEFAULT_ARTIFACT_DIR, help="Latest artifact dir")

    mcp_parser = subparsers.add_parser("mcp", help="Run the read-only MCP stdio server")
    mcp_parser.add_argument("--feed", type=Path, default=mcp_server_module.artifact_api.DEFAULT_FEED, help="Timeline feed path")
    mcp_parser.add_argument("--artifact-dir", type=Path, default=mcp_server_module.artifact_api.DEFAULT_ARTIFACT_DIR, help="Latest artifact dir")

    args = parser.parse_args(argv)
    if args.command == "quickstart":
        print(QUICKSTART_TEXT)
        return 0
    if args.command == "validate":
        return validator.main([str(args.fixtures), "--schema", str(args.schema)])
    if args.command == "replay":
        return replay_module.main(["--fixtures", str(args.fixtures), "--out", str(args.out), "--schema", str(args.schema)])
    if args.command == "inspect":
        return inspect_module.main([str(args.out)])
    if args.command == "preflight":
        return preflight_module.main(
            [
                "--fixtures",
                str(args.fixtures),
                "--artifacts",
                str(args.artifacts),
                "--out",
                str(args.out),
                "--config",
                str(args.config),
                "--schema",
                str(args.schema),
            ]
        )
    if args.command == "timeline":
        return timeline_module.main(["--fixtures", str(args.fixtures), "--out", str(args.out), "--schema", str(args.schema)])
    if args.command == "run-sources":
        argv = ["run-sources", "--config", str(args.config)]
        if args.dry_run:
            argv.append("--dry-run")
        if args.mode:
            argv.extend(["--mode", args.mode])
        argv.extend(["--backend", args.backend])
        return all_source_module.main(argv)
    if args.command == "run-cycle":
        argv = ["run-cycle", "--source-config", str(args.source_config), "--score-config", str(args.score_config)]
        argv.extend(["--fixtures", str(args.fixtures), "--out", str(args.out)])
        if args.dry_run:
            argv.append("--dry-run")
        if args.mode:
            argv.extend(["--mode", args.mode])
        argv.extend(["--backend", args.backend])
        return all_source_module.main(argv)
    if args.command == "healthcheck":
        argv = ["--feed", str(args.feed)]
        if getattr(args, "auto", False):
            argv.append("--auto")
        if getattr(args, "artifact_dir", None) is not None:
            argv.extend(["--artifact-dir", str(args.artifact_dir)])
        argv.extend(["--source-run", str(args.source_run)])
        argv.extend(["--deepseek", str(args.deepseek)])
        argv.extend(["--revisit", str(args.revisit)])
        argv.extend(["--outcome", str(args.outcome)])
        argv.extend(["--eval", str(args.eval)])
        argv.extend(["--max-age-minutes", str(args.max_age_minutes)])
        for source in args.require_source or []:
            argv.extend(["--require-source", source])
        return health_module.main(argv)
    if args.command == "serve":
        site_server_module.run_server(args.host, args.port, args.root, args.feed, args.artifact_dir)
        return 0
    if args.command == "mcp":
        mcp_server_module.run_stdio(args.feed, args.artifact_dir)
        return 0
    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
