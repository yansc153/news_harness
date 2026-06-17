#!/usr/bin/env sh
# News Harness Docker entrypoint — runs site server + cycle loop in background
set -e

echo "=== News Harness V1 ==="

# Load secrets env if present
if [ -f /run/news-harness/news_harness.env ]; then
    set -a
    . /run/news-harness/news_harness.env
    set +a
    echo "  secrets env loaded"
fi

# Ensure artifact dirs exist
mkdir -p /app/artifacts/manual_smoke/latest
mkdir -p /app/web/radar-timeline

CYCLE_MODE="${NEWS_HARNESS_CYCLE_MODE:-manual-smoke}"
CYCLE_BACKEND="${NEWS_HARNESS_CYCLE_BACKEND:-direct-cli}"
SITE_PORT="${NEWS_HARNESS_SITE_PORT:-8765}"

if [ "$CYCLE_MODE" = "manual-smoke" ]; then
    for artifact in \
        /app/web/radar-timeline/timeline_feed.json \
        /app/artifacts/manual_smoke/latest/source_run.json \
        /app/artifacts/manual_smoke/latest/deepseek_scoring.json \
        /app/artifacts/manual_smoke/latest/revisit_schedule.json \
        /app/artifacts/manual_smoke/latest/outcome.json \
        /app/artifacts/manual_smoke/latest/eval.json \
        /app/artifacts/manual_smoke/latest/image_assets.json; do
        if [ -f "$artifact" ] && grep -Eq 'fixture_|fixture-only|fixture_backed|"fixture_only": true|"mode": "dry_run"|"feed_status": "demo"' "$artifact"; then
            rm -f "$artifact"
        fi
    done
fi

# Run cycle once immediately, then every 30 minutes in background
(
    echo "  cycle runner: waiting 10s for site server to start..."
    sleep 10
    while true; do
        echo "  [cycle] $(date -Iseconds) starting mode=$CYCLE_MODE backend=$CYCLE_BACKEND..."
        python3 -m news_harness run-cycle \
            --source-config configs/all_source_runner.json \
            --score-config configs/deepseek_provider.json \
            --fixtures fixtures \
            --out web/radar-timeline/timeline_feed.json \
            --mode "$CYCLE_MODE" \
            --backend "$CYCLE_BACKEND" \
            2>&1 || echo "  [cycle] exited with code $?"
        echo "  [cycle] $(date -Iseconds) done. sleeping 30m..."
        sleep 1800
    done
) &
CYCLE_PID=$!

echo "  cycle runner PID=$CYCLE_PID"

# Run site server in foreground (keeps container alive)
exec python3 -m news_harness serve \
    --host 0.0.0.0 \
    --port "$SITE_PORT" \
    --feed web/radar-timeline/timeline_feed.json \
    --artifact-dir artifacts/manual_smoke/latest
