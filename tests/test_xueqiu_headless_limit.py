from pathlib import Path
import json


def test_xueqiu_headless_export_allows_configured_30_item_batch() -> None:
    script = Path("scripts/xueqiu_headless_export.mjs").read_text(encoding="utf-8")
    assert "Math.min(30, Number(arg(\"--limit\", \"10\")))" in script

    config = json.loads(Path("configs/all_source_runner.json").read_text(encoding="utf-8"))
    xueqiu_limits = {
        source["source"]: source["batch_limit"]
        for source in config["sources"]
        if str(source["source"]).startswith("xueqiu_")
    }
    assert xueqiu_limits == {"xueqiu_hot": 30, "xueqiu_daren": 30}
    assert config["batch_policy"]["max_items_per_source_per_run"] == 30
