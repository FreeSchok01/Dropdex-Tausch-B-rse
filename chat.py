# -*- coding: utf-8 -*-
"""
chat.py
=======
Eigenständiges Mini-Modul für den "💬 Chat"-Reiter: direkte 1:1-Nachrichten
zwischen zwei registrierten Accounts (Twitch-Login über auth_ui.py/db.py).

Nutzt bewusst eine EIGENE, kleine SQLite-Datei (nicht db.py) – so ist diese
Funktion sofort einsatzbereit, ohne das bestehende db.py-Schema anzufassen
(gleiches Muster wie notifications.py und trade_watch.py).

Ein Chat kann nur zwischen zwei registrierten Accounts stattfinden (user_id
aus db.py) – NICHT mit beliebigen, nur über eine Dropdex-Profil-URL bekannten
Personen, da diese nicht zwangsläufig eingeloggt bzw. erreichbar sind. Der
Partner wird daher immer über die Twitch-Username-Suche (db.search_users_by_username)
gefunden, unabhängig von Tausch-Match-Ergebnissen.

Die Chat-Seite wird bewusst NICHT automatisch neu geladen (kein zusätzlicher
Timer neben render_auto_refresh() in der Haupt-App) – stattdessen gibt es
einen manuellen "🔄 Aktualisieren"-Button in der UI.
"""

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Legt die Tabelle an, falls sie noch nicht existiert. Mehrfacher Aufruf ist unkritisch."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user_id  INTEGER NOT NULL,
                to_user_id    INTEGER NOT NULL,
                body          TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                is_read       INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_from_to ON messages (from_user_id, to_user_id)"
        )
        conn.commit()


def send_message(from_user_id: int, to_user_id: int, body: str) -> None:
    """Legt eine neue Nachricht von `from_user_id` an `to_user_id` an. Leere/reine
    Whitespace-Nachrichten werden stillschweigend ignoriert."""
    text = (body or "").strip()
    if not text or from_user_id == to_user_id:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO messages (from_user_id, to_user_id, body, created_at, is_read) "
            "VALUES (?, ?, ?, ?, 0)",
            (from_user_id, to_user_id, text, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


def get_conversation(user_id: int, partner_id: int, limit: int = 300) -> List[Dict[str, Any]]:
    """Alle Nachrichten zwischen `user_id` und `partner_id`, älteste zuerst (für die
    Chronik-Anzeige von oben nach unten)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE "
            "(from_user_id = ? AND to_user_id = ?) OR (from_user_id = ? AND to_user_id = ?) "
            "ORDER BY id ASC LIMIT ?",
            (user_id, partner_id, partner_id, user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def mark_conversation_read(user_id: int, partner_id: int) -> None:
    """Markiert alle eingehenden Nachrichten von `partner_id` an `user_id` als gelesen
    (wird aufgerufen, sobald diese Unterhaltung geöffnet wird)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE messages SET is_read = 1 WHERE to_user_id = ? AND from_user_id = ? AND is_read = 0",
            (user_id, partner_id),
        )
        conn.commit()


def get_conversations_overview(user_id: int) -> List[Dict[str, Any]]:
    """Liste aller Unterhaltungen von `user_id`: pro Gesprächspartner die letzte
    Nachricht, deren Zeitpunkt und die Anzahl ungelesener Nachrichten von ihm.
    Neueste Unterhaltung zuerst."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE from_user_id = ? OR to_user_id = ? ORDER BY id DESC",
            (user_id, user_id),
        ).fetchall()

    convos: Dict[int, Dict[str, Any]] = {}
    for r in rows:
        row = dict(r)
        partner_id = row["to_user_id"] if row["from_user_id"] == user_id else row["from_user_id"]
        if partner_id not in convos:
            convos[partner_id] = {
                "partner_id": partner_id,
                "last_message": row["body"],
                "last_at": row["created_at"],
                "unread": 0,
            }
        if row["to_user_id"] == user_id and not row["is_read"]:
            convos[partner_id]["unread"] += 1
    return list(convos.values())


def unread_count(user_id: int) -> int:
    """Gesamtzahl ungelesener Nachrichten über alle Unterhaltungen hinweg (für das
    Badge neben "💬 Chat" in der Sidebar, analog zu notifications.unread_count())."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE to_user_id = ? AND is_read = 0",
            (user_id,),
        ).fetchone()
        return int(row["c"]) if row else 0
