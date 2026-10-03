# -*- coding: utf-8 -*-
"""
wishlist.py  (Firestore-Version)
================================
Öffentliches "📋 Ich suche"-Board: jeder Account kann Karten, die ihm fehlen, auf eine
öffentlich sichtbare Wunschliste setzen. Andere Nutzer sehen das Board und können den
Wunschgeber direkt über den Chat (siehe chat.py) anschreiben.

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

_cache = fb.TTLCache(ttl=15.0)


def _col():
    return fb.get_client().collection("wishes")


def _doc_id(user_id: int, card_id: str) -> str:
    return f"{int(user_id)}_{hashlib.sha1(str(card_id).encode('utf-8')).hexdigest()[:20]}"


def _sorted_newest_first(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: r.get("ts") or r.get("created_at") or "", reverse=True)


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
    if ref.get().exists:
        ref.update({"note": note.strip(), "image_url": image_url or ""})
    else:
        ref.set({
            "id": ref.id, "user_id": int(user_id), "card_id": str(card_id), "card_name": card_name,
            "rarity": rarity, "note": note.strip(), "image_url": image_url or "",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        })
    _cache.clear()


def remove_wish(user_id: int, wish_id: Any) -> None:
    """Entfernt einen Wunsch - nur der Ersteller selbst darf das (user_id wird mitgeprüft)."""
    ref = _col().document(str(wish_id))
    snap = ref.get()
    if snap.exists and int(snap.to_dict().get("user_id", -1)) == int(user_id):
        ref.delete()
        _cache.clear()


def get_my_wishes(user_id: int) -> List[Dict[str, Any]]:
    """Eigene Wunschliste, neueste zuerst."""
    def load():
        return _sorted_newest_first(
            [dict(d.to_dict(), id=d.id) for d in _col().where("user_id", "==", int(user_id)).stream()])
    return list(_cache.get(("mine", int(user_id)), load))


def get_board(exclude_user_id: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
    """Das komplette öffentliche Board (alle Wünsche aller Nutzer), neueste zuerst. Der eigene
    Nutzer kann ausgeschlossen werden, da man sich selbst nicht anschreiben kann."""
    def load():
        return _sorted_newest_first([dict(d.to_dict(), id=d.id) for d in _col().stream()])
    items = list(_cache.get(("board",), load))[:limit]
    if exclude_user_id is not None:
        items = [i for i in items if int(i["user_id"]) != int(exclude_user_id)]
    return items


def already_wished(user_id: int, card_id: str) -> bool:
    return _col().document(_doc_id(user_id, card_id)).get().exists
