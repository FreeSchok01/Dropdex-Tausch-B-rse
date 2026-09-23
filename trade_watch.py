# -*- coding: utf-8 -*-
"""
trade_watch.py
===============
Beobachtet das eigene Dropdex-Profil im Hintergrund: merkt sich je Karte die zuletzt
bekannte Anzahl. Taucht eine Karte auf, die man vorher wirklich noch NIE besessen hat
(×0 -> ×1+), legen wir automatisch eine Nachricht im "🔔 News"-Reiter an (siehe
notifications.py). Dubletten (man hatte schon welche, hat jetzt noch mehr) und
abgegebene Karten erzeugen bewusst KEINE News mehr - sie laufen nur noch intern mit,
damit der Tauschpartner-Abgleich unten funktioniert.

Zusätzlich versucht das System, bei einer neuen Karte automatisch den Tauschpartner zu
ermitteln: verliert Nutzer A eine Karte X und bekommt kurz danach ein ANDERER
beobachteter Nutzer B genau diese Karte X als echte neue Karte, gilt das als Match -
B's News-Eintrag wird dann automatisch um A's Namen ergänzt (siehe _try_match_partner()
/ _record_event()). Das ist ein Best-Effort-Abgleich: er klappt nur, wenn beide
Beteiligten hier registriert sind UND ihr eigenes Profil hinterlegt haben
(own_profile_url in db.py), da nur dann überhaupt beobachtet wird - ohne Match gibt's
trotzdem die normale "Neue Karte erhalten"-Nachricht, nur eben ohne Partnername.

Eigene, kleine SQLite-Datei – unabhängig von db.py, damit hier nichts am
bestehenden Datenbank-Schema geändert werden muss.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import db
import notifications

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trade_watch.db")

# Muss zur Auto-Refresh-Rate im Frontend passen (siehe _background_trade_check() in der App,
# läuft als Streamlit-Fragment mit run_every=5 - komplett ohne Seiten-Reload).
CHECK_INTERVAL_SECONDS = 5

# Wie lange ein Wechsel (Karte weg ODER neu) auf einen passenden Gegenpart bei einem
# ANDEREN Nutzer wartet, bevor er für den Partner-Abgleich nicht mehr berücksichtigt wird.
MATCH_WINDOW_SECONDS = 600


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
        # Jeder erkannte Wechsel (Karte weg ODER neu) landet hier, bis ein passender
        # Gegenpart bei einem anderen Nutzer gefunden wird (siehe _try_match_partner())
        # oder das Zeitfenster (MATCH_WINDOW_SECONDS) abläuft.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trade_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                card_id         TEXT NOT NULL,
                card_name       TEXT NOT NULL,
                rarity          TEXT,
                direction       TEXT NOT NULL,   -- 'lost' oder 'gained'
                prev_count      INTEGER NOT NULL,
                cur_count       INTEGER NOT NULL,
                notification_id INTEGER,
                matched         INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
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


def _has_synced_before(user_id: int) -> bool:
    """True, wenn für `user_id` schon mindestens einmal ein Snapshot gespeichert wurde.
    Beim allerersten Check überhaupt gibt es noch keinen Vorher-Zustand zum Vergleichen -
    ohne diese Prüfung würde sonst jede einzelne Karte im Profil beim ersten Laden
    fälschlich als "neu erhalten" gemeldet werden."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM card_snapshot WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone()
        return row is not None


def _format_new_card_message(card_name: str, cur_count: int, partner_name: Optional[str] = None) -> str:
    """Baut den News-Text für eine ECHTE neue Karte (vorher ×0). Dubletten und abgegebene
    Karten tauchen bewusst NICHT mehr in den News auf (siehe _record_event()) - intern
    laufen sie aber weiter mit, damit der Tauschpartner-Abgleich funktioniert."""
    von = f" von {partner_name}" if partner_name else ""
    return f"🆕 Neue Karte erhalten{von}: {card_name} (jetzt ×{cur_count})."


def _try_match_partner(user_id: int, card_id: str, direction: str) -> Optional[Dict[str, Any]]:
    """Sucht in trade_events nach einem noch unverbundenen, GEGENSÄTZLICHEN Wechsel derselben
    Karte durch einen ANDEREN Nutzer innerhalb von MATCH_WINDOW_SECONDS (der am längsten
    wartende Eintrag zuerst = FIFO). Gibt die passende Zeile zurück oder None."""
    opposite = "gained" if direction == "lost" else "lost"
    cutoff = (datetime.now() - timedelta(seconds=MATCH_WINDOW_SECONDS)).isoformat(timespec="seconds")
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM trade_events WHERE card_id = ? AND direction = ? AND matched = 0 "
            "AND user_id != ? AND created_at >= ? ORDER BY created_at ASC LIMIT 1",
            (card_id, opposite, user_id, cutoff),
        ).fetchone()
        return dict(row) if row else None


