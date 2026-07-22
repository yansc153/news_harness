"""S7 — 批次编排（fetch→translate→llm→store→mcp_v2 导出）TDD（RED first）。

覆盖（ARCHITECTURE.md §9 / §11 S7，D-02 视频 seam）：
- `BatchPipeline.run()`：逐平台 fetch → 逐条 process → store → export
- 端到端（offline，注入 fake connector / 内存 store / 捕获 export）
- 视频 seam：video_refs 透传进 store 与 export（不 breaking change）
- `BatchPipeline.from_config()`：按 platform 配置构造 reddit/xueqiu connector
"""

import tempfile
import shutil
import unittest
from pathlib import Path

from news_harness.batch import BatchPipeline
from news_harness.connectors.base import SourceConnector
from news_harness.connectors.processing.translate_repack import TranslateRepackConnector
from news_harness.models import Author, ContentItem, MediaRef
from news_harness.store.db import StoreDB


def _fake_translate(text, source_lang=None):
    return f"[译]{text[:8]}"


def _fake_llm(text, instruction):
    return f"[摘要]{text[:8]}"


class _FakeSource(SourceConnector):
    platform = "fake"

    def __init__(self, items):
        self._items = items
        self.fetched = False

    def fetch(self):
        self.fetched = True
        return self._items


def _reddit_item():
    return ContentItem(
        id="reddit_1", platform="reddit", source_label="r/wsb",
        source_url="https://reddit.com/x", canonical_url="https://reddit.com/x",
        author=Author(name="u", handle="u"), copy_text="English stock post. " * 20,
        char_count=len("English stock post. " * 20), published_at="2026-07-22T00:00:00",
        observed_at="2026-07-22T00:00:05", engagement={"likes": 100, "comments": 20},
        content_kind="post", ingest_gate="passed", media_kind="text",
        image_refs=[], video_refs=[], evidence_status="observed", rights_status="ok",
        processing_status="raw",
    )


def _xueqiu_item():
    return ContentItem(
        id="xq_1", platform="xueqiu", source_label="雪球",
        source_url="https://xueqiu.com/x", canonical_url="https://xueqiu.com/x",
        author=Author(name="个人", handle="personal"), user_id="u_p",
        copy_text="这是一篇足够长的雪球长文，讨论A股走势与个股逻辑。 " * 20,
        char_count=len("这是一篇足够长的雪球长文，讨论A股走势与个股逻辑。 " * 20),
        published_at="2026-07-22T00:00:00", observed_at="2026-07-22T00:00:05",
        engagement={"likes": 80, "comments": 15}, content_kind="post",
        ingest_gate="passed", media_kind="video",
        image_refs=[], video_refs=[MediaRef(url="https://vid/x.mp4", mime="video/mp4", dimensions="1080x1920", rights_status="ok")],
        evidence_status="observed", rights_status="ok", processing_status="raw",
    )


class TestBatchPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nh_batch_")
        self.store = StoreDB(Path(self.tmp) / "meta.sqlite")
        self.exports: list[dict] = []
        self.processor = TranslateRepackConnector(translator=_fake_translate, llm=_fake_llm)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pipeline(self, sources):
        return BatchPipeline(
            sources=sources,
            processor=self.processor,
            store=self.store,
            exporter=lambda item, processed: self.exports.append(
                {"id": item.id, "translated_text": processed.translated_text, "llm_summary": processed.llm_summary, "video_refs": item.video_refs}
            ),
        )

    def test_run_fetch_process_store_export(self):
        sources = {"reddit": _FakeSource([_reddit_item()]), "xueqiu": _FakeSource([_xueqiu_item()])}
        pipe = self._pipeline(sources)
        report = pipe.run()
        self.assertEqual(report["items_seen"], 2)
        self.assertEqual(report["items_processed"], 2)
        self.assertEqual(len(self.exports), 2)
        # store 落库
        self.assertIsNotNone(self.store.get_item("reddit_1"))
        self.assertIsNotNone(self.store.get_item("xq_1"))
        # 中文源不翻译；外文源翻译
        reddit_export = next(e for e in self.exports if e["id"] == "reddit_1")
        xueqiu_export = next(e for e in self.exports if e["id"] == "xq_1")
        self.assertTrue(reddit_export["translated_text"].startswith("[译]"))
        self.assertIsNone(xueqiu_export["translated_text"])
        self.assertIsNone(xueqiu_export.get("llm_summary"))
        # 视频 seam 透传
        self.assertEqual(len(xueqiu_export["video_refs"]), 1)

    def test_empty_sources_noop(self):
        pipe = self._pipeline({})
        report = pipe.run()
        self.assertEqual(report["items_seen"], 0)
        self.assertEqual(len(self.exports), 0)

    def test_storeless_run_collects_only(self):
        sources = {"reddit": _FakeSource([_reddit_item()])}
        pipe = BatchPipeline(sources=sources, processor=self.processor)
        report = pipe.run()
        self.assertEqual(report["items_processed"], 1)
        self.assertIsNone(self.store.get_item("reddit_1"))  # 无 store 不落库


class TestFromConfig(unittest.TestCase):
    def test_builds_reddit_and_xueqiu_connectors(self):
        configs = [
            {"platform": "reddit", "config": {"subreddits": ["wallstreetbets"]}, "availability": {}},
            {"platform": "xueqiu", "blocklist_path": "configs/xueqiu_blocklist.json",
             "thresholds": {"min_chars": 500}, "batch_limit": 20, "floor": 5},
        ]
        pipe = BatchPipeline.from_config(configs)
        self.assertIn("reddit", pipe.sources)
        self.assertIn("xueqiu", pipe.sources)
        self.assertEqual(pipe.sources["reddit"].platform, "reddit")
        self.assertEqual(pipe.sources["xueqiu"].platform, "xueqiu")


if __name__ == "__main__":
    unittest.main()
