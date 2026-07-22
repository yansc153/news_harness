"""v2 connector 注册表（ARCHITECTURE.md §4.3）。

S1 阶段为类级注册表：按 platform 名登记 SourceConnector / ProcessingConnector
子类，供 orchestrator 按配置启用。配置驱动的自动发现（扫描
connectors/source、connectors/processing 包）在 S3/S4 接入真实 connector 后补。

设计要点：
- 注册即按 cls.platform 索引，O(1) 查找。
- 一个类可同时是 Source 与 Processing（platform 一致时分别登记）。
- 不在此处加载 secret / 发起网络请求。
- `register` 既可作为类装饰器 `@ConnectorRegistry.register` 使用，也可经实例调用
  （写入共享类级字典，幂等）。
"""

from __future__ import annotations

from typing import Optional, Type

from news_harness.connectors.base import (
    SourceConnector,
    ProcessingConnector,
)

# 类级共享字典（单例语义）
_SOURCES: dict[str, Type[SourceConnector]] = {}
_PROCESSORS: dict[str, Type[ProcessingConnector]] = {}


class ConnectorRegistry:
    _sources = _SOURCES
    _processors = _PROCESSORS

    @classmethod
    def register(cls, connector_cls) -> Type:
        """登记一个 connector 类（按它实现的接口自动归类）。幂等。

        既可作为类装饰器 `@ConnectorRegistry.register` 使用，也可经实例调用。
        """
        if issubclass(connector_cls, SourceConnector):
            cls._sources[connector_cls.platform] = connector_cls
        if issubclass(connector_cls, ProcessingConnector):
            cls._processors[connector_cls.platform] = connector_cls
        return connector_cls

    @classmethod
    def get_source(cls, platform: str) -> Optional[Type[SourceConnector]]:
        return cls._sources.get(platform)

    @classmethod
    def get_processor(cls, platform: str) -> Optional[Type[ProcessingConnector]]:
        return cls._processors.get(platform)

    @classmethod
    def list_sources(cls) -> list[str]:
        return list(cls._sources.keys())

    @classmethod
    def list_processors(cls) -> list[str]:
        return list(cls._processors.keys())


def default_registry() -> ConnectorRegistry:
    """返回全局注册表（orchestrator 取 connector 用）。"""
    return ConnectorRegistry
