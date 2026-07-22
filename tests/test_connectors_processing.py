"""S3 — Processing (translate + LLM) connector TDD (RED first)."""

import unittest

from news_harness.connectors.processing.translate_repack import (
    TranslateRepackConnector,
    build_processed_content,
)
from news_harness.connectors.registry import ConnectorRegistry
from news_harness.models import Author, ContentItem, ProcessedContent


def _reddit_item():
    return ContentItem(
        id="reddit_1",
        platform="reddit",
        source_label="r/wallstreetbets",
        source_url="https://reddit.com/x",
        canonical_url="https://reddit.com/x",
        author=Author(name="u_trader", handle="u_trader"),
        author_type="unknown",
        copy_text="A very long English post about a stock thesis. " * 20,
        char_count=len("A very long English post about a stock thesis. " * 20),
        published_at="2026-07-22T00:00:00",
        observed_at="2026-07-22T00:00:05",
        engagement={"likes": 100, "comments": 20, "views": 3000},
        content_kind="post",
        ingest_gate="passed",
        media_kind="text",
        image_refs=[],
        video_refs=[],
        evidence_status="observed",
        rights_status="ok",
        processing_status="raw",
    )


def _xueqiu_item():
    return ContentItem(
        id="xq_1",
        platform="xueqiu",
        source_label="雪球",
        source_url="https://xueqiu.com/x",
        canonical_url="https://xueqiu.com/x",
        author=Author(name="个人投资者", handle="personal_x"),
        author_type="personal",
        copy_text="这是一篇足够长的雪球长文，讨论A股走势与个股逻辑。 " * 20,
        char_count=len("这是一篇足够长的雪球长文，讨论A股走势与个股逻辑。 " * 20),
        published_at="2026-07-22T00:00:00",
        observed_at="2026-07-22T00:00:05",
        engagement={"likes": 80, "comments": 15, "views": 2000},
        content_kind="post",
        ingest_gate="passed",
        media_kind="image",
        image_refs=[],
        video_refs=[],
        evidence_status="observed",
        rights_status="ok",
        processing_status="raw",
    )


def _fake_translate(text, source_lang=None):
    return f"[译]{text[:12]}"


def _fake_llm(text, instruction):
    return f"[摘要]{text[:12]}"


class TestBuildProcessedContent(unittest.TestCase):
    def test_builds_from_item_and_texts(self):
        item = _reddit_item()
        pc = build_processed_content(
            item,
            translated_text="中文稿",
            llm_summary="搬运摘要",
            model_ref="fake.translate+llm",
        )
        self.assertIsInstance(pc, ProcessedContent)
        self.assertEqual(pc.item_id, item.id)  # 通过 item_id 与素材关联，MCP 导出时再 join
        self.assertEqual(pc.translated_text, "中文稿")
        self.assertEqual(pc.llm_summary, "搬运摘要")
        self.assertEqual(pc.processing_status, "llm_done")
        self.assertEqual(pc.model_ref, "fake.translate+llm")


class TestTranslateRepackConnector(unittest.TestCase):
    def test_reddit_item_gets_translated_then_llm(self):
        conn = TranslateRepackConnector(translator=_fake_translate, llm=_fake_llm)
        pc = conn.process(_reddit_item())
        # fake translate prefixes the source text
        self.assertTrue(pc.translated_text.startswith("[译]"))
        self.assertIn("A very long", pc.translated_text)
        self.assertTrue(pc.llm_summary.startswith("[摘要]"))
        self.assertEqual(pc.processing_status, "llm_done")
        self.assertIsNotNone(pc.model_ref)

    def test_xueqiu_item_skips_translation(self):
        conn = TranslateRepackConnector(translator=_fake_translate, llm=_fake_llm)
        pc = conn.process(_xueqiu_item())
        self.assertIsNone(pc.translated_text)  # 中文源不翻译
        self.assertTrue(pc.llm_summary.startswith("[摘要]"))

    def test_translation_failure_degrades_gracefully(self):
        def boom(text, source_lang=None):
            raise RuntimeError("translate down")

        conn = TranslateRepackConnector(translator=boom, llm=_fake_llm)
        pc = conn.process(_reddit_item())
        self.assertIsNone(pc.translated_text)  # 翻译失败 → 保留原文进 LLM
        self.assertTrue(pc.llm_summary.startswith("[摘要]"))

    def test_llm_failure_degrades_gracefully(self):
        def boom(text, instruction):
            raise RuntimeError("llm down")

        conn = TranslateRepackConnector(translator=_fake_translate, llm=boom)
        pc = conn.process(_reddit_item())
        self.assertIsNotNone(pc.translated_text)
        self.assertIsNotNone(pc.llm_summary)  # 本地兜底摘要
        self.assertIn("fallback", pc.model_ref)

    def test_registry_knows_processor(self):
        reg = ConnectorRegistry()
        reg.register(TranslateRepackConnector)
        self.assertIn("translate_repack", reg.list_processors())
        self.assertIs(reg.get_processor("translate_repack"), TranslateRepackConnector)


if __name__ == "__main__":
    unittest.main()
