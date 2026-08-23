"""Test targeted stock discussion fetching logic without hitting real Xueqiu."""

import unittest

from news_harness.xueqiu_targeted import (
    apply_comment_filter,
    deduplicate_observations,
    map_discussion_row_to_observation,
    merge_stock_results,
)


class TestApplyCommentFilter(unittest.TestCase):
    def test_below_threshold_filtered(self):
        rows = [{"reply_count": 5, "id": "1"}, {"reply_count": 10, "id": "2"}, {"reply_count": 20, "id": "3"}]
        passed = apply_comment_filter(rows, min_comments=10)
        self.assertEqual(len(passed), 2)
        self.assertEqual([r["id"] for r in passed], ["2", "3"])

    def test_empty_rows_ok(self):
        passed = apply_comment_filter([], min_comments=10)
        self.assertEqual(passed, [])


class TestMapDiscussionRow(unittest.TestCase):
    def test_basic_mapping(self):
        row = {
            "id": "12345",
            "user_id": "999",
            "text": "Some analysis about the stock",
            "created_at": 1724302800000,
            "reply_count": 15,
            "fav_count": 42,
            "retweet_count": 3,
            "full_text_status": "full_text_observed",
            "user": {"screen_name": "analyst_wang"},
        }
        obs = map_discussion_row_to_observation(
            row, symbol="SH600519", stock_name="贵州茅台", themes=["白酒"],
        )
        self.assertEqual(obs["source"], "xueqiu_targeted")
        self.assertEqual(obs["target_symbol"], "SH600519")
        self.assertIn("xueqiu.com/999/12345", obs["source_url"])
        self.assertEqual(obs["engagement_snapshot"]["metrics"]["comments"], 15)

    def test_missing_user_handled(self):
        row = {"id": "12345", "text": "text", "reply_count": 12}
        with self.assertRaisesRegex(ValueError, "canonical post URL"):
            map_discussion_row_to_observation(row, symbol="SZ000001", stock_name="test")

    def test_headless_images_and_canonical_url_are_preserved(self):
        row = {
            "id": "12345",
            "url": "https://xueqiu.com/999/12345",
            "text": "analysis",
            "reply_count": 12,
            "images": ["https://example.com/original.jpg"],
            "full_text_status": "full_text_observed",
        }
        obs = map_discussion_row_to_observation(row, symbol="SH600519", stock_name="贵州茅台")
        self.assertEqual(obs["source_url"], row["url"])
        self.assertEqual(obs["image_refs"][0]["url"], row["images"][0])
        self.assertEqual(obs["source_status_id"], "12345")


class TestDeduplicateObservations(unittest.TestCase):
    def test_duplicate_status_merges_target_refs(self):
        first = {"source_status_id": "1", "target_symbol": "SH600000", "target_symbols": ["SH600000"], "target_theme_refs": ["AI"]}
        second = {"source_status_id": "1", "target_symbol": "SZ000001", "target_symbols": ["SZ000001"], "target_theme_refs": ["医药"]}
        merged = deduplicate_observations([first, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["target_symbols"], ["SH600000", "SZ000001"])
        self.assertEqual(merged[0]["target_theme_refs"], ["AI", "医药"])


class TestMergeResults(unittest.TestCase):
    def test_merge_preserves_order(self):
        r1 = ([{"observation_id": "a"}], [])
        r2 = ([{"observation_id": "b"}], [{"error": "timeout"}])
        merged_obs, merged_errors = merge_stock_results([r1, r2])
        self.assertEqual(len(merged_obs), 2)
        self.assertEqual(len(merged_errors), 1)


if __name__ == "__main__":
    unittest.main()
