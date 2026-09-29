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
    is_supporter        INTEGER (0/1)    (Moderations-Rang unterhalb Admin, siehe Freigaben)
    is_banned           INTEGER (0/1)
    is_approved         INTEGER (0/1)    (muss von Admin freigegeben werden)
    last_login          TEXT (ISO-Zeitstempel, UTC)
    own_profile_url     TEXT             (eigenes, privates Dropdex-Profil des Accounts)
    own_profile_name    TEXT
"""

import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import notifications

DB_PATH = Path(__file__).with_name("dropdex_users.db")
SESSION_TTL_DAYS = 30


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """Fügt eine Spalte nachträglich hinzu, falls sie in einer bestehenden DB noch fehlt."""
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    """Legt die Tabellen an und führt einmalig eine Zurücksetzung aller Nicht-Admin-Accounts aus."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                twitch_id          TEXT NOT NULL UNIQUE,
                twitch_username    TEXT NOT NULL,
                profile_image_url  TEXT DEFAULT '',
                is_admin           INTEGER NOT NULL DEFAULT 0,
                is_supporter       INTEGER NOT NULL DEFAULT 0,
                is_approved        INTEGER NOT NULL DEFAULT 0,
                is_banned          INTEGER NOT NULL DEFAULT 0,
                last_login         TEXT
            )
            """
        )
        _ensure_column(conn, "users", "own_profile_url", "TEXT DEFAULT ''")
        _ensure_column(conn, "users", "own_profile_name", "TEXT DEFAULT ''")
        _ensure_column(conn, "users", "last_seen", "TEXT")
        _ensure_column(conn, "users", "show_on_leaderboard", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "users", "is_supporter", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "is_approved", "INTEGER NOT NULL DEFAULT 0")

        # System-Tabelle für Migrationen
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )

        # Einmaliger Reset: Alle bestehenden User & Supporter (außer Admins) auf is_approved = 0 setzen
        migration_check = conn.execute(
            "SELECT value FROM system_settings WHERE key = 'reset_all_approvals_v1'"
        ).fetchone()

        if not migration_check:
            conn.execute("UPDATE users SET is_approved = 0 WHERE is_admin = 0")
            conn.execute(
                "INSERT INTO system_settings (key, value) VALUES ('reset_all_approvals_v1', '1')"
            )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS progress_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                taken_at        TEXT NOT NULL,
                distinct_owned  INTEGER NOT NULL,
                distinct_total  INTEGER NOT NULL,
                total_copies    INTEGER NOT NULL,
                missing_count   INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
            """
        )

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

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                user_id       INTEGER NOT NULL,
                profile_url   TEXT NOT NULL,
                profile_name  TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                PRIMARY KEY (user_id, profile_url)
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


def _notify_moderators_of_pending_signup(twitch_username: str) -> None:
    try:
        notifications.init_db()
        with get_connection() as conn:
            mods = conn.execute(
                "SELECT id FROM users WHERE is_admin = 1 OR is_supporter = 1"
            ).fetchall()
        for m in mods:
            notifications.add_notification(m["id"], f"🆕 {twitch_username} wartet auf Freigabe.")
    except Exception:
        pass


def create_user(twitch_id: str, twitch_username: str, profile_image_url: str = "") -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (twitch_id, twitch_username, profile_image_url, last_login, is_approved) "
            "VALUES (?, ?, ?, ?, 0)",
            (twitch_id, twitch_username, profile_image_url, now),
        )
    _notify_moderators_of_pending_signup(twitch_username)
    return get_user_by_twitch_id(twitch_id)


def touch_last_login(twitch_id: str, twitch_username: str, profile_image_url: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET last_login = ?, twitch_username = ?, profile_image_url = ? "
            "WHERE twitch_id = ?",
            (now, twitch_username, profile_image_url, twitch_id),
        )


def get_or_create_user(twitch_id: str, twitch_username: str, profile_image_url: str = "") -> Dict[str, Any]:
    existing = get_user_by_twitch_id(twitch_id)
    if existing is None:
        return create_user(twitch_id, twitch_username, profile_image_url)
    touch_last_login(twitch_id, twitch_username, profile_image_url)
    return get_user_by_twitch_id(twitch_id)


