from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_source_tabs_show_current_view_and_total_counts() -> None:
    app_js = (ROOT / "web/radar-timeline/app.js").read_text(encoding="utf-8")

    assert "function renderSourceTabs(recentItems, totalItems = recentItems)" in app_js
    assert "renderSourceTabs(recentItems, cleanItems)" in app_js
    assert "source-tab-total" in app_js
