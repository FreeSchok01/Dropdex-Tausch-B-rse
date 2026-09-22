# -*- coding: utf-8 -*-
"""
trade_watch.py
===============
Beobachtet das eigene Dropdex-Profil im Hintergrund: merkt sich je Karte die zuletzt
bekannte Anzahl. Ist eine Karte beim nächsten Check weniger geworden (oder ganz weg),
werten wir das als erfolgreichen Tausch und legen automatisch eine Nachricht im
"🔔 News"-Reiter an (siehe notifications.py).

Eigene, kleine SQLite-Datei – unabhängig von db.py, damit hier nichts am
bestehenden Datenbank-Schema geändert werden muss.
"""

import os
import sqlite3
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import notifications

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_watch.db")

# Muss zur Auto-Refresh-Rate im Frontend passen (siehe render_auto_refresh() in der App).
CHECK_INTERVAL_SECONDS = 300


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Legt die Tabellen an, falls sie noch nicht existieren. Mehrfacher Aufruf ist unkritisch."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS card_snapshot (
                user_id INTEGER NOT NULL,
                card_id TEXT NOT NULL,
                card_name TEXT NOT NULL,
                rarity TEXT,
                count INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, card_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watch_meta (
                user_id INTEGER PRIMARY KEY,
                last_checked_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def _due_for_check(user_id: int) -> bool:
    """True, wenn seit dem letzten Check mindestens CHECK_INTERVAL_SECONDS vergangen sind
    (oder noch nie geprüft wurde). Verhindert doppelte Netzwerk-Abrufe bei mehreren offenen
    Tabs / schnell aufeinanderfolgenden Reruns."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT last_checked_at FROM watch_meta WHERE user_id = ?", (user_id,)
        ).fetchone()
    if not row:
        return True
    try:
        last = datetime.fromisoformat(row["last_checked_at"])
    except ValueError:
        return True
    return (datetime.now() - last).total_seconds() >= CHECK_INTERVAL_SECONDS


def _touch(user_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO watch_meta (user_id, last_checked_at) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_checked_at = excluded.last_checked_at",
            (user_id, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


def sync_inventory(user_id: int, inventory: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Vergleicht `inventory` (aktuelle Kartenliste aus dem frisch geladenen Profil) mit dem
    zuletzt gespeicherten Stand. Für jede Karte, deren Anzahl gesunken ist, wird automatisch
    eine News-Nachricht erzeugt ("Tausch erfolgreich"). Gibt die erkannten Änderungen zurück."""
    changes: List[Dict[str, Any]] = []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT card_id, count FROM card_snapshot WHERE user_id = ?", (user_id,)
        ).fetchall()
        prev_counts = {r["card_id"]: r["count"] for r in rows}

        now = datetime.now().isoformat(timespec="seconds")
        for c in inventory:
            cid = c.get("id")
            if cid is None:
                continue
            cur_count = int(c.get("count", 0) or 0)
            name = c.get("name", "Karte")
            rarity = c.get("rarity", "")

            if cid in prev_counts and cur_count < prev_counts[cid]:
                prev_count = prev_counts[cid]
                changes.append({"id": cid, "name": name, "rarity": rarity,
                                 "prev": prev_count, "cur": cur_count})
                if cur_count == 0:
                    msg = f"🔄 Tausch erfolgreich: {name} ist jetzt weg (vorher ×{prev_count})."
                else:
                    msg = (f"🔄 Tausch erfolgreich: Du hast eine Dublette von {name} abgegeben "
                           f"(jetzt noch ×{cur_count}).")
                notifications.add_notification(user_id, msg)

            conn.execute(
                "INSERT INTO card_snapshot (user_id, card_id, card_name, rarity, count, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, card_id) DO UPDATE SET "
                "card_name = excluded.card_name, rarity = excluded.rarity, "
                "count = excluded.count, updated_at = excluded.updated_at",
                (user_id, cid, name, rarity, cur_count, now),
            )
        conn.commit()
    return changes


def maybe_check(user_id: int, load_fn: Callable[[], Optional[List[Dict[str, Any]]]]) -> None:
    """Prüft (höchstens alle CHECK_INTERVAL_SECONDS) das eigene Profil auf verschwundene Karten.
    `load_fn` lädt ohne Argumente die aktuelle Karten-Liste (z.B. per erneutem Seitenabruf).
    Netzwerk-/Parserfehler werden verschluckt, damit ein Hänger bei dropdex.de nie die App
    zum Absturz bringt – der nächste Check (in 10s) versucht es einfach erneut."""
    if not _due_for_check(user_id):
        return
    _touch(user_id)
    try:
        inventory = load_fn()
    except Exception:  # noqa: BLE001
        return
    if inventory:
        sync_inventory(user_id, inventory)
