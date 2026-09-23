# -*- coding: utf-8 -*-
"""
maintenance.py
===============
Eigenständiges Mini-Modul für den Wartungsmodus: der Admin kann die Seite für
alle anderen Nutzer sperren (z.B. während Arbeiten an der App), während er
selbst weiterhin vollen Zugriff behält.

Nutzt bewusst eine EIGENE, kleine SQLite-Datei (nicht db.py) - gleiches Muster
wie chat.py/wishlist.py/notifications.py, damit hier nichts am bestehenden
Datenbank-Schema geändert werden muss.
"""

import os
import sqlite3

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "maintenance.db")

_KEY = "maintenance_mode"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Legt die Tabelle an und trägt den Startwert (aus) ein, falls noch nicht
    vorhanden. Mehrfacher Aufruf ist unkritisch."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, '0')", (_KEY,)
        )
        conn.commit()


def is_maintenance_mode() -> bool:
    """True, wenn der Wartungsmodus gerade aktiv ist. Ruft init_db() intern
    selbst auf, damit der Aufrufer sich darum nicht kümmern muss."""
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (_KEY,)
        ).fetchone()
        return bool(row) and row["value"] == "1"


def set_maintenance_mode(on: bool) -> None:
    """Schaltet den Wartungsmodus an oder aus (z.B. per Toggle in der Sidebar)."""
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_KEY, "1" if on else "0"),
        )
        conn.commit()
