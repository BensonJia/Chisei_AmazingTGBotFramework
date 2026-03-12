from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass
class MessageRow:
    conversation_key: str
    chat_id: int
    chat_type: str
    sender_id: int | None
    sender_name: str
    sender_is_bot: bool
    role: str
    content: str
    tg_message_id: int | None = None
    metadata_json: str | None = None


class SQLiteStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=3000")
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    conversation_key TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    chat_type TEXT NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_key TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    chat_type TEXT NOT NULL,
                    sender_id INTEGER,
                    sender_name TEXT NOT NULL,
                    sender_is_bot INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tg_message_id INTEGER,
                    metadata_json TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(conversation_key) REFERENCES conversations(conversation_key)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_key TEXT NOT NULL,
                    summary_text TEXT NOT NULL,
                    source_start_id INTEGER NOT NULL,
                    source_end_id INTEGER NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(conversation_key) REFERENCES conversations(conversation_key)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_settings (
                    conversation_key TEXT PRIMARY KEY,
                    record_all INTEGER NOT NULL DEFAULT 0,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(conversation_key) REFERENCES conversations(conversation_key)
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS time_logic_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    actor_a_id TEXT,
                    actor_b_id TEXT,
                    event_text TEXT NOT NULL,
                    confidence REAL DEFAULT 0.0,
                    source_message_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS roles_logic_edges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    src_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    dst_id TEXT NOT NULL,
                    confidence REAL DEFAULT 0.0,
                    source_message_id INTEGER,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS teach_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conversation_key, id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_summaries_conv_id ON summaries(conversation_key, id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_time_logic_session_time ON time_logic_events(session_key, event_time)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_roles_logic_session_src ON roles_logic_edges(session_key, src_id)"
            )
            self._conn.commit()

    def upsert_conversation(self, conversation_key: str, chat_id: int, chat_type: str) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO conversations(conversation_key, chat_id, chat_type)
                VALUES (?, ?, ?)
                ON CONFLICT(conversation_key)
                DO UPDATE SET chat_id=excluded.chat_id, chat_type=excluded.chat_type, updated_at=CURRENT_TIMESTAMP
                """,
                (conversation_key, chat_id, chat_type),
            )
            self._conn.commit()

    def append_message(self, row: MessageRow) -> int:
        self.upsert_conversation(row.conversation_key, row.chat_id, row.chat_type)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO messages(
                    conversation_key, chat_id, chat_type, sender_id, sender_name,
                    sender_is_bot, role, content, tg_message_id, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row.conversation_key,
                    row.chat_id,
                    row.chat_type,
                    row.sender_id,
                    row.sender_name,
                    1 if row.sender_is_bot else 0,
                    row.role,
                    row.content,
                    row.tg_message_id,
                    row.metadata_json,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def count_messages(self, conversation_key: str) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT COUNT(*) AS c FROM messages WHERE conversation_key=?",
                (conversation_key,),
            )
            return int(cur.fetchone()["c"])

    def get_recent_messages(self, conversation_key: str, limit: int) -> list[dict[str, str]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT role, content
                FROM messages
                WHERE conversation_key=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_key, limit),
            )
            rows = cur.fetchall()
        rows.reverse()
        return [{"role": str(r["role"]), "content": str(r["content"])} for r in rows]

    def get_recent_message_rows(self, conversation_key: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, created_at, sender_id, sender_name, role, content
                FROM messages
                WHERE conversation_key=?
                ORDER BY id DESC
                LIMIT ?
                """,
                (conversation_key, limit),
            )
            rows = cur.fetchall()
        rows = list(rows)
        rows.reverse()
        return [
            {
                "id": int(r["id"]),
                "created_at": str(r["created_at"]),
                "sender_id": r["sender_id"],
                "sender_name": str(r["sender_name"]),
                "role": str(r["role"]),
                "content": str(r["content"]),
            }
            for r in rows
        ]

    def get_oldest_messages(self, conversation_key: str, count: int) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, created_at, role, sender_name, content
                FROM messages
                WHERE conversation_key=?
                ORDER BY id ASC
                LIMIT ?
                """,
                (conversation_key, count),
            )
            rows = cur.fetchall()
        return [
            {
                "id": int(r["id"]),
                "created_at": str(r["created_at"]),
                "role": str(r["role"]),
                "sender_name": str(r["sender_name"]),
                "content": str(r["content"]),
            }
            for r in rows
        ]

    def delete_messages_up_to(self, conversation_key: str, max_id: int) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "DELETE FROM messages WHERE conversation_key=? AND id<=?",
                (conversation_key, max_id),
            )
            self._conn.commit()

    def get_latest_summary(self, conversation_key: str) -> str:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT summary_text
                FROM summaries
                WHERE conversation_key=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (conversation_key,),
            )
            row = cur.fetchone()
            return str(row["summary_text"]) if row else ""

    def insert_summary(
        self,
        conversation_key: str,
        summary_text: str,
        source_start_id: int,
        source_end_id: int,
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO summaries(conversation_key, summary_text, source_start_id, source_end_id)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_key, summary_text, source_start_id, source_end_id),
            )
            self._conn.commit()

    def get_record_all(self, conversation_key: str) -> bool:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "SELECT record_all FROM conversation_settings WHERE conversation_key=?",
                (conversation_key,),
            )
            row = cur.fetchone()
            return bool(row and int(row["record_all"]) == 1)

    def set_record_all(self, conversation_key: str, chat_id: int, chat_type: str, enabled: bool) -> None:
        self.upsert_conversation(conversation_key, chat_id, chat_type)
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO conversation_settings(conversation_key, record_all)
                VALUES (?, ?)
                ON CONFLICT(conversation_key)
                DO UPDATE SET record_all=excluded.record_all, updated_at=CURRENT_TIMESTAMP
                """,
                (conversation_key, 1 if enabled else 0),
            )
            self._conn.commit()

    def add_time_logic_event(
        self,
        session_key: str,
        event_time: str,
        actor_a_id: str,
        actor_b_id: str,
        event_text: str,
        confidence: float,
        source_message_id: int | None,
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT INTO time_logic_events(
                    session_key, event_time, actor_a_id, actor_b_id, event_text, confidence, source_message_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_key,
                    event_time,
                    actor_a_id,
                    actor_b_id,
                    event_text,
                    confidence,
                    source_message_id,
                ),
            )
            self._conn.commit()

    def list_time_logic_events(self, session_key: str, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, event_time, actor_a_id, actor_b_id, event_text, confidence, source_message_id
                FROM time_logic_events
                WHERE session_key=?
                ORDER BY event_time DESC, id DESC
                LIMIT ?
                """,
                (session_key, limit),
            )
            rows = cur.fetchall()
        return [
            {
                "id": int(r["id"]),
                "event_time": str(r["event_time"]),
                "actor_a_id": str(r["actor_a_id"] or ""),
                "actor_b_id": str(r["actor_b_id"] or ""),
                "event_text": str(r["event_text"]),
                "confidence": float(r["confidence"] or 0.0),
                "source_message_id": r["source_message_id"],
            }
            for r in rows
        ]

    def add_roles_logic_edge(
        self,
        session_key: str,
        src_id: str,
        relation: str,
        dst_id: str,
        confidence: float,
        source_message_id: int | None,
    ) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT id, confidence
                FROM roles_logic_edges
                WHERE session_key=? AND src_id=? AND relation=? AND dst_id=?
                ORDER BY id DESC
                LIMIT 1
                """,
                (session_key, src_id, relation, dst_id),
            )
            row = cur.fetchone()
            if row is not None:
                merged_conf = max(float(row["confidence"] or 0.0), float(confidence or 0.0))
                cur.execute(
                    """
                    UPDATE roles_logic_edges
                    SET confidence=?, source_message_id=?, created_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (merged_conf, source_message_id, int(row["id"])),
                )
                self._conn.commit()
                return
            cur.execute(
                """
                INSERT INTO roles_logic_edges(
                    session_key, src_id, relation, dst_id, confidence, source_message_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_key, src_id, relation, dst_id, confidence, source_message_id),
            )
            self._conn.commit()

    def list_roles_edges_by_sources(self, session_key: str, src_ids: list[str]) -> list[dict[str, Any]]:
        if not src_ids:
            return []
        placeholders = ",".join(["?"] * len(src_ids))
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                f"""
                SELECT src_id, relation, dst_id, confidence
                FROM roles_logic_edges
                WHERE session_key=? AND src_id IN ({placeholders})
                ORDER BY id DESC
                """,
                [session_key, *src_ids],
            )
            rows = cur.fetchall()
        return [
            {
                "src_id": str(r["src_id"]),
                "relation": str(r["relation"]),
                "dst_id": str(r["dst_id"]),
                "confidence": float(r["confidence"] or 0.0),
            }
            for r in rows
        ]

    def list_neighbors(self, session_key: str, src_id: str) -> list[str]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT dst_id
                FROM roles_logic_edges
                WHERE session_key=? AND src_id=?
                ORDER BY id DESC
                """,
                (session_key, src_id),
            )
            rows = cur.fetchall()
        return [str(r["dst_id"]) for r in rows]

    def create_teach_run(self, session_key: str, status: str, detail: str = "") -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "INSERT INTO teach_runs(session_key, status, detail) VALUES (?, ?, ?)",
                (session_key, status, detail),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_teach_run(self, run_id: int, status: str, detail: str) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                "UPDATE teach_runs SET status=?, detail=? WHERE id=?",
                (status, detail, run_id),
            )
            self._conn.commit()
