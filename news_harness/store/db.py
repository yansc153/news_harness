"""SQLite 元数据索引（ARCHITECTURE.md §6）。

去重 / 发布状态 / 处理状态(processing_status) / 历史 / rights_status。
O(1) 查询，替代扫 JSON。零第三方依赖（sqlite3 内置）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from news_harness.models import Author, ContentItem, MediaRef


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class StoreDB:
    def __init__(self, path: str):
        self.path = path
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS content_items (
                id TEXT PRIMARY KEY,
                platform TEXT,
                source_label TEXT,
                source_url TEXT,
                canonical_url TEXT,
                author_name TEXT,
                author_handle TEXT,
                author_type TEXT,
                copy_text TEXT,
                char_count INTEGER,
                published_at TEXT,
                observed_at TEXT,
                engagement TEXT,
                content_kind TEXT,
                ingest_gate TEXT,
                media_kind TEXT,
                image_refs TEXT,
                video_refs TEXT,
                evidence_status TEXT,
                rights_status TEXT,
                processing_status TEXT DEFAULT 'raw',
                published INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        self._conn.commit()

    def upsert_item(self, item: ContentItem) -> bool:
        """写入或更新一条素材。去重以 id 为键。返回 True 表示新插入。"""
        existing = self.has_item(item.id)
        now = _now_iso()
        self._conn.execute(
            """
            INSERT INTO content_items (
                id, platform, source_label, source_url, canonical_url,
                author_name, author_handle, author_type, copy_text, char_count,
                published_at, observed_at, engagement, content_kind, ingest_gate,
                media_kind, image_refs, video_refs, evidence_status, rights_status,
                created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                platform=excluded.platform, source_label=excluded.source_label,
                source_url=excluded.source_url, canonical_url=excluded.canonical_url,
                author_name=excluded.author_name, author_handle=excluded.author_handle,
                author_type=excluded.author_type, copy_text=excluded.copy_text,
                char_count=excluded.char_count, published_at=excluded.published_at,
                observed_at=excluded.observed_at, engagement=excluded.engagement,
                content_kind=excluded.content_kind, ingest_gate=excluded.ingest_gate,
                media_kind=excluded.media_kind, image_refs=excluded.image_refs,
                video_refs=excluded.video_refs, evidence_status=excluded.evidence_status,
                rights_status=excluded.rights_status, updated_at=excluded.updated_at
            """,
            (
                item.id, item.platform, item.source_label, item.source_url,
                item.canonical_url,
                item.author.name if item.author else None,
                item.author.handle if item.author else None,
                item.author_type, item.copy_text, item.char_count,
                item.published_at, item.observed_at,
                json.dumps(item.engagement or {}, ensure_ascii=False),
                item.content_kind, item.ingest_gate, item.media_kind,
                json.dumps([vars(r) for r in item.image_refs], ensure_ascii=False),
                json.dumps([vars(r) for r in item.video_refs], ensure_ascii=False),
                item.evidence_status, item.rights_status,
                now, now,
            ),
        )
        self._conn.commit()
        return not existing

    def has_item(self, item_id: str) -> bool:
        cur = self._conn.execute("SELECT 1 FROM content_items WHERE id = ?", (item_id,))
        return cur.fetchone() is not None

    def get_item(self, item_id: str) -> Optional[ContentItem]:
        cur = self._conn.execute("SELECT * FROM content_items WHERE id = ?", (item_id,))
        row = cur.fetchone()
        return self._row_to_item(row) if row is not None else None

    def is_published(self, item_id: str) -> bool:
        cur = self._conn.execute("SELECT published FROM content_items WHERE id = ?", (item_id,))
        row = cur.fetchone()
        return bool(row["published"]) if row is not None else False

    def mark_published(self, item_id: str, published: bool = True) -> None:
        self._conn.execute(
            "UPDATE content_items SET published = ?, published_at = ?, updated_at = ? WHERE id = ?",
            (1 if published else 0, _now_iso() if published else None, _now_iso(), item_id),
        )
        self._conn.commit()

    def set_processing_status(self, item_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE content_items SET processing_status = ?, updated_at = ? WHERE id = ?",
            (status, _now_iso(), item_id),
        )
        self._conn.commit()

    def set_rights_status(self, item_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE content_items SET rights_status = ?, updated_at = ? WHERE id = ?",
            (status, _now_iso(), item_id),
        )
        self._conn.commit()

    def list_unpublished_older_than(self, ttl_days: int) -> list:
        """未发布且 observed_at 早于 now - ttl_days 的 item_id 列表（janitor TTL 用）。"""
        cutoff = (datetime.now() - timedelta(days=ttl_days)).isoformat(timespec="seconds")
        cur = self._conn.execute(
            "SELECT id FROM content_items WHERE published = 0 AND observed_at < ?", (cutoff,)
        )
        return [r["id"] for r in cur.fetchall()]

    def delete_item(self, item_id: str) -> None:
        self._conn.execute("DELETE FROM content_items WHERE id = ?", (item_id,))
        self._conn.commit()

    def count(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) AS c FROM content_items")
        return int(cur.fetchone()["c"])

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_item(row) -> ContentItem:
        author = None
        if row["author_name"]:
            author = Author(name=row["author_name"], handle=row["author_handle"])
        image_refs = [MediaRef(**_clean_ref(r)) for r in json.loads(row["image_refs"] or "[]")]
        video_refs = [MediaRef(**_clean_ref(r)) for r in json.loads(row["video_refs"] or "[]")]
        return ContentItem(
            id=row["id"],
            platform=row["platform"],
            source_label=row["source_label"],
            source_url=row["source_url"],
            canonical_url=row["canonical_url"],
            author=author,
            author_type=row["author_type"] or "unknown",
            copy_text=row["copy_text"] or "",
            char_count=row["char_count"] or 0,
            published_at=row["published_at"],
            observed_at=row["observed_at"] or "",
            engagement=json.loads(row["engagement"] or "{}"),
            content_kind=row["content_kind"] or "original",
            ingest_gate=row["ingest_gate"] or "passed",
            media_kind=row["media_kind"] or "text",
            image_refs=image_refs,
            video_refs=video_refs,
            evidence_status=row["evidence_status"] or "observed",
            rights_status=row["rights_status"] or "ok",
            processing_status=row["processing_status"] or "raw",
        )


def _clean_ref(r: dict) -> dict:
    """仅保留 MediaRef 接受的字段，避免多余键导致解包失败。"""
    return {
        "url": r.get("url"),
        "mime": r.get("mime"),
        "dimensions": r.get("dimensions"),
        "byte_size": r.get("byte_size"),
        "sha256": r.get("sha256"),
        "thumbnail_ref": r.get("thumbnail_ref"),
        "rights_status": r.get("rights_status", "unknown"),
    }
