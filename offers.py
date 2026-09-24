# -*- coding: utf-8 -*-
"""
offers.py
=========
Eigenständiges Mini-Modul für das öffentliche "🎁 Ich biete"-Board: Gegenstück zu
wishlist.py ("📋 Ich suche"). Jeder Account kann Karten, die er abgeben würde,
hier eintragen - andere Nutzer sehen das Board und können direkt über den
bestehenden Chat (siehe chat.py) anschreiben.

Wird zusätzlich AUTOMATISCH von trade_watch.py gepflegt (siehe dort
_record_event()): bekommt ein beobachteter Nutzer eine Dublette einer Karte
(er besitzt sie danach ×2 oder mehr), wird sie hier automatisch als Angebot
eingetragen (auto=1) - er hat ja offensichtlich mehr davon, als er selbst
braucht. Sinkt der Bestand später wieder auf 0 oder 1 (Karte abgegeben oder
keine Dublette mehr übrig), wird der automatische Eintrag wieder entfernt
(siehe remove_auto_offer()).

Manuell gesetzte Angebote (auto=0) fasst trade_watch NIE an - nur seine
eigenen, mit auto=1 markierten Einträge.

Eigene, kleine SQLite-Datei - unabhängig von db.py, gleiches Muster wie
wishlist.py/chat.py.
"""

