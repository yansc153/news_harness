from pathlib import Path


def test_xueqiu_headless_export_allows_configured_80_item_batch() -> None:
    script = Path("scripts/xueqiu_headless_export.mjs").read_text(encoding="utf-8")
    assert "Math.min(80, Number(arg(\"--limit\", \"10\")))" in script
