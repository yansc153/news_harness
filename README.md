# News Harness Fixture-First MVP

This repository exposes a local harness loop. The default validation/replay path
is fixture-first, while explicit `manual-smoke` commands can attempt small
read-only real processing with repo-external secrets and redacted artifacts.
Manual smoke does not imply production connector readiness.

## Start Here

Use this path when you only need to prove the harness boots, writes a timeline
feed, and can be inspected locally. It does not touch real sources or secrets.

```bash
python3 -m news_harness validate fixtures
python3 -m news_harness run-cycle \
  --source-config configs/all_source_runner.example.json \
  --score-config configs/deepseek_provider.example.json \
  --fixtures fixtures \
  --out web/radar-timeline/timeline_feed.json \
  --dry-run
python3 -m news_harness serve --host 127.0.0.1 --port 8765
open http://127.0.0.1:8765/
```

The website server hosts the C-facing radar timeline and read-only JSON API.
For command help without reading this file:

```bash
python3 -m news_harness quickstart
```

## VPS Candidate Path

Use this path on the VPS after repo-external source credentials/session state
are configured. It performs one real manual-smoke source -> score -> timeline
cycle, then checks freshness and required sources.

```bash
python3 -m news_harness run-cycle \
  --source-config configs/all_source_runner.json \
  --score-config configs/deepseek_provider.example.json \
  --fixtures fixtures \
  --out web/radar-timeline/timeline_feed.json \
  --mode manual-smoke \
  --backend direct-cli

python3 -m news_harness healthcheck \
  --feed web/radar-timeline/timeline_feed.json \
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

If healthcheck fails, start with `docs/OPERATIONS.md`. The expected launch
status is in `docs/go-no-go-production-readiness.md`.

## Local Verification

Legacy script commands remain supported:

```bash
python3 scripts/validate_fixtures.py fixtures
python3 scripts/replay_fixture.py --fixtures fixtures --out artifacts/replay/latest
python3 -m unittest discover -s tests
```

The same local runtime is also exposed as a package:

```bash
python3 -m news_harness validate fixtures
python3 -m news_harness replay --fixtures fixtures --out artifacts/replay/latest
python3 -m news_harness inspect artifacts/replay/latest
python3 -m news_harness preflight --fixtures fixtures --artifacts artifacts/replay/latest --out artifacts/preflight/latest
python3 -m news_harness timeline --fixtures fixtures --out web/radar-timeline/timeline_feed.json
```

The replay command writes append-only event logs under `artifacts/replay/latest`
and per-run artifacts under `artifacts/replay/latest/runs/run_*/`.

The preflight command writes `artifacts/preflight/latest/readiness_report.json`
and `artifacts/preflight/latest/readiness_report.md`. It is a fixture-only
gate for deciding whether the next task may start a first low-risk read-only
real-source smoke. It does not access real sources, call DeepSeek, run
Agent-Reach, use login state, deploy anything, or prove production readiness.

The promotion fixture is intentionally `shadow_only`; fixture-first validation
cannot promote or activate production strategy behavior.

## Radar Timeline Feed

## Production Website And Read-Only API

The production-shaped website entrypoint is:

```bash
python3 -m news_harness serve \
  --host 0.0.0.0 \
  --port 8765 \
  --feed web/radar-timeline/timeline_feed.json \
  --artifact-dir artifacts/manual_smoke/latest
```

It redirects `/` to the static C-facing timeline at `/web/radar-timeline/`, plus
read-only JSON endpoints for other apps:

- `GET /api/timeline` - latest public feed projection
- `GET /api/items?limit=50&source=x_list` - candidate list
- `GET /api/items/{item_id}` - one item with copy, score, source and refs
- `GET /api/items/{item_id}/images` - image refs and local asset refs
- `GET /api/health` - artifact chain status

The API is read-only. It does not fetch sources, call models, mutate artifacts,
promote strategies, or expose non-public `fixture://` URLs as openable links.

