"""S4 — 雪球 source connector + Gate A/B/C 摄入筛选 TDD（RED first）。

覆盖（ARCHITECTURE.md §4.4 / D-10~D-16）：
- 纯映射 `xueqiu_observation_to_content_item`
- Gate A 账号级排除（块列表，确定性）
- Gate B 硬性门槛（字数/点赞/评论/配图）
- Gate C 个人账号优先（author_type 推导）
- `filter_batch`：拉 20、过闸、保底≥5（fallback 放宽阶梯，D-14/D-16）
- `XueqiuSourceConnector.fetch()` 编排（fetcher seam + 闸门）
"""

import json
import tempfile
import shutil
import unittest
from pathlib import Path

from news_harness.connectors.source.xueqiu import (
    XueqiuSourceConnector,
    xueqiu_observation_to_content_item,
)
from news_harness.connectors.source.xueqiu_gates import (
    apply_gate_a,
    apply_gate_b,
    derive_author_type,
    filter_batch,
    load_blocklist,
)
from news_harness.models import Author, ContentItem

XUEQIU_THRESHOLDS = {
    "min_chars": 500,
    "min_likes": 50,
    "min_comments": 10,
    "require_image": True,
}


def _sample_obs(observation_id="xq_1", *, copy_text=None, image_refs=None,
                likes=80, comments=15, user_id="u_personal", screen_name="personal_investor",
                blocked=False):
    if copy_text is None:
        copy_text = ("这是一篇足够长的雪球长文，讨论A股走势与个股逻辑，包含数据与观点。 " * 20).strip()
    if image_refs is None:
        image_refs = [{
            "image_ref_id": "img_x",
            "original_image_ref": "https://example.com/c.png",
            "thumbnail_ref": "https://example.com/c.png",
            "page_context_ref": "https://xueqiu.com/x",
            "context_position": "preview_image",
            "ownership_scope": "source_content_body",
            "image_role": "original_content_image",
            "evidence_eligible": True,
            "filter_status": "accepted_content_image",
            "dimensions": {"width": 800, "height": 600},
            "alt": "chart", "caption": "chart",
            "access_status": "public_candidate_unverified",
            "download_status": "pending_policy_check",
        }]
    return {
        "observation_id": observation_id,
        "source": "xueqiu_hot",
        "source_label": "雪球·热门",
        "source_url": f"https://xueqiu.com/{observation_id}",
        "canonical_url": f"https://xueqiu.com/{observation_id}",
        "author": screen_name,
        "user": {"id": user_id, "screen_name": screen_name, "identity": "个人投资者"},
        "published_at": "2026-07-22T00:00:00",
        "copy_text": copy_text,
        "image_refs": image_refs,
        "engagement": {"likes": likes, "comments": comments, "views": 2000},
        "topic_or_hook": "A股研判",
    }


def _blocklist_tmp(entries):
    d = tempfile.mkdtemp(prefix="xq_bl_")
    p = Path(d) / "xueqiu_blocklist.json"
    p.write_text(json.dumps({"version": "v1", "accounts": entries}, ensure_ascii=False), encoding="utf-8")
    return d, p


class TestXueqiuMapper(unittest.TestCase):
    def test_maps_core_and_user_id(self):
        obs = _sample_obs()
        item = xueqiu_observation_to_content_item(obs)
        self.assertEqual(item.platform, "xueqiu")
        self.assertEqual(item.user_id, "u_personal")
        self.assertEqual(item.author.name, "personal_investor")
        self.assertEqual(item.author.handle, "personal_investor")
        self.assertEqual(item.char_count, len(item.copy_text.strip()))
        self.assertEqual(item.content_kind, "post")
        self.assertEqual(item.media_kind, "image")
        self.assertEqual(item.ingest_gate, "passed")

    def test_short_text_char_count(self):
        item = xueqiu_observation_to_content_item(_sample_obs(copy_text="  短  "))
        self.assertEqual(item.char_count, 1)


