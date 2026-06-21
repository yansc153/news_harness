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
FEED_PATH="${NEWS_HARNESS_FEED_PATH:-web/data/radar-timeline/timeline_feed.json}"
mkdir -p "$(dirname "$FEED_PATH")"

CYCLE_MODE="${NEWS_HARNESS_CYCLE_MODE:-manual-smoke}"
CYCLE_BACKEND="${NEWS_HARNESS_CYCLE_BACKEND:-direct-cli}"
CYCLE_TIMEOUT_SECONDS="${NEWS_HARNESS_CYCLE_TIMEOUT_SECONDS:-1500}"
SITE_PORT="${NEWS_HARNESS_SITE_PORT:-8765}"

if [ "$CYCLE_MODE" = "manual-smoke" ]; then
    for artifact in \
        "$FEED_PATH" \
        /app/artifacts/manual_smoke/latest/source_run.json \
        /app/artifacts/manual_smoke/latest/deepseek_scoring.json \
        /app/artifacts/manual_smoke/latest/revisit_schedule.json \
        /app/artifacts/manual_smoke/latest/outcome.json \
        /app/artifacts/manual_smoke/latest/eval.json \
        /app/artifacts/manual_smoke/latest/image_assets.json; do
        if [ -f "$artifact" ] && python3 - "$artifact" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    raise SystemExit(1)

manual = payload.get("manual_smoke") if isinstance(payload.get("manual_smoke"), dict) else {}
scoring = manual.get("scoring") if isinstance(manual.get("scoring"), dict) else {}
runtime = payload.get("rolling_runtime") if isinstance(payload.get("rolling_runtime"), dict) else {}
is_demo = bool(
    payload.get("fixture_only") is True
    or payload.get("no_real_source_access") is True
    or payload.get("feed_status") == "demo"
    or payload.get("mode") == "dry_run"
    or manual.get("backend") == "fixture"
    or scoring.get("fallback_used") == "fixture_scoring"
    or "fixture" in str(runtime.get("runtime_stage", ""))
)
raise SystemExit(0 if is_demo else 1)
PY
        then
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
        timeout "$CYCLE_TIMEOUT_SECONDS" python3 -m news_harness run-cycle \
            --source-config configs/all_source_runner.json \
            --score-config configs/deepseek_provider.json \
            --fixtures fixtures \
            --out "$FEED_PATH" \
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
    --feed "$FEED_PATH" \
    --artifact-dir artifacts/manual_smoke/latest
