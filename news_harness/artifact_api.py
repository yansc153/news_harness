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


ALLOWED_MCP_KEYS: set[str] = {
    "object_type", "id", "source", "published_at", "copy_text", "source_url", "image_refs",
}

ALLOWED_MCP_IMAGE_REF_KEYS: set[str] = {
    "url", "original_image_ref", "thumbnail_ref", "alt", "description", "width", "height",
}


FORBIDDEN_MCP_KEYS: set[str] = {
    "radar_score", "hotness_score", "hotness_series", "confidence",
    "rule_ids", "structure_tags", "outcome_labels", "learning_eligibility",
    "eval_status", "promotion_status", "revisit_status",
    "artifact_refs", "non_investment_advice",
}


def _mcp_image_refs(item: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in _image_refs(item):
        clean = {key: value for key, value in ref.items() if key in ALLOWED_MCP_IMAGE_REF_KEYS and value}
        for key in ("url", "original_image_ref", "thumbnail_ref"):
            if key in clean:
                clean[key] = public_url(clean[key])
        clean = {key: value for key, value in clean.items() if value}
        if any(key in clean for key in ("url", "original_image_ref", "thumbnail_ref")):
            refs.append(clean)
    return refs


def validate_mcp_export(item: dict[str, Any]) -> list[str]:
    """Return list of forbidden keys found in an MCP export dict (empty = clean)."""
    problems = [f"forbidden:{key}" for key in FORBIDDEN_MCP_KEYS if key in item]
    problems.extend(f"unexpected:{key}" for key in item if key not in ALLOWED_MCP_KEYS)
    for index, ref in enumerate(item.get("image_refs") or []):
        if not isinstance(ref, dict):
            problems.append(f"image_refs[{index}]:not_object")
            continue
        problems.extend(f"image_refs[{index}].unexpected:{key}" for key in ref if key not in ALLOWED_MCP_IMAGE_REF_KEYS)
    return problems


def project_item_export(item: dict[str, Any]) -> dict[str, Any]:
    """Return the public export read model: only copy, source URL, and image refs."""
    source_url = public_url(item.get("source_url") or item.get("canonical_url"))
    return {
        "object_type": "McpExportItem",
        "id": item.get("id"),
        "source": item.get("source"),
        "published_at": item.get("published_at"),
        "copy_text": item.get("copy_text") or "",
        "source_url": source_url,
        "image_refs": _mcp_image_refs(item),
    }


def project_item_web(item: dict[str, Any], *, include_private_refs: bool = False) -> dict[str, Any]:
    """Return the web dashboard read model — includes scores, sparklines, and status fields."""

    source_url = public_url(item.get("source_url") or item.get("canonical_url"))
    projected = {
        "id": item.get("id"),
        "source": item.get("source"),
        "source_label": item.get("source_label") or item.get("source"),
        "source_group": item.get("source_group") or "",
        "author": item.get("author"),
        "display_name": item.get("display_name") or "",
        "handle": item.get("handle") or "",
        "avatar_url": public_url(item.get("avatar_url")),
        "published_at": item.get("published_at"),
        "title": item.get("topic_or_hook") or "",
        "topic_or_hook": item.get("topic_or_hook") or "",
        "copy_text": item.get("copy_text") or "",
        "source_url": source_url,
        "original_image_ref": public_url(item.get("original_image_ref")),
        "thumbnail_ref": public_url(item.get("thumbnail_ref")),
        "image_status": item.get("image_status") or item.get("image_quality_status") or "unknown",
        "image_refs": _image_refs(item),
        "engagement_snapshot": item.get("engagement_snapshot") or {},
        "source_material_role": item.get("source_material_role") or "",
        "source_quality_status": item.get("source_quality_status") or "",
        "source_quality_risk_flags": item.get("source_quality_risk_flags") or [],
        "full_text_status": item.get("full_text_status") or "",
        "detail_fetch_status": item.get("detail_fetch_status") or "",
        "article_detail_url": public_url(item.get("article_detail_url")),
        "radar_score": _score(item),
        "hotness_score": item.get("hotness_score"),
        "hotness_series": item.get("hotness_series") or [],
        "prediction_scores": item.get("prediction_scores") or {},
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
    return project_item_export(item)


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
    source_run = load_json(artifacts["source_run"]) if artifacts["source_run"].exists() else {}
    failed_sources = [
        status.get("source")
        for status in source_run.get("sources", [])
        if isinstance(status, dict) and status.get("status") != "ok"
    ] if isinstance(source_run, dict) else []
    feed_status = "demo" if is_demo_feed(feed, str(feed_path)) else "live"
    return {
        "object_type": "NewsHarnessWebsiteHealth",
        "status": "ok" if not missing and feed_status == "live" and not failed_sources else "degraded",
        "feed_status": feed_status,
        "feed_path": str(feed_path),
        "generated_at": feed.get("generated_at"),
        "item_count": len(feed.get("items", [])),
        "missing_artifacts": missing,
        "failed_sources": failed_sources,
        "artifacts": {name: str(path) for name, path in artifacts.items()},
    }
