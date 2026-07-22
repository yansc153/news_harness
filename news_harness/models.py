"""v2 数据模型（ARCHITECTURE.md §7）。

统一的素材与处理表示，供 connectors / store / mcp_v2 共用。
零第三方依赖，仅标准库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Author:
    name: str
    handle: Optional[str] = None
    avatar_url: Optional[str] = None
    follower_count: Optional[int] = None


@dataclass
class MediaRef:
    url: str
    mime: Optional[str] = None            # image/jpeg, video/mp4 ...
    dimensions: Optional[str] = None      # "1080x1920"
    byte_size: Optional[int] = None
    sha256: Optional[str] = None          # 媒体库去重键
    thumbnail_ref: Optional[str] = None   # 视频缩略图（视频阶段用）
    rights_status: str = "unknown"        # ok | unknown | restricted


@dataclass
class ContentItem:
    id: str
    platform: str                         # reddit / xueqiu ...
    source_label: str
    source_url: str
    canonical_url: Optional[str] = None
    author: Optional[Author] = None
    user_id: Optional[str] = None         # 平台用户唯一 id（雪球 user.id），Gate A 块列表命中用
    author_type: str = "unknown"          # personal | institutional | unknown（Gate C，D-12）
    copy_text: str = ""                   # 原始文案
    char_count: int = 0                   # len(copy_text.strip())，Gate B①门槛（D-12）
    published_at: Optional[str] = None
    observed_at: str = ""
    engagement: Optional[dict] = None     # {likes, comments, ...} 来自 _engagement_from_row
    content_kind: str = "original"        # original | news | repost（Gate A 判定，D-10）
    ingest_gate: str = "passed"           # passed | dropped_blocklist | dropped_low_engagement | dropped_no_image | dropped_short
    media_kind: str = "text"             # text | image | video | mixed
    image_refs: list = field(default_factory=list)
    video_refs: list = field(default_factory=list)  # 视频阶段填充，当前空
    evidence_status: str = "observed"     # observed | missing
    rights_status: str = "ok"
    processing_status: str = "raw"         # raw | translated | llm_done（store 层追踪，§6）


@dataclass
class ProcessedContent:                    # Processing 层输出（D-08/D-09）
    item_id: str
    translated_text: Optional[str] = None  # 机翻稿（Reddit 外文→中文）
    llm_summary: Optional[str] = None      # LLM 结构化/重写搬运稿
    processing_status: str = "raw"         # raw | translated | llm_done
    model_ref: Optional[str] = None        # 使用的 LLM/translate 标识（可追溯，不泄 secret）


def compute_char_count(copy_text: Optional[str]) -> int:
    """Gate B①：字数门槛取清洗后的字符数（去首尾空白）。"""
    return len((copy_text or "").strip())


def media_kind_from_refs(image_refs, video_refs) -> str:
    """根据媒体引用推导 media_kind（与 ContentItem.media_kind 对齐）。"""
    has_img = bool(image_refs)
    has_vid = bool(video_refs)
    if has_img and has_vid:
        return "mixed"
    if has_vid:
        return "video"
    if has_img:
        return "image"
    return "text"
