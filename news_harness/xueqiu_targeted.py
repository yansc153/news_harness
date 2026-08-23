"""Targeted Xueqiu stock-discussion fetcher.

For each stock in the HourlyTargetSet, fetch recent discussions via the
Xueqiu symbol search/status API, apply a fixed comment threshold, and
produce SourceObservation-compatible dicts.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

from pathlib import Path


OPENCLI_TIMEOUT_SECONDS = 60


def apply_comment_filter(rows: list[dict], *, min_comments: int) -> list[dict]:
    """Filter discussion rows to keep only those meeting the fixed threshold."""
    return [r for r in rows if int(r.get("reply_count") or r.get("comments") or 0) >= min_comments]


def _strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def map_discussion_row_to_observation(
    row: dict,
    *,
    symbol: str,
    stock_name: str,
    themes: list[str] | None = None,
) -> dict:
    """Map one Xueqiu discussion row to a SourceObservation dict."""
    status_id = str(row.get("id") or "").strip()
    user = row.get("user") or {}
    user_id = str(user.get("id") or row.get("user_id") or "").strip()
    screen_name = str(user.get("screen_name") or row.get("author") or "").strip()

    text = _strip_html(row.get("description") or row.get("text") or "")
    created_ms = row.get("created_at")
    published_at = None
    if created_ms:
        try:
            ts = float(created_ms)
            if ts > 1e12:
                ts = ts / 1000
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            published_at = dt.isoformat().replace("+00:00", "Z")
        except (ValueError, TypeError, OSError):
            pass

    reply_count = int(row.get("reply_count") or row.get("replies") or row.get("comments") or 0)
    fav_count = int(row.get("fav_count") or row.get("likes") or 0)
    retweet_count = int(row.get("retweet_count") or row.get("retweets") or 0)

    image_urls = []
    for img_field in ("firstImg", "cover_pic"):
        val = row.get(img_field)
        if isinstance(val, str) and val.startswith("http"):
            image_urls.extend(u.strip() for u in val.split(",") if u.strip().startswith("http"))
    for img in (row.get("image_info_list") or []):
        url = None
        if isinstance(img, dict):
            url = img.get("url") or img.get("originUrl")
        elif isinstance(img, str):
            url = img
        if url and str(url).startswith("http"):
            image_urls.append(str(url))

    for img in (row.get("images") or []):
        url = img.get("url") if isinstance(img, dict) else img
        if url and str(url).startswith("http"):
            image_urls.append(str(url))

    image_urls = list(dict.fromkeys(image_urls))

    row_url = str(row.get("url") or "").strip()
    canonical_url = f"https://xueqiu.com/{user_id}/{status_id}" if user_id and status_id else row_url
    full_text_status = str(row.get("full_text_status") or row.get("detail_fetch_status") or "")
    if not status_id:
        raise ValueError("missing Xueqiu status ID")
    if not text:
        raise ValueError(f"status {status_id} has empty text")
    if not canonical_url.startswith("https://xueqiu.com/") or canonical_url.endswith(f"/S/{symbol}"):
        raise ValueError(f"status {status_id} has no canonical post URL")
    if full_text_status not in {"full_text_observed", "api_full_text_observed"}:
        raise ValueError(f"status {status_id} full text is not confirmed")
    content_hash = hashlib.sha256(f"{canonical_url}:{text}".encode()).hexdigest()
    observation_id = f"obs_xq_targeted_{content_hash[:16]}"

    engagement_metrics = {"comments": reply_count, "likes": fav_count, "retweets": retweet_count}

    return {
        "object_type": "SourceObservation",
        "observation_id": observation_id,
        "source": "xueqiu_targeted",
        "source_label": f"雪球定向:{stock_name}",
        "source_url": canonical_url,
        "canonical_url": canonical_url,
        "author": screen_name or "unknown",
        "published_at": published_at,
        "published_at_status": "observed" if published_at else "missing_or_invalid",
        "fetched_at": _utc_now(),
        "copy_text": text,
        "topic_or_hook": f"{stock_name} 讨论",
        "image_refs": [{"url": u, "original_image_ref": u, "evidence_eligible": True} for u in image_urls],
        "image_status": "available" if image_urls else "no_image",
        "engagement_snapshot": {
            "status": "observed_at_fetch",
            "metrics": engagement_metrics,
            "metrics_are_fixture": False,
        },
        "content_hash": content_hash,
        "connector_identity": {
            "connector_id": "direct_cli.xueqiu_targeted.v1",
            "tool_id": "opencli-xueqiu-comments",
            "tool_version": "0.1.0",
        },
        "fetch_status": "manual_smoke_success",
        "structured_error": None,
        "target_symbol": symbol,
        "target_symbols": [symbol],
        "target_stock_name": stock_name,
        "target_theme_refs": themes or [],
        "source_status_id": status_id or None,
        "xueqiu_status_id": status_id,
        "full_text_status": full_text_status,
    }


def deduplicate_observations(observations: list[dict]) -> list[dict]:
    """Deduplicate posts globally while preserving every matched target reference."""
    merged: dict[str, dict] = {}
    order: list[str] = []
    for observation in observations:
        key = str(
            observation.get("source_status_id")
            or observation.get("canonical_url")
            or observation.get("source_url")
            or observation.get("content_hash")
            or observation.get("observation_id")
        )
        if key not in merged:
            merged[key] = observation
            order.append(key)
            continue
        current = merged[key]
        symbols = list(dict.fromkeys([
            *(current.get("target_symbols") or [current.get("target_symbol")]),
            *(observation.get("target_symbols") or [observation.get("target_symbol")]),
        ]))
        current["target_symbols"] = [symbol for symbol in symbols if symbol]
        themes = list(dict.fromkeys([
            *(current.get("target_theme_refs") or []),
            *(observation.get("target_theme_refs") or []),
        ]))
        current["target_theme_refs"] = themes
    return [merged[key] for key in order]


def fetch_stock_discussions(
    stocks: list[dict],
    *,
    min_comments: int = 10,
    per_stock_limit: int = 20,
) -> tuple[list[dict], list[dict]]:
    """Compatibility entrypoint returning observations and structured errors."""
    result = collect_stock_discussions(
        stocks,
        min_comments=min_comments,
        per_stock_limit=per_stock_limit,
    )
    return result["observations"], result["structured_errors"]


def collect_stock_discussions(
    stocks: list[dict],
    *,
    min_comments: int = 10,
    per_stock_limit: int = 20,
) -> dict[str, Any]:
    """Fetch all targets and return observations plus auditable collection counts."""
    all_obs: list[dict] = []
    all_errors: list[dict] = []
    attempt_warnings: list[dict] = []
    symbol_results: list[dict] = []
    raw_row_count = 0
    threshold_pass_count = 0

    for stock in stocks:
        symbol = stock.get("symbol", "")
        stock_name = stock.get("stock_name", symbol)
        themes = stock.get("theme_ids", [])
        if not symbol:
            all_errors.append({"error_code": "target_symbol_missing", "message": "Target stock has no normalized symbol"})
            symbol_results.append({"symbol": None, "status": "failed", "error_code": "target_symbol_missing"})
            continue
        symbol_error_start = len(all_errors)

        prefer_headless = os.environ.get("NEWS_HARNESS_XUEQIU_HEADLESS") == "1"
        if prefer_headless:
            rows, errors = _fetch_via_headless(symbol, per_stock_limit, min_comments)
        else:
            rows, errors = _fetch_via_opencli(symbol, per_stock_limit)
        if not rows and errors:
            primary_errors = list(errors)
            if prefer_headless:
                rows, fallback_errors = _fetch_via_opencli(symbol, per_stock_limit)
                backend = "opencli"
            else:
                rows, fallback_errors = _fetch_via_headless(symbol, per_stock_limit, min_comments)
                backend = "headless"
            if rows:
                attempt_warnings.extend([
                    {**error, "symbol": symbol, "severity": "warning", "fallback_used": backend}
                    for error in primary_errors
                ])
                errors = fallback_errors
            else:
                errors = primary_errors + [dict(e, backend=backend) for e in fallback_errors]
        all_errors.extend([dict(e, symbol=symbol) for e in errors])

        collection_meta = next((row.get("__collection_meta__") for row in rows if isinstance(row, dict) and row.get("__collection_meta__")), None)
        rows = [row for row in rows if not (isinstance(row, dict) and row.get("__collection_meta__"))]
        raw_row_count += int(collection_meta.get("raw_row_count", len(rows))) if isinstance(collection_meta, dict) else len(rows)
        filtered = apply_comment_filter(rows, min_comments=min_comments)
        threshold_pass_count += int(collection_meta.get("threshold_pass_count", len(filtered))) if isinstance(collection_meta, dict) else len(filtered)
        before_symbol = len(all_obs)
        for row in filtered:
            try:
                obs = map_discussion_row_to_observation(row, symbol=symbol, stock_name=stock_name, themes=themes)
            except ValueError as exc:
                all_errors.append({"error_code": "xueqiu_row_incomplete", "message": str(exc), "symbol": symbol})
                continue
            all_obs.append(obs)
        symbol_results.append({
            "symbol": symbol,
            "status": "ok" if len(all_errors) == symbol_error_start else "failed",
            "raw_row_count": int(collection_meta.get("raw_row_count", len(rows))) if isinstance(collection_meta, dict) else len(rows),
            "threshold_pass_count": len(filtered),
            "observation_count": len(all_obs) - before_symbol,
            "error_count": len(all_errors) - symbol_error_start,
        })
        time.sleep(0.5)

    valid_observation_count = len(all_obs)
    observations = deduplicate_observations(all_obs)
    return {
        "observations": observations,
        "structured_errors": all_errors,
        "raw_row_count": raw_row_count,
        "threshold_pass_count": threshold_pass_count,
        "deduplicated_count": len(observations),
        "rejected_incomplete_count": max(0, threshold_pass_count - valid_observation_count),
        "duplicate_count": valid_observation_count - len(observations),
        "symbol_results": symbol_results,
        "attempt_warnings": attempt_warnings,
    }


def _fetch_via_opencli(symbol: str, limit: int) -> tuple[list[dict], list[dict]]:
    """Fetch stock discussions using opencli xueqiu comments command."""
    try:
        result = subprocess.run(
            ["opencli", "xueqiu", "comments", symbol, "--limit", str(limit), "-f", "json"],
            capture_output=True, text=True, timeout=OPENCLI_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        return [], [{"error_code": "opencli_not_found", "message": "opencli command not found"}]
    except subprocess.TimeoutExpired:
        return [], [{"error_code": "opencli_timeout", "message": f"Timed out after {OPENCLI_TIMEOUT_SECONDS}s"}]

    if result.returncode != 0:
        return [], [{"error_code": "opencli_error", "message": result.stderr[:300]}]

    import json
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [], [{"error_code": "opencli_parse_failed", "message": "Cannot parse stdout as JSON"}]

    if isinstance(parsed, dict) and "rows" in parsed:
        rows = parsed["rows"]
    elif isinstance(parsed, list):
        rows = parsed
    elif isinstance(parsed, dict) and "data" in parsed and isinstance(parsed["data"], list):
        rows = parsed["data"]
    else:
        return [], [{"error_code": "opencli_contract_drift", "message": "Expected list, rows, or data array in OpenCLI JSON"}]

    normalized = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        normalized.append({
            "id": r.get("id") or "",
            "user_id": "",
            "text": r.get("text") or "",
            "created_at": r.get("created_at"),
            "reply_count": r.get("replies") or r.get("reply_count") or 0,
            "fav_count": r.get("likes") or r.get("fav_count") or 0,
            "retweet_count": r.get("retweets") or 0,
            "author": r.get("author") or "",
            "url": r.get("url") or "",
            "images": r.get("images") or r.get("image_refs") or [],
            "detail_fetch_status": r.get("detail_fetch_status") or "",
            "full_text_status": r.get("full_text_status") or "",
        })
    confirmed = [
        row for row in normalized
        if row.get("id") and row.get("url") and row.get("text")
        and row.get("full_text_status") in {"full_text_observed", "api_full_text_observed"}
    ]
    dropped = len(normalized) - len(confirmed)
    errors = ([{
        "error_code": "opencli_detail_unconfirmed",
        "message": f"{dropped} OpenCLI rows lacked confirmed full text or canonical URL",
    }] if dropped else [])
    if confirmed:
        confirmed.append({"__collection_meta__": {"raw_row_count": len(normalized)}})
    return confirmed, errors


def merge_stock_results(results: list[tuple[list, list]]) -> tuple[list, list]:
    """Merge multiple (observations, errors) tuples into single lists."""
    merged_obs: list = []
    merged_errs: list = []
    for obs_list, err_list in results:
        merged_obs.extend(obs_list)
        merged_errs.extend(err_list)
    return merged_obs, merged_errs


def _fetch_via_headless(symbol: str, limit: int, min_comments: int = 10) -> tuple[list[dict], list[dict]]:
    """Fetch stock discussions via the Playwright headless fallback script."""
    import json
    from .fixtures import ROOT
    script = ROOT / "scripts" / "xueqiu_targeted_export.mjs"
    if not script.exists():
        return [], [{"error_code": "headless_script_missing", "message": str(script)}]

    export_dir = Path(os.environ.get("NEWS_HARNESS_XUEQIU_EXPORT_DIR", "/tmp/news-harness-secrets"))
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return [], [{"error_code": "headless_export_dir_unwritable", "message": str(export_dir)}]

    out_path = export_dir / f"xueqiu_targeted_{symbol}.json"
    node = shutil.which("node")
    if not node:
        return [], [{"error_code": "node_not_found", "message": "Node.js is required for headless fallback"}]

    args = [
        node, str(script), "--symbol", symbol, "--limit", str(limit),
        "--min-comments", str(min_comments), "--out", str(out_path),
    ]
    storage_state = os.environ.get("NEWS_HARNESS_XUEQIU_STORAGE_STATE_FILE")
    if storage_state:
        storage_path = Path(storage_state).resolve()
        root = Path(__file__).resolve().parent.parent
        if storage_path == root or root in storage_path.parents:
            return [], [{"error_code": "storage_state_inside_repo", "message": "Xueqiu storage state must stay outside the repository"}]
        if not storage_path.is_file():
            return [], [{"error_code": "storage_state_unreadable", "message": str(storage_path)}]
        args.extend(["--storage-state", storage_state])

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return [], [{"error_code": "headless_timeout", "message": "Timed out after 120s"}]

    if result.returncode != 0:
        stdout = result.stdout.strip()
        error_code = "headless_error"
        message = result.stderr[:300] or stdout[:300]
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict) and parsed.get("code"):
                error_code = parsed["code"]
                message = parsed.get("message", "")
        except (json.JSONDecodeError, ValueError):
            pass
        return [], [{"error_code": error_code, "message": message}]

    try:
        export = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], [{"error_code": "headless_parse_failed", "message": f"Cannot read {out_path}"}]

    raw_rows = export.get("rows") or []
    if isinstance(export.get("collection"), dict):
        raw_rows.append({"__collection_meta__": export["collection"]})
    return raw_rows, []
