from __future__ import annotations

import unittest

from news_harness.direct_cli_backend import _reddit_row_is_meaningful


class RedditQualityFilterTests(unittest.TestCase):
    def test_short_image_post_without_upvotes_is_not_meaningful(self) -> None:
        self.assertFalse(_reddit_row_is_meaningful("When a MAG7 turns into a BAG7", {"score": 28, "num_comments": 312}))

    def test_long_analysis_is_meaningful(self) -> None:
        self.assertTrue(_reddit_row_is_meaningful("analysis " * 80, {"score": 1, "num_comments": 0}))

    def test_short_post_with_strong_upvotes_is_meaningful(self) -> None:
        self.assertTrue(_reddit_row_is_meaningful("short but market-moving source", {"score": 900, "num_comments": 12}))


if __name__ == "__main__":
    unittest.main()
