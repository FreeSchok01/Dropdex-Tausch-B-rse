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
from typing import Any, Dict, List, Tuple

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


# Caches, die nur dann neu laden, wenn sich der Zähler (unread + Version "v" im Counter-Dokument,
# per Live-Listener überwacht) geändert hat oder man selbst geschrieben hat. Sonst: 0 Lesezugriffe.
_conv_cache: Dict[Tuple[int, int], Dict[str, Any]] = {}
_ov_cache: Dict[int, Dict[str, Any]] = {}
_read_ok: Dict[Tuple[int, int], int] = {}


def _version(uid: int) -> int:
    return int(fb.watched(_counter_ref(uid)).get("v", 0) or 0)


def _bump_peek(uid: int, d_unread: int = 0, set_unread: Any = None) -> None:
    """Zieht den überwachten Zähler nach eigenem Schreibzugriff sofort nach (falls überwacht)."""
    cur = fb.watched_peek(_counter_ref(uid))
    if cur is None:
        return
    un = set_unread if set_unread is not None else max(0, int(cur.get("unread", 0) or 0) + d_unread)
    fb.watched_override(_counter_ref(uid), {**cur, "unread": un, "v": int(cur.get("v", 0) or 0) + 1})


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
    batch.set(_counter_ref(to_user_id), {"unread": firestore.Increment(1), "v": firestore.Increment(1)},
              merge=True)
    batch.commit()
    _bump_peek(int(to_user_id), +1)
    _ov_cache.pop(int(from_user_id), None)
    _ov_cache.pop(int(to_user_id), None)
    c = _conv_cache.get((int(from_user_id), int(to_user_id)))
    if c:
        c["dirty"] = True


def get_conversation(user_id: int, partner_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    """Die letzten `limit` Nachrichten zwischen den beiden Nutzern, älteste zuerst. Beim ersten
    Öffnen werden bis zu `limit` Nachrichten gelesen, danach nur noch NEUE (ts > letzte bekannte)."""
    uid, pid = int(user_id), int(partner_id)
    k = (uid, pid)
    v = _version(uid)
    c = _conv_cache.get(k)
    msgs = _conv_ref(uid, pid).collection("messages")
    if c is None:
        docs = msgs.order_by("ts", direction=firestore.Query.DESCENDING).limit(limit).stream()
        rows = [dict(d.to_dict(), id=d.id) for d in docs]
        rows.reverse()
        c = {"rows": rows}
    elif c["v"] != v or c["dirty"]:
        last = c["rows"][-1]["ts"] if c["rows"] else ""
        have = {r["id"] for r in c["rows"]}
        new = [dict(d.to_dict(), id=d.id)
               for d in msgs.where("ts", ">", last).order_by("ts").stream() if d.id not in have]
        c["rows"] = (c["rows"] + new)[-max(limit, 100):]
    c["v"], c["dirty"] = v, False
    _conv_cache[k] = c
    return [dict(r) for r in c["rows"][-limit:]]


def mark_conversation_read(user_id: int, partner_id: int) -> None:
    """Markiert alle eingehenden Nachrichten von `partner_id` als gelesen (wird aufgerufen,
    sobald die Unterhaltung geöffnet wird). Kostet nur dann Zugriffe, wenn sich der Zähler seit
    der letzten Prüfung geändert hat."""
    uid, pid = int(user_id), int(partner_id)
    k = (uid, pid)
    v = _version(uid)
    if _read_ok.get(k) == v:
        return
    conv = _conv_ref(uid, pid)
    snap = conv.get()
    n = int(((snap.to_dict() or {}).get("unread") or {}).get(str(uid), 0) or 0) if snap.exists else 0
    if n <= 0:
        _read_ok[k] = v
        return
    batch = _fs().batch()
    for d in (conv.collection("messages").where("to_user_id", "==", uid)
              .where("is_read", "==", False).stream()):
        batch.update(d.reference, {"is_read": True})
    batch.set(conv, {"unread": {str(uid): 0}}, merge=True)
    batch.set(_counter_ref(uid), {"unread": firestore.Increment(-n), "v": firestore.Increment(1)}, merge=True)
    batch.commit()
    _bump_peek(uid, -n)
    _ov_cache.pop(uid, None)
    _read_ok[k] = _version(uid)


def get_conversations_overview(user_id: int) -> List[Dict[str, Any]]:
    """Liste aller Unterhaltungen: pro Gesprächspartner die letzte Nachricht, deren Zeitpunkt
    und die Anzahl ungelesener Nachrichten von ihm. Neueste Unterhaltung zuerst."""
    uid = int(user_id)
    v = _version(uid)
    cached = _ov_cache.get(uid)
    if cached and cached["v"] == v:
        return [dict(x) for x in cached["rows"]]
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
        _counter_ref(uid).set({"unread": total_unread, "v": firestore.Increment(1)}, merge=True)
        _bump_peek(uid, set_unread=total_unread)
    _ov_cache[uid] = {"v": _version(uid), "rows": [dict(x) for x in convos]}
    return convos


def unread_count(user_id: int) -> int:
    """Gesamtzahl ungelesener Nachrichten (für das Badge neben "💬 Chat" in der Sidebar)."""
    return max(0, int(fb.watched(_counter_ref(user_id)).get("unread", 0) or 0))
