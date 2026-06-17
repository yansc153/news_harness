# Changelog

## 2026-06-16: Topic Discovery Architecture Evolution

Status: architecture evolution, new modules added, existing production modules unchanged.

### Added

- `news_harness/structure_analysis.py`: dbskill × DeepSeek structure analysis module.
  Analyzes source content using dbs-hook (6 hook types), dbs-content (content formats,
  emotions), and dbs-content-system (QST/OPI/CON/CAS/SOL content units) frameworks.
  Falls back to heuristic keyword-based analysis when DeepSeek is unavailable.

- `news_harness/rulebook.py`: internal rule discovery, validation, and promotion module.
  Discovers rules from revisit-verified growing content by grouping structure analysis
  tags × growth outcomes. Rule lifecycle: hypothesis → shadow → verified → active.
  Writes Case Manual markdown files for verified growing cases.

- `tests/test_structure_and_rulebook.py`: 14 tests covering structure analysis
  heuristic detection, rule discovery, rule application, case manual generation.

- `docs/architecture-evolution.md`: documents the evolution from fixture-first MVP
  to topic discovery harness with two-level crawling, Rulebook, and dbskill integration.

- `docs/rulebook-design.md`: detailed Rulebook data structure, lifecycle, discovery
  algorithm, and dbskill framework mapping.

### Changed

- `configs/all_source_runner.example.json`: added `two_level_crawl: true` flags for
  X list, Reddit, and all Xueqiu sources. Added `js_render_required: true` for Xueqiu
  sources. Added Reddit `subreddit_tiers` with differentiated frequency/batch per tier
  (Tier 1: 30min/15 items, Tier 2: 1h/10, Tier 3: 2h/5).

- `news_harness/loop_driver.py`: `_refetch_engagement` now attempts direct-cli refetch
  for auth-gated sources (X list via twitter-cli, Xueqiu via degraded browser bridge
  state) instead of silently returning None. Reddit uses public JSON API refetch.

### Architecture Understanding (corrected)

- News Harness is a **topic discovery infrastructure** for content operators, not a
  generic prediction engine.
- MCP output is **raw content + evidence only**. No recommendations, no structure
  tags, no Rulebook content in MCP output.
- Rulebook is **internal**: guides Level 1 filtering and topic scoring.
- Revisit feedback comes from **source platform engagement growth**, not downstream
  account performance.
- Two-level crawling: Level 1 (list page, fast) → Level 2 (detail page, deep, only
  for items that pass Level 1 screening).

### Known Gaps

- `manual_smoke.py` scoring still uses engagement absolute values as primary signal.
  Structure-based topic scoring integration is designed but not yet wired into the
  real data flow.
- Xueqiu dispute section still lacks a connector.
- Structure analysis DeepSeek integration tested via fixture, not yet wired into
  the `run-cycle` pipeline.
- Rulebook rule discovery tested in isolation, not yet triggered automatically by
  revisit outcomes.

## 2026-06-16: VPS Candidate Readiness

Status: candidate deployable, production confidence still gated.

### Added

- `python3 -m news_harness quickstart` for local and VPS command discovery.
- Root `index.html` operator start page.
- `docs/index.html` documentation map.
- `docs/OPERATIONS.md` for healthcheck and scheduler troubleshooting.

### Changed

- Timeline UI exposes source-quality and image-evidence risks instead of hiding
  stale or missing contract fields.
- Prediction feedback windows are now `15m / 1h / 3h / 6h / 24h`.
- X quote repost wrappers are skipped unless the quoted original can be traced.
- Xueqiu observations record full-text/detail-fetch status.

### Known Gaps

- Existing live artifacts may predate the new prediction contract until the
  first VPS manual-smoke cycle runs.
- `healthcheck --max-age-minutes 90` must pass on VPS before the run is called
  healthy.
- Real 24h labels require elapsed time after deployment.
- Xueqiu dispute still lacks an exact connector.