class TestGateA(unittest.TestCase):
    def setUp(self):
        self.tmp, self.path = _blocklist_tmp([
            {"user_id": "u_blocked", "screen_name": "财联社", "handle": "cailianshe"},
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_blocked_by_user_id(self):
        obs = _sample_obs(user_id="u_blocked", screen_name="财联社")
        passed, reason = apply_gate_a(obs, load_blocklist(self.path))
        self.assertFalse(passed)
        self.assertEqual(reason, "dropped_blocklist")

    def test_blocked_by_screen_name(self):
        obs = _sample_obs(user_id="other", screen_name="财联社")
        passed, reason = apply_gate_a(obs, load_blocklist(self.path))
        self.assertFalse(passed)

    def test_personal_passes(self):
        obs = _sample_obs(user_id="u_personal", screen_name="personal_investor")
        passed, reason = apply_gate_a(obs, load_blocklist(self.path))
        self.assertTrue(passed)
        self.assertEqual(reason, "passed")


class TestGateB(unittest.TestCase):
    def test_all_pass(self):
        item = xueqiu_observation_to_content_item(_sample_obs())
        passed, reason = apply_gate_b(item, XUEQIU_THRESHOLDS)
        self.assertTrue(passed)
        self.assertEqual(reason, "passed")

    def test_short_text_dropped(self):
        item = xueqiu_observation_to_content_item(_sample_obs(copy_text="太短了"))
        passed, reason = apply_gate_b(item, XUEQIU_THRESHOLDS)
        self.assertFalse(passed)
        self.assertEqual(reason, "dropped_short")

    def test_low_likes_dropped(self):
        item = xueqiu_observation_to_content_item(_sample_obs(likes=10))
        passed, reason = apply_gate_b(item, XUEQIU_THRESHOLDS)
        self.assertFalse(passed)
        self.assertEqual(reason, "dropped_low_engagement")

    def test_low_comments_dropped(self):
        item = xueqiu_observation_to_content_item(_sample_obs(comments=2))
        passed, reason = apply_gate_b(item, XUEQIU_THRESHOLDS)
        self.assertFalse(passed)
        self.assertEqual(reason, "dropped_low_engagement")

    def test_no_image_dropped(self):
        item = xueqiu_observation_to_content_item(_sample_obs(image_refs=[]))
        passed, reason = apply_gate_b(item, XUEQIU_THRESHOLDS)
        self.assertFalse(passed)
        self.assertEqual(reason, "dropped_no_image")


class TestGateC(unittest.TestCase):
    def test_personal_author_type(self):
        obs = _sample_obs(user_id="u_personal", screen_name="personal_investor")
        self.assertEqual(derive_author_type(obs, []), "personal")

    def test_institutional_by_identity_marker(self):
        obs = _sample_obs(screen_name="某券商官方", user_id="u_x")
        obs["user"]["identity"] = "官方机构"
        self.assertEqual(derive_author_type(obs, []), "institutional")


class TestFilterBatch(unittest.TestCase):
    def setUp(self):
        self.tmp, self.path = _blocklist_tmp([
            {"user_id": "u_blocked", "screen_name": "财联社", "handle": "cailianshe"},
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mix(self):
        # 10 合格 + 5 被 Gate B 拦（短/无图/低赞） + 1 被 Gate A 拦
        obs = []
        for i in range(10):
            obs.append(_sample_obs(f"ok_{i}"))
        obs.append(_sample_obs("short", copy_text="太短"))
        obs.append(_sample_obs("noimg", image_refs=[]))
        obs.append(_sample_obs("lowlike", likes=5))
        obs.append(_sample_obs("lowcomment", comments=1))
        obs.append(_sample_obs("blocked", user_id="u_blocked", screen_name="财联社"))
        return obs

    def test_passes_floor_with_relaxation_off(self):
        obs = self._mix()
        passed, stats = filter_batch(
            obs, load_blocklist(self.path), XUEQIU_THRESHOLDS,
            batch_limit=20, floor=5, relax=False,
        )
        self.assertEqual(len(passed), 10)
        self.assertGreaterEqual(len(passed), 5)
        self.assertEqual(stats["gate_a_dropped"], 1)
        self.assertEqual(sum(stats["gate_b_dropped"].values()), 4)

    def test_relaxation_recovers_below_floor(self):
        # 仅 3 合格，但需要 5 → 放宽阶梯救回部分
        obs = [_sample_obs(f"ok_{i}") for i in range(3)]
        obs += [_sample_obs("lowlike", likes=5) for _ in range(4)]
        obs += [_sample_obs("noimg", image_refs=[]) for _ in range(4)]
        passed, stats = filter_batch(
            obs, load_blocklist(self.path), XUEQIU_THRESHOLDS,
            batch_limit=20, floor=5, relax=True,
        )
        # 放宽后 lowlike(仅赞不足) 在去掉赞门槛后入选；总数应≥5
        self.assertGreaterEqual(len(passed), 5)
        self.assertTrue(stats["relaxation_level"] >= 1)

    def test_batch_limit_caps_input(self):
        obs = [_sample_obs(f"ok_{i}") for i in range(50)]
        passed, stats = filter_batch(
            obs, load_blocklist(self.path), XUEQIU_THRESHOLDS,
            batch_limit=20, floor=5, relax=False,
        )
        self.assertLessEqual(len(passed), 20)


class _FakeFetcher:
    def __init__(self, observations, errors=None):
        self._observations = observations
        self._errors = errors or []
        self.called_with = None

    def __call__(self, source_config, availability):
        self.called_with = (source_config, availability)
        return (self._observations, self._errors)


class TestXueqiuSourceConnector(unittest.TestCase):
    def setUp(self):
        self.tmp, self.path = _blocklist_tmp([
            {"user_id": "u_blocked", "screen_name": "财联社", "handle": "cailianshe"},
        ])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fetch_applies_gates_and_blocklist(self):
        obs = [_sample_obs("ok_1"), _sample_obs("blocked", user_id="u_blocked", screen_name="财联社")]
        fetcher = _FakeFetcher(obs)
        conn = XueqiuSourceConnector(
            fetcher=fetcher,
            blocklist_path=str(self.path),
            thresholds=XUEQIU_THRESHOLDS,
            batch_limit=20, floor=5,
        )
        items = conn.fetch()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "ok_1")
        self.assertEqual(items[0].ingest_gate, "passed")

    def test_registry_knows_xueqiu_source(self):
        from news_harness.connectors.registry import ConnectorRegistry
        reg = ConnectorRegistry()
        reg.register(XueqiuSourceConnector)
        self.assertIn("xueqiu", reg.list_sources())


if __name__ == "__main__":
    unittest.main()
