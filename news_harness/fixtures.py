"""Fixture loading helpers for the local News Harness runtime."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT / "schemas" / "v1" / "outcome_evaluation.schema.json"


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def ref_path(ref: str) -> str:
    return ref.split("#", 1)[0]


def load_fixture_set(fixtures_dir: Path, schema_path: Path = DEFAULT_SCHEMA) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = load_json(schema_path)
    fixtures: dict[str, Any] = {}
    expected = schema.get("expected_fixtures", None)
    if expected is None:
        for path in sorted(fixtures_dir.glob("*.json")):
            fixtures[path.name] = load_json(path)
    else:
        for filename in expected:
            path = fixtures_dir / filename
            fixtures[filename] = load_json(path)
    return schema, fixtures
