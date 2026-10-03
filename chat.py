# -*- coding: utf-8 -*-
"""
chat.py  (Firestore-Version)
============================
Direkte 1:1-Nachrichten zwischen zwei registrierten Accounts (Twitch-Login über
auth_ui.py/db.py). Ein Chat gibt es nur zwischen zwei registrierten Accounts - den Partner
findet man immer über die Twitch-Username-Suche (db.search_users_by_username).

Die Chat-Seite lädt bewusst NICHT automatisch neu - es gibt einen manuellen
"🔄 Aktualisieren"-Button in der UI.

Firestore:
  conversations/{a}_{b}                 (a < b, User-IDs)  participants, last_message, last_at,
                                        last_ts, unread: {"<uid>": n}
  conversations/{a}_{b}/messages/{auto} from_user_id, to_user_id, body, created_at, ts, is_read
  counters/chat_{uid}                   {unread: n}  - Gesamtzähler für das Sidebar-Badge
                                        (per Live-Listener im Speicher, siehe fb.watched)
"""

from datetime import datetime, timezone
from typing import Any, Dict, List

from google.cloud import firestore

import fb

MAX_MESSAGE_LENGTH = 2000


def _fs():
    return fb.get_client()


def _conv_id(a: int, b: int) -> str:
    lo, hi = sorted((int(a), int(b)))
    return f"{lo}_{hi}"


def _conv_ref(a: int, b: int):
    return _fs().collection("conversations").document(_conv_id(a, b))


def _counter_ref(user_id: int):
    return _fs().collection("counters").document(f"chat_{int(user_id)}")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def init_db() -> None:
    """Bleibt aus Kompatibilitätsgründen erhalten - Firestore braucht kein Schema."""
    fb.get_client()


def send_message(from_user_id: int, to_user_id: int, body: str) -> None:
    """Legt eine neue Nachricht an. Leere Nachrichten und Nachrichten an sich selbst werden
    stillschweigend ignoriert; zu lange Texte werden auf MAX_MESSAGE_LENGTH gekürzt."""
    text = (body or "").strip()[:MAX_MESSAGE_LENGTH]
    if not text or int(from_user_id) == int(to_user_id):
        return
    now_local = datetime.now().isoformat(timespec="seconds")
    ts = _ts()
    conv = _conv_ref(from_user_id, to_user_id)
    msg_ref = conv.collection("messages").document()
    batch = _fs().batch()
    batch.set(msg_ref, {
        "from_user_id": int(from_user_id), "to_user_id": int(to_user_id), "body": text,
        "created_at": now_local, "ts": ts, "is_read": False,
    })
    batch.set(conv, {
        "participants": [int(from_user_id), int(to_user_id)],
        "last_message": text, "last_at": now_local, "last_ts": ts,
        "unread": {str(int(to_user_id)): firestore.Increment(1)},
    }, merge=True)
    batch.set(_counter_ref(to_user_id), {"unread": firestore.Increment(1)}, merge=True)
    batch.commit()


def get_conversation(user_id: int, partner_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    """Die letzten `limit` Nachrichten zwischen den beiden Nutzern, älteste zuerst."""
    docs = (_conv_ref(user_id, partner_id).collection("messages")
            .order_by("ts", direction=firestore.Query.DESCENDING).limit(limit).stream())
    rows = [dict(d.to_dict(), id=d.id) for d in docs]
    rows.reverse()
    return rows


def mark_conversation_read(user_id: int, partner_id: int) -> None:
    """Markiert alle eingehenden Nachrichten von `partner_id` als gelesen (wird aufgerufen,
    sobald die Unterhaltung geöffnet wird). Kostet nur dann weitere Zugriffe, wenn es wirklich
    ungelesene Nachrichten gibt."""
    conv = _conv_ref(user_id, partner_id)
    snap = conv.get()
    if not snap.exists:
        return
    n = int((snap.to_dict().get("unread") or {}).get(str(int(user_id)), 0) or 0)
    if n <= 0:
        return
    batch = _fs().batch()
    for d in (conv.collection("messages").where("to_user_id", "==", int(user_id))
              .where("is_read", "==", False).stream()):
        batch.update(d.reference, {"is_read": True})
    batch.set(conv, {"unread": {str(int(user_id)): 0}}, merge=True)
    batch.set(_counter_ref(user_id), {"unread": firestore.Increment(-n)}, merge=True)
    batch.commit()


def get_conversations_overview(user_id: int) -> List[Dict[str, Any]]:
    """Liste aller Unterhaltungen: pro Gesprächspartner die letzte Nachricht, deren Zeitpunkt
    und die Anzahl ungelesener Nachrichten von ihm. Neueste Unterhaltung zuerst."""
    uid = int(user_id)
    convos = []
    total_unread = 0
    for d in _fs().collection("conversations").where("participants", "array_contains", uid).stream():
        x = d.to_dict()
        partner = next((p for p in x.get("participants", []) if int(p) != uid), None)
        if partner is None:
            continue
        unread = int((x.get("unread") or {}).get(str(uid), 0) or 0)
        total_unread += unread
        convos.append({"partner_id": int(partner), "last_message": x.get("last_message", ""),
                       "last_at": x.get("last_at", ""), "unread": unread,
                       "_ts": x.get("last_ts", "")})
    convos.sort(key=lambda c: c["_ts"], reverse=True)
    for c in convos:
        c.pop("_ts", None)
    # Gesamtzähler selbst heilen, falls er vom echten Wert abweicht
    if total_unread != unread_count(uid):
        _counter_ref(uid).set({"unread": total_unread})
        fb.watched_override(_counter_ref(uid), {"unread": total_unread})
    return convos


def unread_count(user_id: int) -> int:
    """Gesamtzahl ungelesener Nachrichten (für das Badge neben "💬 Chat" in der Sidebar)."""
    return max(0, int(fb.watched(_counter_ref(user_id)).get("unread", 0) or 0))
