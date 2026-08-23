"""Test target discovery from fixture KPL responses."""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(name):
    return json.loads((ROOT / "tests" / "fixtures" / name).read_text(encoding="utf-8"))


class TestNormalizeKplResponse(unittest.TestCase):
    """Each KPL response type normalizes to themes or stocks."""

    def test_kpl41_plate_ranking_produces_themes(self):
        from news_harness.kaipanla_targets import normalize_kpl_response
        raw = _load("kpl_41_sample.json")
        themes = normalize_kpl_response("KPL-41", raw)
        self.assertGreater(len(themes), 0)
        theme = themes[0]
        self.assertIn("theme_name", theme)
        self.assertIn("pct_change", theme)

    def test_kpl47_limitup_reason_produces_stock_list(self):
        from news_harness.kaipanla_targets import normalize_kpl_response
        raw = _load("kpl_47_sample.json")
        stocks = normalize_kpl_response("KPL-47", raw)
        self.assertGreater(len(stocks), 0)
        stock = stocks[0]
        self.assertIn("symbol", stock)
        self.assertIn("stock_name", stock)

    def test_errcode_nonzero_raises(self):
        from news_harness.kaipanla_targets import normalize_kpl_response
        with self.assertRaises(ValueError):
            normalize_kpl_response("KPL-41", {"errcode": "1"})


class TestNormalizeSymbol(unittest.TestCase):
    def test_six_prefix_gets_sh(self):
        from news_harness.kaipanla_targets import normalize_symbol
        self.assertEqual(normalize_symbol("600000"), "SH600000")

    def test_zero_prefix_gets_sz(self):
        from news_harness.kaipanla_targets import normalize_symbol
        self.assertEqual(normalize_symbol("000001"), "SZ000001")

    def test_three_prefix_gets_sz(self):
        from news_harness.kaipanla_targets import normalize_symbol
        self.assertEqual(normalize_symbol("300750"), "SZ300750")

    def test_existing_symbol_passthrough(self):
        from news_harness.kaipanla_targets import normalize_symbol
        self.assertEqual(normalize_symbol("SH600519"), "SH600519")


class TestDeduplicateStocks(unittest.TestCase):
    def test_dedup_across_themes(self):
        from news_harness.kaipanla_targets import deduplicate_stocks
        themes_with_stocks = [
            {"theme_id": "t1", "stocks": [{"symbol": "SH600000", "stock_name": "A"}, {"symbol": "SZ000001", "stock_name": "B"}]},
            {"theme_id": "t2", "stocks": [{"symbol": "SH600000", "stock_name": "A"}, {"symbol": "SH600519", "stock_name": "C"}]},
        ]
        result = deduplicate_stocks(themes_with_stocks, max_total=12)
        symbols = [s["symbol"] for s in result]
        self.assertEqual(len(symbols), len(set(symbols)))
        self.assertIn("SH600000", symbols)
        self.assertIn("SH600519", symbols)

    def test_max_total_truncation(self):
        from news_harness.kaipanla_targets import deduplicate_stocks
        themes_with_stocks = [
            {"theme_id": f"t{i}", "stocks": [{"symbol": f"SH{600000+i:06d}", "stock_name": f"S{i}"} for i in range(5)]}
            for i in range(5)
        ]
        result = deduplicate_stocks(themes_with_stocks, max_total=12)
        self.assertLessEqual(len(result), 12)


if __name__ == "__main__":
    unittest.main()
