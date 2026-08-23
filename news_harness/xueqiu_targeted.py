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

    canonical_url = f"https://xueqiu.com/{user_id}/{status_id}" if user_id and status_id else ""
    content_hash = hashlib.sha256(f"{canonical_url}:{text}".encode()).hexdigest()
    observation_id = f"obs_xq_targeted_{content_hash[:16]}"

    engagement_metrics = {"comments": reply_count, "likes": fav_count, "retweets": retweet_count}

    return {
        "object_type": "SourceObservation",
        "observation_id": observation_id,
        "source": "xueqiu_targeted",
        "source_label": f"雪球定向:{stock_name}",
        "source_url": canonical_url or f"https://xueqiu.com/S/{symbol}",
        "canonical_url": canonical_url,
        "author": screen_name or "unknown",
        "published_at": published_at or _utc_now(),
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
        "target_stock_name": stock_name,
        "target_theme_refs": themes or [],
    }


def fetch_stock_discussions(
    stocks: list[dict],
    *,
    min_comments: int = 10,
    per_stock_limit: int = 20,
) -> tuple[list[dict], list[dict]]:
    """Main entrypoint: fetch discussions for all target stocks via opencli."""
    all_obs: list[dict] = []
    all_errors: list[dict] = []

    for stock in stocks:
        symbol = stock.get("symbol", "")
        stock_name = stock.get("stock_name", symbol)
        themes = stock.get("theme_ids", [])
        if not symbol:
            continue

        rows, errors = _fetch_via_opencli(symbol, per_stock_limit)
        if not rows and errors:
            # OpenCLI failed; try headless fallback before giving up.
            rows, headless_errors = _fetch_via_headless(symbol, per_stock_limit)
            errors = errors + [dict(e, backend="headless") for e in headless_errors] if not rows else headless_errors
        all_errors.extend([dict(e, symbol=symbol) for e in errors])

        filtered = apply_comment_filter(rows, min_comments=min_comments)
        for row in filtered:
            obs = map_discussion_row_to_observation(row, symbol=symbol, stock_name=stock_name, themes=themes)
            all_obs.append(obs)
        time.sleep(0.5)

    return all_obs, all_errors


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
        rows = []

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
        })
    return normalized, []


def merge_stock_results(results: list[tuple[list, list]]) -> tuple[list, list]:
    """Merge multiple (observations, errors) tuples into single lists."""
    merged_obs: list = []
    merged_errs: list = []
    for obs_list, err_list in results:
        merged_obs.extend(obs_list)
        merged_errs.extend(err_list)
    return merged_obs, merged_errs


def _fetch_via_headless(symbol: str, limit: int) -> tuple[list[dict], list[dict]]:
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

    args = [node, str(script), "--symbol", symbol, "--limit", str(limit), "--out", str(out_path)]
    storage_state = os.environ.get("NEWS_HARNESS_XUEQIU_STORAGE_STATE_FILE")
    if storage_state:
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
    return raw_rows, []
