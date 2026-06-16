# bot_config_store.py
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Any

DEFAULT_POLL_INTERVAL_SECONDS = 60


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BotSettings:
    chat_id: int
    monitoring_enabled: bool
    poll_interval_seconds: int


class BotConfigStore:
    def __init__(self, db_path: str = "bot_config.db") -> None:
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
                CREATE TABLE IF NOT EXISTS bot_chat_settings (
                    chat_id INTEGER PRIMARY KEY,
                    monitoring_enabled INTEGER NOT NULL DEFAULT 1,
                    poll_interval_seconds INTEGER NOT NULL DEFAULT 60,
                    reply_keyboard_removed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_dialog_state (
                    chat_id INTEGER PRIMARY KEY,
                    state TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'telegram',
                    source_input TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    username TEXT,
                    title TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(chat_id, source_id)
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quik_instruments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    sec_code TEXT NOT NULL,
                    class_code TEXT NOT NULL,
                    display_name TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(chat_id, class_code, sec_code)
                )
                """
            )

    def ensure_chat_settings(self, chat_id: int) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_chat_settings (
                    chat_id, monitoring_enabled, poll_interval_seconds, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO NOTHING
                """,
                (
                    chat_id,
                    1,
                    DEFAULT_POLL_INTERVAL_SECONDS,
                    now,
                    now,
                ),
            )

    def get_chat_settings(self, chat_id: int) -> BotSettings:
        self.ensure_chat_settings(chat_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT chat_id, monitoring_enabled, poll_interval_seconds
                FROM bot_chat_settings
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()

        return BotSettings(
            chat_id=row["chat_id"],
            monitoring_enabled=bool(row["monitoring_enabled"]),
            poll_interval_seconds=int(row["poll_interval_seconds"]),
        )

    def set_monitoring_enabled(self, chat_id: int, enabled: bool) -> None:
        self.ensure_chat_settings(chat_id)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE bot_chat_settings
                SET monitoring_enabled = ?, updated_at = ?
                WHERE chat_id = ?
                """,
                (1 if enabled else 0, utc_now_iso(), chat_id),
            )

    def set_dialog_state(self, chat_id: int, state: Optional[str]) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_dialog_state (chat_id, state, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    state = excluded.state,
                    updated_at = excluded.updated_at
                """,
                (chat_id, state, now),
            )

    def get_dialog_state(self, chat_id: int) -> Optional[str]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT state
                FROM bot_dialog_state
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
        return row["state"] if row else None

    def add_telegram_source(
        self,
        chat_id: int,
        source_input: str,
        source_id: str,
        username: Optional[str],
        title: Optional[str],
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO telegram_sources (
                    chat_id, source_input, source_id, username, title, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(chat_id, source_id) DO UPDATE SET
                    source_input = excluded.source_input,
                    username = excluded.username,
                    title = excluded.title,
                    is_active = 1,
                    updated_at = excluded.updated_at
                """,
                (chat_id, source_input, source_id, username, title, now, now),
            )

    def list_telegram_sources(self, chat_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, source_input, source_id, username, title, is_active
                FROM telegram_sources
                WHERE chat_id = ?
                ORDER BY is_active DESC, id ASC
                """,
                (chat_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def toggle_telegram_source(self, chat_id: int, row_id: int) -> None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT is_active
                FROM telegram_sources
                WHERE chat_id = ? AND id = ?
                """,
                (chat_id, row_id),
            ).fetchone()

            if not row:
                return

            conn.execute(
                """
                UPDATE telegram_sources
                SET is_active = ?, updated_at = ?
                WHERE chat_id = ? AND id = ?
                """,
                (0 if row["is_active"] else 1, utc_now_iso(), chat_id, row_id),
            )

    def delete_telegram_source(self, chat_id: int, row_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM telegram_sources
                WHERE chat_id = ? AND id = ?
                """,
                (chat_id, row_id),
            )

    def add_quik_instrument(
        self,
        chat_id: int,
        sec_code: str,
        class_code: str,
        display_name: Optional[str],
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO quik_instruments (
                    chat_id, sec_code, class_code, display_name, is_active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(chat_id, class_code, sec_code) DO UPDATE SET
                    display_name = excluded.display_name,
                    is_active = 1,
                    updated_at = excluded.updated_at
                """,
                (chat_id, sec_code, class_code, display_name, now, now),
            )

    def list_quik_instruments(self, chat_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, sec_code, class_code, display_name, is_active
                FROM quik_instruments
                WHERE chat_id = ?
                ORDER BY is_active DESC, id ASC
                """,
                (chat_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def toggle_quik_instrument(self, chat_id: int, row_id: int) -> None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT is_active
                FROM quik_instruments
                WHERE chat_id = ? AND id = ?
                """,
                (chat_id, row_id),
            ).fetchone()

            if not row:
                return

            conn.execute(
                """
                UPDATE quik_instruments
                SET is_active = ?, updated_at = ?
                WHERE chat_id = ? AND id = ?
                """,
                (0 if row["is_active"] else 1, utc_now_iso(), chat_id, row_id),
            )

    def delete_quik_instrument(self, chat_id: int, row_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                DELETE FROM quik_instruments
                WHERE chat_id = ? AND id = ?
                """,
                (chat_id, row_id),
            )

    def is_reply_keyboard_removed(self, chat_id: int) -> bool:
        self.ensure_chat_settings(chat_id)
        with self.connect() as conn:
            row = conn.execute(
            """
            SELECT reply_keyboard_removed
            FROM bot_chat_settings
            WHERE chat_id = ?
            """,
            (chat_id,),
            ).fetchone()
        return bool(row["reply_keyboard_removed"])

    def set_reply_keyboard_removed(self, chat_id: int, value: bool = True) -> None:
        self.ensure_chat_settings(chat_id)
        with self.connect() as conn:
            conn.execute(
            """
            UPDATE bot_chat_settings
            SET reply_keyboard_removed = ?, updated_at = ?
            WHERE chat_id = ?
            """,
            (1 if value else 0, utc_now_iso(), chat_id),
        )
            
    def list_monitored_chat_ids(self) -> list[int]:
        with self.connect() as conn:
            rows = conn.execute(
            """
            SELECT chat_id
            FROM bot_chat_settings
            WHERE monitoring_enabled = 1
            ORDER BY chat_id
            """
            ).fetchall()
            return [int(row["chat_id"]) for row in rows]