import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offers.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Legt die Tabelle an, falls sie noch nicht existiert. Mehrfacher Aufruf ist unkritisch."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS offers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                card_id     TEXT NOT NULL,
                card_name   TEXT NOT NULL,
                rarity      TEXT,
                note        TEXT,
                image_url   TEXT DEFAULT '',
                auto        INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                UNIQUE (user_id, card_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_offers_user ON offers (user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_offers_card ON offers (card_id)")
        conn.commit()


def add_offer(user_id: int, card_id: str, card_name: str, rarity: str = "", note: str = "",
              image_url: str = "", auto: bool = False) -> None:
    """Setzt eine Karte auf die eigene Angebotsliste. Steht sie schon drauf, werden nur
    Notiz/Bild/Seltenheit aktualisiert - der auto-Status eines bereits bestehenden Eintrags
    bleibt dabei unangetastet (ein manueller Eintrag wird durch einen automatischen
    trade_watch-Treffer NICHT nachträglich zu einem "auto"-Eintrag, den remove_auto_offer()
    später wieder löschen könnte, und umgekehrt)."""
    if not card_id or not card_name:
        return
    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM offers WHERE user_id = ? AND card_id = ?", (user_id, card_id)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE offers SET note = ?, image_url = ?, rarity = ? WHERE id = ?",
                (note.strip(), image_url or "", rarity, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO offers (user_id, card_id, card_name, rarity, note, image_url, auto, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, card_id, card_name, rarity, note.strip(), image_url or "",
                 1 if auto else 0, datetime.now().isoformat(timespec="seconds")),
            )
        conn.commit()


def remove_offer(user_id: int, offer_id: int) -> None:
    """Entfernt ein Angebot - nur der Ersteller selbst darf das (user_id wird mitgeprüft)."""
    with _connect() as conn:
        conn.execute("DELETE FROM offers WHERE id = ? AND user_id = ?", (offer_id, user_id))
        conn.commit()


def remove_auto_offer(user_id: int, card_id: str) -> None:
    """Entfernt einen automatisch von trade_watch gesetzten Eintrag wieder (Karte abgegeben
    oder keine Dublette mehr übrig). Manuelle Einträge (auto=0) bleiben davon unberührt,
    selbst wenn sie zufällig dieselbe Karte betreffen."""
    with _connect() as conn:
        conn.execute(
            "DELETE FROM offers WHERE user_id = ? AND card_id = ? AND auto = 1",
            (user_id, card_id),
        )
        conn.commit()


def get_my_offers(user_id: int) -> List[Dict[str, Any]]:
    """Eigene Angebotsliste, neueste zuerst."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM offers WHERE user_id = ? ORDER BY id DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_board(exclude_user_id: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
    """Das komplette öffentliche Board (alle Angebote aller Nutzer), neueste zuerst."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM offers ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    items = [dict(r) for r in rows]
    if exclude_user_id is not None:
        items = [i for i in items if i["user_id"] != exclude_user_id]
    return items


def already_offered(user_id: int, card_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM offers WHERE user_id = ? AND card_id = ?", (user_id, card_id)
        ).fetchone()
        return row is not None


# ---------------------------------------------------------------------------
# Automatischer Abgleich Angebot <-> Wunsch (siehe wishlist.py)
# ---------------------------------------------------------------------------

def get_matches_for_wisher(user_id: int) -> List[Dict[str, Any]]:
    """Für die eigene Wunschliste: welche Angebote ANDERER Nutzer passen zu Karten, die
    `user_id` sucht? Gibt Angebots-Zeilen zurück (inkl. anbietendem user_id), damit die
    aufrufende Seite z.B. "🎉 3 Angebote passen zu deinen Wünschen" anzeigen und direkt
    zum Chat mit dem Anbieter verlinken kann."""
    import wishlist  # lokal importiert, um einen Zirkelimport auf Modulebene zu vermeiden
    wanted_ids = {w["card_id"] for w in wishlist.get_my_wishes(user_id)}
    if not wanted_ids:
        return []
    board = get_board(exclude_user_id=user_id)
    return [o for o in board if o["card_id"] in wanted_ids]


def get_matches_for_offerer(user_id: int) -> List[Dict[str, Any]]:
    """Umgekehrte Richtung: für die eigenen Angebote, welche ANDEREN Nutzer suchen genau
    diese Karten? Gibt Wunsch-Zeilen (aus wishlist.py, inkl. suchendem user_id) zurück."""
    import wishlist
    offered_ids = {o["card_id"] for o in get_my_offers(user_id)}
    if not offered_ids:
        return []
    wish_board = wishlist.get_board(exclude_user_id=user_id)
    return [w for w in wish_board if w["card_id"] in offered_ids]


def get_perfect_matches(user_id: int) -> List[Dict[str, Any]]:
    """Echte 1:1-Tausch-Matches: andere Nutzer, die GLEICHZEITIG etwas anbieten, das ich
    suche, UND etwas suchen, das ich anbiete - ein Tausch ganz ohne Umweg über Dritte.
    Gibt pro passendem Partner eine Zeile zurück:
        partner_id  - der andere Nutzer
        from_them   - seine Angebots-Zeilen, die zu meinen Wünschen passen (was er mir geben könnte)
        from_me     - seine Wunsch-Zeilen, die zu meinen Angeboten passen (was ich ihm geben könnte)
    """
    import wishlist
    my_wants = {w["card_id"] for w in wishlist.get_my_wishes(user_id)}
    my_offers_ids = {o["card_id"] for o in get_my_offers(user_id)}
    if not my_wants or not my_offers_ids:
        return []

    other_offers = get_board(exclude_user_id=user_id)            # was andere anbieten
    other_wishes = wishlist.get_board(exclude_user_id=user_id)   # was andere suchen

    # Wer bietet mir etwas an, das ich suche? -> gruppiert nach Anbieter
    offers_for_me: Dict[int, List[Dict[str, Any]]] = {}
    for o in other_offers:
        if o["card_id"] in my_wants:
            offers_for_me.setdefault(o["user_id"], []).append(o)

    # Wer sucht etwas, das ich anbiete? -> gruppiert nach Sucher
    wishes_matching_me: Dict[int, List[Dict[str, Any]]] = {}
    for w in other_wishes:
        if w["card_id"] in my_offers_ids:
            wishes_matching_me.setdefault(w["user_id"], []).append(w)

    # Nur Partner, bei denen BEIDE Richtungen gleichzeitig zutreffen, ergeben einen
    # perfekten Tausch (statt nur eines einseitigen Wunsches/Angebots).
    matches: List[Dict[str, Any]] = []
    for partner_id, their_offers in offers_for_me.items():
        their_wishes = wishes_matching_me.get(partner_id)
        if their_wishes:
            matches.append({
                "partner_id": partner_id,
                "from_them": their_offers,
                "from_me": their_wishes,
            })
    return matches
