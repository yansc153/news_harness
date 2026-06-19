from __future__ import annotations

import unittest

from news_harness.timeline import merge_manual_timeline_items


class TimelineMergeTests(unittest.TestCase):
    def test_manual_timeline_merge_keeps_history_and_updates_duplicates(self) -> None:
        prior_items = [
            {
                "id": "old-1",
                "source_url": "https://example.com/old",
                "published_at": "2026-06-18T01:00:00Z",
                "hotness_score": 10,
                "copy_text": "old text",
            },
            {
                "id": "same-old",
                "source_url": "https://example.com/same",
                "published_at": "2026-06-18T02:00:00Z",
                "hotness_score": 20,
                "copy_text": "stale text",
            },
        ]
        current_items = [
            {
                "id": "same-new",
                "source_url": "https://example.com/same",
                "published_at": "2026-06-18T03:00:00Z",
                "hotness_score": 30,
                "copy_text": "fresh text",
            },
            {
                "id": "new-1",
                "source_url": "https://example.com/new",
                "published_at": "2026-06-18T04:00:00Z",
                "hotness_score": 15,
                "copy_text": "new text",
            },
        ]

        merged = merge_manual_timeline_items(current_items, prior_items, max_items=10)

        self.assertEqual(3, len(merged))
        self.assertEqual("same-new", merged[0]["id"])
        self.assertNotIn("stale text", {item["copy_text"] for item in merged})
        self.assertEqual(
            {"https://example.com/old", "https://example.com/same", "https://example.com/new"},
            {item["source_url"] for item in merged},
        )

    def test_manual_timeline_merge_applies_item_cap_after_sorting(self) -> None:
        merged = merge_manual_timeline_items(
            current_items=[
                {"id": "low", "published_at": "2026-06-18T02:00:00Z", "hotness_score": 1},
                {"id": "high", "published_at": "2026-06-18T01:00:00Z", "hotness_score": 9},
            ],
            prior_items=[{"id": "mid", "published_at": "2026-06-18T03:00:00Z", "hotness_score": 5}],
            max_items=2,
        )

        self.assertEqual(["high", "mid"], [item["id"] for item in merged])

    def test_manual_timeline_merge_drops_challenge_pages(self) -> None:
        merged = merge_manual_timeline_items(
            current_items=[],
            prior_items=[
                {
                    "id": "blocked",
                    "copy_text": "Access Verification please slide to complete the verification process TraceID: abc",
                    "published_at": "2026-06-18T02:00:00Z",
                    "hotness_score": 99,
                },
                {"id": "real", "copy_text": "actual source text", "published_at": "2026-06-18T01:00:00Z", "hotness_score": 1},
            ],
            max_items=10,
        )

        self.assertEqual(["real"], [item["id"] for item in merged])

    def test_manual_timeline_merge_drops_challenge_image_metadata(self) -> None:
        merged = merge_manual_timeline_items(
            current_items=[],
            prior_items=[
                {
                    "id": "blocked-image",
                    "copy_text": "normal looking summary",
                    "image_refs": [{"alt": "Access Verification slide to verify TraceID: abc"}],
                    "published_at": "2026-06-18T02:00:00Z",
                    "hotness_score": 99,
                },
                {"id": "real", "copy_text": "actual source text", "published_at": "2026-06-18T01:00:00Z", "hotness_score": 1},
            ],
            max_items=10,
        )

        self.assertEqual(["real"], [item["id"] for item in merged])


if __name__ == "__main__":
    unittest.main()
