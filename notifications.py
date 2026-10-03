# -*- coding: utf-8 -*-
"""
notifications.py  (Firestore-Version)
=====================================
"🔔 News"-Reiter: speichert je Nutzer eine Benachrichtigung, sobald ein Tausch als
"getauscht" markiert wurde.

Firestore:
  users/{uid}/notifications/{auto}   message, created_at, ts, is_read
  counters/news_{uid}                {unread: n}  - Zähler für das Sidebar-Badge

Die Nachrichten-ID nach außen ist "{uid}_{auto}" (ein String). So lässt sich eine Nachricht
später (update_message / mark_read) wiederfinden, ohne dass der Nutzer mitgegeben werden muss.

unread_count() wird von der Haupt-App alle paar Sekunden aufgerufen. Damit das nicht jedes Mal
einen Lesezugriff kostet, wird das Zähler-Dokument per Live-Listener im Speicher gehalten
(siehe fb.watched).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from google.cloud import firestore

import fb


def _fs():
    return fb.get_client()


def _col(user_id: int):
    return _fs().collection("users").document(str(int(user_id))).collection("notifications")


def _counter_ref(user_id: int):
    return _fs().collection("counters").document(f"news_{int(user_id)}")


# Die Liste wird nur neu geladen, wenn sich der Zähler (unread + Versionsnummer "v" im Counter-
# Dokument, das ohnehin per Live-Listener überwacht wird) geändert hat. Sonst: 0 Lesezugriffe.
_MAX_ROWS = 100
_cache: Dict[int, Tuple[Tuple[int, int], List[Dict[str, Any]]]] = {}


def _after_write(uid: int, d_unread: int = 0, set_unread: Any = None) -> None:
    """Nach eigenem Schreibzugriff: Cache leeren und den überwachten Zähler sofort nachziehen."""
    _cache.pop(int(uid), None)
    cur = fb.watched_peek(_counter_ref(uid))
    if cur is None:
        return
    un = set_unread if set_unread is not None else max(0, int(cur.get("unread", 0) or 0) + d_unread)
    fb.watched_override(_counter_ref(uid), {**cur, "unread": un, "v": int(cur.get("v", 0) or 0) + 1})


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _split_id(notification_id: Any) -> Tuple[int, str]:
    uid, _, auto = str(notification_id).partition("_")
    return int(uid), auto


def init_db() -> None:
    """Bleibt aus Kompatibilitätsgründen erhalten - Firestore braucht kein Schema."""
    fb.get_client()


def add_notification(user_id: int, message: str) -> str:
    """Legt eine neue Nachricht für `user_id` an. Gibt die ID der Nachricht zurück, damit sie
    z.B. per update_message() später ergänzt werden kann (siehe trade_watch.py)."""
    ref = _col(user_id).document()
    batch = _fs().batch()
    batch.set(ref, {
        "message": message,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "ts": _ts(),
        "is_read": False,
    })
    batch.set(_counter_ref(user_id), {"unread": firestore.Increment(1), "v": firestore.Increment(1)},
              merge=True)
    batch.commit()
    _after_write(user_id, +1)
    return f"{int(user_id)}_{ref.id}"


def update_message(notification_id: str, message: str) -> None:
    """Ersetzt den Text einer bestehenden Nachricht und markiert sie wieder als ungelesen."""
    uid, auto = _split_id(notification_id)
    ref = _col(uid).document(auto)
    snap = ref.get()
    if not snap.exists:
        return
    was_read = bool(snap.to_dict().get("is_read"))
    batch = _fs().batch()
    batch.update(ref, {"message": message, "is_read": False})
    counter = {"v": firestore.Increment(1)}
    if was_read:
        counter["unread"] = firestore.Increment(1)
    batch.set(_counter_ref(uid), counter, merge=True)
    batch.commit()
    _after_write(uid, +1 if was_read else 0)


def get_notifications(user_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    """Nachrichten für `user_id`, neueste zuerst (höchstens _MAX_ROWS gespeicherte)."""
    uid = int(user_id)
    st = fb.watched(_counter_ref(uid))
    key = (int(st.get("v", 0) or 0), int(st.get("unread", 0) or 0))
    hit = _cache.get(uid)
    if hit and hit[0] == key:
        rows = hit[1]
    else:
        docs = (_col(uid).order_by("ts", direction=firestore.Query.DESCENDING)
                .limit(_MAX_ROWS).stream())
        rows = []
        for d in docs:
            x = d.to_dict()
            rows.append({"id": f"{uid}_{d.id}", "message": x.get("message", ""),
                         "created_at": x.get("created_at", ""), "is_read": bool(x.get("is_read"))})
        # Zähler selbst heilen, falls er je vom echten Wert abweicht (nur wenn wir ALLE
        # Nachrichten gesehen haben).
        if len(rows) < _MAX_ROWS:
            real = sum(1 for r in rows if not r["is_read"])
            if real != key[1]:
                _counter_ref(uid).set({"unread": real, "v": firestore.Increment(1)}, merge=True)
                _after_write(uid, set_unread=real)
                st = fb.watched(_counter_ref(uid))
                key = (int(st.get("v", 0) or 0), int(st.get("unread", 0) or 0))
        _cache[uid] = (key, rows)
    return [dict(r) for r in rows[:limit]]


def unread_count(user_id: int) -> int:
    return max(0, int(fb.watched(_counter_ref(user_id)).get("unread", 0) or 0))


def mark_read(notification_id: str) -> None:
    uid, auto = _split_id(notification_id)
    ref = _col(uid).document(auto)
    snap = ref.get()
    if not snap.exists or snap.to_dict().get("is_read"):
        return
    batch = _fs().batch()
    batch.update(ref, {"is_read": True})
    batch.set(_counter_ref(uid), {"unread": firestore.Increment(-1), "v": firestore.Increment(1)},
              merge=True)
    batch.commit()
    _after_write(uid, -1)


def mark_all_read(user_id: int) -> None:
    batch = _fs().batch()
    n = 0
    for d in _col(user_id).where("is_read", "==", False).stream():
        batch.update(d.reference, {"is_read": True})
        n += 1
        if n % 400 == 0:
            batch.commit()
            batch = _fs().batch()
    batch.set(_counter_ref(user_id), {"unread": 0, "v": firestore.Increment(1)}, merge=True)
    batch.commit()
    _after_write(user_id, set_unread=0)
