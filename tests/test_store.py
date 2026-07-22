"""S2 store 层测试（TDD RED → GREEN）。

覆盖 db.py / media.py / cache.py / janitor.py 四个模块的核心契约：
- db：去重、发布状态、处理状态、rights_status、未发布超 TTL 清理
- media：哈希去重、refcount、manifest(size/refcount/last_accessed/rights_status)、total_bytes
- cache：TTL 过期、容量上限 LRU 淘汰
- janitor：配额 LRU 淘汰、未发布超 TTL 清理、refcount=0 立即删

仅用标准库 + unittest，从项目根 `python3 -m unittest` 运行。
"""

import os
import shutil
import tempfile
import time
import unittest

from news_harness.models import Author, ContentItem, MediaRef
from news_harness.store.db import StoreDB
from news_harness.store.media import MediaLibrary
from news_harness.store.cache import ResponseCache
from news_harness.store.janitor import Janitor


def _make_item(item_id, observed_at, published=False, image_refs=None):
    return ContentItem(
        id=item_id,
        platform="xueqiu",
        source_label="Xueqiu",
        source_url=f"https://xueqiu.com/{item_id}",
        author=Author(name="alice", handle="alice", follower_count=100),
        author_type="personal",
        copy_text="x" * 600,
        char_count=600,
        observed_at=observed_at,
        published_at=None,
        engagement={"likes": 80, "comments": 20},
        content_kind="original",
        ingest_gate="passed",
        media_kind="image" if image_refs else "text",
        image_refs=image_refs or [],
        video_refs=[],
        rights_status="ok",
    )


class TestStoreDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nh_db_")
        self.db = StoreDB(os.path.join(self.tmp, "meta.sqlite"))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_upsert_dedup_by_id(self):
        item = _make_item("a1", "2020-01-01T00:00:00")
        self.assertTrue(self.db.upsert_item(item))   # new
        self.assertFalse(self.db.upsert_item(item))  # duplicate
        self.assertEqual(self.db.count(), 1)
        self.assertTrue(self.db.has_item("a1"))

    def test_get_roundtrip_fields(self):
        item = _make_item("b1", "2020-01-01T00:00:00")
        self.db.upsert_item(item)
        got = self.db.get_item("b1")
        self.assertIsNotNone(got)
        self.assertEqual(got.platform, "xueqiu")
        self.assertEqual(got.char_count, 600)
        self.assertEqual(got.author.handle, "alice")
        self.assertEqual(got.engagement["likes"], 80)

    def test_mark_published_and_processing_and_rights(self):
        item = _make_item("c1", "2020-01-01T00:00:00")
        self.db.upsert_item(item)
        self.assertFalse(self.db.is_published("c1"))
        self.db.mark_published("c1")
        self.assertTrue(self.db.is_published("c1"))
        self.db.set_processing_status("c1", "llm_done")
        self.db.set_rights_status("c1", "restricted")
        got = self.db.get_item("c1")
        self.assertEqual(got.processing_status, "llm_done")
        self.assertEqual(got.rights_status, "restricted")

    def test_list_unpublished_older_than(self):
        old = _make_item("old1", "2020-01-01T00:00:00")      # > 7 days
        recent = _make_item("new1", _now_iso())              # today
        pub_old = _make_item("pub1", "2020-01-01T00:00:00")
        self.db.upsert_item(old)
        self.db.upsert_item(recent)
        self.db.upsert_item(pub_old)
        self.db.mark_published("pub1")
        due = self.db.list_unpublished_older_than(7)
        self.assertIn("old1", due)
        self.assertNotIn("new1", due)
        self.assertNotIn("pub1", due)
        self.assertEqual(len(due), 1)

    def test_delete_item(self):
        item = _make_item("d1", "2020-01-01T00:00:00")
        self.db.upsert_item(item)
        self.db.delete_item("d1")
        self.assertIsNone(self.db.get_item("d1"))
        self.assertFalse(self.db.has_item("d1"))


class TestMediaLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nh_media_")
        self.lib = MediaLibrary(os.path.join(self.tmp, "media"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hash_dedup_single_file_and_refcount(self):
        data = b"hello-media"
        h1 = self.lib.add(data, "image/png")
        h2 = self.lib.add(data, "image/png")  # same content
        self.assertEqual(h1, h2)
        self.assertTrue(self.lib.resolve(h1).endswith(".png"))
        self.assertEqual(self.lib.total_bytes(), len(data))
        man = self.lib.get_manifest(h1)
        self.assertEqual(man["refcount"], 2)
        self.assertEqual(man["size"], len(data))
        self.assertEqual(man["rights_status"], "unknown")
        self.assertIn("last_accessed", man)

    def test_refcount_zero_deletes_file(self):
        data = b"delete-me"
        h = self.lib.add(data, "image/jpeg")
        self.assertIsNotNone(self.lib.resolve(h))
        self.assertEqual(self.lib.get_manifest(h)["refcount"], 1)
        self.lib.decrement_refcount(h)   # refcount -> 0 -> entry removed immediately
        self.assertIsNone(self.lib.get_manifest(h))
        self.assertIsNone(self.lib.resolve(h))     # file removed
        self.assertEqual(self.lib.total_bytes(), 0)
        self.assertNotIn(h, self.lib.list_all())

    def test_path_layout_sha256_prefix(self):
        h = self.lib.add(b"abc", "image/png")
        path = self.lib.resolve(h)
        # media/{sha256[:2]}/{sha256}.ext
        self.assertIn(os.path.join(h[:2], h + ".png"), path)

    def test_record_access_updates_last_accessed(self):
        h = self.lib.add(b"zzz", "image/png")
        before = self.lib.get_manifest(h)["last_accessed"]
        time.sleep(0.01)
        self.lib.record_access(h)
        after = self.lib.get_manifest(h)["last_accessed"]
        self.assertNotEqual(before, after)

    def test_rights_status_stored(self):
        h = self.lib.add(b"r", "image/png", rights_status="ok")
        self.assertEqual(self.lib.get_manifest(h)["rights_status"], "ok")


class TestResponseCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nh_cache_")
        self.clock = [1000.0]
        self.cache = ResponseCache(
            os.path.join(self.tmp, "cache"),
            ttl_seconds=3600,
            max_mb=512,
            clock=lambda: self.clock[0],
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_put_get(self):
        self.cache.put("k1", b"payload")
        self.assertEqual(self.cache.get("k1"), b"payload")

    def test_ttl_expiry(self):
        self.cache.put("k2", b"data")
        self.clock[0] += 99999  # jump far into future
        self.assertIsNone(self.cache.get("k2"))
        self.assertTrue(self.cache.is_expired("k2"))

    def test_evict_expired(self):
        self.cache.put("e1", b"x")
        self.clock[0] += 99999
        n = self.cache.evict_expired()
        self.assertEqual(n, 1)
        self.assertIsNone(self.cache.get("e1"))

    def test_capacity_lru_eviction(self):
        # 0.001 MB ≈ 1 KB; three 500-byte entries overflow → evict oldest
        small = ResponseCache(
            os.path.join(self.tmp, "cache2"),
            ttl_seconds=999999,
            max_mb=0.001,
            clock=lambda: self.clock[0],
        )
        small.put("a", b"x" * 500)  # oldest
        self.clock[0] += 1
        small.put("b", b"y" * 500)
        self.clock[0] += 1
        small.put("c", b"z" * 500)  # newest, total > cap
        removed = small.enforce_capacity()
        self.assertGreaterEqual(removed, 1)
        # oldest key 'a' should be gone, newest 'c' should survive
        self.assertIsNone(small.get("a"))
        self.assertEqual(small.get("c"), b"z" * 500)


class TestJanitor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nh_jan_")
        self.db = StoreDB(os.path.join(self.tmp, "meta.sqlite"))
        self.lib = MediaLibrary(os.path.join(self.tmp, "media"))
        # tiny quota so a single 1KB media file exceeds it
        self.janitor = Janitor(self.lib, self.db, quota_gb=0.0000005, ttl_days=7)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_prune_zero_refcount(self):
        h = self.lib.add(b"orphan", "image/png")
        # simulate a residual zero-refcount entry (manifest desync) that the
        # janitor's defensive prune must clean up
        self.lib._manifest[h]["refcount"] = 0
        self.lib._save_manifest()
        report = self.janitor.run()
        self.assertIn(h, report["media_pruned"])
        self.assertIsNone(self.lib.resolve(h))

    def test_quota_lru_eviction(self):
        h_old = self.lib.add(b"o" * 1024, "image/png")
        self.lib.record_access(h_old)
        h_new = self.lib.add(b"n" * 1024, "image/png")
        self.lib.record_access(h_new)
        report = self.janitor.run()
        # over quota → at least one evicted
        self.assertGreaterEqual(len(report["media_evicted"]), 1)
        self.assertLessEqual(self.lib.total_bytes(), 1024)  # under quota

    def test_clean_unpublished_expired_and_dry_run(self):
        # item observed long ago, unpublished
        item = _make_item("exp1", "2020-01-01T00:00:00")
        h = self.lib.add(b"img" * 200, "image/png")
        item.image_refs = [MediaRef(url="u", sha256=h, rights_status="ok")]
        item.media_kind = "image"
        self.db.upsert_item(item)
        # dry run must not mutate
        dry = self.janitor.plan()
        self.assertTrue(dry["dry_run"])
        self.assertIn("exp1", dry["items_purged"])
        self.assertIsNotNone(self.db.get_item("exp1"))  # not yet deleted
        self.assertIsNotNone(self.lib.resolve(h))        # media still present
        # real run
        real = self.janitor.run()
        self.assertFalse(real["dry_run"])
        self.assertIsNone(self.db.get_item("exp1"))
        # media refcount should drop to 0 and be pruned
        self.assertIsNone(self.lib.resolve(h))


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())


if __name__ == "__main__":
    unittest.main()
