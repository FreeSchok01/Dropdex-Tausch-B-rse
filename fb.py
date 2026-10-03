# -*- coding: utf-8 -*-
"""
fb.py
=====
Zentrale Firestore-Verbindung für die Dropdex-Tauschbörse. Alle Module (db.py und später
chat.py, wishlist.py, offers.py ...) holen sich den Client über get_client().

Zugangsdaten (Service-Account) werden in dieser Reihenfolge gesucht:
  1. Streamlit-Secrets:   [firebase]-Block in .streamlit/secrets.toml
  2. Umgebungsvariable    FIREBASE_CREDENTIALS_JSON  (kompletter JSON-Inhalt als Text)
  3. Datei                Pfad in GOOGLE_APPLICATION_CREDENTIALS bzw. firebase-key.json neben dieser Datei

Der Schlüssel darf NIE ins Git-Repository (siehe .gitignore).
"""

import json
import os
from typing import Any, Dict, Optional

import firebase_admin
from firebase_admin import credentials, firestore

_client = None


def _load_credentials_dict() -> Optional[Dict[str, Any]]:
    # 1) Streamlit-Secrets (nur wenn Streamlit vorhanden und der Block gesetzt ist)
    try:
        import streamlit as st
        if "firebase_json" in st.secrets:  # kompletter Inhalt der JSON-Datei als Text
            return json.loads(st.secrets["firebase_json"])
        if "firebase" in st.secrets:
            return dict(st.secrets["firebase"])
    except Exception:  # noqa: BLE001
        pass
    # 2) Umgebungsvariable mit dem JSON-Text
    raw = os.environ.get("FIREBASE_CREDENTIALS_JSON")
    if raw:
        return json.loads(raw)
    # 3) Schlüsseldatei
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or os.path.join(here, "firebase-key.json")
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def _normalize_private_key(key: str) -> str:
    """Baut den PEM-Schlüssel sauber neu zusammen. Fängt typische Fehler beim Einfügen ab:
    wörtliche "\\n" statt Zeilenumbrüchen, verlorene Zeilenumbrüche, zusätzliche Leerzeichen
    oder Anführungszeichen."""
    import re
    k = key.strip().strip('"').strip("'").replace("\\n", "\n")
    m = re.search(r"-----BEGIN PRIVATE KEY-----(.*?)-----END PRIVATE KEY-----", k, re.S)
    if not m:
        return k
    body = re.sub(r"[^A-Za-z0-9+/=]", "", m.group(1))
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return "-----BEGIN PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END PRIVATE KEY-----\n"


def _key_diagnosis(key: Any) -> str:
    """Kurze Beschreibung des Schlüssels OHNE dessen Inhalt - hilft, den Fehler einzugrenzen."""
    import re
    if not isinstance(key, str):
        return "private_key fehlt oder ist kein Text."
    m = re.search(r"-----BEGIN PRIVATE KEY-----(.*?)-----END PRIVATE KEY-----", key, re.S)
    if not m:
        return (f"Kopf/Fuß fehlen (Länge {len(key)}). Beginnt mit BEGIN: "
                f"{key.strip().startswith('-----BEGIN PRIVATE KEY-----')}, "
                f"endet mit END: {key.strip().endswith('-----END PRIVATE KEY-----')}.")
    body = re.sub(r"[^A-Za-z0-9+/=]", "", m.group(1))
    return (f"Schlüsselkörper {len(body)} Zeichen (ein gültiger 2048-Bit-Schlüssel hat meist "
            f"ca. 1624), Rest mod 4 = {len(body) % 4} (muss 0 sein).")


def get_client():
    """Gibt den (einmalig erzeugten) Firestore-Client zurück."""
    global _client
    if _client is not None:
        return _client
    if not firebase_admin._apps:
        data = _load_credentials_dict()
        if not data:
            raise RuntimeError(
                "Keine Firebase-Zugangsdaten gefunden. Lege den Service-Account-Schlüssel in "
                ".streamlit/secrets.toml unter [firebase] ab (siehe fb.py)."
            )
        if isinstance(data.get("private_key"), str):
            data["private_key"] = _normalize_private_key(data["private_key"])
        try:
            firebase_admin.initialize_app(credentials.Certificate(data))
        except ValueError as exc:
            raise RuntimeError(
                "Der Firebase-private_key ist beschädigt. Diagnose (ohne Schlüsselinhalt): "
                + _key_diagnosis(data.get("private_key"))
            ) from exc
    _client = firestore.client()
    return _client


# ---------------------------------------------------------------------------
# Hilfsmittel gegen unnötige Firestore-Lesezugriffe
# ---------------------------------------------------------------------------
# Die Haupt-App fragt Zähler (ungelesene News/Chats, Wartungsmodus) sehr oft ab (alle paar
# Sekunden pro Nutzer). Damit das nicht bei jedem Aufruf einen bezahlten Lesezugriff auslöst,
# gibt es zwei Werkzeuge:
#   watched(ref)   hält ein Dokument per Live-Listener im Speicher - abgerechnet wird nur,
#                  wenn sich das Dokument tatsächlich ändert.
#   TTLCache       kurzer Zwischenspeicher für Listen (Boards, Wunschlisten ...).

import threading
import time
from typing import Callable, List, Tuple

