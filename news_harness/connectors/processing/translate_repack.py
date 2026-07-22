"""v2 Processing connector：翻译 + LLM 重写（ARCHITECTURE.md §4.2，D-08/D-09）。

规则：
- 雪球（中文源）不做任何 DeepSeek 改写，直接保留原文。
- Reddit 等外文源：先机翻，再 LLM 改写；改写指令注入 anti-AI 清理规则。
- 翻译 / LLM 均为可替换 seam（默认委托 news_harness.translate / news_harness.llm），
  未配置 provider 时优雅降级：translated_text=None（原文进 LLM）、llm_summary 用本地兜底。
- 输出 ProcessedContent（按 item_id 关联，MCP 导出时再 join 原文/媒体）。
"""

from __future__ import annotations

from typing import Callable, Optional

from news_harness.connectors.base import ProcessingConnector
from news_harness.connectors.registry import ConnectorRegistry
from news_harness.llm import REPACK_INSTRUCTION, llm_rewrite
from news_harness.models import ContentItem, ProcessedContent
from news_harness.translate import detect_source_lang, translate_text

TranslateFn = Callable[[str, Optional[str]], str]
LlmFn = Callable[[str, str], str]


def _is_xueqiu(item: ContentItem) -> bool:
    platform = str(item.platform or "").strip().lower()
    label = str(item.source_label or "").strip().lower()
    return platform.startswith("xueqiu") or "雪球" in label or "xueqiu" in label


def build_processed_content(
    item: ContentItem,
    *,
    translated_text: Optional[str],
    llm_summary: Optional[str],
    model_ref: Optional[str],
    processing_status: str = "llm_done",
) -> ProcessedContent:
    """纯构造：由素材 + 处理稿组装 ProcessedContent。"""
    return ProcessedContent(
        item_id=item.id,
        translated_text=translated_text,
        llm_summary=llm_summary,
        processing_status=processing_status,
        model_ref=model_ref,
    )


@ConnectorRegistry.register
class TranslateRepackConnector(ProcessingConnector):
    platform = "translate_repack"

    def __init__(
        self,
        translator: Optional[TranslateFn] = None,
        llm: Optional[LlmFn] = None,
    ) -> None:
        self._translator = translator
        self._llm = llm

    def _do_translate(self, text: str, source_lang: Optional[str]) -> Optional[str]:
        try:
            if self._translator is not None:
                return self._translator(text, source_lang)
            return translate_text(text, target_lang="zh", source_lang=source_lang)
        except Exception:
            return None  # 翻译失败/未配置 → 不翻译，原文进 LLM

    def _do_llm(self, text: str) -> tuple[str, str]:
        try:
            if self._llm is not None:
                return self._llm(text, REPACK_INSTRUCTION), "inject.llm"
            return llm_rewrite(text, instruction=REPACK_INSTRUCTION)
        except Exception:
            from news_harness.llm import _local_fallback_summary

            return _local_fallback_summary(text), "local.fallback"

    def process(self, item: ContentItem) -> ProcessedContent:
        # 雪球：零 DeepSeek 改写，直接原文出口。
        if _is_xueqiu(item):
            return build_processed_content(
                item,
                translated_text=None,
                llm_summary=None,
                model_ref="source.passthrough.xueqiu",
                processing_status="raw",
            )

        # Reddit 等外文源：先机翻，再 LLM（anti-AI 指令在 llm.REPACK_INSTRUCTION）。
        translated: Optional[str] = None
        working_text = item.copy_text
        if detect_source_lang(item.copy_text) != "zh":
            translated = self._do_translate(item.copy_text, source_lang=None)
            if translated is not None:
                working_text = translated
        summary, model_ref = self._do_llm(working_text)
        return build_processed_content(
            item,
            translated_text=translated,
            llm_summary=summary,
            model_ref=model_ref,
            processing_status="llm_done",
        )
