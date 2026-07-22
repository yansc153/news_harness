"""v2 Processing connector：翻译 + LLM 重写（ARCHITECTURE.md §4.2，D-08/D-09）。

- 外文源（Reddit）先机翻，再 LLM 重写；中文源（雪球）跳过翻译直接 LLM。
- 翻译 / LLM 均为可替换 seam（默认委托 `news_harness.translate` / `news_harness.llm`），
  未配置 provider 时优雅降级：translated_text=None（原文进 LLM）、llm_summary 用本地兜底。
- 输出 `ProcessedContent`（按 item_id 关联，MCP 导出时再 join 原文/媒体）。
"""

from __future__ import annotations

from typing import Callable, Optional

from news_harness.connectors.base import ProcessingConnector
from news_harness.connectors.registry import ConnectorRegistry
from news_harness.llm import REPACK_INSTRUCTION, llm_rewrite
from news_harness.models import ContentItem, ProcessedContent
from news_harness.translate import translate_text, detect_source_lang

TranslateFn = Callable[[str, Optional[str]], str]
LlmFn = Callable[[str, str], str]


def build_processed_content(
    item: ContentItem,
    *,
    translated_text: Optional[str],
    llm_summary: str,
    model_ref: str,
) -> ProcessedContent:
    """纯构造：由素材 + 处理稿组装 ProcessedContent。"""
    return ProcessedContent(
        item_id=item.id,
        translated_text=translated_text,
        llm_summary=llm_summary,
        processing_status="llm_done",
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
        # 翻译与否由语种探测决定：中文源（雪球）跳过，外文源（Reddit 等）机翻
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
        )
