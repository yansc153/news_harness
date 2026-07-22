import unittest

from news_harness.models import (
    Author,
    MediaRef,
    ContentItem,
    ProcessedContent,
    compute_char_count,
    media_kind_from_refs,
)


class TestAuthor(unittest.TestCase):
    def test_construct(self):
        a = Author(name="张三", handle="zhangsan", avatar_url=None, follower_count=100)
        self.assertEqual(a.name, "张三")
        self.assertEqual(a.handle, "zhangsan")
        self.assertIsNone(a.avatar_url)
        self.assertEqual(a.follower_count, 100)


class TestMediaRef(unittest.TestCase):
    def test_default_rights_status(self):
        m = MediaRef(url="http://x/y.jpg")
        self.assertEqual(m.url, "http://x/y.jpg")
        self.assertEqual(m.rights_status, "unknown")
        self.assertIsNone(m.mime)

    def test_full_fields(self):
        m = MediaRef(
            url="http://x/y.jpg",
            mime="image/jpeg",
            dimensions="1080x1920",
            byte_size=1234,
            sha256="abc",
            thumbnail_ref=None,
            rights_status="ok",
        )
        self.assertEqual(m.mime, "image/jpeg")
        self.assertEqual(m.dimensions, "1080x1920")
        self.assertEqual(m.rights_status, "ok")


class TestContentItem(unittest.TestCase):
    def test_construct_full(self):
        author = Author(name="李四", handle="lisi")
        img = MediaRef(url="http://x/a.jpg", mime="image/jpeg", rights_status="ok")
        item = ContentItem(
            id="x1",
            platform="xueqiu",
            source_label="雪球",
            source_url="http://x/1",
            canonical_url=None,
            author=author,
            author_type="personal",
            copy_text="这是一篇雪球长文，超过五百字的内容示例用于搬运。",
            char_count=22,
            published_at="2026-07-22T00:00:00+08:00",
            observed_at="2026-07-22T00:30:00+08:00",
            engagement={"likes": 120, "comments": 30},
            content_kind="original",
            ingest_gate="passed",
            media_kind="image",
            image_refs=[img],
            video_refs=[],
            evidence_status="observed",
            rights_status="ok",
        )
        self.assertEqual(item.id, "x1")
        self.assertEqual(item.char_count, 22)
        self.assertEqual(item.author.name, "李四")
        self.assertEqual(len(item.image_refs), 1)
        self.assertEqual(item.video_refs, [])

    def test_compute_char_count_strips(self):
        self.assertEqual(compute_char_count("  hello world  "), 11)
        self.assertEqual(compute_char_count(""), 0)
        self.assertEqual(compute_char_count(None), 0)


class TestMediaKind(unittest.TestCase):
    def test_text(self):
        self.assertEqual(media_kind_from_refs([], []), "text")

    def test_image(self):
        self.assertEqual(media_kind_from_refs([object()], []), "image")

    def test_video(self):
        self.assertEqual(media_kind_from_refs([], [object()]), "video")

    def test_mixed(self):
        self.assertEqual(media_kind_from_refs([object()], [object()]), "mixed")


class TestProcessedContent(unittest.TestCase):
    def test_construct(self):
        p = ProcessedContent(
            item_id="x1",
            translated_text="翻译稿",
            llm_summary="搬运摘要",
            processing_status="llm_done",
            model_ref="deepseek",
        )
        self.assertEqual(p.item_id, "x1")
        self.assertEqual(p.translated_text, "翻译稿")
        self.assertEqual(p.llm_summary, "搬运摘要")
        self.assertEqual(p.processing_status, "llm_done")
        self.assertEqual(p.model_ref, "deepseek")

    def test_defaults(self):
        p = ProcessedContent(item_id="x1")
        self.assertIsNone(p.translated_text)
        self.assertIsNone(p.llm_summary)
        self.assertEqual(p.processing_status, "raw")
        self.assertIsNone(p.model_ref)


if __name__ == "__main__":
    unittest.main()
