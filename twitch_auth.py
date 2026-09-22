# -*- coding: utf-8 -*-
"""
twitch_auth.py
===============
Twitch OAuth2 "Authorization Code"-Flow für Streamlit-Apps.

Ablauf:
  1. get_login_url()               -> Link, der den Nutzer zu Twitch schickt
  2. Twitch leitet zurück zu REDIRECT_URI?code=...&state=...
  3. verify_state(state)           -> CSRF-Check
  4. exchange_code_for_token(code) -> Access Token
  5. get_twitch_user(access_token) -> {twitch_id, twitch_username, profile_image_url}

Twitch-App registrieren unter: https://dev.twitch.tv/console/apps
  - OAuth Redirect URL dort MUSS exakt mit REDIRECT_URI unten übereinstimmen
    (bei lokalem Testen z.B. "http://localhost:8501").
"""

import os
import time
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests

# ---------------------------------------------------------------------------
# 🔑 HIER DEINE TWITCH APP-ZUGANGSDATEN EINTRAGEN
# ---------------------------------------------------------------------------
# Am liebsten NICHT hart im Code eintragen, sondern als Umgebungsvariable
# setzen (z.B. in .streamlit/secrets.toml oder per `export`):
#
#   TWITCH_CLIENT_ID=deine_client_id
#   TWITCH_CLIENT_SECRET=dein_client_secret
#   TWITCH_REDIRECT_URI=https://deine-app-url.streamlit.app
#
# Falls du es trotzdem direkt im Code eintragen willst, ersetze einfach die
# Platzhalter-Strings in den os.environ.get(...)-Aufrufen unten.
# ---------------------------------------------------------------------------
CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID", "DEINE_CLIENT_ID_HIER")
CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET", "DEIN_CLIENT_SECRET_HIER")
REDIRECT_URI = os.environ.get("TWITCH_REDIRECT_URI", "http://localhost:8501")

AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
USERS_URL = "https://api.twitch.tv/helix/users"

SCOPES = ["user:read:email"]

# ---------------------------------------------------------------------------
# Einfacher In-Memory-Speicher für den CSRF-"state"-Parameter.
# Funktioniert, solange die App als EIN Prozess läuft (Standard bei
# Streamlit Community Cloud / einem einzelnen Server). Bei horizontal
# skalierten Deployments (mehrere Prozesse hinter einem Load Balancer)
# müsste der state stattdessen in einer DB/Redis liegen, da sonst der
# Callback ggf. auf einer anderen Instanz landet.
# ---------------------------------------------------------------------------
_PENDING_STATES: Dict[str, float] = {}
_STATE_TTL_SECONDS = 600


def _cleanup_states() -> None:
    now = time.time()
    for s in [s for s, ts in _PENDING_STATES.items() if now - ts > _STATE_TTL_SECONDS]:
        _PENDING_STATES.pop(s, None)


def get_login_url() -> str:
    """Erzeugt die Twitch-Login-URL (inkl. CSRF-Schutz per state-Parameter)."""
    _cleanup_states()
    state = secrets.token_urlsafe(24)
    _PENDING_STATES[state] = time.time()
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def verify_state(state: Optional[str]) -> bool:
    """Prüft den beim Redirect zurückgegebenen state-Parameter und verbraucht ihn."""
    if not state:
        return False
    _cleanup_states()
    return _PENDING_STATES.pop(state, None) is not None


def exchange_code_for_token(code: str) -> Optional[str]:
    """Tauscht den Authorization Code gegen ein Access Token."""
    try:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
            },
            timeout=10,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    return resp.json().get("access_token")


def get_twitch_user(access_token: str) -> Optional[Dict[str, Any]]:
    """Holt twitch_id, twitch_username und Profilbild-URL des eingeloggten Nutzers."""
    headers = {"Authorization": f"Bearer {access_token}", "Client-Id": CLIENT_ID}
    try:
        resp = requests.get(USERS_URL, headers=headers, timeout=10)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    data = resp.json().get("data") or []
    if not data:
        return None
    u = data[0]
    return {
        "twitch_id": u.get("id"),
        "twitch_username": u.get("display_name") or u.get("login") or "Unbekannt",
        "profile_image_url": u.get("profile_image_url", ""),
    }
