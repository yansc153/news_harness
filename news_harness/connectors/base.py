"""v2 connector 抽象基类（ARCHITECTURE.md §3 / §4）。

两类 connector：
- SourceConnector：从金融平台抓取原文 + 媒体，产出 ContentItem[]。
- ProcessingConnector：对 ContentItem 做翻译 / LLM 重写，产出 ProcessedContent。

均不直接读 secret、不直接发布（MCP 只读，发布走独立 publish.py）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from news_harness.models import ContentItem, ProcessedContent


class ConnectorError(Exception):
    """connector 抓取/处理失败的确定性异常（结构化失败，不吞错）。"""


class BaseConnector(ABC):
    platform: str = ""          # reddit / xueqiu ...
    source_label: str = ""      # 人类可读标签

    @property
    def name(self) -> str:
        return self.platform or self.__class__.__name__


class SourceConnector(BaseConnector):
    """金融平台抓取源。"""

    @abstractmethod
    def fetch(self) -> list[ContentItem]:
        """拉取本平台一批内容，返回 ContentItem 列表（未经筛选/存储）。"""
        raise NotImplementedError


class ProcessingConnector(BaseConnector):
    """翻译 / LLM 处理源。"""

    @abstractmethod
    def process(self, item: ContentItem) -> ProcessedContent:
        """对单条 ContentItem 做处理，返回 ProcessedContent。"""
        raise NotImplementedError
