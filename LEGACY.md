# Legacy Assets — 12h/24h Era

These assets were built for the fixture-first MVP with 12h/24h prediction windows.
They are **not compatible** with the V1 1h/4h outcome-first architecture.

Do not mix legacy 12h/24h assets with V1 1h/4h evaluator.

## Legacy directories

- `tests/` — All tests reference old fixture shapes, 12h/24h constants, and pre-OutcomeEvaluation contracts.
- `fixtures/` — All fixtures use 12h/24h windows and old object schemas.
- `configs/` — All `.example` configs reference 12h/24h windows and fixture-only mode.
- `scripts/` — Legacy wrapper scripts and pre-V1 tooling.
- `web/artifacts/` — Test runtime artifacts from old runs.

## Migration path

As V1 workstreams progress, these will be either:
- Migrated to the new 1h/4h contracts (update)
- Replaced with new V1 fixtures/tests (replace)
- Deleted if no longer relevant (remove)

## V1 authoritative docs

- `docs/superpowers/specs/2026-06-16-harness-v1-design.md` — Architecture spec
- `AGENTS.md` — Project rules (to be updated for V1)
- `LEGACY.md` — This file
