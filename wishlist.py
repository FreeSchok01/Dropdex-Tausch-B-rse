# -*- coding: utf-8 -*-
"""
wishlist.py  (Firestore-Version)
================================
Öffentliches "📋 Ich suche"-Board: jeder Account kann Karten, die ihm fehlen, auf eine
öffentlich sichtbare Wunschliste setzen. Andere Nutzer sehen das Board und können den
Wunschgeber direkt über den Chat (siehe chat.py) anschreiben.

Nach jeder Änderung wird match_alerts.check_user() aufgerufen: entsteht dadurch ein neues
Perfect-Match, bekommen beide Nutzer eine News-Nachricht (und optional eine Discord-Meldung).

Firestore: Sammlung wishes/{user_id}_{hash(card_id)}  (ersetzt den UNIQUE-Constraint:
pro Nutzer und Karte gibt es genau ein Dokument). Die "id" nach außen ist die Dokument-ID
(ein String) und wird von der Haupt-App nur durchgereicht (remove_wish(user_id, w["id"])).

Anzeigenamen/Avatare werden bewusst NICHT hier gespeichert, sondern von der aufrufenden
Seite per db.get_user_by_id() frisch nachgeschlagen.
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import fb

# Das komplette Board liegt per Live-Listener im Speicher (fb.watched_query): Abfragen kosten
# nichts, die Daten sind trotzdem immer aktuell. Eigene Änderungen werden sofort eingetragen.
_KEY = "wishes"


def _all() -> List[Dict[str, Any]]:
    return [dict(d, id=doc_id) for doc_id, d in fb.watched_query(_KEY, _col)]


def _col():
    return fb.get_client().collection("wishes")


def _doc_id(user_id: int, card_id: str) -> str:
    return f"{int(user_id)}_{hashlib.sha1(str(card_id).encode('utf-8')).hexdigest()[:20]}"


def _sorted_newest_first(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: r.get("ts") or r.get("created_at") or "", reverse=True)


def _check_matches(user_id: int) -> None:
    """Meldet neue Perfect-Matches. Lokal importiert, um einen Zirkelimport zu vermeiden
    (match_alerts importiert offers, offers importiert wishlist)."""
    import match_alerts
    match_alerts.check_user(user_id)


def init_db() -> None:
    """Bleibt aus Kompatibilitätsgründen erhalten - Firestore braucht kein Schema."""
    fb.get_client()


def add_wish(user_id: int, card_id: str, card_name: str, rarity: str = "", note: str = "",
             image_url: str = "") -> None:
    """Setzt eine Karte auf die eigene Wunschliste. Steht die Karte schon drauf, werden nur
    Notiz und Bild aktualisiert (kein doppelter Eintrag)."""
    if not card_id or not card_name:
        return
    ref = _col().document(_doc_id(user_id, card_id))
    if already_wished(user_id, card_id):
        fields = {"note": note.strip(), "image_url": image_url or ""}
        ref.update(fields)
        fb.wq_patch(_KEY, ref.id, fields, merge=True)
        return
    data = {
        "id": ref.id, "user_id": int(user_id), "card_id": str(card_id), "card_name": card_name,
        "rarity": rarity, "note": note.strip(), "image_url": image_url or "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    }
    ref.set(data)
    fb.wq_patch(_KEY, ref.id, data)
    _check_matches(user_id)


def remove_wish(user_id: int, wish_id: Any) -> None:
    """Entfernt einen Wunsch - nur der Ersteller selbst darf das (user_id wird mitgeprüft)."""
    doc = next((r for r in _all() if r["id"] == str(wish_id)), None)
    if doc and int(doc.get("user_id", -1)) == int(user_id):
        _col().document(str(wish_id)).delete()
        fb.wq_patch(_KEY, str(wish_id), None)
        _check_matches(user_id)


def get_my_wishes(user_id: int) -> List[Dict[str, Any]]:
    """Eigene Wunschliste, neueste zuerst."""
    return _sorted_newest_first([r for r in _all() if int(r.get("user_id", -1)) == int(user_id)])


def get_board(exclude_user_id: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
    """Das komplette öffentliche Board (alle Wünsche aller Nutzer), neueste zuerst. Der eigene
    Nutzer kann ausgeschlossen werden, da man sich selbst nicht anschreiben kann."""
    items = _sorted_newest_first(_all())[:limit]
    if exclude_user_id is not None:
        items = [i for i in items if int(i["user_id"]) != int(exclude_user_id)]
    return items


def already_wished(user_id: int, card_id: str) -> bool:
    did = _doc_id(user_id, card_id)
    return any(r["id"] == did for r in _all())
