"""S5 — mcp_v2 导出 TDD（RED first）。

覆盖（ARCHITECTURE.md §8，D-02 视频 seam，secret 永不外泄）：
- `build_mcp_export`：ContentItem + ProcessedContent → McpExportItem（白名单）
- 带 translated_text / llm_summary（图文优先）
- 严禁泄漏 secret / score / label / rulebook 内部
- 视频 seam：media_kind + video_refs 预留（不 breaking change）
- `validate_mcp_v2_export`：禁令牌 + 白名单校验
"""

import unittest

from news_harness.mcp_v2 import (
    ALLOWED_MCP_V2_KEYS,
    FORBIDDEN_MCP_V2_KEYS,
    build_mcp_export,
    validate_mcp_v2_export,
)
from news_harness.models import Author, ContentItem, MediaRef, ProcessedContent


def _reddit_item():
    return ContentItem(
        id="reddit_1",
        platform="reddit",
        source_label="r/wallstreetbets",
        source_url="https://reddit.com/x",
        canonical_url="https://reddit.com/x",
        author=Author(name="u_trader", handle="u_trader", avatar_url="https://a/x.png", follower_count=123),
        author_type="unknown",
        copy_text="A long English post about a stock thesis. " * 20,
        char_count=len("A long English post about a stock thesis. " * 20),
        published_at="2026-07-22T00:00:00",
        observed_at="2026-07-22T00:00:05",
        engagement={"likes": 100, "comments": 20},
        content_kind="post",
        ingest_gate="passed",
        media_kind="image",
        image_refs=[MediaRef(url="https://img/x.png", dimensions="800x600", rights_status="ok")],
        video_refs=[],
        evidence_status="observed",
        rights_status="ok",
        processing_status="raw",
    )


def _processed():
    return ProcessedContent(
        item_id="reddit_1",
        translated_text="中文翻译稿",
        llm_summary="搬运摘要",
        processing_status="llm_done",
        model_ref="local.fallback",
    )


class TestMcpV2Export(unittest.TestCase):
    def test_exports_whitelisted_fields(self):
        export = build_mcp_export(_reddit_item(), _processed())
        self.assertEqual(export["object_type"], "McpExportItem")
        self.assertEqual(export["id"], "reddit_1")
        self.assertEqual(export["platform"], "reddit")
        self.assertEqual(export["source_label"], "r/wallstreetbets")
        self.assertEqual(export["copy_text"], _reddit_item().copy_text)
        self.assertEqual(export["translated_text"], "中文翻译稿")
        self.assertEqual(export["llm_summary"], "搬运摘要")
        self.assertEqual(export["source_url"], "https://reddit.com/x")
        self.assertEqual(export["canonical_url"], "https://reddit.com/x")
        self.assertEqual(export["evidence_status"], "observed")
        self.assertTrue(export["public_url_available"])

    def test_author_mapped(self):
        export = build_mcp_export(_reddit_item(), _processed())
        self.assertEqual(
            export["author"],
            {"name": "u_trader", "handle": "u_trader", "avatar_url": "https://a/x.png", "follower_count": 123},
        )

    def test_image_refs_mapped_with_dimensions_and_rights(self):
        export = build_mcp_export(_reddit_item(), _processed())
        self.assertEqual(len(export["image_refs"]), 1)
        ref = export["image_refs"][0]
        self.assertEqual(ref["url"], "https://img/x.png")
        self.assertEqual(ref["dimensions"], "800x600")
        self.assertEqual(ref["rights_status"], "ok")

    def test_no_processed_yields_none_texts(self):
        export = build_mcp_export(_reddit_item(), None)
        self.assertIsNone(export["translated_text"])
        self.assertIsNone(export["llm_summary"])

    def test_secret_never_leaked(self):
        export = build_mcp_export(_reddit_item(), _processed())
        # model_ref / secret_ref 等绝不进入导出
        self.assertNotIn("model_ref", export)
        self.assertNotIn("secret", " ".join(export.keys()))
        raw = str(export)
        self.assertNotIn("local.fallback", raw)  # model_ref 值不泄漏

    def test_video_seam_reserved(self):
        item = _reddit_item()
        item.media_kind = "video"
        item.video_refs = [MediaRef(url="https://vid/x.mp4", mime="video/mp4", dimensions="1080x1920", rights_status="ok")]
        export = build_mcp_export(item, _processed())
        self.assertEqual(export["media_kind"], "video")
        self.assertEqual(len(export["video_refs"]), 1)
        self.assertEqual(export["video_refs"][0]["url"], "https://vid/x.mp4")


class TestMcpV2Validation(unittest.TestCase):
    def test_clean_export_passes(self):
        export = build_mcp_export(_reddit_item(), _processed())
        self.assertEqual(validate_mcp_v2_export(export), [])

    def test_forbidden_keys_flagged(self):
        export = build_mcp_export(_reddit_item(), _processed())
        for bad in FORBIDDEN_MCP_V2_KEYS:
            dirty = dict(export)
            dirty[bad] = "x"
            problems = validate_mcp_v2_export(dirty)
            self.assertTrue(any(p.startswith(f"forbidden:{bad}") for p in problems), f"missing flag for {bad}")

    def test_unexpected_keys_flagged(self):
        export = build_mcp_export(_reddit_item(), _processed())
        dirty = dict(export)
        dirty["totally_unexpected"] = 1
        problems = validate_mcp_v2_export(dirty)
        self.assertTrue(any(p.startswith("unexpected:totally_unexpected") for p in problems))

    def test_image_ref_unexpected_key_flagged(self):
        export = build_mcp_export(_reddit_item(), _processed())
        export["image_refs"][0]["leak"] = "x"
        problems = validate_mcp_v2_export(export)
        self.assertTrue(any("image_refs[0].unexpected:leak" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
