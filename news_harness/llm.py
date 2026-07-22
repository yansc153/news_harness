"""v2 LLM seam（ARCHITECTURE.md §4.2）。

基于原文/翻译稿做结构化与重写，产出搬运稿 `llm_summary`。
LLM 仅做处理，**不参与抓取决策、不自评**（D-08/D-09）。
默认实现为离线可运行的 provider seam：未配置 provider 时抛 ConnectorError，
由 connector 捕获并产出本地兜底摘要（model_ref 带 .fallback 标记，可追溯）。
S7 接入 DeepSeek 时仅需在 `_resolve_provider()` 注册。
"""

from __future__ import annotations

import re

from news_harness.connectors.base import ConnectorError

# provider 注册点：name -> callable(text, *, instruction) -> str
_PROVIDERS: dict[str, object] = {}

REPACK_INSTRUCTION = (
    "将以下素材改写为适合中文社媒搬运的短文：保留核心事实与观点，"
    "去掉冗余，语言口语化、易读，不超过原文 1/2 长度。"
)


def register_provider(name: str, fn) -> None:
    """S7 接入真实 LLM provider 时调用。"""
    _PROVIDERS[name] = fn


def _resolve_provider() -> object | None:
    if not _PROVIDERS:
        return None
    return next(iter(_PROVIDERS.values()))


def _local_fallback_summary(text: str) -> str:
    """离线兜底：取首段 / 前 240 字，带明确标记。"""
    first_para = (text or "").lstrip().split("\n\n", 1)[0]
    snippet = first_para[:240]
    return f"[本地兜底摘要·非LLM] {snippet}"


def llm_rewrite(text: str, *, instruction: str = REPACK_INSTRUCTION) -> tuple[str, str]:
    """返回 (摘要, model_ref)。未配置 provider 时抛 ConnectorError。"""
    provider = _resolve_provider()
    if provider is None:
        raise ConnectorError(
            "llm_provider_unconfigured",
            "未配置 LLM provider；将在 S7 接入 DeepSeek 后可用",
        )
    summary = provider(text, instruction=instruction)
    return summary, "llm.provider"