def search_users_by_username(query: str, exclude_user_id: Optional[int] = None, limit: int = 20) -> List[Dict[str, Any]]:
    q = (query or "").strip()
    if not q:
        return []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE twitch_username LIKE ? AND is_banned = 0 "
            "ORDER BY twitch_username COLLATE NOCASE LIMIT ?",
            (f"%{q}%", limit),
        ).fetchall()
    results = [dict(r) for r in rows]
    if exclude_user_id is not None:
        results = [r for r in results if r["id"] != exclude_user_id]
    return results


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
        if admin:
            conn.execute("UPDATE users SET is_admin = 1, is_approved = 1 WHERE id = ?", (user_id,))
        else:
            conn.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (user_id,))


def set_supporter(user_id: int, supporter: bool) -> None:
    with get_connection() as conn:
        if supporter:
            conn.execute("UPDATE users SET is_supporter = 1 WHERE id = ?", (user_id,))
        else:
            conn.execute("UPDATE users SET is_supporter = 0 WHERE id = ?", (user_id,))


def set_approved(user_id: int, approved: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET is_approved = ? WHERE id = ?", (1 if approved else 0, user_id))


def touch_last_seen(user_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("UPDATE users SET last_seen = ? WHERE id = ?", (now, user_id))


ONLINE_THRESHOLD_SECONDS = 5 * 60


def is_user_online(last_seen: Optional[str]) -> bool:
    if not last_seen:
        return False
    try:
        seen_at = datetime.fromisoformat(last_seen)
    except ValueError:
        return False
    now = datetime.now(timezone.utc) if seen_at.tzinfo else datetime.utcnow()
    delta_s = (now - seen_at).total_seconds()
    return 0 <= delta_s <= ONLINE_THRESHOLD_SECONDS


def set_leaderboard_visible(user_id: int, visible: bool) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET show_on_leaderboard = ? WHERE id = ?",
            (1 if visible else 0, user_id),
        )


def get_pending_users() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM users WHERE is_approved = 0 AND is_banned = 0 ORDER BY last_login DESC NULLS LAST"
        ).fetchall()
        return [dict(r) for r in rows]


def set_own_profile(user_id: int, url: str, name: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET own_profile_url = ?, own_profile_name = ? WHERE id = ?",
            (url.strip(), name.strip(), user_id),
        )


def add_progress_snapshot(
    user_id: int,
    distinct_owned: int,
    distinct_total: int,
    total_copies: int,
    missing_count: int,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO progress_snapshots "
            "(user_id, taken_at, distinct_owned, distinct_total, total_copies, missing_count) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, now, distinct_owned, distinct_total, total_copies, missing_count),
        )


def get_progress_history(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM progress_snapshots WHERE user_id = ? ORDER BY taken_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_latest_progress(user_id: int) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM progress_snapshots WHERE user_id = ? ORDER BY taken_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def get_leaderboard(limit: int = 50) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT u.id AS user_id, u.twitch_username, u.profile_image_url,
                   p.taken_at, p.distinct_owned, p.distinct_total, p.total_copies, p.missing_count
            FROM users u
            JOIN progress_snapshots p ON p.id = (
                SELECT id FROM progress_snapshots
                WHERE user_id = u.id
                ORDER BY taken_at DESC
                LIMIT 1
            )
            WHERE u.is_banned = 0 AND u.show_on_leaderboard = 1
            ORDER BY p.distinct_owned DESC, p.total_copies DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_session(twitch_id: str) -> str:
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
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT twitch_id FROM sessions WHERE token = ? AND expires_at > ?",
            (token, now),
        ).fetchone()
        if not row:
            return None
        user_row = conn.execute(
            "SELECT * FROM users WHERE twitch_id = ?", (row["twitch_id"],)
        ).fetchone()
        return dict(user_row) if user_row else None


def delete_session(token: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def delete_expired_sessions() -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))


def add_favorite(user_id: int, profile_url: str, profile_name: str) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO favorites (user_id, profile_url, profile_name, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, profile_url) DO UPDATE SET profile_name = excluded.profile_name",
            (user_id, profile_url.strip(), profile_name.strip(), now),
        )


def remove_favorite(user_id: int, profile_url: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND profile_url = ?",
            (user_id, profile_url.strip()),
        )


def is_favorite(user_id: int, profile_url: str) -> bool:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND profile_url = ?",
            (user_id, profile_url.strip()),
        ).fetchone()
        return row is not None


def get_favorites(user_id: int) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM favorites WHERE user_id = ? ORDER BY profile_name COLLATE NOCASE",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
