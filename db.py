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

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
    """Legt die Tabellen an, falls sie noch nicht existieren. Idempotent."""
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
        # Für den "eingeloggt bleiben"-Mechanismus: ein langlebiges Session-Token,
        # das (statt der Twitch-Zugangsdaten) in der URL mitgeführt wird, damit ein
        # Reload (F5) nicht ausloggt.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token       TEXT PRIMARY KEY,
                twitch_id   TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL
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


# ---------------------------------------------------------------------------
# Sessions: "eingeloggt bleiben" über einen Seiten-Reload hinweg.
# ---------------------------------------------------------------------------

SESSION_TTL_DAYS = 30


def create_session(twitch_id: str) -> str:
    """Legt ein neues, langlebiges Session-Token für diesen Nutzer an und gibt es zurück."""
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO sessions (token, twitch_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, twitch_id, now.isoformat(timespec="seconds"), expires.isoformat(timespec="seconds")),
        )
    return token


def get_user_by_session_token(token: str) -> Optional[Dict[str, Any]]:
    """Löst ein Session-Token auf: gültig + nicht abgelaufen -> zugehöriger Nutzer, sonst None."""
    if not token:
        return None
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT twitch_id FROM sessions WHERE token = ? AND expires_at > ?",
            (token, now_iso),
        ).fetchone()
    if not row:
        return None
    return get_user_by_twitch_id(row["twitch_id"])


def delete_session(token: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_expired_sessions() -> None:
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_iso,))
