# News Harness Operations

This is the runbook for the VPS candidate. It is intentionally short. When the
system is loud at 03:00, read this before spelunking through code.

## Healthy State

A healthy VPS candidate has:

- fresh `web/data/radar-timeline/timeline_feed.json`
- fresh `artifacts/manual_smoke/latest/source_run.json`
- fresh `artifacts/manual_smoke/latest/deepseek_scoring.json`
- `artifacts/manual_smoke/latest/revisit_schedule.json` with
  `1h / 4h` (primary) + `24h` (audit, sampled only)
- timeline page showing evidence-risk chips where evidence is weak
- website/API server reachable at `/` and `/api/health`
- healthcheck passing with `--max-age-minutes 90`

## One-Cycle Manual Smoke

```bash
python3 -m news_harness run-cycle \
  --source-config configs/all_source_runner.json \
  --score-config configs/deepseek_provider.example.json \
  --fixtures fixtures \
  --out web/data/radar-timeline/timeline_feed.json \
  --mode manual-smoke \
  --backend direct-cli
```

## Healthcheck

```bash
python3 -m news_harness healthcheck \
  --feed web/data/radar-timeline/timeline_feed.json \
  --source-run artifacts/manual_smoke/latest/source_run.json \
  --deepseek artifacts/manual_smoke/latest/deepseek_scoring.json \
  --revisit artifacts/manual_smoke/latest/revisit_schedule.json \
  --outcome artifacts/manual_smoke/latest/outcome.json \
  --eval artifacts/manual_smoke/latest/eval.json \
  --max-age-minutes 90 \
  --require-source x_list \
  --require-source reddit \
  --require-source xueqiu_hot \
  --require-source xueqiu_daren
```

## Website And API Server

Run locally or on VPS:

```bash
python3 -m news_harness serve \
  --host 0.0.0.0 \
  --port 8765 \
  --feed web/data/radar-timeline/timeline_feed.json \
  --artifact-dir artifacts/manual_smoke/latest
```

Check it:

```bash
curl -fsS http://127.0.0.1:8765/api/health
curl -fsS http://127.0.0.1:8765/api/timeline | python3 -m json.tool | head -80
```

Important: if the latest feed is fixture-backed, the website must show
`演示数据`. Do not relabel a fixture/manual-smoke proof as live production
content.

API endpoints:

- `/api/timeline`
- `/api/items`
- `/api/items/{item_id}`
- `/api/items/{item_id}/images`
- `/api/health`

Install the example website service:

```bash
sudo cp configs/systemd/news-harness-site.service.example /etc/systemd/system/news-harness-site.service
sudo systemctl daemon-reload
sudo systemctl enable --now news-harness-site.service
journalctl -u news-harness-site.service -n 120 --no-pager
```

Put Nginx/Caddy in front of port `8765` for TLS and domain routing. The Python
server is the app process, not the TLS terminator.

## MCP Connector

For another project or agent to read copy and image refs, configure MCP stdio:

```bash
python3 -m news_harness mcp \
  --feed web/data/radar-timeline/timeline_feed.json \
  --artifact-dir artifacts/manual_smoke/latest
```

Tools exposed:

- `get_latest_feed`
- `list_radar_items`
- `get_radar_item`
- `get_image_refs`
- `get_health`

MCP is read-only. It must not crawl sources, call DeepSeek, mutate artifacts,
or promote scoring rules. The website's `MCP 通道` section mirrors the command
and client config for operators. See `docs/MCP_CONNECTOR.md`.

## If `feed_fresh` Fails

What it means: the timeline feed is too old. The product page may still load,
but the harness is not proving a live cycle.

Do this:

1. Run the one-cycle manual smoke command above.
2. Check whether the timer is installed:

```bash
systemctl list-timers | grep news-harness || true
systemctl status news-harness-cycle.timer
systemctl status news-harness-cycle.service
```

3. If the service failed, inspect logs:

```bash
journalctl -u news-harness-cycle.service -n 120 --no-pager
```

4. Rerun healthcheck.

## If A Required Source Fails

Read `source_run.json` first:

```bash
python3 - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("artifacts/manual_smoke/latest/source_run.json").read_text())
for item in data.get("source_statuses", []):
    print(item.get("source"), item.get("status"), item.get("item_count"), item.get("structured_errors"))
PY
```

Common states:

- `x_quote_repost_original_unresolved`: quote wrapper was skipped because the
  original was unavailable. This is correct.
- `auth_or_challenge_required`: source requires login/session/CAPTCHA. Do not
  fake data.
