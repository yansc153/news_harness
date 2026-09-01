"""Production wiring tests for the KPL-guided Xueqiu source."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from news_harness import direct_cli_backend


ROOT = Path(__file__).resolve().parent.parent


class TestTargetedProductionWiring(unittest.TestCase):
    def test_production_source_config_is_valid_json(self):
        config = json.loads((ROOT / "configs" / "all_source_runner.json").read_text(encoding="utf-8"))
        self.assertEqual([source["source"] for source in config["sources"]], ["xueqiu_targeted"])

    def test_target_discovery_feeds_xueqiu_collection(self):
        target_set = {
            "object_type": "HourlyTargetSet",
            "status": "ok",
            "stock_count": 1,
            "target_stocks": [{"symbol": "SH600519", "stock_name": "贵州茅台", "theme_ids": ["消费"]}],
            "structured_errors": [],
            "endpoint_results": [],
            "output_hash": "abc",
        }
        observations = [{"observation_id": "obs-1", "source": "xueqiu_targeted"}]
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(direct_cli_backend, "HOURLY_TARGET_ARTIFACT", Path(tmp) / "target.json"), \
             patch.object(direct_cli_backend, "build_target_set", return_value=target_set) as discover, \
             patch.object(direct_cli_backend, "collect_stock_discussions", return_value={
                 "observations": observations,
                 "structured_errors": [],
                 "raw_row_count": 1,
                 "threshold_pass_count": 1,
                 "duplicate_count": 0,
                 "rejected_incomplete_count": 0,
                 "symbol_results": [{"symbol": "SH600519", "status": "ok"}],
                 "attempt_warnings": [],
             }) as collect:
            result, errors = direct_cli_backend._fetch_xueqiu_targeted({
                "targeting_config_ref": "configs/hourly_targeting.v1.json",
                "batch_limit": 20,
            })
        discover.assert_called_once()
        collect.assert_called_once()
        self.assertEqual(result, observations)
        self.assertEqual(errors, [])

    def test_no_targets_without_errors_is_legitimate(self):
        target_set = {
            "object_type": "HourlyTargetSet", "status": "no_targets", "stock_count": 0,
            "target_stocks": [], "structured_errors": [], "endpoint_results": [], "output_hash": "abc",
        }
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(direct_cli_backend, "HOURLY_TARGET_ARTIFACT", Path(tmp) / "target.json"), \
             patch.object(direct_cli_backend, "build_target_set", return_value=target_set):
            result, errors = direct_cli_backend._fetch_xueqiu_targeted({})
        self.assertEqual(result, [])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
