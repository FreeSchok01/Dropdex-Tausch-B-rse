# -*- coding: utf-8 -*-
"""
maintenance.py  (Firestore-Version)
===================================
Wartungsmodus: der Admin kann die Seite für alle anderen Nutzer sperren, während er selbst
weiterhin vollen Zugriff behält.

Firestore: Dokument settings/maintenance -> {on: bool}. Die Abfrage läuft über einen
Live-Listener (fb.watched), kostet also keinen Lesezugriff pro Seitenaufruf.
"""

import fb


def _ref():
    return fb.get_client().collection("settings").document("maintenance")


def init_db() -> None:
    """Bleibt aus Kompatibilitätsgründen erhalten - Firestore braucht kein Schema."""
    fb.get_client()


def is_maintenance_mode() -> bool:
    """True, wenn der Wartungsmodus gerade aktiv ist."""
    return bool(fb.watched(_ref()).get("on", False))


def set_maintenance_mode(on: bool) -> None:
    """Schaltet den Wartungsmodus an oder aus (z.B. per Toggle in der Sidebar)."""
    _ref().set({"on": bool(on)})
    fb.watched_override(_ref(), {"on": bool(on)})
