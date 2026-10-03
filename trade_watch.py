# -*- coding: utf-8 -*-
"""
trade_watch.py  (Firestore-Version)
===================================
Beobachtet das eigene Dropdex-Profil im Hintergrund: merkt sich je Karte die zuletzt bekannte
Anzahl. Taucht eine Karte auf, die man vorher wirklich noch NIE besessen hat (×0 -> ×1+),
legen wir automatisch eine Nachricht im "🔔 News"-Reiter an (siehe notifications.py).
Dubletten und abgegebene Karten erzeugen bewusst KEINE News - sie laufen nur intern mit,
damit der Tauschpartner-Abgleich funktioniert.

Verliert Nutzer A eine Karte X und bekommt kurz danach ein ANDERER beobachteter Nutzer B genau
diese Karte X als echte neue Karte, gilt das als Match - B's News-Eintrag wird um A's Namen
ergänzt (_try_match_partner() / _record_event()). Best-Effort: klappt nur, wenn beide hier
registriert sind UND ihr eigenes Profil hinterlegt haben (own_profile_url in db.py).

Zusätzlich pflegt dieses Modul das "🎁 Ich biete"-Board (siehe offers.py): Dubletten (×2 oder
mehr) werden automatisch eingetragen, sinkt der Bestand auf ×0/×1, wird der Eintrag entfernt.

Firestore:
  trade_snapshots/{user_id}   {cards: {<key>: {i: card_id, n: name, r: rarity, c: count}}, updated_at}
                              EIN Dokument pro Nutzer (statt einer Zeile pro Karte) - ein Check
                              kostet dadurch nur einen Lesezugriff und schreibt nur bei Änderungen.
  trade_events/{auto}         erkannte Wechsel für den Partner-Abgleich (expires_at ermöglicht
                              optional eine automatische Löschung per Firestore-TTL-Richtlinie).

Der Zeitpunkt des letzten Checks wird im Speicher gehalten (die App läuft als EIN Prozess).
"""

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import db
import fb
import notifications
import offers

# So oft wird das Profil höchstens geprüft (Sekunden). Die Haupt-App ruft maybe_check() alle
# 5 s auf (Fragment run_every=5) - dieses Intervall drosselt die tatsächlichen Abrufe bei
# dropdex.de und die Firestore-Zugriffe. Gern kleiner stellen, wenn es schneller sein soll.
CHECK_INTERVAL_SECONDS = 30

# Wie lange ein Wechsel (Karte weg ODER neu) auf einen passenden Gegenpart bei einem
# ANDEREN Nutzer wartet, bevor er für den Partner-Abgleich nicht mehr berücksichtigt wird.
MATCH_WINDOW_SECONDS = 600

_last_check: Dict[int, float] = {}


def _fs():
    return fb.get_client()


def _snap_ref(user_id: int):
    return _fs().collection("trade_snapshots").document(str(int(user_id)))


def _key(card_id: Any) -> str:
    """Map-Schlüssel in Firestore: Sonderzeichen ersetzen (die echte ID steht zusätzlich im Eintrag)."""
    return re.sub(r"[.\[\]*~/`]", "_", str(card_id)) or "_"


def init_db() -> None:
    """Bleibt aus Kompatibilitätsgründen erhalten - Firestore braucht kein Schema."""
    fb.get_client()


def _due_for_check(user_id: int) -> bool:
    return time.time() - _last_check.get(int(user_id), 0.0) >= CHECK_INTERVAL_SECONDS


def _touch(user_id: int) -> None:
    _last_check[int(user_id)] = time.time()


def _load_cards(user_id: int) -> Optional[Dict[str, Any]]:
    """Gespeicherter Stand {key: {i,n,r,c}} oder None, wenn für den Nutzer noch nie ein
    Snapshot gespeichert wurde (dann ist der nächste Sync der "erste" und meldet nichts)."""
    snap = _snap_ref(user_id).get()
    if not snap.exists:
        return None
    return (snap.to_dict() or {}).get("cards", {})


def _has_synced_before(user_id: int) -> bool:
    return _snap_ref(user_id).get().exists


def _format_new_card_message(card_name: str, cur_count: int, partner_name: Optional[str] = None) -> str:
    """News-Text für eine ECHTE neue Karte (vorher ×0)."""
    von = f" von {partner_name}" if partner_name else ""
    return f"🆕 Neue Karte erhalten{von}: {card_name} (jetzt ×{cur_count})."


def _try_match_partner(user_id: int, card_id: str, direction: str) -> Optional[Dict[str, Any]]:
    """Sucht einen noch unverbundenen, GEGENSÄTZLICHEN Wechsel derselben Karte durch einen
    ANDEREN Nutzer innerhalb von MATCH_WINDOW_SECONDS (ältester zuerst = FIFO)."""
    opposite = "gained" if direction == "lost" else "lost"
    cutoff = (datetime.now() - timedelta(seconds=MATCH_WINDOW_SECONDS)).isoformat(timespec="seconds")
    docs = (_fs().collection("trade_events").where("card_id", "==", str(card_id))
            .where("direction", "==", opposite).where("matched", "==", False).stream())
    candidates = []
    for d in docs:
        x = d.to_dict()
        if int(x.get("user_id", -1)) != int(user_id) and x.get("created_at", "") >= cutoff:
            candidates.append(dict(x, id=d.id))
    candidates.sort(key=lambda r: r["created_at"])
    return candidates[0] if candidates else None


