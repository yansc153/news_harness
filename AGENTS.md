# News Harness — Standing Facts

V3: Xueqiu-only market-content harness. Kaipanla discovery → Xueqiu targeted fetch → fixed gates → timeline/MCP export.

## What this is

选题 harness: uses Kaipanla market themes to select stocks, reads Xueqiu discussions, applies fixed comment threshold, and exports source-grounded content (copy_text + image_refs). Web dashboard is monitoring only.

## What this is not

Not a news aggregator, content farm, publishing system, or investment advice product.

## Architecture (V2 kaipanla-guided)

- Discovery: Kaipanla API generates hourly TargetSet (max 12 stocks) from market signals (themes, limit-up, bidding anomalies).
- Collection: Per-stock Xueqiu discussion crawl with fixed comment threshold >= 10. Never auto-relaxed.
- Schedule: one cycle per hour.
- Discovery: Kaipanla API produces an hourly TargetSet (max 12 stocks).
- Collection: Xueqiu targeted discussion crawl with fixed `comments >= 10`; never auto-relaxed.
- Selection: no DeepSeek, prediction, revisit, evaluation, Reddit, Twitter/X, or multi-source ranking.
- MCP export: `McpExportItem` whitelist — copy_text, image_refs, source_url only. No scores, no labels, no rulebook internals.
- Web dashboard: `WebProjection` — source text, engagement, images, source links, and fetch status only.
- Evidence: preserve original source URL/image references. No download/cache/replace. Missing = explicit state.
- Structured failures only — never empty success on error.
- All artifact writes atomic. Secrets external to repo. Fetch GET-only.

## Key modules

| Module | Role |
|--------|------|
| `direct_cli_backend.py` | Kaipanla → Xueqiu targeted source fetch |
| `kaipanla_targets.py` | Theme/stock discovery and target-set validation |
| `xueqiu_targeted.py` | Xueqiu discussion fetch and fixed comment gate |
| `manual_smoke.py` | Legacy artifact compatibility and timeline materialization |
| `connector_quality.py` | Per-run connector health reports |
| `runtime_gates.py` | Atomic writes, liveness, retry with backoff |
| `artifact_api.py` | WebProjection / McpExportItem split |
| `loop_driver.py` | Self-iterating cycle loop |

## Commands

```
python3 -m news_harness run-cycle
python3 -m news_harness validate fixtures
python3 -m news_harness healthcheck --auto
python3 -m news_harness serve
python3 -m news_harness mcp
```

## Versioned configs

`configs/all_source_runner.json`, `configs/hourly_targeting.v1.json`.
Schemas: `schemas/v1/`. Spec: `docs/superpowers/specs/2026-06-16-harness-v1-design.md`.
Legacy 12h/24h assets: `LEGACY.md`.
