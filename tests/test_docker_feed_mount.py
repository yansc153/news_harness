from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hostinger_feed_volume_does_not_mask_radar_static_assets() -> None:
    compose = (ROOT / "docker-compose.hostinger.yml").read_text(encoding="utf-8")

    assert "news_harness_feed:/app/web/data/radar-timeline" in compose
    assert "news_harness_feed:/app/web/radar-timeline" not in compose
    assert "NEWS_HARNESS_FEED_PATH=web/data/radar-timeline/timeline_feed.json" in compose
    assert "NEWS_HARNESS_CYCLE_TIMEOUT_SECONDS=1500" in compose
    assert ".get('status') == 'ok'" in compose


def test_docker_entrypoint_uses_configurable_feed_path() -> None:
    entrypoint = (ROOT / "scripts/docker_entrypoint.sh").read_text(encoding="utf-8")

    assert 'FEED_PATH="${NEWS_HARNESS_FEED_PATH:-web/radar-timeline/timeline_feed.json}"' in entrypoint
    assert 'CYCLE_TIMEOUT_SECONDS="${NEWS_HARNESS_CYCLE_TIMEOUT_SECONDS:-1500}"' in entrypoint
    assert 'timeout "$CYCLE_TIMEOUT_SECONDS" python3 -m news_harness run-cycle' in entrypoint
    assert '--out "$FEED_PATH"' in entrypoint
    assert '--feed "$FEED_PATH"' in entrypoint
