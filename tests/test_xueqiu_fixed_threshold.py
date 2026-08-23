"""Verify that comment threshold cannot be relaxed regardless of candidate count."""

import unittest

from news_harness.connectors.source.xueqiu_gates import (
    apply_gate_b,
    filter_batch,
)
from news_harness.models import ContentItem


def _item(comments: int, likes: int = 0, images: int = 0, chars: int = 100) -> ContentItem:
    return ContentItem(
        id=f"test_{comments}_{likes}_{images}_{chars}",
        platform="xueqiu",
        source_label="test",
        source_url=f"https://xueqiu.com/x/{comments}",
        copy_text="x" * chars,
        char_count=chars,
        engagement={"likes": likes, "comments": comments},
        image_refs=[{"url": f"https://img/{i}.jpg"} for i in range(images)],
    )


class TestFixedCommentThreshold(unittest.TestCase):
    """The comment threshold is immutable; relaxation must not occur."""

    def test_below_threshold_rejected(self):
        ok, _ = apply_gate_b(_item(comments=9), {"min_comments": 10})
        self.assertFalse(ok)

    def test_at_threshold_accepted(self):
        ok, _ = apply_gate_b(_item(comments=10), {"min_comments": 10})
        self.assertTrue(ok)

    def test_above_threshold_accepted(self):
        ok, _ = apply_gate_b(_item(comments=50), {"min_comments": 10})
        self.assertTrue(ok)

    def test_zero_likes_still_passes(self):
        ok, _ = apply_gate_b(_item(comments=15, likes=0), {"min_comments": 10, "min_likes": 0})
        self.assertTrue(ok)

    def test_no_image_still_passes(self):
        ok, _ = apply_gate_b(_item(comments=15, images=0), {"min_comments": 10, "require_image": False})
        self.assertTrue(ok)

    def test_short_text_still_passes_when_min_chars_zero(self):
        ok, _ = apply_gate_b(_item(comments=15, chars=20), {"min_comments": 10, "min_chars": 0})
        self.assertTrue(ok)

    def test_filter_batch_never_relaxes(self):
        observations = [_make_obs(comments=c) for c in [3, 5, 8, 9]]
        passed, stats = filter_batch(
            observations, [], {"min_comments": 10}, batch_limit=20, floor=5, relax=True,
        )
        self.assertEqual(len(passed), 0)
        self.assertEqual(stats["passed"], 0)
        self.assertNotIn("relaxation_level", stats)

    def test_filter_batch_passes_qualifying(self):
        observations = [_make_obs(comments=c) for c in [3, 5, 12, 15]]
        passed, stats = filter_batch(
            observations, [], {"min_comments": 10}, batch_limit=20, floor=0, relax=False,
        )
        self.assertEqual(len(passed), 2)
        self.assertEqual(stats["passed"], 2)


def _make_obs(*, comments: int) -> dict:
    return {
        "observation_id": f"obs_{comments}",
        "copy_text": "x" * 200,
        "engagement": {"likes": 0, "comments": comments},
        "image_refs": [],
        "user": {},
        "source_url": f"https://xueqiu.com/{comments}",
    }


if __name__ == "__main__":
    unittest.main()
