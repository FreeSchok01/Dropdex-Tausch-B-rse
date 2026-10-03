# -*- coding: utf-8 -*-
"""
offers.py  (Firestore-Version)
==============================
Öffentliches "🎁 Ich biete"-Board: Gegenstück zu wishlist.py ("📋 Ich suche"). Jeder Account
kann Karten, die er abgeben würde, hier eintragen - andere Nutzer können direkt über den
Chat (siehe chat.py) anschreiben.

Wird zusätzlich AUTOMATISCH von trade_watch.py gepflegt (siehe dort _record_event()): bekommt
ein beobachteter Nutzer eine Dublette (×2 oder mehr), wird sie hier automatisch als Angebot
eingetragen (auto=1). Sinkt der Bestand wieder auf 0 oder 1, wird der automatische Eintrag
wieder entfernt (remove_auto_offer()). Manuell gesetzte Angebote (auto=0) fasst trade_watch
NIE an.

Nach jeder Änderung wird match_alerts.check_user() aufgerufen: entsteht dadurch ein neues
Perfect-Match, bekommen beide Nutzer eine News-Nachricht (und optional eine Discord-Meldung).

Firestore: Sammlung offers/{user_id}_{hash(card_id)} (pro Nutzer und Karte genau ein
Dokument, ersetzt den UNIQUE-Constraint). Die "id" nach außen ist die Dokument-ID (String).
"""

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import fb

_cache = fb.TTLCache(ttl=15.0)


def _col():
    return fb.get_client().collection("offers")


def _doc_id(user_id: int, card_id: str) -> str:
    return f"{int(user_id)}_{hashlib.sha1(str(card_id).encode('utf-8')).hexdigest()[:20]}"


def _sorted_newest_first(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: r.get("ts") or r.get("created_at") or "", reverse=True)


def _check_matches(user_id: int) -> None:
    """Meldet neue Perfect-Matches. Lokal importiert, um einen Zirkelimport zu vermeiden
    (match_alerts importiert offers)."""
    import match_alerts
    match_alerts.check_user(user_id)


def init_db() -> None:
    """Bleibt aus Kompatibilitätsgründen erhalten - Firestore braucht kein Schema."""
    fb.get_client()


def add_offer(user_id: int, card_id: str, card_name: str, rarity: str = "", note: str = "",
              image_url: str = "", auto: bool = False) -> None:
    """Setzt eine Karte auf die eigene Angebotsliste. Steht sie schon drauf, werden nur
    Notiz/Bild/Seltenheit aktualisiert - der auto-Status eines bestehenden Eintrags bleibt
    unangetastet (ein manueller Eintrag wird durch einen automatischen trade_watch-Treffer
    NICHT zu einem "auto"-Eintrag, den remove_auto_offer() später löschen könnte)."""
    if not card_id or not card_name:
        return
    ref = _col().document(_doc_id(user_id, card_id))
    if ref.get().exists:
        ref.update({"note": note.strip(), "image_url": image_url or "", "rarity": rarity})
        _cache.clear()
        return
    ref.set({
        "id": ref.id, "user_id": int(user_id), "card_id": str(card_id), "card_name": card_name,
        "rarity": rarity, "note": note.strip(), "image_url": image_url or "",
        "auto": 1 if auto else 0,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
    })
    _cache.clear()
    _check_matches(user_id)


def remove_offer(user_id: int, offer_id: Any) -> None:
    """Entfernt ein Angebot - nur der Ersteller selbst darf das (user_id wird mitgeprüft)."""
    ref = _col().document(str(offer_id))
    snap = ref.get()
    if snap.exists and int(snap.to_dict().get("user_id", -1)) == int(user_id):
        ref.delete()
        _cache.clear()
        _check_matches(user_id)


def remove_auto_offer(user_id: int, card_id: str) -> None:
    """Entfernt einen automatisch von trade_watch gesetzten Eintrag wieder. Manuelle Einträge
    (auto=0) bleiben unberührt, selbst wenn sie dieselbe Karte betreffen."""
    ref = _col().document(_doc_id(user_id, card_id))
    snap = ref.get()
    if snap.exists and int(snap.to_dict().get("auto", 0)) == 1:
        ref.delete()
        _cache.clear()
        _check_matches(user_id)


