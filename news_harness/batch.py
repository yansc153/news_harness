"""v2 批次编排（ARCHITECTURE.md §9 / §11 S7，D-02 视频 seam）。

将各平台 source connector → Processing(translate/llm) → store → mcp_v2 导出
串成一次批次运行。零第三方依赖，全部可注入（offline / 单测友好）。

设计要点：
- `BatchPipeline.run()`：逐平台 fetch → 逐条 process → store(可选) → export(可选)。
- store 仅落 ContentItem + processing_status（llm_done）；MCP 导出时再按
  item_id 与 ProcessedContent join，保持 store 精简（S2 决策）。
- 视频 seam：ContentItem.video_refs 全程透传进 store 与 export，不引入
  breaking change；真正「视频生成」待独立能力接入。
- `from_config()`：按 platform 配置构造 reddit / xueqiu connector（credential 层复用）。
"""

from __future__ import annotations

from typing import Callable, Optional

from news_harness.connectors.base import ProcessingConnector, SourceConnector
from news_harness.connectors.processing.translate_repack import TranslateRepackConnector
from news_harness.connectors.source.reddit import RedditSourceConnector
from news_harness.connectors.source.xueqiu import XueqiuSourceConnector
from news_harness.models import ContentItem, ProcessedContent
from news_harness.store.db import StoreDB

ExportFn = Callable[[ContentItem, ProcessedContent], None]


class BatchPipeline:
    def __init__(
        self,
        sources: dict[str, SourceConnector],
        processor: ProcessingConnector,
        store: Optional[StoreDB] = None,
        exporter: Optional[ExportFn] = None,
    ) -> None:
        self.sources = sources
        self.processor = processor
        self.store = store
        self.exporter = exporter

    def run(self) -> dict:
        report = {
            "items_seen": 0,
            "items_processed": 0,
            "per_platform": {},
        }
        for platform, connector in self.sources.items():
            items = connector.fetch()
            seen = 0
            processed = 0
            for item in items:
                seen += 1
                report["items_seen"] += 1
                processed_content = self.processor.process(item)
                if self.store is not None:
                    self.store.upsert_item(item)
                    self.store.set_processing_status(item.id, processed_content.processing_status)
                if self.exporter is not None:
                    self.exporter(item, processed_content)
                processed += 1
                report["items_processed"] += 1
            report["per_platform"][platform] = {"seen": seen, "processed": processed}
        return report

    @classmethod
    def from_config(
        cls,
        platform_configs: list[dict],
        *,
        availability: Optional[dict] = None,
        store: Optional[StoreDB] = None,
        exporter: Optional[ExportFn] = None,
    ) -> "BatchPipeline":
        """按 platform 配置构造各 source connector（credential 层复用，D-08）。"""
        sources: dict[str, SourceConnector] = {}
        for cfg in platform_configs:
            platform = cfg.get("platform")
            if platform == "reddit":
                sources[platform] = RedditSourceConnector(
                    config=cfg.get("config") or {},
                    availability=cfg.get("availability") or availability or {},
                )
            elif platform == "xueqiu":
                sources[platform] = XueqiuSourceConnector(
                    blocklist_path=cfg.get("blocklist_path"),
                    thresholds=cfg.get("thresholds"),
                    batch_limit=cfg.get("batch_limit", 20),
                    floor=cfg.get("floor", 5),
                )
            # 其它平台（未来扩展）在此追加
        return cls(sources=sources, processor=TranslateRepackConnector(), store=store, exporter=exporter)