## MCP Read-Only Connector

Other projects or agents can attach to the same artifact chain through the MCP
stdio server:

```bash
python3 -m news_harness mcp \
  --feed web/radar-timeline/timeline_feed.json \
  --artifact-dir artifacts/manual_smoke/latest
```

The MCP server exposes read-only tools:

- `get_latest_feed`
- `list_radar_items`
- `get_radar_item`
- `get_image_refs`
- `get_health`

This is the stable connector for transferring crawled copy and image evidence
to another project. It reads the same `timeline_feed.json` and artifact refs as
the website, so there is no second data source to drift. The website also
shows the MCP command and client config under the `MCP 通道` section.

`fixtures/sample_radar_timeline_feed.json` and replay-generated
`timeline_feed.json` define the first product-facing output surface:
`RadarTimelineItem`. It projects preserved evidence, structure, prediction, and
fixture outcome status into a feed that a website can consume directly.

`fixtures/rolling_source_schedule.json`, `fixtures/timeline_store.json`, and
`fixtures/revisit_schedule.json` upgrade the feed into a fixture-only rolling
runtime: shadow batch run, append/update store, 12h/24h revisit registration,
and rolling feed export. The store keeps a 120-hour / five-day window; expired
store items are excluded from exported `timeline_feed.json`.

Rolling source cadence is fixture-enforced:

- X list: every 1 hour, max 10 items per source run.
- Xueqiu `热门` and `达人`: every 30 minutes, max 10 items per source run.
- Reddit: every 1 hour, max 10 items per subreddit run across the configured
  subreddit pool.

The web feed exposes `view_config` for the default 120-hour view plus hotness or
published-time sorting, and `auto_refresh` so the site can poll the feed file.

## All-Source Runner And DeepSeek Fixture Scoring

`configs/all_source_runner.example.json`,
`configs/deepseek_provider.example.json`,
`configs/source_runner_runtime.example.json`,
`fixtures/sample_all_source_runner_dry_run.json`, and
`fixtures/sample_deepseek_scoring_fixture.json` define the unified source runner
and DeepSeek scoring boundary.

Local dry-run commands:

```bash
python3 -m news_harness run-sources --config configs/all_source_runner.example.json --dry-run
python3 -m news_harness score --config configs/deepseek_provider.example.json --dry-run
```

Both commands are fixture-only. They do not read real secrets, call DeepSeek,
access X/Reddit/Xueqiu, open a browser, start a scheduler, or prove production
connector readiness. DeepSeek fixture output is model inference, not outcome
truth; 12h/24h revisit remains the outcome path.

Explicit local manual smoke commands are separate:

```bash
python3 -m news_harness run-sources --config configs/all_source_runner.json --mode manual-smoke
python3 -m news_harness run-sources --config configs/all_source_runner.json --mode manual-smoke --backend direct-cli
python3 -m news_harness score --config configs/deepseek_provider.example.json --mode manual-smoke
```

VPS schedulers should call the single-cycle command instead of wiring three
commands by hand:

```bash
python3 -m news_harness run-cycle \
  --source-config configs/all_source_runner.json \
  --score-config configs/deepseek_provider.example.json \
  --fixtures fixtures \
  --out web/radar-timeline/timeline_feed.json \
  --mode manual-smoke \
  --backend direct-cli
```

The cycle performs one complete source -> score -> timeline pass and is intended
to be triggered every 30 minutes by systemd or cron. It also runs the closed-loop
revisit/eval phase: dry-run mode writes fixture-backed source/scoring/revisit/
outcome/eval artifacts, while manual-smoke mode collects only due revisit tasks
and leaves future 24h outcomes pending. See
`configs/vps_run_cycle.example.json`.

Runtime health can be checked with:

