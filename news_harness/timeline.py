"""Build the product-facing radar timeline feed from fixture artifacts."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from .events import canonical_json
from .fixtures import DEFAULT_SCHEMA, ROOT, load_fixture_set
from .manual_smoke import load_manual_timeline_items, write_manual_timeline_store, TIMELINE_FEED_ARTIFACT, _utc_now
from .paths import safe_output_path, write_json_artifact


TIMELINE_FEED_VERSION = "radar.timeline.feed.v1"
TIMELINE_ITEM_VERSION = "radar.timeline.item.v1"
IMAGE_STATUSES = {"available", "no_image", "image_unavailable"}
SHADOW_SOURCE_FIXTURE = "sample_shadow_source_fetch_result.json"
ROLLING_SOURCE_SCHEDULE_FIXTURE = "rolling_source_schedule.json"
TIMELINE_STORE_FIXTURE = "timeline_store.json"
REVISIT_SCHEDULE_FIXTURE = "revisit_schedule.json"
DEFAULT_MANUAL_TIMELINE_MAX_ITEMS = 1000


def _write_json(path: Path, data: Any) -> None:
    write_json_artifact(path, data)


def _manual_timeline_max_items() -> int:
    value = os.environ.get("NEWS_HARNESS_TIMELINE_MAX_ITEMS")
    if not value:
        return DEFAULT_MANUAL_TIMELINE_MAX_ITEMS
    try:
        parsed = int(value)
    except ValueError:
        return DEFAULT_MANUAL_TIMELINE_MAX_ITEMS
    return max(1, parsed)


def _timeline_item_key(item: dict[str, Any]) -> str:
    for field in ("source_url", "canonical_url", "article_detail_url", "evidence_ref", "id"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return f"{field}:{value.strip()}"
    return f"id:{item.get('id', '')}"


def _timeline_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    try:
        hotness = float(item.get("hotness_score", 0) or 0)
    except (TypeError, ValueError):
        hotness = 0.0
    published_at = item.get("published_at")
    return hotness, published_at if isinstance(published_at, str) else ""


def _blocked_timeline_text(item: dict[str, Any]) -> bool:
    parts = [
        str(item.get("copy_text") or ""),
        str(item.get("topic_or_hook") or ""),
        str(item.get("title") or ""),
        str(item.get("source_quality") or ""),
        str(item.get("detail_fetch_status") or ""),
    ]
    for image in item.get("image_refs", []) if isinstance(item.get("image_refs"), list) else []:
        if isinstance(image, dict):
            parts.append(str(image.get("alt") or ""))
            parts.append(str(image.get("caption") or ""))
    text = " ".join(parts).lower()
    return any(
        marker in text
        for marker in (
            "access verification",
            "slide to complete",
            "slide to verify",
            "traceid",
            "captcha",
            "auth_or_challenge_required",
            "验证码",
            "访问验证",
            "安全验证",
            "滑动验证",
        )
    )


def _xueqiu_item_not_full_text(item: dict[str, Any]) -> bool:
    source = " ".join(str(item.get(field) or "") for field in ("source", "source_label")).lower()
    if "xueqiu" not in source and "雪球" not in source:
        return False
    full_text_status = str(item.get("full_text_status") or "").strip()
    detail_fetch_status = str(item.get("detail_fetch_status") or "").strip()
    source_quality = str(item.get("source_quality") or "").strip()
    if full_text_status and full_text_status != "full_text_observed":
        return True
    if source_quality in {"summary_or_list_excerpt_only", "detail_attempt_incomplete"}:
        return True
    if detail_fetch_status and detail_fetch_status not in {"full_text_observed", "api_full_text_observed"}:
        return True
    text = " ".join(str(item.get(field) or "") for field in ("copy_text", "topic_or_hook", "title"))
    return bool(re.search(r"(\.{3,}|…|展开全文|阅读全文|查看全文)\s*$", text.strip()))


def _load_timeline_feed_items(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as handle:
            feed = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(feed, dict) or not isinstance(feed.get("items"), list):
        return []
    return [item for item in feed["items"] if isinstance(item, dict)]


def merge_manual_timeline_items(
    current_items: list[dict[str, Any]],
    prior_items: list[dict[str, Any]],
    *,
    max_items: int | None = None,
) -> list[dict[str, Any]]:
    """Merge the latest cycle into the rolling live feed, replacing duplicates."""

    merged: dict[str, dict[str, Any]] = {}
    for item in [*prior_items, *current_items]:
        if isinstance(item, dict) and not _blocked_timeline_text(item) and not _xueqiu_item_not_full_text(item):
            merged[_timeline_item_key(item)] = item
    sorted_items = sorted(merged.values(), key=_timeline_sort_key, reverse=True)
    limit = max_items if max_items is not None else _manual_timeline_max_items()
    return sorted_items[: max(1, limit)]


def _base_fixture_item(fixtures: dict[str, Any]) -> dict[str, Any]:
    evidence = fixtures["sample_evidence.json"]
    structure = fixtures["sample_structure.json"]
    prediction = fixtures["sample_prediction.json"]
    first_image_ref = evidence.get("image_refs", [])[0]

    return {
        "object_type": "RadarTimelineItem",
        "item_version": TIMELINE_ITEM_VERSION,
        "id": "radar_timeline_fixture_001",
        "source": "fixture_source",
        "source_label": "Fixture Source",
        "source_url": evidence["source_url"],
        "author": evidence["author"],
        "published_at": evidence["published_at"],
        "copy_text": "The discussion is accelerating around a new policy interpretation.",
        "topic_or_hook": structure["hook"],
        "image_refs": [
            {
                "image_ref_id": first_image_ref["image_ref_id"],
                "original_image_ref": first_image_ref["original_image_ref"],
                "thumbnail_ref": first_image_ref["thumbnail_ref"],
                "page_context_ref": first_image_ref["page_context_ref"],
                "dimensions": first_image_ref["dimensions"],
                "access_status": first_image_ref["access_status"],
                "download_status": "not_downloaded_reference_only",
            }
        ],
        "image_status": "available",
        "hotness_score": prediction["24h_score"],
        "hotness_series": [0.18, 0.22, 0.31, 0.39, 0.52, 0.63, prediction["24h_score"]],
        "timeline_status": "ranked_fixture",
        "prediction_status": "fixture_prediction_not_ground_truth",
        "outcome_status": "fixture_outcome_not_ground_truth",
        "non_investment_advice": True,
        "evidence_ref": "fixtures/sample_evidence.json",
    }


def _shadow_timeline_items(fixtures: dict[str, Any]) -> list[dict[str, Any]]:
    shadow = fixtures.get(SHADOW_SOURCE_FIXTURE)
    if not isinstance(shadow, dict):
        return []

    items: list[dict[str, Any]] = []
    for index, shadow_item in enumerate(shadow.get("items", []), start=1):
        projection = shadow_item.get("timeline_projection", {})
        image_status = projection.get("image_status", "no_image")
        image_refs = shadow_item.get("image_refs", [])
        item_id = str(shadow_item["shadow_item_id"]).replace("shadow_", "radar_shadow_", 1)
        items.append(
            {
                "object_type": "RadarTimelineItem",
                "item_version": TIMELINE_ITEM_VERSION,
                "id": item_id,
                "source": shadow_item["source"],
                "source_label": shadow_item["source_label"],
                "source_url": shadow_item["source_url"],
                "author": shadow_item["author"],
                "published_at": shadow_item["published_at"],
                "copy_text": projection["copy_text"],
                "topic_or_hook": projection["topic_or_hook"],
                "image_refs": image_refs if image_status != "no_image" else [],
                "image_status": image_status,
                "hotness_score": projection["hotness_score"],
                "hotness_series": projection["hotness_series"],
                "timeline_status": projection["timeline_status"],
                "prediction_status": projection["prediction_status"],
                "outcome_status": projection["outcome_status"],
                "non_investment_advice": shadow.get("projection_contract", {}).get("non_investment_advice") is True,
                "evidence_ref": f"fixtures/{SHADOW_SOURCE_FIXTURE}#items[{index - 1}]",
                "shadow_source_item_id": shadow_item["shadow_item_id"],
                "source_channel": shadow_item["source_channel"],
            }
        )
    return items


def _rolling_timeline_items(store: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in store.get("items", []):
        if item.get("expired") is True:
            continue
        exported = dict(item)
        exported.pop("expired", None)
        items.append(exported)
    return items


def _rolling_source_refs() -> list[str]:
    return [
        "fixtures/sample_evidence.json",
        "fixtures/sample_candidate.json",
        "fixtures/sample_structure.json",
        "fixtures/sample_prediction.json",
        "fixtures/sample_outcome.json",
        f"fixtures/{SHADOW_SOURCE_FIXTURE}",
        "fixtures/sample_x_list_secret_ref_dry_run.json",
        "fixtures/sample_all_source_runner_dry_run.json",
        "fixtures/sample_deepseek_scoring_fixture.json",
        f"fixtures/{ROLLING_SOURCE_SCHEDULE_FIXTURE}",
        f"fixtures/{TIMELINE_STORE_FIXTURE}",
        f"fixtures/{REVISIT_SCHEDULE_FIXTURE}",
    ]


def build_rolling_timeline_feed(fixtures: dict[str, Any]) -> dict[str, Any]:
    store = fixtures[TIMELINE_STORE_FIXTURE]
    schedule = fixtures[ROLLING_SOURCE_SCHEDULE_FIXTURE]
    revisit = fixtures[REVISIT_SCHEDULE_FIXTURE]
    retention = store.get("retention_policy", {})
    items = _rolling_timeline_items(store)
    items = sorted(items, key=lambda item: (item["hotness_score"], item["published_at"]), reverse=True)

    return {
        "object_type": "RadarTimelineFeed",
        "feed_id": "radar_timeline_feed_fixture_001",
        "feed_version": TIMELINE_FEED_VERSION,
        "generated_at": store["updated_at"],
        "fixture_only": True,
        "no_real_source_access": True,
        "source_refs": _rolling_source_refs(),
        "sorting_policy": {
            "default_sort": "hotness",
            "sort_by": ["hotness_score_desc", "published_at_desc"],
            "supported_sorts": ["hotness", "published_at"],
            "hotness_series_units": "relative_fixture_score_0_to_1",
            "outcome_ground_truth": "not_ground_truth_fixture_only",
        },
        "display_contract": {
            "first_screen_fields": ["source_label", "copy_text", "image_refs", "hotness_score", "hotness_series"],
            "hidden_audit_fields": [
                "evidence_ref",
                "prediction_status",
                "outcome_status",
                "revisit_status",
                "non_investment_advice",
            ],
        },
        "rolling_runtime": {
            "runtime_stage": "fixture_only_rolling_feed",
            "exported_from_store_ref": f"fixtures/{TIMELINE_STORE_FIXTURE}",
            "source_schedule_ref": f"fixtures/{ROLLING_SOURCE_SCHEDULE_FIXTURE}",
            "revisit_schedule_ref": f"fixtures/{REVISIT_SCHEDULE_FIXTURE}",
            "shadow_source_ref": f"fixtures/{SHADOW_SOURCE_FIXTURE}",
            "retention_window_hours": retention.get("export_window_hours", 120),
            "expired_items_excluded": retention.get("expired_items_exported") is False,
            "active_item_count": len(items),
            "store_item_count": len(store.get("items", [])),
            "expired_store_item_count": sum(1 for item in store.get("items", []) if item.get("expired") is True),
            "duplicate_update_count": len(store.get("duplicate_update_log", [])),
            "revisit_task_count": len(revisit.get("tasks", [])),
            "source_refresh_intervals": {
                source["source"]: source["refresh_interval_seconds"]
                for source in schedule.get("sources", [])
                if isinstance(source, dict) and "source" in source
            },
        },
        "view_config": {
            "default_recent_hours": retention.get("export_window_hours", 120),
            "supported_recent_hours": [12, 24, 48, 72, 120],
            "default_sort": "hotness",
            "supported_sorts": [
                {"id": "hotness", "field": "hotness_score", "direction": "desc"},
                {"id": "published_at", "field": "published_at", "direction": "desc"},
            ],
        },
        "auto_refresh": {
            "enabled": True,
            "poll_interval_seconds": 60,
            "cache_mode": "no-store",
        },
        "items": items,
    }


def build_timeline_feed(fixtures: dict[str, Any]) -> dict[str, Any]:
    """Project evidence, structure, prediction, and outcome fixtures into UI feed items."""

    if TIMELINE_STORE_FIXTURE in fixtures:
        return build_rolling_timeline_feed(fixtures)

    items = [_base_fixture_item(fixtures), *_shadow_timeline_items(fixtures)]
    items = sorted(items, key=lambda item: (item["hotness_score"], item["published_at"]), reverse=True)

    return {
        "object_type": "RadarTimelineFeed",
        "feed_id": "radar_timeline_feed_fixture_001",
        "feed_version": TIMELINE_FEED_VERSION,
        "generated_at": "2026-06-15T00:18:00Z",
        "fixture_only": True,
        "no_real_source_access": True,
        "source_refs": [
            "fixtures/sample_evidence.json",
            "fixtures/sample_candidate.json",
            "fixtures/sample_structure.json",
            "fixtures/sample_prediction.json",
            "fixtures/sample_outcome.json",
            f"fixtures/{SHADOW_SOURCE_FIXTURE}",
        ],
        "sorting_policy": {
            "sort_by": ["hotness_score_desc", "published_at_desc"],
            "hotness_series_units": "relative_fixture_score_0_to_1",
            "outcome_ground_truth": "not_ground_truth_fixture_only",
        },
        "display_contract": {
            "first_screen_fields": ["source_label", "copy_text", "image_refs", "hotness_score", "hotness_series"],
            "hidden_audit_fields": ["evidence_ref", "prediction_status", "outcome_status", "non_investment_advice"],
        },
        "items": items,
    }


def generate_timeline_feed(fixtures_dir: Path, out_path: Path, schema_path: Path = DEFAULT_SCHEMA) -> dict[str, Any]:
    out_path = safe_output_path(ROOT / "web", out_path)
    _, fixtures = load_fixture_set(fixtures_dir, schema_path)
    feed = build_timeline_feed(fixtures)
    manual_items, manual_metadata = load_manual_timeline_items()
    if manual_items or manual_metadata:
        feed["feed_id"] = "radar_timeline_manual_smoke_latest"
        feed["fixture_only"] = False
        feed["no_real_source_access"] = False
        feed["generated_at"] = _utc_now()
        feed["manual_smoke"] = {
            "enabled": True,
            "production_connector_ready": False,
            **manual_metadata,
        }
        feed["source_refs"].extend(
            ref
            for ref in [
                manual_metadata.get("source_run_ref"),
                manual_metadata.get("scoring_ref"),
                manual_metadata.get("image_asset_ref"),
                manual_metadata.get("revisit", {}).get("schedule_ref") if isinstance(manual_metadata.get("revisit"), dict) else None,
                manual_metadata.get("revisit", {}).get("outcome_ref") if isinstance(manual_metadata.get("revisit"), dict) else None,
                manual_metadata.get("direct_cli_ref"),
            ]
            if ref
        )
        fixture_item_count = len(feed["items"])
        if manual_items:
            prior_items = [
                *_load_timeline_feed_items(TIMELINE_FEED_ARTIFACT),
                *_load_timeline_feed_items(out_path),
            ]
            feed["items"] = merge_manual_timeline_items(manual_items, prior_items)
            feed["manual_smoke"]["fixture_items_hidden_from_product_feed"] = fixture_item_count
            feed["manual_smoke"]["current_cycle_item_count"] = len(manual_items)
            feed["manual_smoke"]["retained_prior_item_count"] = max(0, len(feed["items"]) - len(manual_items))
            feed["manual_smoke"]["timeline_max_items"] = _manual_timeline_max_items()
            write_manual_timeline_store(feed["items"], feed["manual_smoke"])
        else:
            feed["items"] = []
            feed["manual_smoke"]["fixture_items_hidden_from_product_feed"] = fixture_item_count
        feed["rolling_runtime"]["runtime_stage"] = "manual_smoke_live_feed"
        feed["rolling_runtime"]["active_item_count"] = len(feed["items"])
        feed["rolling_runtime"]["current_cycle_item_count"] = len(manual_items)
        feed["rolling_runtime"]["store_item_count"] = len(feed["items"])
        feed["rolling_runtime"]["max_store_item_count"] = _manual_timeline_max_items()
        _write_json(TIMELINE_FEED_ARTIFACT, feed)
    _write_json(out_path, feed)
    return {
        "status": "ok",
        "feed_id": feed["feed_id"],
        "item_count": len(feed["items"]),
        "out": str(out_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a RadarTimelineFeed artifact from local fixtures.")
    parser.add_argument("--fixtures", type=Path, required=True, help="Fixture directory")
    parser.add_argument("--out", type=Path, required=True, help="Output timeline_feed.json path")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="Schema contract file")
    args = parser.parse_args(argv)

    result = generate_timeline_feed(args.fixtures, args.out, args.schema)
    print(canonical_json(result))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
