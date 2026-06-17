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

# Run cycle once immediately, then every 30 minutes in background
(
    echo "  cycle runner: waiting 10s for site server to start..."
    sleep 10
    while true; do
        echo "  [cycle] $(date -Iseconds) starting..."
        python3 -m news_harness run-cycle \
            --source-config configs/all_source_runner.json \
            --score-config configs/deepseek_provider.json \
            --fixtures fixtures \
            --out web/radar-timeline/timeline_feed.json \
            --mode manual-smoke \
            --backend direct-cli \
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
    --port 8765 \
    --feed web/radar-timeline/timeline_feed.json \
    --artifact-dir artifacts/manual_smoke/latest
