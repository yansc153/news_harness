"""v2 翻译 seam（ARCHITECTURE.md §4.2）。

外文→中文机翻。Reddit 英文帖需要翻译；雪球中文帖不需要。
默认实现为离线可运行的 provider seam：未配置真实 provider 时抛出
ConnectorError，由上层 connector 捕获并降级（translated_text=None，原文进 LLM）。
S7 接入 DeepSeek / 其它机翻 provider 时，仅需在 `_resolve_provider()` 注册即可，
不动任何调用方逻辑。
"""

from __future__ import annotations

import re

from news_harness.connectors.base import ConnectorError

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# provider 注册点：name -> callable(text, *, source_lang, target_lang) -> str
_PROVIDERS: dict[str, object] = {}


def register_provider(name: str, fn) -> None:
    """S7 接入真实机翻 provider 时调用。"""
    _PROVIDERS[name] = fn


def detect_source_lang(text: str) -> str:
    """极简语种探测：CJK 占比 > 30% 视为中文，否则英文。"""
    if not text:
        return "en"
    cjk = sum(1 for ch in text if _CJK_RE.match(ch))
    return "zh" if cjk / max(1, len(text)) > 0.3 else "en"


def _resolve_provider() -> object | None:
    # 仅当显式注册了 provider 才使用；否则离线降级
    if not _PROVIDERS:
        return None
    # 取第一个已注册 provider（S7 可改为按配置选择）
    return next(iter(_PROVIDERS.values()))


def translate_text(
    text: str,
    *,
    target_lang: str = "zh",
    source_lang: str | None = None,
) -> str:
    """翻译文本。已为目标语种时原样返回；未配置 provider 时抛 ConnectorError。"""
    src = source_lang or detect_source_lang(text)
    if src == target_lang:
        return text
    provider = _resolve_provider()
    if provider is None:
        raise ConnectorError(
            "translate_provider_unconfigured",
            "未配置机翻 provider；Reddit 线将在 S7 接入 DeepSeek 后可用",
        )
    return provider(text, source_lang=src, target_lang=target_lang)
