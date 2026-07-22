"""v2 MCP 导出（ARCHITECTURE.md §8，D-02 视频 seam，secret 永不外泄）。

只读导出：将 `ContentItem` + `ProcessedContent`（按 item_id 关联）投影为
`McpExportItem` 白名单。导出同时带原文（copy_text）与处理稿
（translated_text / llm_summary），图文优先；视频经 media_kind + video_refs
预留 seam，下游首次见到再适配，不引入二次 breaking change。

设计要点：
- 严格白名单：ALLOWED_MCP_V2_KEYS / ALLOWED_MCP_V2_IMAGE_REF_KEYS。
- 禁令牌：FORBIDDEN_MCP_V2_KEYS（secret / score / label / rulebook 内部）。
- model_ref / secret_ref 等模型标识与凭证**绝不**进入导出。
- MCP 保持只读；真正「发」走独立 publish 受控路径（不在本模块）。
"""

from __future__ import annotations

from typing import Optional

from news_harness.models import ContentItem, ProcessedContent

# v2 仅导出公开源 URL / 公开候选图片 URL（绝不导出本地路径、secret、score）
ALLOWED_MCP_V2_KEYS: set[str] = {
    "object_type", "id", "platform", "source_label", "author",
    "copy_text", "translated_text", "llm_summary",
    "source_url", "canonical_url", "image_refs", "video_refs",
    "media_kind", "evidence_status", "public_url_available",
}

ALLOWED_MCP_V2_IMAGE_REF_KEYS: set[str] = {
    "url", "dimensions", "rights_status", "mime",
}

ALLOWED_MCP_V2_VIDEO_REF_KEYS: set[str] = {
    "url", "dimensions", "rights_status", "mime", "thumbnail_ref",
}

# 任何属于这些类别的字段都不得出现在导出中
FORBIDDEN_MCP_V2_KEYS: set[str] = {
    "radar_score", "hotness_score", "hotness_series", "confidence",
    "rule_ids", "structure_tags", "outcome_labels", "learning_eligibility",
    "eval_status", "promotion_status", "revisit_status",
    "model_ref", "secret_ref", "secret", "cookie", "token", "session",
    "artifact_refs", "non_investment_advice", "user_id",
    "ingest_gate", "processing_status", "rights_status", "observed_at",
}


def _public_url(value) -> str:
    """v2 仅承载公开源 URL；此处直接透传并保持为字符串。"""
    if value is None:
        return ""
    return str(value)


def _author(item: ContentItem) -> dict:
    a = item.author
    if a is None:
        return {"name": "", "handle": "", "avatar_url": "", "follower_count": None}
    return {
        "name": a.name,
        "handle": a.handle,
        "avatar_url": _public_url(a.avatar_url),
        "follower_count": a.follower_count,
    }


def _image_refs(item: ContentItem) -> list[dict]:
    refs: list[dict] = []
    for ref in item.image_refs or []:
        clean = {
            "url": _public_url(ref.url),
            "dimensions": ref.dimensions,
            "rights_status": ref.rights_status,
            "mime": ref.mime,
        }
        clean = {k: v for k, v in clean.items() if v is not None and v != ""}
        if clean.get("url"):
            refs.append(clean)
    return refs


def _video_refs(item: ContentItem) -> list[dict]:
    refs: list[dict] = []
    for ref in item.video_refs or []:
        clean = {
            "url": _public_url(ref.url),
            "dimensions": ref.dimensions,
            "rights_status": ref.rights_status,
            "mime": ref.mime,
            "thumbnail_ref": _public_url(ref.thumbnail_ref),
        }
        clean = {k: v for k, v in clean.items() if v is not None and v != ""}
        if clean.get("url"):
            refs.append(clean)
    return refs


def build_mcp_export(
    item: ContentItem,
    processed: Optional[ProcessedContent] = None,
) -> dict:
    """ContentItem + ProcessedContent → 只读 McpExportItem（白名单）。"""
    translated = None
    summary = None
    if processed is not None:
        translated = processed.translated_text
        summary = processed.llm_summary

    export: dict = {
        "object_type": "McpExportItem",
        "id": item.id,
        "platform": item.platform,
        "source_label": item.source_label,
        "author": _author(item),
        "copy_text": item.copy_text,
        "translated_text": translated,
        "llm_summary": summary,
        "source_url": _public_url(item.source_url),
        "canonical_url": _public_url(item.canonical_url),
        "image_refs": _image_refs(item),
        "video_refs": _video_refs(item),
        "media_kind": item.media_kind,
        "evidence_status": item.evidence_status,
        "public_url_available": True,
    }
    return export


def validate_mcp_v2_export(item: dict) -> list[str]:
    """返回导出中的非法键列表（空 = 干净）。同时校验 image/video ref 键。"""
    problems: list[str] = []
    problems.extend(f"forbidden:{key}" for key in FORBIDDEN_MCP_V2_KEYS if key in item)
    problems.extend(f"unexpected:{key}" for key in item if key not in ALLOWED_MCP_V2_KEYS)
    for index, ref in enumerate(item.get("image_refs") or []):
        if not isinstance(ref, dict):
            problems.append(f"image_refs[{index}]:not_object")
            continue
        problems.extend(
            f"image_refs[{index}].unexpected:{key}"
            for key in ref
            if key not in ALLOWED_MCP_V2_IMAGE_REF_KEYS
        )
    for index, ref in enumerate(item.get("video_refs") or []):
        if not isinstance(ref, dict):
            problems.append(f"video_refs[{index}]:not_object")
            continue
        problems.extend(
            f"video_refs[{index}].unexpected:{key}"
            for key in ref
            if key not in ALLOWED_MCP_V2_VIDEO_REF_KEYS
        )
    return problems