def get_my_offers(user_id: int) -> List[Dict[str, Any]]:
    """Eigene Angebotsliste, neueste zuerst."""
    def load():
        return _sorted_newest_first(
            [dict(d.to_dict(), id=d.id) for d in _col().where("user_id", "==", int(user_id)).stream()])
    return list(_cache.get(("mine", int(user_id)), load))


def get_board(exclude_user_id: Optional[int] = None, limit: int = 500) -> List[Dict[str, Any]]:
    """Das komplette öffentliche Board (alle Angebote aller Nutzer), neueste zuerst."""
    def load():
        return _sorted_newest_first([dict(d.to_dict(), id=d.id) for d in _col().stream()])
    items = list(_cache.get(("board",), load))[:limit]
    if exclude_user_id is not None:
        items = [i for i in items if int(i["user_id"]) != int(exclude_user_id)]
    return items


def already_offered(user_id: int, card_id: str) -> bool:
    return _col().document(_doc_id(user_id, card_id)).get().exists


# ---------------------------------------------------------------------------
# Automatischer Abgleich Angebot <-> Wunsch (siehe wishlist.py)
# ---------------------------------------------------------------------------

def get_matches_for_wisher(user_id: int) -> List[Dict[str, Any]]:
    """Welche Angebote ANDERER Nutzer passen zu Karten, die `user_id` sucht?"""
    import wishlist  # lokal importiert, um einen Zirkelimport auf Modulebene zu vermeiden
    wanted_ids = {w["card_id"] for w in wishlist.get_my_wishes(user_id)}
    if not wanted_ids:
        return []
    return [o for o in get_board(exclude_user_id=user_id) if o["card_id"] in wanted_ids]


def get_matches_for_offerer(user_id: int) -> List[Dict[str, Any]]:
    """Umgekehrt: welche ANDEREN Nutzer suchen genau die Karten, die `user_id` anbietet?"""
    import wishlist
    offered_ids = {o["card_id"] for o in get_my_offers(user_id)}
    if not offered_ids:
        return []
    return [w for w in wishlist.get_board(exclude_user_id=user_id) if w["card_id"] in offered_ids]


def get_perfect_matches(user_id: int) -> List[Dict[str, Any]]:
    """Echte 1:1-Tausch-Matches: andere Nutzer, die GLEICHZEITIG etwas anbieten, das ich suche,
    UND etwas suchen, das ich anbiete. Pro passendem Partner eine Zeile:
        partner_id  - der andere Nutzer
        from_them   - seine Angebots-Zeilen, die zu meinen Wünschen passen
        from_me     - seine Wunsch-Zeilen, die zu meinen Angeboten passen
    """
    import wishlist
    my_wants = {w["card_id"] for w in wishlist.get_my_wishes(user_id)}
    my_offers_ids = {o["card_id"] for o in get_my_offers(user_id)}
    if not my_wants or not my_offers_ids:
        return []

    other_offers = get_board(exclude_user_id=user_id)
    other_wishes = wishlist.get_board(exclude_user_id=user_id)

    offers_for_me: Dict[int, List[Dict[str, Any]]] = {}
    for o in other_offers:
        if o["card_id"] in my_wants:
            offers_for_me.setdefault(o["user_id"], []).append(o)

    wishes_matching_me: Dict[int, List[Dict[str, Any]]] = {}
    for w in other_wishes:
        if w["card_id"] in my_offers_ids:
            wishes_matching_me.setdefault(w["user_id"], []).append(w)

    matches: List[Dict[str, Any]] = []
    for partner_id, their_offers in offers_for_me.items():
        their_wishes = wishes_matching_me.get(partner_id)
        if their_wishes:
            matches.append({"partner_id": partner_id, "from_them": their_offers,
                            "from_me": their_wishes})
    return matches
