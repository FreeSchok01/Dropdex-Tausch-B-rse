# -*- coding: utf-8 -*-
"""
db.py  (Firestore-Version)
==========================
Nutzer-Datenbank für die Dropdex-Tauschbörse - gleiche Funktionen wie die frühere
SQLite-Version, speichert aber in Firebase Firestore (siehe fb.py).

Firestore-Struktur:
  users/{id}                    Account (id bleibt eine Zahl, damit chat.py, offers.py usw.
                                unverändert weiterarbeiten; Dokument-ID = str(id))
    users/{id}/favorites/{hash} Favoriten-Profile
    users/{id}/snapshots/{auto} Fortschrittsverlauf
  twitch_index/{twitch_id}      -> {id}   schneller Lookup Twitch-ID -> interne ID
  sessions/{token}              "Eingeloggt bleiben" (expires_at, siehe delete_expired_sessions)
  meta/counters                 {users: <letzte vergebene ID>}

Alle Zeitstempel bleiben UTC-ISO-Strings (is_user_online() & Co. in der Haupt-App erwarten das).
"""

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from google.cloud import firestore

import fb

ONLINE_THRESHOLD_SECONDS = 300
SESSION_LIFETIME_DAYS = 30

# last_seen höchstens alle X Sekunden schreiben (spart Firestore-Schreibzugriffe, da die
# App touch_last_seen() bei jedem Rerun aufruft; ONLINE_THRESHOLD ist deutlich größer).
_TOUCH_MIN_INTERVAL = 120
_last_touch: Dict[int, float] = {}

# Alle Accounts liegen per Live-Listener im Speicher (fb.watched_query): get_user_by_id(),
# get_all_users(), Suche, Bestenliste usw. kosten dadurch KEINE Lesezugriffe mehr, sind aber
# immer aktuell. Eigene Schreibzugriffe werden sofort per fb.wq_patch() eingetragen.
_USERS_KEY = "users"

# Eigene Daten (nur der Nutzer selbst ändert sie) werden gecacht und bei eigenen Änderungen geleert.
_fav_cache: Dict[int, List[Dict[str, Any]]] = {}
_hist_cache: Dict[Any, List[Dict[str, Any]]] = {}
_last_cleanup = 0.0


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _fs():
    return fb.get_client()


def _users():
    return _fs().collection("users")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat(timespec="seconds")


