"""Event and canonical JSON helpers for fixture replay."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .constants import EVENT_TYPES


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_json(data: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def make_event(
    *,
    event_type: str,
    ordinal: int,
    run_id: str,
    item_id: str | None,
    timestamp: str,
    tool_id: str | None,
    strategy_version: str,
    harness_version: str,
    input_ref: str | None,
    output_ref: str | None,
    error_code: str | None,
    payload_ref: str | None,
    event_schema_version: str,
) -> dict[str, Any]:
    return {
        "event_id": f"evt_{run_id}_{ordinal:03d}",
        "event_type": event_type,
        "run_id": run_id,
        "item_id": item_id,
        "timestamp": timestamp,
        "tool_id": tool_id,
        "strategy_version": strategy_version,
        "harness_version": harness_version,
        "input_ref": input_ref,
        "output_ref": output_ref,
        "error_code": error_code,
        "payload_ref": payload_ref,
        "event_schema_version": event_schema_version,
    }