- `xueqiu_full_text_not_confirmed`: the card may only have a list excerpt.
  Treat confidence as lower until detail extraction works.

## If DeepSeek Scoring Fails

The timeline may still be generated with fallback scoring, but do not treat it
as production confidence.

Check:

```bash
python3 -m news_harness score --config configs/deepseek_provider.example.json --mode manual-smoke
```

Then inspect:

```bash
python3 - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("artifacts/manual_smoke/latest/deepseek_scoring.json").read_text())
print(data.get("provider_called"), data.get("fallback_used"), data.get("structured_errors"))
PY
```

## If Revisit/Eval Is Missing

The first run can produce predictions immediately. Real outcome labels need
time. The fast windows are:

- `1h`
- `4h`
- `24h` (audit only)

`run-cycle` now runs due revisit and eval automatically. For a manual recovery,
run revisit/eval explicitly:

```bash
python3 -m news_harness revisit \
  --schedule artifacts/manual_smoke/latest/revisit_schedule.json \
  --source-run artifacts/manual_smoke/latest/source_run.json \
  --out artifacts/manual_smoke/latest/outcome.json

python3 -m news_harness eval \
  --scoring artifacts/manual_smoke/latest/deepseek_scoring.json \
  --outcome artifacts/manual_smoke/latest/outcome.json \
  --out artifacts/manual_smoke/latest/eval.json
```

Then rerun healthcheck. It should fail red until due revisit tasks have matching
outcome rows and those outcomes are joined into eval rows.

## Scheduler

Install the example timer on the VPS after copying the repo to `/opt/news_harness`
and placing repo-external env refs in `/run/news-harness/news_harness.env`.

```bash
sudo cp configs/systemd/news-harness-cycle.service.example /etc/systemd/system/news-harness-cycle.service
sudo cp configs/systemd/news-harness-cycle.timer.example /etc/systemd/system/news-harness-cycle.timer
sudo cp configs/systemd/news-harness-healthcheck.service.example /etc/systemd/system/news-harness-healthcheck.service
sudo cp configs/systemd/news-harness-site.service.example /etc/systemd/system/news-harness-site.service
sudo systemctl daemon-reload
sudo systemctl enable --now news-harness-cycle.timer
sudo systemctl enable --now news-harness-site.service
```

The cycle timer runs every 30 minutes. Healthcheck can be run after any cycle:

```bash
sudo systemctl start news-harness-healthcheck.service
journalctl -u news-harness-healthcheck.service -n 120 --no-pager
```

## Required Environment References

Use repo-external files only. Do not store secret values in this repo.

- `DEEPSEEK_API_KEY_FILE`
- `NEWS_HARNESS_X_COOKIE_FILE`
- `NEWS_HARNESS_MANUAL_SMOKE_ACK=I_UNDERSTAND_THIS_IS_READ_ONLY_MANUAL_SMOKE`
- `NEWS_HARNESS_REAL_SOURCE_SMOKE=1`
- `NEWS_HARNESS_DEEPSEEK_SMOKE=1`
- `NEWS_HARNESS_X_HEADLESS=1`
- `NEWS_HARNESS_REDDIT_COOKIE_FILE`
- `NEWS_HARNESS_XUEQIU_HEADLESS=1`
- `NEWS_HARNESS_XUEQIU_STORAGE_STATE_FILE`
- `NEWS_HARNESS_XUEQIU_EXPORT_DIR`

Use `configs/all_source_runner.json` for the production-shaped three-source
candidate: X list, Reddit, Xueqiu `热门`, and Xueqiu `达人`. Do not describe it
as production ready until a real run and healthcheck verify all four required
source IDs.

## Promotion Rule

Do not automatically promote scoring, prompts, rules, model choices, or connector
behavior from manual-smoke or fixture-backed evals. Promotion must remain
`shadow_only` until a separate eval report proves before/after improvement and
regression safety.

## Do Not Do This

- Do not hand-edit engagement numbers.
- Do not turn quote repost wrappers into candidates unless the quoted original
  is preserved.
- Do not replace missing evidence images with generated images.
- Do not call a stale feed healthy because the page still renders.
- Do not present model scores as investment advice.
- Do not auto-promote learning changes without eval proof and rollback criteria.

## Fast Links

- Product: `/web/radar-timeline/index.html`
- Website/API: `python3 -m news_harness serve --host 0.0.0.0 --port 8765`
- MCP: `docs/MCP_CONNECTOR.md`
- Docs map: `/docs/index.html`
- Go/no-go: `docs/go-no-go-production-readiness.md`
