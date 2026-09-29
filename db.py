import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import notifications

DB_NAME = "database.db"


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialisiert die Datenbank-Tabellen."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                twitch_id TEXT UNIQUE NOT NULL,
                twitch_username TEXT NOT NULL,
                profile_image_url TEXT,
                last_login TEXT NOT NULL,
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_supporter INTEGER NOT NULL DEFAULT 0,
                is_approved INTEGER NOT NULL DEFAULT 0
            )
            """
        )


def _notify_moderators_of_pending_signup(twitch_username: str) -> None:
    """Sendet eine Benachrichtigung an alle Admins und Supporter, wenn ein neuer User auf Freigabe wartet."""
    try:
        notifications.init_db()
        with get_connection() as conn:
            mods = conn.execute(
                "SELECT id FROM users WHERE is_admin = 1 OR is_supporter = 1"
            ).fetchall()
        for m in mods:
            notifications.add_notification(
                m["id"], f"⏳ Der User **{twitch_username}** wartet auf eine Freigabe."
            )
    except Exception:
        pass


def get_user_by_twitch_id(twitch_id: str) -> Optional[Dict[str, Any]]:
    """Sucht einen User anhand seiner Twitch-ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE twitch_id = ?", (twitch_id,)
        ).fetchone()
        return dict(row) if row else None


def create_user(
    twitch_id: str, twitch_username: str, profile_image_url: str = ""
) -> Dict[str, Any]:
    """Erstellt einen neuen User und benachrichtigt Moderatoren."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO users (twitch_id, twitch_username, profile_image_url, last_login, is_approved) "
            "VALUES (?, ?, ?, ?, 0)",
            (twitch_id, twitch_username, profile_image_url, now),
        )
    _notify_moderators_of_pending_signup(twitch_username)
    return get_user_by_twitch_id(twitch_id)


def update_last_login(twitch_id: str, profile_image_url: str = "") -> None:
    """Aktualisiert den Zeitstempel des letzten Logins und das Profilbild."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_connection() as conn:
        if profile_image_url:
            conn.execute(
                "UPDATE users SET last_login = ?, profile_image_url = ? WHERE twitch_id = ?",
                (now, profile_image_url, twitch_id),
            )
        else:
            conn.execute(
                "UPDATE users SET last_login = ? WHERE twitch_id = ?",
                (now, twitch_id),
            )


def get_all_users() -> List[Dict[str, Any]]:
    """Gibt alle registrierten User zurück."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY twitch_username ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def toggle_user_approval(user_id: int, is_approved: bool) -> None:
    """Aktiviert oder deaktiviert den Freigabestatus eines Users."""
    val = 1 if is_approved else 0
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_approved = ? WHERE id = ?", (val, user_id)
        )


def toggle_user_supporter(user_id: int, is_supporter: bool) -> None:
    """Setzt oder entfernt den Supporter-Status eines Users."""
    val = 1 if is_supporter else 0
    with get_connection() as conn:
        conn.execute(
            "UPDATE users SET is_supporter = ? WHERE id = ?", (val, user_id)
        )


def revoke_all_approvals_except_admins_and_supporters() -> int:
    """Entzieht allen normalen Usern die Freigabe. Admins und Supporter behalten sie."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE users SET is_approved = 0 WHERE is_admin = 0 AND is_supporter = 0"
        )
        return cursor.rowcount


def revoke_all_approvals_except_admins() -> int:
    """Entzieht allen Usern inkl. Supporter die Freigabe. Nur Admins behalten sie."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE users SET is_approved = 0 WHERE is_admin = 0"
        )
        return cursor.rowcount
