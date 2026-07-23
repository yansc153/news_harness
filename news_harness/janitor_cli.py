"""CLI wrapper for the store janitor with dry-run as the safe default."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .events import canonical_json
from .fixtures import ROOT
from .store.db import StoreDB
from .store.janitor import Janitor
from .store.media import MediaLibrary

DEFAULT_DB_PATH = ROOT / "artifacts" / "store" / "meta.sqlite"
DEFAULT_MEDIA_ROOT = ROOT / "artifacts" / "media"


def _env_float(key: str, default: float) -> float:
    value = os.environ.get(key)
    return float(value) if value else default


def _env_int(key: str, default: int) -> int:
    value = os.environ.get(key)
    return int(value) if value else default


def run_janitor(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    media_root: Path = DEFAULT_MEDIA_ROOT,
    quota_gb: float | None = None,
    ttl_days: int | None = None,
    apply: bool = False,
) -> dict:
    quota = quota_gb if quota_gb is not None else _env_float("NEWS_HARNESS_JANITOR_QUOTA_GB", 20.0)
    ttl = ttl_days if ttl_days is not None else _env_int("NEWS_HARNESS_JANITOR_TTL_DAYS", 7)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    media_root.mkdir(parents=True, exist_ok=True)
    db = StoreDB(str(db_path))
    try:
        janitor = Janitor(MediaLibrary(str(media_root)), db, quota_gb=quota, ttl_days=ttl)
        report = janitor.run() if apply else janitor.plan()
        report.update(
            {
                "status": "ok",
                "command": "janitor",
                "db_path": str(db_path),
                "media_root": str(media_root),
                "quota_gb": quota,
                "ttl_days": ttl,
            }
        )
        return report
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan or run store retention cleanup.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--media-root", type=Path, default=DEFAULT_MEDIA_ROOT)
    parser.add_argument("--quota-gb", type=float, default=None)
    parser.add_argument("--ttl-days", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Plan cleanup without deleting data")
    parser.add_argument("--apply", action="store_true", help="Delete data according to the janitor plan")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run are mutually exclusive")
    report = run_janitor(
        db_path=args.db,
        media_root=args.media_root,
        quota_gb=args.quota_gb,
        ttl_days=args.ttl_days,
        apply=args.apply,
    )
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
