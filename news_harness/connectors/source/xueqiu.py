"""v2 雪球源 connector（ARCHITECTURE.md §4 / §4.4，D-10~D-16）。

- 抓取沿用既有 credential 层（`direct_cli_backend._fetch_xueqiu_with_opencli`），
  cookie/session 路径不变（D-08 明确保留）。**「最新」tab 须由 headless 桥显式点击**
  （D-15，先验证桥可达性；见 scripts/xueqiu_headless_export.mjs），非网页刷新。
- 抓取结果经纯函数 `xueqiu_observation_to_content_item` 映射为 v2 ContentItem。
- 摄入筛选 Gate A/B/C 由 `xueqiu_gates` 应用：拉 20、过闸、保底≥5（D-14/D-16）。
- 网络抓取是可替换 seam：构造参数 `fetcher` 注入，便于测试与未来换桥。
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from news_harness.connectors.base import SourceConnector
from news_harness.connectors.registry import ConnectorRegistry
from news_harness.connectors.source.xueqiu_gates import (
    filter_batch,
    load_blocklist,
)
from news_harness.models import (
    Author,
    ContentItem,
    MediaRef,
    compute_char_count,
    media_kind_from_refs,
)

XUEQIU_DEFAULT_THRESHOLDS = {
    "min_chars": 500,
    "min_likes": 50,
    "min_comments": 10,
    "require_image": True,
}

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


def xueqiu_observation_to_content_item(obs: dict) -> ContentItem:
    """纯映射：legacy xueqiu observation dict → v2 ContentItem。"""
    copy_text = (obs.get("copy_text") or "").strip()
    user = obs.get("user") or {}
    screen_name = (
        obs.get("author")
        or user.get("screen_name")
        or user.get("name")
        or "xueqiu"
    )
    user_id = user.get("id")
    image_refs = []
    for r in (obs.get("image_refs") or []):
        mr = _image_ref_from_legacy(r)
        if mr.url:
            image_refs.append(mr)
    return ContentItem(
        id=obs.get("observation_id") or obs.get("id") or "",
        platform="xueqiu",
        source_label=obs.get("source_label") or "雪球",
        source_url=obs.get("source_url") or obs.get("canonical_url") or "",
        canonical_url=obs.get("canonical_url") or obs.get("source_url"),
        author=Author(name=screen_name, handle=screen_name),
        user_id=user_id,
        author_type="unknown",  # 由 Gate C derive_author_type 推导
        copy_text=copy_text,
        char_count=compute_char_count(copy_text),
        published_at=obs.get("published_at"),
        observed_at=obs.get("observed_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        engagement=obs.get("engagement") or {},
        content_kind="post",
        ingest_gate="passed",  # 闸门在 filter_batch 中单独应用
        media_kind=media_kind_from_refs(image_refs, []),
        image_refs=image_refs,
        video_refs=[],
        evidence_status="observed",
        rights_status="ok",
        processing_status="raw",
    )


@ConnectorRegistry.register
class XueqiuSourceConnector(SourceConnector):
    platform = "xueqiu"

    def __init__(
        self,
        fetcher: Optional[FetchFn] = None,
        blocklist_path: Optional[str] = None,
        thresholds: Optional[dict] = None,
        batch_limit: int = 20,
        floor: int = 5,
        config: Optional[dict] = None,
        availability: Optional[dict] = None,
    ) -> None:
        self._fetcher = fetcher
        self.blocklist_path = blocklist_path
        self.thresholds = thresholds or dict(XUEQIU_DEFAULT_THRESHOLDS)
        self.batch_limit = batch_limit
        self.floor = floor
        self.config = config or {}
        self.availability = availability or {}
        self.last_errors: list[dict] = []
        self.last_stats: Optional[dict] = None

    def _default_fetcher(self, source_config: dict, availability: dict):
        from news_harness.direct_cli_backend import _fetch_xueqiu_with_opencli

        return _fetch_xueqiu_with_opencli(source_config, availability)

    def fetch(self, availability: Optional[dict] = None) -> list[ContentItem]:
        fetcher = self._fetcher or self._default_fetcher
        avail = availability if availability is not None else self.availability
        observations, errors = fetcher(self.config, avail)
        self.last_errors = list(errors or [])
        blocklist = load_blocklist(self.blocklist_path) if self.blocklist_path else []
        items, stats = filter_batch(
            observations or [],
            blocklist,
            self.thresholds,
            batch_limit=self.batch_limit,
            floor=self.floor,
            relax=True,
        )
        self.last_stats = stats
        return items
