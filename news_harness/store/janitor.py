"""配额守护（ARCHITECTURE.md §6）。

- 超配额（默认 20GB）按 LRU 淘汰媒体文件；
- 未发布超 TTL（默认 7 天）清理素材及其媒体引用；
- refcount=0 立即删（防御性扫描）。
零第三方依赖。
"""

from __future__ import annotations

from typing import Optional

_GB = 1024 * 1024 * 1024


class Janitor:
    def __init__(self, media_lib, db, quota_gb: float = 20.0, ttl_days: int = 7):
        self.media_lib = media_lib
        self.db = db
        self.quota_gb = quota_gb
        self.ttl_days = ttl_days

    def plan(self) -> dict:
        """dry-run：返回将要清理的内容，不修改任何状态。"""
        return {
            "media_evicted": self._enforce_quota(dry=True),
            "items_purged": self._clean_expired_unpublished(dry=True),
            "media_pruned": self._prune_zero_refcount(dry=True),
            "dry_run": True,
        }

    def run(self) -> dict:
        before = self.media_lib.total_bytes()
        evicted = self._enforce_quota(dry=False)
        purged = self._clean_expired_unpublished(dry=False)
        pruned = self._prune_zero_refcount(dry=False)
        after = self.media_lib.total_bytes()
        return {
            "media_evicted": evicted,
            "items_purged": purged,
            "media_pruned": pruned,
            "bytes_freed": max(0, before - after),
            "dry_run": False,
        }

    def _enforce_quota(self, dry: bool = False) -> list:
        quota_bytes = int(self.quota_gb * _GB)
        entries = self.media_lib.list_all_with_meta()
        entries.sort(key=lambda e: e["last_accessed"])  # LRU 先淘汰
        total = sum(e["size"] for e in entries)
        removed = []
        for e in entries:
            if total <= quota_bytes:
                break
            removed.append(e["sha256"])
            total -= e["size"]
        if not dry:
            for sha in removed:
                self.media_lib.delete(sha)
        return removed

    def _clean_expired_unpublished(self, dry: bool = False) -> list:
        purged = []
        due = self.db.list_unpublished_older_than(self.ttl_days)
        for item_id in due:
            item = self.db.get_item(item_id)
            if item is None:
                continue
            purged.append(item_id)
            if dry:
                continue
            for ref in list(item.image_refs) + list(item.video_refs):
                sha = getattr(ref, "sha256", None)
                if sha:
                    self.media_lib.decrement_refcount(sha)
            self.db.delete_item(item_id)
        return purged

    def _prune_zero_refcount(self, dry: bool = False) -> list:
        pruned = []
        for e in self.media_lib.list_all_with_meta():
            if e["refcount"] == 0:
                pruned.append(e["sha256"])
                if not dry:
                    self.media_lib.delete(e["sha256"])
        return pruned
