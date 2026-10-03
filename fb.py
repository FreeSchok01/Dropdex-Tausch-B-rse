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
                "Der Firebase-private_key ist beschädigt (vermutlich beim Einfügen in die Secrets "
                "abgeschnitten oder verändert). Erzeuge einen neuen Schlüssel und trage ihn "
                "mit dem Umwandlungs-Befehl aus der Anleitung ein."
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
