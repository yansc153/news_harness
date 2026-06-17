"""Runtime safety infrastructure for the News Harness.

- Atomic JSON writes (temp -> fsync -> rename)
- Liveness artifact recording and staleness checks
- Structured retry with exponential backoff
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from .config import find_raw_secret_material

T = TypeVar("T")

LIVENESS_FILENAME = "liveness.json"

# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path | str, data: Any) -> None:
    """Write *data* as JSON to *path* atomically (temp -> fsync -> rename)."""
    path = Path(path)
    findings = find_raw_secret_material(data)
    if findings:
        raise RuntimeError(
            f"Refusing to write raw secret material to {path}: {findings}"
        )

    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(tmp_name, str(path))
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Liveness artifact
# ---------------------------------------------------------------------------


def write_liveness_artifact(
    artifact_dir: Path,
    last_cycle_started: str | None = None,
    last_cycle_completed: str | None = None,
    last_success: str | None = None,
    last_error: str | None = None,
    disk_free_bytes: int | None = None,
) -> None:
    """Write (or update) the liveness.json artifact."""
    now = datetime.now(timezone.utc).isoformat()
    existing: dict[str, Any] = {}
    existing_path = artifact_dir / LIVENESS_FILENAME
    if existing_path.exists():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, ValueError):
            existing = {}

    liveness: dict[str, Any] = {
        "object_type": "LivenessArtifact",
        "written_at": now,
        "last_cycle_started_at": last_cycle_started or existing.get("last_cycle_started_at"),
        "last_cycle_completed_at": last_cycle_completed or existing.get("last_cycle_completed_at"),
        "last_success_at": last_success or existing.get("last_success_at"),
        "last_error": last_error or existing.get("last_error"),
        "disk_free_bytes": disk_free_bytes if disk_free_bytes is not None else existing.get("disk_free_bytes"),
    }
    atomic_write_json(artifact_dir / LIVENESS_FILENAME, liveness)


def check_liveness(artifact_dir: Path, max_staleness_minutes: int = 120) -> dict[str, Any]:
    """Read liveness.json and return staleness status."""
    path = artifact_dir / LIVENESS_FILENAME
    if not path.exists():
        return {"status": "missing", "staleness_minutes": None}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"status": "missing", "staleness_minutes": None}

    if not isinstance(data, dict):
        return {"status": "missing", "staleness_minutes": None}

    last_completed = data.get("last_cycle_completed_at")
    staleness_minutes: float | None = None

    if isinstance(last_completed, str) and last_completed:
        try:
            parsed = datetime.fromisoformat(last_completed.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            staleness_minutes = (now - parsed.astimezone(timezone.utc)).total_seconds() / 60.0
        except ValueError:
            staleness_minutes = None

    status = "ok"
    if staleness_minutes is not None and staleness_minutes > max_staleness_minutes:
        status = "stale"

    return {
        "status": status,
        "last_cycle_started_at": data.get("last_cycle_started_at"),
        "last_cycle_completed_at": data.get("last_cycle_completed_at"),
        "last_success_at": data.get("last_success_at"),
        "last_error": data.get("last_error"),
        "disk_free_bytes": data.get("disk_free_bytes"),
        "staleness_minutes": round(staleness_minutes, 2) if staleness_minutes is not None else None,
    }


# ---------------------------------------------------------------------------
# Retry with exponential backoff
# ---------------------------------------------------------------------------

_RETRYABLE_ERROR_CLASSES = (TimeoutError, ConnectionError, OSError)
_RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


def retry_with_backoff(
    fn: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
) -> tuple[T | None, int, Exception | None]:
    """Call *fn*, retrying with exponential backoff on transient failures.

    Returns: (result, retry_count, final_error_or_None).
    """
    last_error: Exception | None = None
    attempts = 0

    for attempt in range(max_retries + 1):
        attempts = attempt
        try:
            result = fn()
            return result, attempt, None
        except Exception as exc:
            last_error = exc
            if attempt == max_retries:
                break

            delay = _backoff_delay(attempt, base_delay, max_delay, backoff_factor, exc)
            if delay is None:
                break

            time.sleep(delay)

    return None, attempts, last_error


def _backoff_delay(
    attempt: int, base_delay: float, max_delay: float, backoff_factor: float, exc: Exception
) -> float | None:
    retry_after = getattr(exc, "retry_after", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return min(float(retry_after), max_delay)

    status = getattr(exc, "status", None) or getattr(exc, "code", None)
    if status is not None:
        status = int(status)
        if status == 429:
            pass
        elif status == 401:
            if attempt > 0:
                return None
        elif status in _RETRYABLE_HTTP_STATUSES:
            pass
        else:
            return None

    if not _is_retryable_exception(exc):
        return None

    delay = base_delay * (backoff_factor ** attempt)
    return min(delay, max_delay)


def _is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE_ERROR_CLASSES):
        return True
    exc_name = type(exc).__name__
    if exc_name in ("HTTPError", "URLError"):
        return True
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return True
    if "rate limit" in msg or "too many requests" in msg:
        return True
    return False