def _doc_to_user(d: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if d is None:
        return None
    u = dict(d)
    for key in ("is_admin", "is_supporter", "is_approved", "is_banned"):
        u[key] = bool(u.get(key, False))
    u["show_on_leaderboard"] = bool(u.get("show_on_leaderboard", True))
    u["own_profile_url"] = u.get("own_profile_url") or ""
    u["own_profile_name"] = u.get("own_profile_name") or ""
    u["profile_image_url"] = u.get("profile_image_url") or ""
    u.setdefault("last_seen", None)
    u.pop("latest_snapshot", None)
    return u


def init_db() -> None:
    """Firestore braucht kein Schema. Die Funktion bleibt, damit bestehende Aufrufer
    (auth_ui.render_login_gate() usw.) unverändert funktionieren."""
    fb.get_client()


# ---------------------------------------------------------------------------
# Nutzer
# ---------------------------------------------------------------------------

def _docs() -> List[Any]:
    return fb.watched_query(_USERS_KEY, _users)


def _raw(user_id: Any) -> Optional[Dict[str, Any]]:
    sid = str(int(user_id))
    for doc_id, d in _docs():
        if doc_id == sid:
            return d
    return None


def get_user_by_id(user_id: Any) -> Optional[Dict[str, Any]]:
    try:
        d = _raw(user_id)
    except (TypeError, ValueError):
        return None
    return _doc_to_user(d) if d is not None else None


def get_user_by_twitch_id(twitch_id: Any) -> Optional[Dict[str, Any]]:
    if twitch_id is None:
        return None
    tid = str(twitch_id)
    for _doc_id, d in _docs():
        if str(d.get("twitch_id")) == tid:
            return _doc_to_user(d)
    # Nicht im Speicher (z.B. ganz frisch angelegt) -> einmalig über den Index nachschlagen.
    idx = _fs().collection("twitch_index").document(tid).get()
    if not idx.exists:
        return None
    snap = _users().document(str(idx.to_dict().get("id"))).get()
    if not snap.exists:
        return None
    fb.wq_patch(_USERS_KEY, snap.id, snap.to_dict())
    return _doc_to_user(snap.to_dict())


@firestore.transactional
def _create_user_txn(txn, idx_ref, counter_ref, users_col, data):
    idx = idx_ref.get(transaction=txn)
    if idx.exists:
        return idx.to_dict()["id"], False
    c = counter_ref.get(transaction=txn)
    nxt = (c.to_dict().get("users", 0) if c.exists else 0) + 1
    txn.set(counter_ref, {"users": nxt}, merge=True)
    txn.set(users_col.document(str(nxt)), {**data, "id": nxt})
    txn.set(idx_ref, {"id": nxt})
    return nxt, True


def get_or_create_user(twitch_id: str, twitch_username: str,
                       profile_image_url: str = "") -> Dict[str, Any]:
    """Legt den Account beim ersten Login an (noch NICHT freigegeben) oder aktualisiert
    bei bestehenden Accounts Anzeigename und Profilbild von Twitch."""
    twitch_id = str(twitch_id)
    fs = _fs()
    data = {
        "twitch_id": twitch_id,
        "twitch_username": twitch_username,
        "profile_image_url": profile_image_url or "",
        "is_admin": False, "is_supporter": False, "is_approved": False, "is_banned": False,
        "own_profile_url": "", "own_profile_name": "",
        "show_on_leaderboard": True, "last_seen": None,
        "created_at": _utc_now_iso(),
    }
    uid, created = _create_user_txn(
        fs.transaction(), fs.collection("twitch_index").document(twitch_id),
        fs.collection("meta").document("counters"), _users(), data,
    )
    if not created:
        _users().document(str(uid)).update(
            {"twitch_username": twitch_username, "profile_image_url": profile_image_url or ""}
        )
    snap = _users().document(str(uid)).get()   # 1 Lesezugriff pro Login
    fb.wq_patch(_USERS_KEY, str(uid), snap.to_dict())
    return _doc_to_user(snap.to_dict())  # type: ignore[return-value]


def get_all_users() -> List[Dict[str, Any]]:
    """Alle Accounts (Admins zuerst, dann Supporter, dann alphabetisch). Aus dem Speicher."""
    users = [_doc_to_user(d) for _id, d in _docs()]
    users.sort(key=lambda u: (not u["is_admin"], not u["is_supporter"],
                              (u.get("twitch_username") or "").lower()))
    return users  # type: ignore[return-value]


def search_users_by_username(query: str, exclude_user_id: Optional[int] = None,
                             limit: int = 20) -> List[Dict[str, Any]]:
    """Teilstring-Suche (ohne Groß-/Kleinschreibung) über Twitch-Namen - nur freigegebene,
    nicht gesperrte Accounts."""
    q = (query or "").strip().lower()
    if not q:
        return []
    out = [u for u in get_all_users()
           if u["is_approved"] and not u["is_banned"]
           and q in (u.get("twitch_username") or "").lower()
           and (exclude_user_id is None or u["id"] != int(exclude_user_id))]
    out.sort(key=lambda u: (u.get("twitch_username") or "").lower())
    return out[:limit]


def _update(user_id: int, fields: Dict[str, Any]) -> None:
    uid = int(user_id)
    _users().document(str(uid)).update(fields)
    fb.wq_patch(_USERS_KEY, str(uid), fields, merge=True)  # Rollen/Sperren sofort sichtbar


def set_approved(user_id: int, approved: bool) -> None:
    _update(user_id, {"is_approved": bool(approved)})


def set_banned(user_id: int, banned: bool) -> None:
    """Sperren entzieht zugleich die Freigabe. Entsperren stellt sie NICHT automatisch
    wieder her (dafür gibt es set_approved)."""
    if banned:
        _update(user_id, {"is_banned": True, "is_approved": False})
    else:
        _update(user_id, {"is_banned": False})


def set_admin(user_id: int, is_admin: bool) -> None:
    """Beim Vergeben wird der Account automatisch freigegeben (und entsperrt)."""
    if is_admin:
        _update(user_id, {"is_admin": True, "is_approved": True, "is_banned": False})
    else:
        _update(user_id, {"is_admin": False})


def set_supporter(user_id: int, is_supporter: bool) -> None:
    """Beim Vergeben wird der Account automatisch freigegeben (und entsperrt)."""
    if is_supporter:
        _update(user_id, {"is_supporter": True, "is_approved": True, "is_banned": False})
    else:
        _update(user_id, {"is_supporter": False})


def revoke_all_approvals_except_admins() -> int:
    """Entzieht allen Nicht-Admins die Freigabe. Gibt die Zahl der betroffenen Accounts zurück."""
    batch = _fs().batch()
    ids = []
    for doc_id, d in _docs():
        if d.get("is_approved") and not d.get("is_admin"):
            batch.update(_users().document(doc_id), {"is_approved": False})
            ids.append(doc_id)
    if ids:
        batch.commit()
        for doc_id in ids:
            fb.wq_patch(_USERS_KEY, doc_id, {"is_approved": False}, merge=True)
    return len(ids)


def set_own_profile(user_id: int, profile_url: str, profile_name: str) -> None:
    _update(user_id, {"own_profile_url": profile_url or "", "own_profile_name": profile_name or ""})


def set_leaderboard_visible(user_id: int, visible: bool) -> None:
    _update(user_id, {"show_on_leaderboard": bool(visible)})


# ---------------------------------------------------------------------------
# Online-Status
# ---------------------------------------------------------------------------

def touch_last_seen(user_id: int) -> None:
    uid = int(user_id)
    now = time.time()
    if now - _last_touch.get(uid, 0.0) < _TOUCH_MIN_INTERVAL:
        return
    _last_touch[uid] = now
    _update(uid, {"last_seen": _utc_now_iso()})


def is_user_online(last_seen: Optional[str]) -> bool:
    """True, wenn der letzte Seitenaufruf höchstens ONLINE_THRESHOLD_SECONDS zurückliegt."""
    if not last_seen:
        return False
    try:
        seen = datetime.fromisoformat(last_seen)
    except ValueError:
        return False
    now = _utc_now() if seen.tzinfo else datetime.utcnow()
    return (now - seen).total_seconds() <= ONLINE_THRESHOLD_SECONDS


# ---------------------------------------------------------------------------
# Sessions ("Eingeloggt bleiben")
# ---------------------------------------------------------------------------

def create_session(twitch_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = _utc_now()
    _fs().collection("sessions").document(token).set({
        "twitch_id": str(twitch_id),
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=SESSION_LIFETIME_DAYS)).isoformat(timespec="seconds"),
    })
    return token


