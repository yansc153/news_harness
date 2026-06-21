from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_source_tabs_show_current_view_and_total_counts() -> None:
    app_js = (ROOT / "web/radar-timeline/app.js").read_text(encoding="utf-8")

    assert "function renderSourceTabs(recentItems, totalItems = recentItems)" in app_js
    assert "renderSourceTabs(recentItems, cleanItems)" in app_js
    assert '<span class="source-tab-total">${compactCount(primary)}</span>' in app_js
    assert "当前 ${compactCount(current)}" in app_js
    assert 'recentHours: ALL_HOURS' in app_js
    assert "if (recentHours === ALL_HOURS) return cleanItems" in app_js
    assert '"./timeline_feed.json"' not in app_js
    assert "/opt/news_harness/web/data/radar-timeline/timeline_feed.json" in app_js
