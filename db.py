# -*- coding: utf-8 -*-
"""
db.py
=====
Leichtgewichtige SQLite-Datenbank für die Nutzerverwaltung (Twitch-Login,
Admin- und Bann-Status). Wird von auth_ui.py verwendet.

Tabelle `users`:
    id                  INTEGER PRIMARY KEY
    twitch_id           TEXT UNIQUE      (eindeutige Twitch-User-ID)
    twitch_username     TEXT             (aktueller Anzeigename)
    profile_image_url   TEXT
    is_admin            INTEGER (0/1)
    is_banned           INTEGER (0/1)
    last_login          TEXT (ISO-Zeitstempel, UTC)
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Liegt neben der App -> bei Neustart bleibt sie erhalten, solange der
# Speicher des Hosters nicht flüchtig ist (siehe Hinweis im Admin-Panel
# der Haupt-App zu dropdex_namen.json - gilt hier analog).
DB_PATH = Path(__file__).with_name("dropdex_users.db")


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    """Legt die Tabelle an, falls sie noch nicht existiert. Idempotent."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                twitch_id          TEXT NOT NULL UNIQUE,
                twitch_username    TEXT NOT NULL,
                profile_image_url  TEXT DEFAULT '',
                is_admin           INTEGER NOT NULL DEFAULT 0,
                is_banned          INTEGER NOT NULL DEFAULT 0,
                last_login         TEXT
            )
            """
        )


def get_user_by_twitch_id(twitch_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE twitch_id = ?", (twitch_id,)
        ).fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def create_user(twitch_id: str, twitch_username: str, profile_image_url: str = "") -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (twitch_id, twitch_username, profile_image_url, last_login) "
            "VALUES (?, ?, ?, ?)",
            (twitch_id, twitch_username, profile_image_url, now),
        )
    return get_user_by_twitch_id(twitch_id)


def touch_last_login(twitch_id: str, twitch_username: str, profile_image_url: str = "") -> None:
    """Aktualisiert last_login + ggf. geänderten Namen/Avatar bei jedem Login."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET last_login = ?, twitch_username = ?, profile_image_url = ? "
            "WHERE twitch_id = ?",
            (now, twitch_username, profile_image_url, twitch_id),
        )


def get_or_create_user(twitch_id: str, twitch_username: str, profile_image_url: str = "") -> Dict[str, Any]:
    """Kernfunktion für den Login: legt den Nutzer bei Erstlogin an, sonst last_login updaten."""
    existing = get_user_by_twitch_id(twitch_id)
    if existing is None:
        return create_user(twitch_id, twitch_username, profile_image_url)
    touch_last_login(twitch_id, twitch_username, profile_image_url)
    return get_user_by_twitch_id(twitch_id)


def get_all_users() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY last_login DESC NULLS LAST"
        ).fetchall()
        return [dict(r) for r in rows]


def set_banned(user_id: int, banned: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET is_banned = ? WHERE id = ?", (1 if banned else 0, user_id))


def set_admin(user_id: int, admin: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if admin else 0, user_id))
