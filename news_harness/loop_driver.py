"""Self-iterating loop driver for continuous prediction → wait → revisit → eval cycles.

Usage (standalone):
  python3 -m news_harness loop --source-config configs/all_source_runner.example.json \
    --score-config configs/deepseek_provider.example.json --max-turns 5
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .all_source import run_sources as _run_sources, score as _score
from .direct_cli_backend import run_direct_cli_sources as _run_direct_cli
from .events import canonical_json
from .fixtures import ROOT, load_json
from .manual_smoke import (
    EVAL_ARTIFACT,
    FAST_FEEDBACK_WINDOWS,
    OUTCOME_ARTIFACT,
    REVISIT_SCHEDULE_ARTIFACT,
    SCORING_ARTIFACT,
    SOURCE_RUN_ARTIFACT,
    _load_optional_json,
    _run_id,
    _write_manual_json,
    build_revisit_schedule,
    run_eval as _run_eval,
    run_revisit,
)
from .paths import safe_output_path
from .rolling_store import (
    DEFAULT_STORE_PATH,
    earliest_due_at,
    load as _load_store,
    record_outcome,
    register_candidates,
    save as _save_store,
)

LOOP_RUN_DIR = ROOT / "artifacts" / "manual_smoke" / "loop"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refetch_engagement(source_url: str, source: str) -> dict[str, Any] | None:
    """Attempt to re-fetch a single source URL for fresh engagement.

    Uses the most appropriate refetch strategy per source type.
    Returns observation dict with engagement_snapshot on success, None on failure.

    For auth-gated sources, this now attempts a direct-cli refetch instead
    of silently returning None. If the CLI session is expired or unavailable,
    it returns a structured degraded result instead of hiding the failure.
    """
    import os

    # Public sources: direct HTTP refetch
    if source in ("rss", "public_web"):
        try:
            from .manual_smoke import _fetch_rss, _fetch_public_web
            if source == "rss":
                return _fetch_rss(source_url, "")
            return _fetch_public_web(source_url)
        except Exception:
            return None

    # Reddit: try public JSON API first (works without auth for most subreddits)
    if source == "reddit" and "reddit.com" in source_url:
        try:
            from .manual_smoke import _fetch_reddit, _structured_error
            result = _fetch_reddit({"subreddits": [], "max_items_per_subreddit_per_run": 1}, None)
            # Reddit public API refetch for a specific URL
            import urllib.request, json as _json
            json_url = source_url.rstrip("/") + ".json?limit=1&raw_json=1"
            req = urllib.request.Request(json_url, headers={
                "User-Agent": "news-harness-manual-smoke/0.1 read-only",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            children = data[0].get("data", {}).get("children", []) if isinstance(data, list) else []
            if children:
                post_data = children[0].get("data", {})
                return {
                    "engagement_snapshot": {
                        "status": "refetched",
                        "metrics": {
                            "score": post_data.get("score"),
                            "num_comments": post_data.get("num_comments"),
                            "upvote_ratio": post_data.get("upvote_ratio"),
                        },
                    }
                }
        except Exception:
            pass  # Fall through to None

    # X list and Xueqiu: attempt direct-cli refetch if available
    if source in ("x_list", "xueqiu_hot", "xueqiu_daren", "xueqiu_dispute"):
        try:
            from .direct_cli_backend import _find_command, _run_command
            if source == "x_list":
                command = _find_command("twitter")
                if command:
                    # Try to refetch the specific tweet
                    import re
                    match = re.search(r"/status/(\d+)", source_url)
                    if match:
                        tweet_id = match.group(1)
                        result = _run_command(
                            [command, "tweet", tweet_id, "--json"],
                            timeout=30,
                            env={"OUTPUT": "json"},
                        )
                        if result.get("status") == "ok" and result.get("stdout"):
                            import json as _json
                            parsed = _json.loads(result["stdout"])
                            if isinstance(parsed, dict):
                                metrics = {}
                                for k in ("likes", "like_count", "retweets", "retweet_count",
                                          "replies", "reply_count", "views", "view_count"):
                                    if k in parsed:
                                        metrics[k] = parsed[k]
                                if metrics:
                                    return {"engagement_snapshot": {"status": "refetched", "metrics": metrics}}
            elif source.startswith("xueqiu_"):
                # Xueqiu refetch requires browser bridge; return structured degraded state
                return {
                    "engagement_snapshot": {
                        "status": "refetch_unavailable_browser_required",
                        "metrics": {},
                    },
                    "refetch_degraded": True,
                }
        except Exception:
            pass

    return None


def run_loop(
    source_config: Path,
    score_config: Path,
    *,
    max_turns: int = 10,
    timeout_minutes: int = 1440,
    failure_budget: int = 3,
    store_path: Path = DEFAULT_STORE_PATH,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a continuous self-iterating prediction loop.

    Flow per turn:
      1. Run source fetch + DeepSeek scoring
      2. Build revisit schedule
      3. Register candidates in rolling store
      4. Find earliest pending due_at
      5. Sleep until due_at (or min 15m if no pending)
      6. Run revisit with real refetch
      7. Run eval
      8. Repeat until max_turns or timeout

    Returns loop summary dict.
    """
    started = datetime.now(timezone.utc)
    store = _load_store(store_path)
    loop_id = _run_id("loop")
    turns: list[dict[str, Any]] = []
    consecutive_failures = 0

    turn = 0
    while turn < max_turns:
        turn += 1
        turn_started = time.monotonic()
        turn_id = f"{loop_id}_turn_{turn:03d}"
        turn_dir = LOOP_RUN_DIR / turn_id
        turn_dir.mkdir(parents=True, exist_ok=True)
        turn_errors: list[dict[str, Any]] = []

        # --- 1. Source fetch + scoring ---
        if dry_run:
            source_result = _run_sources(source_config, dry_run=True)
        else:
            source_result = _run_sources(source_config, mode="manual-smoke", backend="builtin")

        score_result = _score(score_config, dry_run=dry_run)

        # --- 2. Build revisit schedule & register in store ---
        source_artifact = _load_optional_json(SOURCE_RUN_ARTIFACT, {})
        observations = source_artifact.get("observations", []) if isinstance(source_artifact, dict) else []
        # scored_candidates live in the artifact file, not the summary dict
        scoring_artifact = _load_optional_json(SCORING_ARTIFACT, {})
        candidates = scoring_artifact.get("scored_candidates", []) if isinstance(scoring_artifact, dict) else []

        # Gate: skip scoring if source fetch produced no observations
        if not observations and not dry_run:
            turn_errors.append({"code": "no_observations", "message": "source fetch produced zero observations"})
        if not candidates and not dry_run:
            turn_errors.append({"code": "no_candidates", "message": "scoring produced zero candidates"})

        schedule = build_revisit_schedule(turn_id, source_artifact, candidates)
        _write_manual_json(REVISIT_SCHEDULE_ARTIFACT, schedule)

        if candidates:
            register_candidates(store, turn_id, candidates, observations, FAST_FEEDBACK_WINDOWS)
            _save_store(store)

        # --- 3. Wait for earliest due window ---
        if not dry_run:
            next_due = earliest_due_at(store)
            if next_due:
                wait_seconds = max(0, (next_due - datetime.now(timezone.utc)).total_seconds())
            else:
                wait_seconds = 15 * 60  # fallback: 15m
            if wait_seconds > 0 and turn < max_turns:
                time.sleep(min(wait_seconds, 900))  # cap at 15m per sleep iteration
        else:
            next_due = None
            wait_seconds = 0

        # --- 4. Run revisit with real refetch ---
        revisit_result = run_revisit(
            schedule_path=REVISIT_SCHEDULE_ARTIFACT,
            source_run_path=SOURCE_RUN_ARTIFACT,
            out_path=OUTCOME_ARTIFACT,
            refetch_fn=_refetch_engagement,
            rolling_store=store,
        )
        _save_store(store)

        # --- 5. Run eval ---
        eval_result = _run_eval(SCORING_ARTIFACT, OUTCOME_ARTIFACT, EVAL_ARTIFACT)

        # --- 6. Record turn ---
        source_failed = source_result.get("status") != "ok"
        score_failed = score_result.get("status") != "ok"
        revisit_failed = revisit_result.get("status") != "ok"
        any_failure = source_failed or score_failed or revisit_failed

        if any_failure:
            consecutive_failures += 1
        else:
            consecutive_failures = 0

        turn_record = {
            "turn": turn,
            "turn_id": turn_id,
            "started_at": _utc_now(),
            "duration_seconds": round(time.monotonic() - turn_started, 3),
            "source_status": source_result.get("status"),
            "score_status": score_result.get("status"),
            "candidate_count": len(candidates),
            "revisit_outcomes": revisit_result.get("outcome_count", 0),
            "revisit_errors": revisit_result.get("structured_error_count", 0),
            "eval_status": eval_result.get("status"),
            "refetch_count": 0,
            "rolling_store_candidates": len(store.get("candidates", {})),
            "next_due_at": next_due.isoformat() if next_due else None,
            "waited_seconds": round(wait_seconds, 1),
            "errors": turn_errors,
        }
        turns.append(turn_record)

        # --- 7. Check exit conditions ---
        elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 60
        if elapsed >= timeout_minutes:
            break
        if consecutive_failures >= failure_budget:
            break

    store["updated_at"] = _utc_now()
    _save_store(store)

    return {
        "status": "ok" if consecutive_failures < failure_budget else "failed",
        "loop_id": loop_id,
        "started_at": started.isoformat(),
        "completed_at": _utc_now(),
        "total_turns": len(turns),
        "max_turns": max_turns,
        "timeout_minutes": timeout_minutes,
        "consecutive_failures": consecutive_failures,
        "turns": turns,
        "rolling_store_ref": str(store_path),
    }
