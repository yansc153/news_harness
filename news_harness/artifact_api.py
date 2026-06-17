"""Read-only website/API projection over News Harness artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fixtures import ROOT


DEFAULT_FEED = ROOT / "web" / "radar-timeline" / "timeline_feed.json"
DEFAULT_ARTIFACT_DIR = ROOT / "artifacts" / "manual_smoke" / "latest"


class ArtifactReadError(ValueError):
    """Raised when a requested read-only artifact is missing or malformed."""


def load_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise ArtifactReadError(f"artifact_not_found:{path}") from exc
    except json.JSONDecodeError as exc:
        raise ArtifactReadError(f"artifact_invalid_json:{path}") from exc


def load_feed(feed_path: Path = DEFAULT_FEED) -> dict[str, Any]:
    feed = load_json(feed_path)
    if not isinstance(feed, dict) or not isinstance(feed.get("items"), list):
        raise ArtifactReadError(f"timeline_feed_invalid:{feed_path}")
    return feed


def is_demo_feed(feed: dict[str, Any], loaded_from: str = "") -> bool:
    smoke = feed.get("manual_smoke") if isinstance(feed.get("manual_smoke"), dict) else {}
    scoring = smoke.get("scoring") if isinstance(smoke.get("scoring"), dict) else {}
    runtime = feed.get("rolling_runtime") if isinstance(feed.get("rolling_runtime"), dict) else {}
    runtime_stage = str(runtime.get("runtime_stage", ""))
    return bool(
        feed.get("fixture_only")
        or feed.get("no_real_source_access")
        or "fixture" in loaded_from
        or "embedded" in loaded_from
        or smoke.get("backend") == "fixture"
        or scoring.get("fallback_used") == "fixture_scoring"
        or "fixture" in runtime_stage
    )


def public_url(value: Any) -> str:
    url = str(value or "")
    if url.startswith(("http://", "https://")):
        return url
    return ""


def _score(item: dict[str, Any]) -> int:
    value = item.get("radar_score", item.get("hotness_score", 0))
    try:
        return round(float(value) * 100)
    except (TypeError, ValueError):
        return 0


def _image_refs(item: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in item.get("image_refs") or []:
        if isinstance(ref, dict):
            refs.append(ref)
    for asset in item.get("asset_refs") or []:
        if isinstance(asset, dict):
            refs.append(
                {
                    "asset_ref": asset.get("asset_ref"),
                    "dimensions": asset.get("dimensions"),
                    "byte_size": asset.get("byte_size"),
                    "source": "downloaded_artifact",
                }
            )
    return refs


FORBIDDEN_MCP_KEYS: set[str] = {
    "radar_score", "hotness_score", "hotness_series", "confidence",
    "rule_ids", "structure_tags", "outcome_labels", "learning_eligibility",
    "eval_status", "promotion_status", "revisit_status",
    "artifact_refs", "non_investment_advice",
}


def validate_mcp_export(item: dict[str, Any]) -> list[str]:
    """Return list of forbidden keys found in an MCP export dict (empty = clean)."""
    return [k for k in FORBIDDEN_MCP_KEYS if k in item]


def project_item_web(item: dict[str, Any], *, include_private_refs: bool = False) -> dict[str, Any]:
    """Return the web dashboard read model — includes scores, sparklines, and status fields."""

    source_url = public_url(item.get("source_url") or item.get("canonical_url"))
    projected = {
        "id": item.get("id"),
        "source": item.get("source"),
        "source_label": item.get("source_label") or item.get("source"),
        "author": item.get("author"),
        "published_at": item.get("published_at"),
        "title": item.get("topic_or_hook") or "",
        "copy_text": item.get("copy_text") or "",
        "source_url": source_url,
        "image_status": item.get("image_status") or item.get("image_quality_status") or "unknown",
        "image_refs": _image_refs(item),
        "radar_score": _score(item),
        "hotness_score": item.get("hotness_score"),
        "hotness_series": item.get("hotness_series") or [],
        "revisit_status": item.get("revisit_status") or item.get("outcome_status") or "pending",
        "eval_status": item.get("eval_status") or "pending",
        "non_investment_advice": item.get("non_investment_advice") is True,
        "public_url_available": bool(source_url),
    }
    if include_private_refs:
        projected["artifact_refs"] = {
            "evidence_ref": item.get("evidence_ref"),
            "content_hash": item.get("content_hash"),
            "revisit_task_refs": item.get("revisit_task_refs") or [],
        }
    return projected


def project_item_mcp(item: dict[str, Any]) -> dict[str, Any]:
    """Return the MCP export read model — evidence/read fields only, no scores/status/refs."""
    source_url = public_url(item.get("source_url") or item.get("canonical_url"))
    return {
        "id": item.get("id"),
        "source": item.get("source"),
        "source_label": item.get("source_label") or item.get("source"),
        "author": item.get("author"),
        "published_at": item.get("published_at"),
        "title": item.get("topic_or_hook") or "",
        "copy_text": item.get("copy_text") or "",
        "source_url": source_url,
        "canonical_url": item.get("canonical_url") or "",
        "image_refs": _image_refs(item),
        "image_status": item.get("image_status") or item.get("image_quality_status") or "unknown",
        "evidence_status": item.get("evidence_status") or "",
        "public_url_available": bool(source_url),
    }


# backward-compat alias
project_item = project_item_web


def _proj(projection: str, item: dict[str, Any], *, include_private_refs: bool = False) -> dict[str, Any]:
    if projection == "mcp":
        return project_item_mcp(item)
    return project_item_web(item, include_private_refs=include_private_refs)


def latest_feed(feed_path: Path = DEFAULT_FEED, *, include_private_refs: bool = False, projection: str = "web") -> dict[str, Any]:
    feed = load_feed(feed_path)
    items = [_proj(projection, item, include_private_refs=include_private_refs) for item in feed.get("items", []) if isinstance(item, dict)]
    return {
        "object_type": "NewsHarnessLatestFeed",
        "feed_id": feed.get("feed_id"),
        "feed_version": feed.get("feed_version"),
        "generated_at": feed.get("generated_at"),
        "status": "demo" if is_demo_feed(feed, str(feed_path)) else "live",
        "item_count": len(items),
        "items": items,
    }


def list_items(feed_path: Path = DEFAULT_FEED, *, limit: int = 50, source: str | None = None, projection: str = "web") -> dict[str, Any]:
    feed = latest_feed(feed_path, projection=projection)
    items = feed["items"]
    if source:
        items = [item for item in items if item.get("source") == source or item.get("source_label") == source]
    return {**feed, "items": items[: max(0, limit)], "item_count": len(items[: max(0, limit)])}


def get_item(item_id: str, feed_path: Path = DEFAULT_FEED, *, include_private_refs: bool = True, projection: str = "web") -> dict[str, Any]:
    feed = load_feed(feed_path)
    for item in feed.get("items", []):
        if isinstance(item, dict) and str(item.get("id")) == item_id:
            return _proj(projection, item, include_private_refs=include_private_refs)
    raise ArtifactReadError(f"item_not_found:{item_id}")


def image_refs(item_id: str, feed_path: Path = DEFAULT_FEED, *, projection: str = "web") -> dict[str, Any]:
    item = get_item(item_id, feed_path, include_private_refs=False, projection=projection)
    return {
        "object_type": "NewsHarnessImageRefs",
        "item_id": item_id,
        "image_status": item.get("image_status"),
        "image_refs": item.get("image_refs") or [],
    }


def artifact_health(feed_path: Path = DEFAULT_FEED, artifact_dir: Path = DEFAULT_ARTIFACT_DIR) -> dict[str, Any]:
    feed = load_feed(feed_path)
    artifacts = {
        "source_run": artifact_dir / "source_run.json",
        "image_assets": artifact_dir / "image_assets.json",
        "deepseek_scoring": artifact_dir / "deepseek_scoring.json",
        "revisit_schedule": artifact_dir / "revisit_schedule.json",
        "outcome": artifact_dir / "outcome.json",
        "eval": artifact_dir / "eval.json",
        "timeline_feed": feed_path,
    }
    missing = [name for name, path in artifacts.items() if not path.exists()]
    return {
        "object_type": "NewsHarnessWebsiteHealth",
        "status": "ok" if not missing else "degraded",
        "feed_status": "demo" if is_demo_feed(feed, str(feed_path)) else "live",
        "feed_path": str(feed_path),
        "generated_at": feed.get("generated_at"),
        "item_count": len(feed.get("items", [])),
        "missing_artifacts": missing,
        "artifacts": {name: str(path) for name, path in artifacts.items()},
    }
