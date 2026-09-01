"""Verify production cycle semantics for legitimate zero-candidate hours."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from news_harness import all_source


class TestNoCandidatesIsNotFailure(unittest.TestCase):
    def test_zero_observations_without_failures_preserves_timeline(self):
        source_result = {"status": "ok", "observation_count": 0, "source_statuses": {"xueqiu_targeted": "ok"}}
        with TemporaryDirectory() as tmp, \
             patch.object(all_source, "run_sources", return_value=source_result), \
             patch.object(all_source, "generate_timeline_feed", return_value={"status": "ok", "item_count": 4}) as timeline:
            result = all_source._run_cycle_inner(
                source_config=Path(tmp) / "sources.json", score_config=Path(tmp) / "score.json",
                fixtures_dir=Path(tmp) / "fixtures", timeline_out=Path(tmp) / "timeline.json",
                dry_run=False, mode="manual-smoke", backend="direct-cli", store_path=Path(tmp) / "store.json",
            )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["score_status"], "skipped")
        self.assertEqual(result["timeline_status"], "ok")
        timeline.assert_called_once()

    def test_zero_observations_with_source_failure_is_error(self):
        source_result = {"status": "ok", "observation_count": 0, "source_statuses": {"xueqiu_targeted": "failed"}}
        with TemporaryDirectory() as tmp, patch.object(all_source, "run_sources", return_value=source_result):
            result = all_source._run_cycle_inner(
                source_config=Path(tmp) / "sources.json", score_config=Path(tmp) / "score.json",
                fixtures_dir=Path(tmp) / "fixtures", timeline_out=Path(tmp) / "timeline.json",
                dry_run=False, mode="manual-smoke", backend="direct-cli", store_path=Path(tmp) / "store.json",
            )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["errors"][0]["code"], "source_failed")


if __name__ == "__main__":
    unittest.main()
