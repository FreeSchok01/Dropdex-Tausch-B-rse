# -*- coding: utf-8 -*-
"""
notifications.py
=================
Eigenständiges Mini-Modul für den "🔔 News"-Reiter: speichert je Nutzer eine
Benachrichtigung, sobald ein Tausch als "getauscht" markiert wurde.

Nutzt bewusst eine EIGENE, kleine SQLite-Datei (nicht db.py) – so ist diese
Funktion sofort einsatzbereit, ohne das bestehende db.py-Schema anzufassen.
"""

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notifications.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Legt die Tabelle an, falls sie noch nicht existiert. Mehrfacher Aufruf ist unkritisch."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()


def add_notification(user_id: int, message: str) -> None:
    """Legt eine neue Nachricht für `user_id` an (z.B. wenn ein Tausch bestätigt wurde)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO notifications (user_id, message, created_at, is_read) VALUES (?, ?, ?, 0)",
            (user_id, message, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


def get_notifications(user_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    """Alle Nachrichten für `user_id`, neueste zuerst."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, message, created_at, is_read FROM notifications "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def unread_count(user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND is_read = 0",
            (user_id,),
        ).fetchone()
        return int(row["c"]) if row else 0


def mark_read(notification_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (notification_id,))
        conn.commit()


def mark_all_read(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,))
        conn.commit()