```bash
python3 -m news_harness healthcheck \
  --feed web/radar-timeline/timeline_feed.json \
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

Example systemd unit/timer files are in `configs/systemd/`.

Healthcheck is a closed-loop gate. It fails when predictions have no revisit
schedule, due revisit tasks have no outcome, outcomes have no eval join, the
feed is stale, high-scoring items lack image evidence, required sources fail, or
raw secret material appears in artifacts.

Promotion is always shadow-first. Fixture, manual-smoke, and fast-feedback eval
artifacts must not automatically promote prompts, scoring rules, models, or
connector behavior.

The recommended real-processing backend is now `direct-cli`. It uses
`twitter-cli` for the X list and `rdt-cli` for Reddit, avoiding the
Agent-Reach route. Xueqiu can be diagnosed locally through OpenCLI Browser
Bridge or a repo-external Chrome DOM export, but that bridge is not the future
VPS runtime. The production-shaped Xueqiu path is a source-specific headless
browser connector with a repo-external storage-state/session file, strict
read-only DOM extraction, and structured failure on login challenge, captcha,
WAF, or parse failure. `scripts/xueqiu_headless_export.mjs` is the first
headless-ready extraction surface; `run-sources --backend direct-cli` can call it
when `NEWS_HARNESS_XUEQIU_HEADLESS=1`. The Docker runtime also uses
`NEWS_HARNESS_X_HEADLESS=1` for the X list when `twitter-cli` is unavailable.
See `docs/direct-cli-real-processing-runbook.md`.

The production source config is `configs/all_source_runner.json`. It targets
the X list, Reddit's 20-subreddit pool, and Xueqiu `热门` / `达人`; `争议讨论`
stays out until the connector is fully supported and verified.

`fixtures/sample_shadow_source_fetch_result.json` is the MVP shadow-source
contract for the future X list / Xueqiu runner. It models one X list item and
Xueqiu `达人`, `热门`, and `争议讨论` observations as fixture-only source fetch
results, then projects them into `RadarTimelineItem` cards. The item URLs remain
`fixture://` refs, entrance URLs are preserved separately, image handling is
reference-only, and all engagement-like numbers are marked
`shadow_fixture_metrics_not_ground_truth`.

The first UI contract is intentionally small: source, copy text, optional image
reference/status, and a hotness mini-series for a small sparkline. Audit fields
such as `evidence_ref`, prediction status, outcome status, and
`non_investment_advice` remain in the feed for filtering and review, but do not
need to dominate the product view.

The default timeline fixture remains fixture-first, while explicit
`manual-smoke` runs can merge real, redacted source observations into
`web/radar-timeline/timeline_feed.json`. Manual-smoke items still do not prove
production readiness, and model scores remain inference rather than ground
truth.

## Source Readiness Gate

`fixtures/sample_source_registry.json`,
`fixtures/sample_source_score.json`, and
`fixtures/sample_connector_readiness_matrix.json` model a fixture-only source
admission gate. Planned, unsupported, auth-required, diagnostic-only,
shadow-only, and risk-blocked sources cannot become production eligible or feed
candidate discovery. Source Score only affects priority, scan frequency, and
budget class; it cannot override ConnectorReadinessGate.

## Source Pool Intake

`fixtures/sample_source_pool_intake.json`,
`fixtures/sample_real_smoke_candidate_plan.json`, and
`configs/source_pool.example.json` register the user-provided Xueqiu
and X list entrances as audit-only source-pool records. They do not read either
site. Xueqiu remains planned/unverified, and the X list remains
`auth_required` / `blocked_for_real_smoke`.

The preflight report still includes a backend-only low-risk smoke recommendation
and the blocked user-source list. That recommendation is a safety gate, not the
product roadmap. The product path is timeline feed first, then X list / Xueqiu
read-only shadow smoke after the required auth, legal, rate-limit, and
read-only boundaries are approved.

## Source Smoke Test Design