def get_user_by_session_token(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    snap = _fs().collection("sessions").document(token).get()
    if not snap.exists:
        return None
    d = snap.to_dict()
    try:
        if datetime.fromisoformat(d["expires_at"]) < _utc_now():
            delete_session(token)
            return None
    except (ValueError, KeyError):
        return None
    return get_user_by_twitch_id(d.get("twitch_id"))


def delete_session(token: Optional[str]) -> None:
    if token:
        _fs().collection("sessions").document(token).delete()


def delete_expired_sessions() -> None:
    """Räumt abgelaufene Sessions auf - höchstens 1x pro Stunde (wird bei jedem Rerun aufgerufen)."""
    global _last_cleanup
    if time.time() - _last_cleanup < 3600:
        return
    _last_cleanup = time.time()
    batch = _fs().batch()
    n = 0
    for s in _fs().collection("sessions").where("expires_at", "<", _utc_now_iso()).limit(400).stream():
        batch.delete(s.reference)
        n += 1
    if n:
        batch.commit()


# ---------------------------------------------------------------------------
# Favoriten
# ---------------------------------------------------------------------------

def _fav_col(user_id: int):
    return _users().document(str(int(user_id))).collection("favorites")


def _fav_id(profile_url: str) -> str:
    return hashlib.sha1(profile_url.encode("utf-8")).hexdigest()


def get_favorites(user_id: int) -> List[Dict[str, Any]]:
    uid = int(user_id)
    rows = _fav_cache.get(uid)
    if rows is None:
        rows = [s_.to_dict() for s_ in _fav_col(uid).stream()]
        for r in rows:
            r["user_id"] = uid
        rows.sort(key=lambda r: (r.get("profile_name") or "").lower())
        _fav_cache[uid] = rows
    return [dict(r) for r in rows]


def is_favorite(user_id: int, profile_url: str) -> bool:
    return any(r.get("profile_url") == profile_url for r in get_favorites(user_id))


def add_favorite(user_id: int, profile_url: str, profile_name: str) -> None:
    ref = _fav_col(user_id).document(_fav_id(profile_url))
    if is_favorite(user_id, profile_url):
        ref.update({"profile_name": profile_name})
    else:
        ref.set({"profile_url": profile_url, "profile_name": profile_name,
                 "created_at": _utc_now_iso()})
    _fav_cache.pop(int(user_id), None)


def remove_favorite(user_id: int, profile_url: str) -> None:
    _fav_col(user_id).document(_fav_id(profile_url)).delete()
    _fav_cache.pop(int(user_id), None)


# ---------------------------------------------------------------------------
# Fortschritt & Bestenliste
# ---------------------------------------------------------------------------

_SNAP_FIELDS = ("distinct_owned", "distinct_total", "total_copies", "missing_count")


def add_progress_snapshot(user_id: int, distinct_owned: int, distinct_total: int,
                          total_copies: int, missing_count: int) -> None:
    """Speichert einen Fortschritts-Schnappschuss; identischer Stand wie der letzte wird
    übersprungen. Der neueste Stand steht zusätzlich im User-Dokument (für die Bestenliste)."""
    uid = int(user_id)
    new = dict(zip(_SNAP_FIELDS, (int(distinct_owned), int(distinct_total),
                                  int(total_copies), int(missing_count))))
    user_ref = _users().document(str(uid))
    last = (_raw(uid) or {}).get("latest_snapshot")   # aus dem Speicher, kein Lesezugriff
    if last and all(last.get(k) == new[k] for k in _SNAP_FIELDS):
        return
    taken_at = _utc_now_iso()
    user_ref.collection("snapshots").add({**new, "taken_at": taken_at})
    _update(uid, {"latest_snapshot": {**new, "taken_at": taken_at}})
    for k in [k for k in _hist_cache if k[0] == uid]:
        _hist_cache.pop(k, None)


def get_progress_history(user_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    """Die letzten `limit` Schnappschüsse, ältester zuerst (für das Verlaufsdiagramm)."""
    key = (int(user_id), int(limit))
    rows = _hist_cache.get(key)
    if rows is None:
        docs = (_users().document(str(int(user_id))).collection("snapshots")
                .order_by("taken_at", direction=firestore.Query.DESCENDING).limit(limit).stream())
        rows = [dict(d.to_dict(), user_id=int(user_id)) for d in docs]
        rows.reverse()
        _hist_cache[key] = rows
    return [dict(r) for r in rows]


def get_leaderboard(limit: int = 50) -> List[Dict[str, Any]]:
    """Neuester Schnappschuss je sichtbarem, nicht gesperrtem Account, bester zuerst (aus dem Speicher)."""
    out: List[Dict[str, Any]] = []
    for _id, d in _docs():
        snap = d.get("latest_snapshot")
        if not d.get("show_on_leaderboard", True) or d.get("is_banned") or not snap:
            continue
        out.append({"user_id": d["id"], "twitch_username": d.get("twitch_username", ""), **snap})
    out.sort(key=lambda r: (-r["distinct_owned"], -r["total_copies"], r["twitch_username"].lower()))
    return out[:limit]
