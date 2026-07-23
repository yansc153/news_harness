from pathlib import Path
import json


def test_xueqiu_headless_export_allows_configured_20_item_batch() -> None:
    script = Path("scripts/xueqiu_headless_export.mjs").read_text(encoding="utf-8")
    assert "Math.min(20, Number(arg(\"--limit\", \"10\")))" in script

    config = json.loads(Path("configs/all_source_runner.json").read_text(encoding="utf-8"))
    xueqiu_limits = {
        source["source"]: source["batch_limit"]
        for source in config["sources"]
        if str(source["source"]).startswith("xueqiu_")
    }
    assert xueqiu_limits == {"xueqiu_hot": 20, "xueqiu_daren": 20}
    assert config["batch_policy"]["max_items_per_source_per_run"] == 20


def test_xueqiu_headless_export_fails_below_minimum_confirmed_rows() -> None:
    script = Path("scripts/xueqiu_headless_export.mjs").read_text(encoding="utf-8")
    assert "const MIN_CONFIRMED_XUEQIU_ROWS = 5;" in script
    assert "xueqiu_min_rows_not_met" in script
