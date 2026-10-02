# -*- coding: utf-8 -*-
"""
db.py
=====
Nutzer-Datenbank (SQLite) für die Dropdex-Tauschbörse:

  - users               Twitch-Accounts inkl. Rollen/Status (Admin, Supporter, freigegeben, gesperrt),
                        eigenem Dropdex-Profil, Online-Status (last_seen) und Bestenlisten-Opt-in
  - sessions            "Eingeloggt bleiben" per Token in der URL (siehe auth_ui.py)
  - favorites           Favoriten-Profile je Account
  - progress_snapshots  Fortschrittsverlauf je Account (Verlaufsdiagramm + Bestenliste)

Alle Zeitstempel werden als UTC-ISO-Strings gespeichert (siehe is_user_online() und
online_status_html() in der Haupt-App, die davon ausgehen).

Die Datei dropdex_users.db liegt neben dieser Datei und ist in .gitignore eingetragen.
Spalten, die in einer älteren Version der DB fehlen, werden in init_db() automatisch
nachgerüstet (_ensure_column), damit bestehende Datenbanken weiter funktionieren.
"""

import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dropdex_users.db")

# Innerhalb dieser Zeitspanne seit dem letzten Seitenaufruf gilt ein Account als "online".
ONLINE_THRESHOLD_SECONDS = 300

# So lange bleibt ein Login-Token (?session=...) gültig.
SESSION_LIFETIME_DAYS = 30


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat(timespec="seconds")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _row_to_user(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    u = dict(row)
    for key in ("is_admin", "is_supporter", "is_approved", "is_banned", "show_on_leaderboard"):
        u[key] = bool(u.get(key))
    u["own_profile_url"] = u.get("own_profile_url") or ""
    u["own_profile_name"] = u.get("own_profile_name") or ""
    u["profile_image_url"] = u.get("profile_image_url") or ""
    return u


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Legt alle Tabellen an (falls nötig) und rüstet fehlende Spalten nach.
    Mehrfacher Aufruf ist unkritisch."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                twitch_id           TEXT NOT NULL UNIQUE,
                twitch_username     TEXT NOT NULL,
                profile_image_url   TEXT DEFAULT '',
                is_admin            INTEGER NOT NULL DEFAULT 0,
                is_supporter        INTEGER NOT NULL DEFAULT 0,
                is_approved         INTEGER NOT NULL DEFAULT 0,
                is_banned           INTEGER NOT NULL DEFAULT 0,
                own_profile_url     TEXT DEFAULT '',
                own_profile_name    TEXT DEFAULT '',
                show_on_leaderboard INTEGER NOT NULL DEFAULT 1,
                last_seen           TEXT,
                created_at          TEXT NOT NULL
            )
            """
        )
        # Nachrüsten für ältere DB-Versionen
        _ensure_column(conn, "users", "profile_image_url", "TEXT DEFAULT ''")
        _ensure_column(conn, "users", "is_admin", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "is_supporter", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "is_approved", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "is_banned", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "users", "own_profile_url", "TEXT DEFAULT ''")
        _ensure_column(conn, "users", "own_profile_name", "TEXT DEFAULT ''")
        _ensure_column(conn, "users", "show_on_leaderboard", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(conn, "users", "last_seen", "TEXT")

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
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL,
                profile_url   TEXT NOT NULL,
                profile_name  TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                UNIQUE (user_id, profile_url)
            )
            """
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
                missing_count   INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_snapshots_user ON progress_snapshots (user_id, id)"
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Nutzer
# ---------------------------------------------------------------------------

def get_user_by_id(user_id: Any) -> Optional[Dict[str, Any]]:
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return _row_to_user(row)


def get_user_by_twitch_id(twitch_id: Any) -> Optional[Dict[str, Any]]:
    if twitch_id is None:
        return None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE twitch_id = ?", (str(twitch_id),)).fetchone()
    return _row_to_user(row)


