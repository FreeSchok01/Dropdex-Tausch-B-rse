# -*- coding: utf-8 -*-
"""
wishlist.py
===========
Eigenständiges Mini-Modul für das öffentliche "📋 Ich suche"-Board: jeder Account kann
Karten, die ihm fehlen, auf eine öffentlich sichtbare Wunschliste setzen. Andere Nutzer
sehen das Board, statt aktiv nach "Wer hat Karte X" suchen zu müssen, und können den
Wunschgeber direkt über den bestehenden Chat (siehe chat.py) anschreiben.

Eigene, kleine SQLite-Datei - unabhängig von db.py, damit hier nichts am bestehenden
Datenbank-Schema geändert werden muss (gleiches Muster wie chat.py/trade_watch.py).

Anzeigenamen/Avatare der Nutzer werden bewusst NICHT hier gespeichert (könnten veralten),
sondern von der aufrufenden Seite jeweils frisch per db.get_user_by_id() nachgeschlagen.
"""

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wishlist.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Legt die Tabelle an, falls sie noch nicht existiert. Mehrfacher Aufruf ist unkritisch."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS wishes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                card_id     TEXT NOT NULL,
                card_name   TEXT NOT NULL,
                rarity      TEXT,
                note        TEXT,
                created_at  TEXT NOT NULL,
                UNIQUE (user_id, card_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_wishes_user ON wishes (user_id)")
        conn.commit()


def add_wish(user_id: int, card_id: str, card_name: str, rarity: str = "", note: str = "") -> None:
    """Setzt eine Karte auf die eigene Wunschliste. Steht die Karte schon drauf, wird nur
    die Notiz aktualisiert (kein doppelter Eintrag, siehe UNIQUE-Constraint)."""
    if not card_id or not card_name:
        return
    with _connect() as conn:
        conn.execute(
            "INSERT INTO wishes (user_id, card_id, card_name, rarity, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, card_id) DO UPDATE SET note = excluded.note",
            (user_id, card_id, card_name, rarity, note.strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


def remove_wish(user_id: int, wish_id: int) -> None:
    """Entfernt einen Wunsch - nur der Ersteller selbst darf das (user_id wird mitgeprüft)."""
    with _connect() as conn:
        conn.execute("DELETE FROM wishes WHERE id = ? AND user_id = ?", (wish_id, user_id))
        conn.commit()


def get_my_wishes(user_id: int) -> List[Dict[str, Any]]:
    """Eigene Wunschliste, neueste zuerst."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM wishes WHERE user_id = ? ORDER BY id DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_board(exclude_user_id: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
    """Das komplette öffentliche Board (alle Wünsche aller Nutzer), neueste zuerst. Der
    eigene Nutzer kann ausgeschlossen werden, da man sich selbst nicht anschreiben kann."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM wishes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    items = [dict(r) for r in rows]
    if exclude_user_id is not None:
        items = [i for i in items if i["user_id"] != exclude_user_id]
    return items


def already_wished(user_id: int, card_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM wishes WHERE user_id = ? AND card_id = ?", (user_id, card_id)
        ).fetchone()
        return row is not None