`fixtures/sample_source_smoke_matrix.json`, and the
`fixtures/sample_source_smoke_result_*.json` files model a fixture-only smoke
planning layer for RSS, public web, Xueqiu, and Reddit. The smoke layer runs
after the source registry/readiness gate and before candidate discovery in
fixture replay.

These smoke fixtures do not perform real source reads. `diagnostic_success` is
kept separate from `source_reach_success`, auth-required sources cannot fake an
authenticated read, and real engagement remains `unavailable` or
`not_verified`.

## Future MCP / VPS / DeepSeek Runtime

`configs/vps_auth_gated_sources.example.json`,
`fixtures/sample_vps_source_runner_plan.json`,
`fixtures/sample_x_list_auth_gated_smoke_plan.json`, and
`fixtures/sample_xueqiu_browser_assisted_smoke_plan.json` define the fixture-only
VPS auth-gated source runner boundary. The X list plan may be validated as a
read-only VPS runner contract using only redacted `secret_ref` and
`session_state_ref` placeholders, but it is not production-ready and no real X
smoke is executed. Xueqiu remains `browser_assisted_required` /
`session_optional_unverified`; those assumptions cannot bypass readiness.

Preflight distinguishes `fixture_ready`, `vps_runner_plan_ready`,
`real_source_smoke_not_executed`, and `production_connector_ready=false`.
Fixture success does not prove real engagement, legal/ToS eligibility, source
reach, or production connector readiness.

Future LLM-required work should use DeepSeek by default, with model calls
recording provider/model/config/prompt/context/replay metadata. DeepSeek output
is model inference, not ground truth. Secrets, login state, cookies, API keys,
and session material must stay outside evidence, event logs, and replay
artifacts.

## Current Roadmap Position

Completed:

1. Spec / Harness Contract
2. Fixture-first MVP
3. Agent-Reach Wrapper Mock Adapter
4. Source Registry + ConnectorReadinessGate MVP
5. Source Smoke Test Design MVP
6. Harness Core Runtime MVP
7. Pre-Real-Source Readiness Gate
8. Source Pool Intake + Real Smoke Candidate Plan
9. VPS Auth-Gated Source Runner Boundary MVP
10. RadarTimelineItem Contract + Timeline Feed MVP

Current state: News Harness is a validated fixture-first harness with explicit
local manual-smoke real processing. The recommended real path is `direct-cli`:
X list through `twitter-cli`, Reddit through `rdt-cli`, Xueqiu through the
existing partial fetcher, then DeepSeek scoring and radar timeline export. This
has produced real local manual-smoke observations, but it still does not prove
VPS scheduling, production connector readiness, legal/ToS eligibility, or
12h/24h outcome quality.

Next roadmap:

11. Build Web Apps Radar Timeline Console
12. X list / Xueqiu Read-only Shadow Smoke Runner
13. VPS Scheduler + Secret Injection MVP
14. Image Asset Pipeline + MCP Export Contract
15. Real Evidence + Asset Artifact Store
16. 12h/24h Outcome Collector MVP
17. Evaluator + Metrics Hardening
18. MCP Export Surface
19. Automated Promotion / Rollback
20. Production Storage + Monitoring

The product UI target is a simple original-source radar feed. First-version
cards should show only source, copy, and image status/preview; scoring,
guardrails, and outcomes can remain internal fields used for filtering and
ranking.

`Image Asset Pipeline + MCP Export Contract` remains important, but it is now
behind the timeline contract. MCP export needs copy plus image assets, not only
source image URLs, and must add a separate asset layer without replacing raw
evidence:

- Raw evidence keeps original image URL/reference, page context, author/source,
  and access status for audit and replay.
- Asset layer may download allowed images into controlled storage, record hash,
  mime type, size, dimensions, source ref, download status, and rights/risk
  status.
- MCP export returns copy plus asset references only when export policy allows
  it; otherwise it returns text-only or a blocked status.
- Auth-gated, private, signed, oversized, unsupported, or rights-blocked images
  must produce structured blocked/failure states.