def _record_event(user_id: int, card_id: str, card_name: str, rarity: str, direction: str,
                   cur_count: int, prev_count: int) -> None:
    """Verarbeitet einen erkannten Wechsel. Nur eine ECHTE neue Karte (direction == 'gained'
    UND prev_count == 0, d.h. man hatte vorher wirklich keine einzige davon) erzeugt eine
    News-Nachricht. Dubletten (gained mit prev_count > 0) und abgegebene Karten (lost) laufen
    NICHT mehr in den News auf, sondern nur noch intern als trade_events mit, damit der
    Tauschpartner-Abgleich (_try_match_partner()) weiterhin funktioniert: verliert A eine Karte
    und bekommt B sie kurz danach als echte neue Karte, wird B's Nachricht automatisch um
    A's Namen ergänzt."""
    is_new_card = direction == "gained" and prev_count == 0
    now = datetime.now().isoformat(timespec="seconds")

    # Dubletten (gained, prev_count > 0) interessieren niemanden mehr - dafür weder News
    # noch Partner-Abgleich, damit sie auch keinen echten späteren Match "verbrauchen".
    if direction == "gained" and not is_new_card:
        return

    partner_row = _try_match_partner(user_id, card_id, direction)
    partner_name = None
    if partner_row:
        partner_user = db.get_user_by_id(partner_row["user_id"])
        partner_name = partner_user["twitch_username"] if partner_user else None

    notif_id = None
    if is_new_card:
        msg = _format_new_card_message(card_name, cur_count, partner_name)
        notif_id = notifications.add_notification(user_id, msg)

    with _connect() as conn:
        conn.execute(
            "INSERT INTO trade_events (user_id, card_id, card_name, rarity, direction, "
            "prev_count, cur_count, notification_id, matched, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, card_id, card_name, rarity, direction, prev_count, cur_count,
             notif_id, 1 if partner_row else 0, now),
        )
        if partner_row:
            conn.execute("UPDATE trade_events SET matched = 1 WHERE id = ?", (partner_row["id"],))
        conn.commit()

    # Treffer: der wartende Gegenpart hatte selbst schon eine "neue Karte"-Nachricht (nur die
    # gibt es ja noch) - die wird jetzt nachträglich um unseren Namen ergänzt.
    if partner_row and partner_row.get("notification_id"):
        current_user = db.get_user_by_id(user_id)
        current_name = current_user["twitch_username"] if current_user else None
        partner_msg = _format_new_card_message(
            partner_row["card_name"], partner_row["cur_count"], current_name,
        )
        notifications.update_message(partner_row["notification_id"], partner_msg)


def sync_inventory(user_id: int, inventory: List[Dict[str, Any]],
                    is_first_sync: bool = False) -> List[Dict[str, Any]]:
    """Vergleicht `inventory` (aktuelle Kartenliste aus dem frisch geladenen Profil) mit dem
    zuletzt gespeicherten Stand. Für jede Karte, deren Anzahl sich verändert hat, wird eine
    News-Nachricht erzeugt (abgegeben ODER neu erhalten, siehe _record_event()) - außer beim
    allerersten Sync (`is_first_sync`), der nur die Ausgangslage speichert. Gibt die erkannten
    Änderungen zurück."""
    changes: List[Dict[str, Any]] = []
    pending_events: List[tuple] = []

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
            prev_count = prev_counts.get(cid)

            if not is_first_sync and prev_count is not None and cur_count != prev_count:
                direction = "lost" if cur_count < prev_count else "gained"
                changes.append({"id": cid, "name": name, "rarity": rarity,
                                 "prev": prev_count, "cur": cur_count, "direction": direction})
                pending_events.append((cid, name, rarity, direction, cur_count, prev_count))

            conn.execute(
                "INSERT INTO card_snapshot (user_id, card_id, card_name, rarity, count, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, card_id) DO UPDATE SET "
                "card_name = excluded.card_name, rarity = excluded.rarity, "
                "count = excluded.count, updated_at = excluded.updated_at",
                (user_id, cid, name, rarity, cur_count, now),
            )
        conn.commit()

    # Erst NACH dem Commit des Snapshots die News-Einträge/Partner-Abgleiche anlegen (jede
    # einzelne davon macht ihre eigene kurze Schreib-Transaktion, siehe _record_event()) -
    # so bleibt die Snapshot-Transaktion oben kurz und es gibt kein "database is locked".
    for cid, name, rarity, direction, cur_count, prev_count in pending_events:
        _record_event(user_id, cid, name, rarity, direction, cur_count, prev_count)

    return changes


def maybe_check(user_id: int, load_fn: Callable[[], Optional[List[Dict[str, Any]]]]) -> None:
    """Prüft (höchstens alle CHECK_INTERVAL_SECONDS) das eigene Profil auf verschwundene ODER
    neu hinzugekommene Karten. `load_fn` lädt ohne Argumente die aktuelle Karten-Liste (z.B.
    per erneutem Seitenabruf). Netzwerk-/Parserfehler werden verschluckt, damit ein Hänger bei
    dropdex.de nie die App zum Absturz bringt – der nächste Check versucht es einfach erneut."""
    if not _due_for_check(user_id):
        return
    is_first_sync = not _has_synced_before(user_id)
    _touch(user_id)
    try:
        inventory = load_fn()
    except Exception:  # noqa: BLE001
        return
    if inventory:
        sync_inventory(user_id, inventory, is_first_sync=is_first_sync)