def _record_event(user_id: int, card_id: str, card_name: str, rarity: str, direction: str,
                   cur_count: int, prev_count: int) -> None:
    """Verarbeitet einen erkannten Wechsel. Nur eine ECHTE neue Karte (gained UND prev_count == 0)
    erzeugt eine News-Nachricht. Dubletten und abgegebene Karten laufen nur intern als
    trade_events mit, damit der Tauschpartner-Abgleich funktioniert."""
    is_new_card = direction == "gained" and prev_count == 0
    now = datetime.now().isoformat(timespec="seconds")

    # ---- Automatischer Abgleich mit dem "🎁 Ich biete"-Board (siehe offers.py) ----
    if direction == "gained" and cur_count > 1:
        offers.add_offer(user_id, card_id, card_name, rarity, auto=True)
    elif direction == "lost" and cur_count <= 1:
        offers.remove_auto_offer(user_id, card_id)

    # Dubletten interessieren niemanden mehr - weder News noch Partner-Abgleich.
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

    events = _fs().collection("trade_events")
    batch = _fs().batch()
    batch.set(events.document(), {
        "user_id": int(user_id), "card_id": str(card_id), "card_name": card_name, "rarity": rarity,
        "direction": direction, "prev_count": int(prev_count), "cur_count": int(cur_count),
        "notification_id": notif_id, "matched": bool(partner_row), "created_at": now,
        "expires_at": datetime.now(timezone.utc) + timedelta(days=14),
    })
    if partner_row:
        batch.update(events.document(partner_row["id"]), {"matched": True})
    batch.commit()

    # Treffer: der wartende Gegenpart hatte selbst schon eine "neue Karte"-Nachricht - die wird
    # jetzt nachträglich um unseren Namen ergänzt.
    if partner_row and partner_row.get("notification_id"):
        current_user = db.get_user_by_id(user_id)
        current_name = current_user["twitch_username"] if current_user else None
        notifications.update_message(
            partner_row["notification_id"],
            _format_new_card_message(partner_row["card_name"], partner_row["cur_count"], current_name),
        )


def sync_inventory(user_id: int, inventory: List[Dict[str, Any]], is_first_sync: bool = False,
                    _prev_cards: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Vergleicht `inventory` (aktuelle Kartenliste aus dem frisch geladenen Profil) mit dem
    zuletzt gespeicherten Stand. Beim allerersten Sync (`is_first_sync`) wird nur die
    Ausgangslage gespeichert. Gibt die erkannten Änderungen zurück."""
    prev_cards = _prev_cards if _prev_cards is not None else (_load_cards(user_id) or {})
    cards = dict(prev_cards)  # nicht mehr gelistete Karten bleiben im Snapshot erhalten
    changes: List[Dict[str, Any]] = []
    pending_events: List[tuple] = []
    dirty = False

    for c in inventory:
        cid = c.get("id")
        if cid is None:
            continue
        cid = str(cid)
        cur_count = int(c.get("count", 0) or 0)
        name = c.get("name", "Karte")
        rarity = c.get("rarity", "")
        key = _key(cid)
        prev = prev_cards.get(key)
        prev_count = prev.get("c") if prev else None

        if not is_first_sync and prev_count is not None and cur_count != prev_count:
            direction = "lost" if cur_count < prev_count else "gained"
            changes.append({"id": cid, "name": name, "rarity": rarity,
                            "prev": prev_count, "cur": cur_count, "direction": direction})
            pending_events.append((cid, name, rarity, direction, cur_count, prev_count))

        entry = {"i": cid, "n": name, "r": rarity, "c": cur_count}
        if prev != entry:
            cards[key] = entry
            dirty = True

    # Erst den Snapshot sichern, DANN die News/Partner-Abgleiche anlegen.
    if dirty or not prev_cards:
        _snap_ref(user_id).set({"cards": cards,
                                "updated_at": datetime.now().isoformat(timespec="seconds")})

    for cid, name, rarity, direction, cur_count, prev_count in pending_events:
        _record_event(user_id, cid, name, rarity, direction, cur_count, prev_count)

    return changes


def maybe_check(user_id: int, load_fn: Callable[[], Optional[List[Dict[str, Any]]]]) -> None:
    """Prüft (höchstens alle CHECK_INTERVAL_SECONDS) das eigene Profil auf verschwundene ODER
    neu hinzugekommene Karten. `load_fn` lädt die aktuelle Karten-Liste (z.B. per erneutem
    Seitenabruf). Netzwerk-/Parserfehler werden verschluckt, damit ein Hänger bei dropdex.de nie
    die App zum Absturz bringt - der nächste Check versucht es einfach erneut."""
    if not _due_for_check(user_id):
        return
    _touch(user_id)
    try:
        prev_cards = _load_cards(user_id)
        inventory = load_fn()
    except Exception:  # noqa: BLE001
        return
    if inventory:
        sync_inventory(user_id, inventory, is_first_sync=prev_cards is None,
                       _prev_cards=prev_cards or {})
