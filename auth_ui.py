# -*- coding: utf-8 -*-
"""
auth_ui.py
==========
Verbindet db.py + twitch_auth.py zu fertigen Streamlit-Bausteinen:

  - render_login_gate()     -> in main() ganz oben aufrufen.
                                Gibt True zurück, wenn der Nutzer eingeloggt
                                UND nicht gesperrt ist. Bei False: einfach
                                `return` in main(), der Rest der App läuft
                                dann nicht.
  - render_admin_dashboard() -> irgendwo in main() aufrufen (z.B. direkt nach
                                dem Login-Gate). Rendert sich nur, wenn der
                                eingeloggte Nutzer is_admin == True hat.
"""

from typing import Set

import pandas as pd
import streamlit as st

import db
import twitch_auth

# ---------------------------------------------------------------------------
# Twitch-IDs (nicht Usernamen!), die beim ersten Login automatisch
# is_admin = True bekommen. So kommst du selbst initial ins Admin-Dashboard,
# ohne direkt in der SQLite-Datei herumzueditieren.
# Deine Twitch-ID findest du z.B. über https://streamscharts.com/tools/convert-username
# ---------------------------------------------------------------------------
BOOTSTRAP_ADMIN_TWITCH_IDS: Set[str] = {
     "171478372",
}


def _handle_oauth_callback() -> None:
    """Wird bei jedem Rerun aufgerufen; reagiert nur, wenn Twitch uns per
    Redirect ?code=...&state=... zurückgeschickt hat."""
    params = st.query_params
    code = params.get("code")
    state = params.get("state")
    if not code:
        return

    if not twitch_auth.verify_state(state):
        st.error("⚠️ Login fehlgeschlagen (ungültiger oder abgelaufener Login-Versuch). Bitte erneut versuchen.")
        st.query_params.clear()
        return

    access_token = twitch_auth.exchange_code_for_token(code)
    if not access_token:
        st.error("⚠️ Login bei Twitch fehlgeschlagen (Token konnte nicht abgerufen werden).")
        st.query_params.clear()
        return

    twitch_user = twitch_auth.get_twitch_user(access_token)
    if not twitch_user:
        st.error("⚠️ Twitch-Nutzerdaten konnten nicht abgerufen werden.")
        st.query_params.clear()
        return

    user = db.get_or_create_user(**twitch_user)
    if user["twitch_id"] in BOOTSTRAP_ADMIN_TWITCH_IDS and not user["is_admin"]:
        db.set_admin(user["id"], True)
        user = db.get_user_by_twitch_id(user["twitch_id"])

    st.session_state["auth_user"] = user
    st.query_params.clear()
    st.rerun()


def render_login_gate() -> bool:
    """Login-Button / Bann-Meldung in der Sidebar.

    Rückgabewert:
        True  -> Nutzer ist eingeloggt und NICHT gesperrt -> App darf rendern
        False -> Nutzer ist ausgeloggt ODER gesperrt -> main() sollte returnen
    """
    db.init_db()
    _handle_oauth_callback()

    user = st.session_state.get("auth_user")

    # Status frisch aus der DB nachladen (ein Admin könnte ihn inzwischen
    # geändert haben, z.B. gerade eben gesperrt).
    if user:
        fresh = db.get_user_by_twitch_id(user["twitch_id"])
        if fresh:
            user = fresh
            st.session_state["auth_user"] = user
        else:
            user = None
            st.session_state.pop("auth_user", None)

    with st.sidebar:
        st.markdown("### 👤 Account")
        if user:
            col_img, col_name = st.columns([1, 3])
            with col_img:
                if user.get("profile_image_url"):
                    st.image(user["profile_image_url"], width=48)
            with col_name:
                st.markdown(f"**{user['twitch_username']}**")
                if user["is_admin"]:
                    st.caption("🛡️ Admin")
            if st.button("Ausloggen", use_container_width=True):
                st.session_state.pop("auth_user", None)
                st.rerun()
        else:
            login_url = twitch_auth.get_login_url()
            st.link_button("🟣 Mit Twitch anmelden", login_url, type="primary", use_container_width=True)
            st.caption("Login erforderlich, um die Tauschbörse zu nutzen.")
        st.divider()

    if not user:
        st.info("👋 Bitte melde dich links in der Seitenleiste mit Twitch an, um die Tauschbörse zu nutzen.")
        return False

    if user["is_banned"]:
        st.error("🚫 Dein Account ist für dieses Tool gesperrt.")
        return False

    return True


def render_admin_dashboard() -> None:
    """Admin-Dashboard: Nutzerliste mit Sperren/Entsperren + Admin-Vergabe.
    Rendert sich nur, wenn der eingeloggte Nutzer is_admin == True hat."""
    user = st.session_state.get("auth_user")
    if not user or not user.get("is_admin"):
        return

    with st.expander("🛠️ Admin-Dashboard: Nutzerverwaltung", expanded=False):
        users = db.get_all_users()
        if not users:
            st.caption("Noch keine Nutzer registriert.")
            return

        st.caption(f"{len(users)} registrierte Nutzer – Häkchen setzen und speichern:")

        df = pd.DataFrame(users)[
            ["id", "twitch_username", "twitch_id", "last_login", "is_admin", "is_banned"]
        ].rename(columns={
            "id": "ID",
            "twitch_username": "Twitch-Name",
            "twitch_id": "Twitch-ID",
            "last_login": "Letzter Login",
            "is_admin": "Admin",
            "is_banned": "Gesperrt",
        })
        df["Admin"] = df["Admin"].astype(bool)
        df["Gesperrt"] = df["Gesperrt"].astype(bool)

        edited = st.data_editor(
            df,
            column_config={
                "ID": st.column_config.NumberColumn(disabled=True),
                "Twitch-Name": st.column_config.TextColumn(disabled=True),
                "Twitch-ID": st.column_config.TextColumn(disabled=True),
                "Letzter Login": st.column_config.TextColumn(disabled=True),
                "Admin": st.column_config.CheckboxColumn(help="Adminrechte vergeben/entziehen"),
                "Gesperrt": st.column_config.CheckboxColumn(help="Nutzer sperren/entsperren"),
            },
            hide_index=True,
            use_container_width=True,
            key="admin_user_editor",
        )

        if st.button("💾 Änderungen speichern", key="admin_save_users"):
            by_id = {u["id"]: u for u in users}
            changed = 0
            for _, row in edited.iterrows():
                uid = int(row["ID"])
                orig = by_id.get(uid)
                if orig is None:
                    continue
                if bool(row["Admin"]) != bool(orig["is_admin"]):
                    db.set_admin(uid, bool(row["Admin"]))
                    changed += 1
                if bool(row["Gesperrt"]) != bool(orig["is_banned"]):
                    db.set_banned(uid, bool(row["Gesperrt"]))
                    changed += 1
            if changed:
                st.success(f"{changed} Änderung(en) gespeichert.")
                st.rerun()
            else:
                st.info("Keine Änderungen erkannt.")