def get_or_create_user(twitch_id: str, twitch_username: str,
                       profile_image_url: str = "") -> Dict[str, Any]:
    """Legt den Account beim ersten Login an (noch NICHT freigegeben) oder aktualisiert
    bei bestehenden Accounts Anzeigename und Profilbild von Twitch."""
    twitch_id = str(twitch_id)
    with _connect() as conn:
        row = conn.execute("SELECT id FROM users WHERE twitch_id = ?", (twitch_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET twitch_username = ?, profile_image_url = ? WHERE id = ?",
                (twitch_username, profile_image_url or "", row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO users (twitch_id, twitch_username, profile_image_url, created_at) "
                "VALUES (?, ?, ?, ?)",
                (twitch_id, twitch_username, profile_image_url or "", _utc_now_iso()),
            )
        conn.commit()
    return get_user_by_twitch_id(twitch_id)  # type: ignore[return-value]


def get_all_users() -> List[Dict[str, Any]]:
    """Alle Accounts (Admins zuerst, dann Supporter, dann alphabetisch)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY is_admin DESC, is_supporter DESC, "
            "LOWER(twitch_username) ASC"
        ).fetchall()
    return [_row_to_user(r) for r in rows]  # type: ignore[misc]


def search_users_by_username(query: str, exclude_user_id: Optional[int] = None,
                             limit: int = 20) -> List[Dict[str, Any]]:
    """Teilstring-Suche (ohne Groß-/Kleinschreibung) über Twitch-Namen - nur freigegebene,
    nicht gesperrte Accounts, da nur diese die App überhaupt nutzen können."""
    q = (query or "").strip()
    if not q:
        return []
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    sql = ("SELECT * FROM users WHERE twitch_username LIKE ? ESCAPE '\\' "
           "AND is_approved = 1 AND is_banned = 0")
    params: List[Any] = [like]
    if exclude_user_id is not None:
        sql += " AND id != ?"
        params.append(int(exclude_user_id))
    sql += " ORDER BY LOWER(twitch_username) ASC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_user(r) for r in rows]  # type: ignore[misc]


def _set_flag(user_id: int, column: str, value: bool) -> None:
    # column kommt ausschließlich aus dem Code unten, nie aus Nutzereingaben
    with _connect() as conn:
        conn.execute(f"UPDATE users SET {column} = ? WHERE id = ?", (1 if value else 0, int(user_id)))
        conn.commit()


def set_approved(user_id: int, approved: bool) -> None:
    _set_flag(user_id, "is_approved", approved)


def set_banned(user_id: int, banned: bool) -> None:
    """Sperren entzieht zugleich die Freigabe. Entsperren stellt sie NICHT automatisch
    wieder her (dafür gibt es set_approved)."""
    with _connect() as conn:
        if banned:
            conn.execute("UPDATE users SET is_banned = 1, is_approved = 0 WHERE id = ?", (int(user_id),))
        else:
            conn.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (int(user_id),))
        conn.commit()


def set_admin(user_id: int, is_admin: bool) -> None:
    """Admin-Recht vergeben/entziehen. Beim Vergeben wird der Account automatisch freigegeben
    (und entsperrt)."""
    with _connect() as conn:
        if is_admin:
            conn.execute(
                "UPDATE users SET is_admin = 1, is_approved = 1, is_banned = 0 WHERE id = ?",
                (int(user_id),),
            )
        else:
            conn.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (int(user_id),))
        conn.commit()


def set_supporter(user_id: int, is_supporter: bool) -> None:
    """Supporter-Rang vergeben/entziehen. Supporter dürfen moderieren und müssen dafür selbst
    freigegeben sein - beim Vergeben wird der Account daher automatisch freigegeben."""
    with _connect() as conn:
        if is_supporter:
            conn.execute(
                "UPDATE users SET is_supporter = 1, is_approved = 1, is_banned = 0 WHERE id = ?",
                (int(user_id),),
            )
        else:
            conn.execute("UPDATE users SET is_supporter = 0 WHERE id = ?", (int(user_id),))
        conn.commit()


def revoke_all_approvals_except_admins() -> int:
    """Entzieht allen Nicht-Admins die Freigabe. Gibt die Zahl der betroffenen Accounts zurück."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE users SET is_approved = 0 WHERE is_admin = 0 AND is_approved = 1"
        )
        conn.commit()
        return cur.rowcount


def set_own_profile(user_id: int, profile_url: str, profile_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET own_profile_url = ?, own_profile_name = ? WHERE id = ?",
            (profile_url or "", profile_name or "", int(user_id)),
        )
        conn.commit()


def set_leaderboard_visible(user_id: int, visible: bool) -> None:
    _set_flag(user_id, "show_on_leaderboard", visible)


# ---------------------------------------------------------------------------
# Online-Status
# ---------------------------------------------------------------------------

def touch_last_seen(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET last_seen = ? WHERE id = ?", (_utc_now_iso(), int(user_id)))
        conn.commit()


def is_user_online(last_seen: Optional[str]) -> bool:
    """True, wenn der letzte Seitenaufruf höchstens ONLINE_THRESHOLD_SECONDS zurückliegt."""
    if not last_seen:
        return False
    try:
        seen = datetime.fromisoformat(last_seen)
    except ValueError:
        return False
    now = _utc_now() if seen.tzinfo else datetime.utcnow()
    return (now - seen).total_seconds() <= ONLINE_THRESHOLD_SECONDS


# ---------------------------------------------------------------------------
# Sessions ("Eingeloggt bleiben")
# ---------------------------------------------------------------------------

def create_session(twitch_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = _utc_now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token, twitch_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, str(twitch_id), now.isoformat(timespec="seconds"),
             (now + timedelta(days=SESSION_LIFETIME_DAYS)).isoformat(timespec="seconds")),
        )
        conn.commit()
    return token


def get_user_by_session_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT twitch_id, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row["expires_at"]) < _utc_now():
            delete_session(token)
            return None
    except ValueError:
        return None
    return get_user_by_twitch_id(row["twitch_id"])


