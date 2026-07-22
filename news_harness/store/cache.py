"""抓取响应缓存（ARCHITECTURE.md §6）。

带 TTL + 容量上限，避免无限增长。零第三方依赖。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional


def _safe_name(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ResponseCache:
    def __init__(self, root: str, ttl_seconds: int = 3600, max_mb: int = 512, clock=None):
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_mb * 1024 * 1024
        self.clock = clock or time.time
        os.makedirs(root, exist_ok=True)

    def _paths(self, key: str):
        name = _safe_name(key)
        return os.path.join(self.root, name + ".dat"), os.path.join(self.root, name + ".meta")

    def put(self, key: str, data: bytes) -> None:
        data_path, meta_path = self._paths(key)
        tmp = data_path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, data_path)
        meta = {"stored_at": self.clock(), "size": len(data), "key": key}
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)

    def is_expired(self, key: str) -> bool:
        _, meta_path = self._paths(key)
        if not os.path.exists(meta_path):
            return True
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            return True
        return (self.clock() - meta["stored_at"]) >= self.ttl_seconds

    def get(self, key: str) -> Optional[bytes]:
        if self.is_expired(key):
            return None
        data_path, _ = self._paths(key)
        if not os.path.exists(data_path):
            return None
        with open(data_path, "rb") as f:
            return f.read()

    def evict_expired(self) -> int:
        removed = 0
        for name in os.listdir(self.root):
            if not name.endswith(".meta"):
                continue
            meta_path = os.path.join(self.root, name)
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (json.JSONDecodeError, OSError):
                os.remove(meta_path)
                removed += 1
                continue
            if (self.clock() - meta["stored_at"]) >= self.ttl_seconds:
                key = meta.get("key")
                if key:
                    data_path, _ = self._paths(key)
                    if os.path.exists(data_path):
                        os.remove(data_path)
                os.remove(meta_path)
                removed += 1
        return removed

    def enforce_capacity(self) -> int:
        entries = []
        for name in os.listdir(self.root):
            if not name.endswith(".meta"):
                continue
            try:
                with open(os.path.join(self.root, name), "r", encoding="utf-8") as f:
                    entries.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                continue
        entries.sort(key=lambda m: m["stored_at"])  # LRU（最早写入）先淘汰
        total = sum(m["size"] for m in entries)
        removed = 0
        for m in entries:
            if total <= self.max_bytes:
                break
            key = m.get("key")
            if key:
                data_path, meta_path = self._paths(key)
                if os.path.exists(data_path):
                    os.remove(data_path)
                if os.path.exists(meta_path):
                    os.remove(meta_path)
            total -= m["size"]
            removed += 1
        return removed

    def total_bytes(self) -> int:
        return sum(
            os.path.getsize(os.path.join(self.root, n))
            for n in os.listdir(self.root)
            if n.endswith(".dat")
        )

    def clear(self) -> None:
        for n in os.listdir(self.root):
            os.remove(os.path.join(self.root, n))
