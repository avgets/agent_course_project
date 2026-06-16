from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class IngestStore:
    def __init__(self, db_path: str = "ingest_state.db") -> None:
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def connect(self):
        #conn = sqlite3.connect(self.db_path)
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            conn.row_factory = sqlite3.Row
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_state (
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    cursor_ts TEXT,
                    cursor_id TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_type, source_id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kommersant_news (
                    source_id TEXT NOT NULL,
                    doc_id INTEGER NOT NULL,
                    published_at TEXT,
                    title TEXT,
                    subtitle TEXT,
                    url TEXT,
                    tags_json TEXT,
                    raw_json TEXT,
                    light_html TEXT,
                    article_fetched_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, doc_id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_messages (
                    source_id TEXT NOT NULL,
                    chat_id INTEGER,
                    chat_title TEXT,
                    chat_username TEXT,
                    message_id INTEGER NOT NULL,
                    published_at TEXT,
                    author_id INTEGER,
                    is_reply INTEGER NOT NULL DEFAULT 0,
                    reply_to_msg_id INTEGER,
                    text TEXT,
                    links_json TEXT,
                    has_media INTEGER NOT NULL DEFAULT 0,
                    file_json TEXT,
                    raw_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source_id, message_id)
                )
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_telegram_messages_source_published
                ON telegram_messages (source_id, published_at DESC, message_id DESC)
                """
            )

    def get_cursor(self, source_type: str, source_id: str) -> tuple[Optional[str], Optional[str]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT cursor_ts, cursor_id
                FROM sync_state
                WHERE source_type = ? AND source_id = ?
                """,
                (source_type, source_id),
            ).fetchone()

        if not row:
            return None, None
        return row["cursor_ts"], row["cursor_id"]

    def save_cursor(
        self,
        source_type: str,
        source_id: str,
        cursor_ts: Optional[str],
        cursor_id: Optional[str],
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (
                    source_type, source_id, cursor_ts, cursor_id, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_type, source_id) DO UPDATE SET
                    cursor_ts = excluded.cursor_ts,
                    cursor_id = excluded.cursor_id,
                    updated_at = excluded.updated_at
                """,
                (
                    source_type,
                    source_id,
                    cursor_ts,
                    cursor_id,
                    utc_now_iso(),
                ),
            )

    def upsert_kommersant_news(self, items: Iterable[Dict[str, Any]], source_id: str = "kommersant") -> int:
        rows = list(items)
        if not rows:
            return 0

        now_iso = utc_now_iso()

        with self.connect() as conn:
            for item in rows:
                conn.execute(
                    """
                    INSERT INTO kommersant_news (
                        source_id,
                        doc_id,
                        published_at,
                        title,
                        subtitle,
                        url,
                        tags_json,
                        raw_json,
                        light_html,
                        article_fetched_at,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, doc_id) DO UPDATE SET
                        published_at = excluded.published_at,
                        title = excluded.title,
                        subtitle = excluded.subtitle,
                        url = excluded.url,
                        tags_json = excluded.tags_json,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        source_id,
                        item["docs_id"],
                        item.get("published_at_iso"),
                        item.get("title"),
                        item.get("subtitle"),
                        item.get("url"),
                        json.dumps(item.get("tags", []), ensure_ascii=False),
                        json.dumps(item.get("raw", {}), ensure_ascii=False),
                        None,
                        None,
                        now_iso,
                        now_iso,
                    ),
                )

        return len(rows)

    def save_kommersant_article_html(
        self,
        doc_id: int,
        light_html: str,
        article_fetched_at: Optional[str] = None,
        source_id: str = "kommersant",
    ) -> None:
        fetched_at = article_fetched_at or utc_now_iso()

        with self.connect() as conn:
            conn.execute(
                """
                UPDATE kommersant_news
                SET
                    light_html = ?,
                    article_fetched_at = ?,
                    updated_at = ?
                WHERE source_id = ? AND doc_id = ?
                """,
                (
                    light_html,
                    fetched_at,
                    utc_now_iso(),
                    source_id,
                    doc_id,
                ),
            )

    def get_doc_ids_without_article_html(self, source_id: str = "kommersant") -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT doc_id
                FROM kommersant_news
                WHERE source_id = ?
                  AND (light_html IS NULL OR light_html = '')
                ORDER BY published_at DESC, doc_id DESC
                """,
                (source_id,),
            ).fetchall()

        return [int(row["doc_id"]) for row in rows]

    def get_kommersant_news_from_db(
        self,
        minutes: int = 12 * 60,
        source_id: str = "kommersant",
        only_with_html: bool = False,
    ) -> list[dict]:
        threshold = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

        sql = """
        SELECT
            doc_id,
            published_at,
            title,
            subtitle,
            url,
            tags_json,
            light_html,
            article_fetched_at
        FROM kommersant_news
        WHERE source_id = ?
          AND published_at >= ?
        """
        params: list[Any] = [source_id, threshold]

        if only_with_html:
            sql += " AND light_html IS NOT NULL AND light_html != '' "

        sql += " ORDER BY published_at DESC, doc_id DESC "

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        result: list[dict] = []
        for row in rows:
            result.append(
                {
                    "doc_id": row["doc_id"],
                    "published_at": row["published_at"],
                    "title": row["title"],
                    "subtitle": row["subtitle"],
                    "url": row["url"],
                    "tags_json": row["tags_json"],
                    "light_html": row["light_html"],
                    "article_fetched_at": row["article_fetched_at"],
                }
            )
        return result

    def upsert_telegram_messages(
        self,
        items: Iterable[Dict[str, Any]],
        source_id: str,
    ) -> int:
        rows = list(items)
        if not rows:
            return 0

        now_iso = utc_now_iso()

        with self.connect() as conn:
            for item in rows:
                conn.execute(
                    """
                    INSERT INTO telegram_messages (
                        source_id,
                        chat_id,
                        chat_title,
                        chat_username,
                        message_id,
                        published_at,
                        author_id,
                        is_reply,
                        reply_to_msg_id,
                        text,
                        links_json,
                        has_media,
                        file_json,
                        raw_json,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, message_id) DO UPDATE SET
                        chat_id = excluded.chat_id,
                        chat_title = excluded.chat_title,
                        chat_username = excluded.chat_username,
                        published_at = excluded.published_at,
                        author_id = excluded.author_id,
                        is_reply = excluded.is_reply,
                        reply_to_msg_id = excluded.reply_to_msg_id,
                        text = excluded.text,
                        links_json = excluded.links_json,
                        has_media = excluded.has_media,
                        file_json = excluded.file_json,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        source_id,
                        item.get("chat_id"),
                        item.get("chat_title"),
                        item.get("chat_username"),
                        item["message_id"],
                        item.get("date"),
                        item.get("author_id"),
                        1 if item.get("is_reply") else 0,
                        item.get("reply_to_msg_id"),
                        item.get("text"),
                        json.dumps(item.get("links", []), ensure_ascii=False),
                        1 if item.get("has_media") else 0,
                        json.dumps(item.get("file"), ensure_ascii=False) if item.get("file") is not None else None,
                        json.dumps(item, ensure_ascii=False),
                        now_iso,
                        now_iso,
                    ),
                )

        return len(rows)

    def get_telegram_messages_from_db(
        self,
        source_id: str,
        minutes: int = 12 * 60,
        only_with_media: bool = False,
        only_replies: bool = False,
        limit: Optional[int] = None,
    ) -> list[dict]:
        threshold = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()

        sql = """
        SELECT
            source_id,
            chat_id,
            chat_title,
            chat_username,
            message_id,
            published_at,
            author_id,
            is_reply,
            reply_to_msg_id,
            text,
            links_json,
            has_media,
            file_json,
            raw_json
        FROM telegram_messages
        WHERE source_id = ?
          AND published_at >= ?
        """
        params: list[Any] = [source_id, threshold]

        if only_with_media:
            sql += " AND has_media = 1 "

        if only_replies:
            sql += " AND is_reply = 1 "

        sql += " ORDER BY published_at DESC, message_id DESC "

        if limit is not None:
            sql += " LIMIT ? "
            params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        result: list[dict] = []
        for row in rows:
            result.append(
                {
                    "source_id": row["source_id"],
                    "chat_id": row["chat_id"],
                    "chat_title": row["chat_title"],
                    "chat_username": row["chat_username"],
                    "message_id": row["message_id"],
                    "published_at": row["published_at"],
                    "author_id": row["author_id"],
                    "is_reply": bool(row["is_reply"]),
                    "reply_to_msg_id": row["reply_to_msg_id"],
                    "text": row["text"],
                    "links_json": row["links_json"],
                    "has_media": bool(row["has_media"]),
                    "file_json": row["file_json"],
                    "raw_json": row["raw_json"],
                }
            )
        return result


def compute_since_minutes(
    cursor_ts: Optional[str],
    default_minutes: int,
    overlap_minutes: int = 10,
) -> datetime:
    if cursor_ts:
        dt = datetime.fromisoformat(cursor_ts)
        return dt - timedelta(minutes=overlap_minutes)

    return datetime.now(timezone.utc) - timedelta(minutes=default_minutes)