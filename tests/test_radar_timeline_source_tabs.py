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
    assert 'sortMode: "published_at"' in app_js
    assert 'default_sort: "published_at"' in app_js
    assert "state.sortTouched = true" in app_js
    assert '"./timeline_feed.json"' not in app_js
    assert "/opt/news_harness/web/data/radar-timeline/timeline_feed.json" in app_js


def test_run_status_explains_refresh_and_revisit_state() -> None:
    html = (ROOT / "web/radar-timeline/index.html").read_text(encoding="utf-8")
    app_js = (ROOT / "web/radar-timeline/app.js").read_text(encoding="utf-8")

    assert 'id="runStatus"' in html
    assert "function renderRunStatus(feed, items, loadedFrom)" in app_js
    assert "页面每 ${refreshSeconds} 秒读取一次，后台跑完才会变" in app_js
    assert "等 1h/4h 回看" in app_js
    assert "最新预测" in app_js
    assert "renderRunStatus(feed, items, loadedFrom)" in app_js
