# News Harness — Standing Facts

V1: Outcome-first prediction harness. Fetch → Predict (1h/4h) → Revisit → Evaluate → Rulebook shadow → MCP export.

## What this is

选题 harness: crawls source content, predicts spread potential, revisits to verify, exports verified content (copy_text + image_refs) via MCP to downstream automation. Web dashboard is monitoring only.

## What this is not

Not a news aggregator, content farm, publishing system, or investment advice product.

## Architecture (V1 outcome-first)

- Windows: 1h early_momentum, 4h primary_outcome. 24h audit (sampled only).
- Evaluator: deterministic (`evaluator.py`). `delta > 0` is NOT a win. Low-base protection, platform baselines, connector quality gates applied before learning.
- Rulebook (V2): shadow-only. Consumes `OutcomeEvaluation`, never raw growth. Compute-and-log only; blocked from production scoring.
- MCP export: `McpExportItem` whitelist — copy_text, image_refs, source_url only. No scores, no labels, no rulebook internals.
- Web dashboard: `WebProjection` — includes scores, sparklines, status. Separate from MCP.
- Model: DeepSeek is predictor + structure assistant. Cannot self-evaluate, cannot promote rules.
- Evidence: preserve original source URL/image references. No download/cache/replace. Missing = explicit state.
- Structured failures only — never empty success on error.
- All artifact writes atomic. Secrets external to repo. Fetch GET-only.

## Key modules

| Module | Role |
|--------|------|
| `manual_smoke.py` | Source fetch, scoring, revisit, eval (orchestrator) |
| `evaluator.py` | Deterministic OutcomeEvaluation |
| `baseline.py` | Platform baseline snapshots and validation |
| `rulebook.py` | Rule discovery from evaluations, shadow calibration |
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

`configs/platform_metrics.v1.json`, `configs/outcome_thresholds.v1.json`.
Schemas: `schemas/v1/`. Spec: `docs/superpowers/specs/2026-06-16-harness-v1-design.md`.
Legacy 12h/24h assets: `LEGACY.md`.
