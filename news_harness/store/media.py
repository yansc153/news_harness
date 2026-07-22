"""内容哈希媒体库（ARCHITECTURE.md §6）。

落盘路径 media/{sha256[:2]}/{sha256}.ext 天然去重；manifest 记录
size / refcount / last_accessed / rights_status。refcount 降为 0 立即删文件。
零第三方依赖。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

_MIME_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "application/octet-stream": "bin",
}


def _ext_for(mime: str) -> str:
    return _MIME_EXT.get((mime or "").lower(), "bin")


class MediaLibrary:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self._manifest_path = os.path.join(root, "manifest.json")
        self._manifest = self._load_manifest()

    def _load_manifest(self) -> dict:
        if os.path.exists(self._manifest_path):
            try:
                with open(self._manifest_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_manifest(self) -> None:
        tmp = self._manifest_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._manifest, f, ensure_ascii=False)
        os.replace(tmp, self._manifest_path)

    @staticmethod
    def compute_sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _path_for(self, sha256: str, mime: str) -> str:
        return os.path.join(self.root, sha256[:2], sha256 + "." + _ext_for(mime))

    def add(self, data: bytes, mime: str, rights_status: str = "unknown",
            sha256: Optional[str] = None) -> str:
        """写入媒体（同 sha256 不重复落盘），refcount +1。返回 sha256。"""
        h = sha256 or self.compute_sha256(data)
        path = self._path_for(h, mime)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        now = time.time()
        if h not in self._manifest:
            self._manifest[h] = {
                "size": len(data),
                "refcount": 0,
                "last_accessed": now,
                "rights_status": rights_status,
                "path": path,
                "mime": mime,
            }
        else:
            if self._manifest[h].get("rights_status") == "unknown" and rights_status != "unknown":
                self._manifest[h]["rights_status"] = rights_status
        self._manifest[h]["refcount"] = self._manifest[h].get("refcount", 0) + 1
        self._save_manifest()
        return h

    def resolve(self, sha256: str) -> Optional[str]:
        entry = self._manifest.get(sha256)
        if entry is None or not os.path.exists(entry["path"]):
            return None
        return entry["path"]

    def get_manifest(self, sha256: str) -> Optional[dict]:
        entry = self._manifest.get(sha256)
        if entry is None or not os.path.exists(entry["path"]):
            return None
        return dict(entry)

    def record_access(self, sha256: str) -> None:
        entry = self._manifest.get(sha256)
        if entry is None:
            return
        entry["last_accessed"] = time.time()
        self._save_manifest()

    def increment_refcount(self, sha256: str, n: int = 1) -> None:
        entry = self._manifest.get(sha256)
        if entry is None:
            return
        entry["refcount"] = entry.get("refcount", 0) + n
        self._save_manifest()

    def decrement_refcount(self, sha256: str, n: int = 1) -> None:
        entry = self._manifest.get(sha256)
        if entry is None:
            return
        entry["refcount"] = max(0, entry.get("refcount", 0) - n)
        if entry["refcount"] == 0:
            self._delete_entry(sha256)   # refcount=0 立即删
        else:
            self._save_manifest()

    def delete(self, sha256: str) -> None:
        self._delete_entry(sha256)

    def _delete_entry(self, sha256: str) -> None:
        entry = self._manifest.pop(sha256, None)
        if entry is not None and os.path.exists(entry["path"]):
            try:
                os.remove(entry["path"])
            except OSError:
                pass
        self._save_manifest()

    def total_bytes(self) -> int:
        return sum(e.get("size", 0) for e in self._manifest.values() if os.path.exists(e["path"]))

    def list_all(self) -> list:
        return [h for h, e in self._manifest.items() if os.path.exists(e["path"])]

    def list_all_with_meta(self) -> list:
        out = []
        for sha, e in self._manifest.items():
            if os.path.exists(e["path"]):
                out.append({
                    "sha256": sha,
                    "size": e.get("size", 0),
                    "last_accessed": e.get("last_accessed", 0),
                    "refcount": e.get("refcount", 0),
                    "rights_status": e.get("rights_status", "unknown"),
                })
        return out