def delete_session(token: Optional[str]) -> None:
    if not token:
        return
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


def delete_expired_sessions() -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_utc_now_iso(),))
        conn.commit()


# ---------------------------------------------------------------------------
# Favoriten
# ---------------------------------------------------------------------------

def get_favorites(user_id: int) -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM favorites WHERE user_id = ? ORDER BY LOWER(profile_name) ASC",
            (int(user_id),),
        ).fetchall()
    return [dict(r) for r in rows]


def is_favorite(user_id: int, profile_url: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND profile_url = ?",
            (int(user_id), profile_url),
        ).fetchone()
    return row is not None


def add_favorite(user_id: int, profile_url: str, profile_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO favorites (user_id, profile_url, profile_name, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, profile_url) DO UPDATE SET profile_name = excluded.profile_name",
            (int(user_id), profile_url, profile_name, _utc_now_iso()),
        )
        conn.commit()


def remove_favorite(user_id: int, profile_url: str) -> None:
    with _connect() as conn:
        conn.execute(
            "DELETE FROM favorites WHERE user_id = ? AND profile_url = ?",
            (int(user_id), profile_url),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Fortschritt & Bestenliste
# ---------------------------------------------------------------------------

def add_progress_snapshot(user_id: int, distinct_owned: int, distinct_total: int,
                          total_copies: int, missing_count: int) -> None:
    """Speichert einen Fortschritts-Schnappschuss. Ist der Stand identisch zum letzten
    Schnappschuss, wird nichts gespeichert (die App ruft das bei jedem Profil-Laden auf)."""
    with _connect() as conn:
        last = conn.execute(
            "SELECT distinct_owned, distinct_total, total_copies, missing_count "
            "FROM progress_snapshots WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (int(user_id),),
        ).fetchone()
        new = (int(distinct_owned), int(distinct_total), int(total_copies), int(missing_count))
        if last and tuple(last) == new:
            return
        conn.execute(
            "INSERT INTO progress_snapshots (user_id, taken_at, distinct_owned, distinct_total, "
            "total_copies, missing_count) VALUES (?, ?, ?, ?, ?, ?)",
            (int(user_id), _utc_now_iso(), *new),
        )
        conn.commit()


def get_progress_history(user_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    """Die letzten `limit` Schnappschüsse, ältester zuerst (für das Verlaufsdiagramm)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM (SELECT * FROM progress_snapshots WHERE user_id = ? "
            "ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
            (int(user_id), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_leaderboard(limit: int = 50) -> List[Dict[str, Any]]:
    """Neuester Schnappschuss je sichtbarem, nicht gesperrtem Account, bester zuerst."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT u.id AS user_id, u.twitch_username, s.distinct_owned, s.distinct_total,
                   s.total_copies, s.missing_count, s.taken_at
            FROM users u
            JOIN progress_snapshots s ON s.id = (
                SELECT MAX(id) FROM progress_snapshots WHERE user_id = u.id
            )
            WHERE u.show_on_leaderboard = 1 AND u.is_banned = 0
            ORDER BY s.distinct_owned DESC, s.total_copies DESC, LOWER(u.twitch_username) ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# Datenbank beim Import initialisieren (auth_ui.render_login_gate() ruft init_db() ohnehin
# nochmal auf - mehrfacher Aufruf ist unkritisch).
init_db()
