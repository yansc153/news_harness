"""v2 Reddit 源 connector（ARCHITECTURE.md §4 / §5）。

- 抓取沿用既有 credential 层（`direct_cli_backend._fetch_reddit_with_rdt_cli`），
  cookie 文件路径 `NEWS_HARNESS_REDDIT_COOKIE_FILE` 不变（D-08 明确保留）。
- 抓取结果（observation dict）经纯函数 `reddit_observation_to_content_item`
  映射为 `ContentItem`，与 legacy 解耦、可单测。
- 网络抓取是可替换 seam：通过构造参数 `fetcher` 注入，便于测试与未来换桥。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from news_harness.connectors.base import SourceConnector, ConnectorError
from news_harness.connectors.registry import ConnectorRegistry
from news_harness.models import (
    Author,
    ContentItem,
    MediaRef,
    compute_char_count,
    media_kind_from_refs,
)

# 默认 fetcher 签名：(source_config: dict, availability: dict) -> (observations, errors)
FetchFn = Callable[[dict, dict], tuple[list[dict], list[dict]]]


def _image_ref_from_legacy(ref: dict) -> MediaRef:
    dims = ref.get("dimensions") or {}
    w = dims.get("width")
    h = dims.get("height")
    dimensions = f"{w}x{h}" if isinstance(w, int) and isinstance(h, int) else None
    return MediaRef(
        url=ref.get("original_image_ref") or ref.get("url") or "",
        mime="image/jpeg",
        dimensions=dimensions,
        thumbnail_ref=ref.get("thumbnail_ref"),
        rights_status="unknown",
    )


def reddit_observation_to_content_item(obs: dict) -> ContentItem:
    """纯映射：legacy observation dict → v2 ContentItem。"""
    copy_text = (obs.get("copy_text") or "").strip()
    image_refs = [_image_ref_from_legacy(r) for r in (obs.get("image_refs") or [])]
    image_refs = [r for r in image_refs if r.url]
    return ContentItem(
        id=obs.get("observation_id") or obs.get("id") or "",
        platform="reddit",
        source_label=obs.get("source_label") or "Reddit",
        source_url=obs.get("source_url") or obs.get("canonical_url") or "",
        canonical_url=obs.get("canonical_url") or obs.get("source_url"),
        author=Author(
            name=obs.get("author") or "reddit",
            handle=obs.get("author"),
        ),
        author_type="unknown",  # Gate C 在雪球 S4；Reddit 默认 unknown，下游可启发式
        copy_text=copy_text,
        char_count=compute_char_count(copy_text),
        published_at=obs.get("published_at"),
        observed_at=obs.get("observed_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        engagement=obs.get("engagement") or {},
        content_kind="post",
        ingest_gate="passed",  # Reddit 无闸门（闸门在 S4 雪球 Gate A/B/C）
        media_kind=media_kind_from_refs(image_refs, []),
        image_refs=image_refs,
        video_refs=[],
        evidence_status="observed",
        rights_status="ok",
        processing_status="raw",
    )


@ConnectorRegistry.register
class RedditSourceConnector(SourceConnector):
    platform = "reddit"

    def __init__(
        self,
        fetcher: Optional[FetchFn] = None,
        config: Optional[dict] = None,
        availability: Optional[dict] = None,
    ) -> None:
        self._fetcher = fetcher
        self.config = config or {}
        self.availability = availability or {}
        self.last_errors: list[dict] = []

    def _default_fetcher(self, source_config: dict, availability: dict):
        # 延迟导入 legacy，避免循环依赖；credential 层完全复用
        from news_harness.direct_cli_backend import _fetch_reddit_with_rdt_cli

        return _fetch_reddit_with_rdt_cli(source_config, availability)

    def fetch(self, availability: Optional[dict] = None) -> list[ContentItem]:
        fetcher = self._fetcher or self._default_fetcher
        avail = availability if availability is not None else self.availability
        observations, errors = fetcher(self.config, avail)
        self.last_errors = list(errors or [])
        items: list[ContentItem] = []
        for obs in observations or []:
            copy_text = (obs.get("copy_text") or "").strip()
            if not copy_text:
                continue  # 空正文跳过
            items.append(reddit_observation_to_content_item(obs))
        return items
