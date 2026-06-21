from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hostinger_feed_volume_does_not_mask_radar_static_assets() -> None:
    compose = (ROOT / "docker-compose.hostinger.yml").read_text(encoding="utf-8")

    assert "news_harness_feed:/app/web/data/radar-timeline" in compose
    assert "news_harness_feed:/app/web/radar-timeline" not in compose
    assert "NEWS_HARNESS_FEED_PATH=web/data/radar-timeline/timeline_feed.json" in compose
    assert "NEWS_HARNESS_CYCLE_TIMEOUT_SECONDS=1500" in compose
    assert "NEWS_HARNESS_XUEQIU_DETAIL_DELAY_MIN_MS=400" in compose
    assert "NEWS_HARNESS_XUEQIU_DETAIL_DELAY_MAX_MS=1200" in compose
    assert ".get('status') == 'ok'" in compose


def test_hostinger_uses_platform_traefik_router() -> None:
    compose = (ROOT / "docker-compose.hostinger.yml").read_text(encoding="utf-8")

    assert "traefik.enable=true" in compose
    assert "traefik.http.routers.news-harness.rule=Host(`${NEWS_HARNESS_DOMAIN:-newshardness.hellopepper.work}`)" in compose
    assert "traefik.http.services.news-harness.loadbalancer.server.port=8765" in compose
    assert "network_mode: host" not in compose
    assert "image: caddy:alpine" not in compose
    assert "docker/Dockerfile.caddy" not in compose


def test_docker_entrypoint_uses_configurable_feed_path() -> None:
    entrypoint = (ROOT / "scripts/docker_entrypoint.sh").read_text(encoding="utf-8")

    assert 'FEED_PATH="${NEWS_HARNESS_FEED_PATH:-web/data/radar-timeline/timeline_feed.json}"' in entrypoint
    assert 'CYCLE_TIMEOUT_SECONDS="${NEWS_HARNESS_CYCLE_TIMEOUT_SECONDS:-1500}"' in entrypoint
    assert 'timeout "$CYCLE_TIMEOUT_SECONDS" python3 -m news_harness run-cycle' in entrypoint
    assert '--out "$FEED_PATH"' in entrypoint
    assert '--feed "$FEED_PATH"' in entrypoint


def test_caddy_sidecar_serves_http_only_behind_hostinger() -> None:
    caddyfile = (ROOT / "docker/Caddyfile").read_text(encoding="utf-8")

    assert caddyfile.startswith("# News Harness V1")
    assert ":80 {" in caddyfile
    assert ":8443" not in caddyfile
    assert "{$NEWS_HARNESS_DOMAIN" not in caddyfile


def test_runtime_defaults_use_non_static_feed_path() -> None:
    assert not (ROOT / "web/radar-timeline/timeline_feed.json").exists()
    for relative in ("news_harness/artifact_api.py", "news_harness/health.py", "news_harness/all_source.py"):
        content = (ROOT / relative).read_text(encoding="utf-8")
        assert 'ROOT / "web" / "data" / "radar-timeline" / "timeline_feed.json"' in content
