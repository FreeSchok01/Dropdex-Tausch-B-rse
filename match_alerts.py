# -*- coding: utf-8 -*-
"""
match_alerts.py
===============
Meldet neue Perfect-Matches (1:1-Tausch möglich) per News-Eintrag und optional per Discord.

Ablauf: offers.py und wishlist.py rufen check_user(user_id) auf, sobald ein Wunsch oder
Angebot angelegt oder entfernt wird (auch die automatischen Angebote von trade_watch.py).
Gemeldet wird nur, wenn eine Karte NEU im Match auftaucht.

Firestore:
  match_alerts/{lo}_{hi}   participants: [lo, hi]
                           lo_gets: [card_id]  Karten, die der kleinere User bekommen würde
                           hi_gets: [card_id]  Karten, die der größere User bekommen würde
                           (= bereits gemeldeter Stand; leer, sobald das Match verschwindet)

Optional Discord: Webhook-URL in der Umgebungsvariable oder den Streamlit-Secrets unter
DISCORD_MATCH_WEBHOOK. Ohne diese Einstellung wird nur der News-Eintrag angelegt.
"""

import logging
import os
from typing import Any, Dict, List

from google.cloud import firestore

import db
import fb
import notifications
import offers

log = logging.getLogger(__name__)

MAX_NAMES = 3  # so viele Kartennamen werden pro Seite in der Nachricht genannt


def _col():
    return fb.get_client().collection("match_alerts")


def _pair_id(a: int, b: int) -> str:
    lo, hi = sorted((int(a), int(b)))
    return f"{lo}_{hi}"


def init_db() -> None:
    """Bleibt aus Kompatibilitätsgründen erhalten - Firestore braucht kein Schema."""
    fb.get_client()


@firestore.transactional
def _update_pair_txn(txn, ref, lo: int, hi: int, lo_gets: List[str], hi_gets: List[str]) -> bool:
    """Speichert den aktuellen Stand und gibt True zurück, wenn eine Karte NEU dazugekommen
    ist. Die Transaktion verhindert Doppelmeldungen, falls beide Nutzer gleichzeitig prüfen."""
    snap = ref.get(transaction=txn)
    old = snap.to_dict() if snap.exists else {}
    is_new = bool((set(lo_gets) - set(old.get("lo_gets", [])))
                  or (set(hi_gets) - set(old.get("hi_gets", []))))
    txn.set(ref, {"participants": [lo, hi],
                  "lo_gets": sorted(lo_gets), "hi_gets": sorted(hi_gets)})
    return is_new


def _names(rows: List[Dict[str, Any]]) -> str:
    seen: Dict[str, str] = {}
    for r in rows:
        seen.setdefault(str(r["card_id"]), r.get("card_name", "Karte"))
    names = list(seen.values())
    text = ", ".join(names[:MAX_NAMES])
    if len(names) > MAX_NAMES:
        text += f" (+{len(names) - MAX_NAMES} weitere)"
    return text


def _message(partner_name: str, gets: str, gives: str) -> str:
    return f"🤝 Tausch-Match mit {partner_name}: Du bekommst {gets} ↔ du gibst {gives}."


def _discord(text: str) -> None:
    url = os.environ.get("DISCORD_MATCH_WEBHOOK")
    if not url:
        try:
            import streamlit as st
            url = st.secrets.get("DISCORD_MATCH_WEBHOOK")
        except Exception:  # noqa: BLE001
            url = None
    if not url:
        return
    try:
        import requests
        requests.post(url, json={"content": text}, timeout=5)
    except Exception:  # noqa: BLE001
        log.warning("Discord-Webhook für Match-Meldung fehlgeschlagen", exc_info=True)


def _check(user_id: int) -> None:
    uid = int(user_id)
    me = db.get_user_by_id(uid)
    if not me or me["is_banned"] or not me["is_approved"]:
        return
    active = set()
    fs = fb.get_client()

    for m in offers.get_perfect_matches(uid):
        pid = int(m["partner_id"])
        active.add(pid)
        partner = db.get_user_by_id(pid)
        if not partner or partner["is_banned"] or not partner["is_approved"]:
            continue

        recv = sorted({str(o["card_id"]) for o in m["from_them"]})   # das bekomme ich
        give = sorted({str(w["card_id"]) for w in m["from_me"]})     # das gebe ich
        lo, hi = sorted((uid, pid))
        lo_gets, hi_gets = (recv, give) if uid == lo else (give, recv)

        ref = _col().document(_pair_id(uid, pid))
        if not _update_pair_txn(fs.transaction(), ref, lo, hi, lo_gets, hi_gets):
            continue

        gets_txt, gives_txt = _names(m["from_them"]), _names(m["from_me"])
        notifications.add_notification(uid, _message(partner["twitch_username"], gets_txt, gives_txt))
        notifications.add_notification(pid, _message(me["twitch_username"], gives_txt, gets_txt))
        _discord(f"🤝 **{me['twitch_username']}** und **{partner['twitch_username']}** können tauschen: "
                 f"{gets_txt} ↔ {gives_txt}")

    # Verschwundene Matches zurücksetzen, damit ein erneutes Auftauchen wieder gemeldet wird.
    for d in _col().where("participants", "array_contains", uid).stream():
        x = d.to_dict() or {}
        other = next((int(p) for p in x.get("participants", []) if int(p) != uid), None)
        if other is not None and other not in active and (x.get("lo_gets") or x.get("hi_gets")):
            d.reference.set({"lo_gets": [], "hi_gets": []}, merge=True)


def check_user(user_id: int) -> None:
    """Prüft die Perfect-Matches des Nutzers und meldet neue. Fehler hier dürfen nie das
    Speichern eines Wunsches oder Angebots kaputtmachen."""
    try:
        _check(user_id)
    except Exception:  # noqa: BLE001
        log.exception("match_alerts.check_user fehlgeschlagen (user %s)", user_id)