_watched: Dict[str, Dict[str, Any]] = {}
_watched_lock = threading.Lock()


def watched(ref) -> Dict[str, Any]:
    """Gibt den aktuellen Inhalt des Dokuments `ref` als dict zurück ({} wenn es nicht existiert).
    Beim ersten Aufruf wird ein Live-Listener angelegt (Wartezeit höchstens ~5 s), danach kommt
    die Antwort sofort aus dem Speicher."""
    key = ref.path
    with _watched_lock:
        entry = _watched.get(key)
        if entry is None:
            entry = {"data": None, "ready": threading.Event()}

            def _cb(doc_snapshot, changes, read_time, _e=entry):
                for d in doc_snapshot:
                    _e["data"] = (d.to_dict() or {}) if d.exists else {}
                _e["ready"].set()

            entry["unsub"] = ref.on_snapshot(_cb)
            _watched[key] = entry
    if entry["data"] is None:
        entry["ready"].wait(timeout=5)
    if entry["data"] is None:  # Listener noch nicht bereit -> einmalig normal lesen
        snap = ref.get()
        return (snap.to_dict() or {}) if snap.exists else {}
    return dict(entry["data"])


def watched_override(ref, data: Dict[str, Any]) -> None:
    """Setzt den im Speicher gehaltenen Wert sofort (nach einem eigenen Schreibzugriff), damit
    die nächste Abfrage nicht kurz den alten Wert sieht."""
    entry = _watched.get(ref.path)
    if entry is not None:
        entry["data"] = dict(data)


class TTLCache:
    """Minimaler Zwischenspeicher: get(key, loader) liefert den gespeicherten Wert oder ruft
    loader() auf, wenn er älter als `ttl` Sekunden ist. clear() leert alles (nach Schreibzugriffen)."""

    def __init__(self, ttl: float = 15.0) -> None:
        self.ttl = ttl
        self._data: Dict[Any, Any] = {}

    def get(self, key, loader):
        now = time.time()
        hit = self._data.get(key)
        if hit and now - hit[0] < self.ttl:
            return hit[1]
        value = loader()
        self._data[key] = (now, value)
        return value

    def clear(self) -> None:
        self._data.clear()


# ---------------------------------------------------------------------------
# Live-Abfragen (Collection/Query im Speicher, abgerechnet wird nur bei Änderungen)
# ---------------------------------------------------------------------------
# watched_query(key, make_query) hält das Ergebnis einer Collection oder Query per Listener im
# Speicher. Gelesen wird einmal beim Start (N Dokumente), danach kostet nur noch jedes
# GEÄNDERTE Dokument einen Lesezugriff - das Abfragen selbst ist kostenlos. Daten sind dadurch
# immer aktuell (Verzögerung meist < 1 s), egal wie oft die App neu rendert.
#
# wq_patch() schreibt eigene Änderungen sofort in den Speicher (damit man direkt nach einem
# Schreibzugriff die eigene Änderung sieht); der Listener bestätigt sie kurz danach.

_wq: Dict[str, Dict[str, Any]] = {}
_wq_lock = threading.Lock()
_WQ_MAX_AGE = 6 * 3600  # Listener nach 6 h sicherheitshalber neu aufbauen


def watched_query(key: str, make_query: Callable[[], Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Liefert [(doc_id, daten), ...] der Collection/Query. Die dicts NICHT verändern - kopieren."""
    now = time.time()
    with _wq_lock:
        e = _wq.get(key)
        if e is not None and now - e["born"] > _WQ_MAX_AGE:
            try:
                e["unsub"].unsubscribe()
            except Exception:  # noqa: BLE001
                pass
            e = None
        if e is None:
            e = {"docs": None, "ready": threading.Event(), "born": now}

            def _cb(docs, changes, read_time, _e=e):
                _e["docs"] = [(d.id, d.to_dict() or {}) for d in docs]
                _e["ready"].set()

            e["unsub"] = make_query().on_snapshot(_cb)
            _wq[key] = e
    if e["docs"] is None:
        e["ready"].wait(timeout=10)
    if e["docs"] is None:  # Listener noch nicht bereit -> einmalig normal lesen
        return [(d.id, d.to_dict() or {}) for d in make_query().stream()]
    return list(e["docs"])


def wq_patch(key: str, doc_id: str, data: Optional[Dict[str, Any]] = None, merge: bool = False) -> None:
    """Wendet eine eigene Änderung sofort auf den Speicher an (data=None löscht das Dokument)."""
    e = _wq.get(key)
    if e is None or e["docs"] is None:
        return
    docs = e["docs"]
    old = next((d for i, d in docs if i == doc_id), None)
    if data is None:
        e["docs"] = [(i, d) for i, d in docs if i != doc_id]
    elif old is None:
        e["docs"] = [(doc_id, dict(data))] + docs
    else:
        new = {**old, **data} if merge else dict(data)
        e["docs"] = [(i, new) if i == doc_id else (i, d) for i, d in docs]


def watched_peek(ref) -> Optional[Dict[str, Any]]:
    """Wie watched(), legt aber KEINEN neuen Listener an: None, wenn das Dokument nicht überwacht wird."""
    entry = _watched.get(ref.path)
    if entry is None or entry["data"] is None:
        return None
    return dict(entry["data"])
