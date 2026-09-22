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
        db.set_admin(user["id"], True)  # setzt intern auch is_approved = True
        user = db.get_user_by_twitch_id(user["twitch_id"])

    st.session_state["auth_user"] = user

    # "Eingeloggt bleiben": Session-Token erzeugen und in der URL mitführen, statt sie
    # zu leeren. Bei einem Reload (F5) schickt der Browser genau diese URL erneut mit,
    # wir erkennen das Token unten in _restore_session_from_url() und loggen automatisch
    # wieder ein - ganz ohne erneuten Twitch-Redirect.
    session_token = db.create_session(user["twitch_id"])
    st.query_params.clear()
    st.query_params["session"] = session_token
    st.session_state["session_token"] = session_token
    st.rerun()


def _restore_session_from_url() -> None:
    """Loggt automatisch ein, wenn die URL noch ein gültiges ?session=... Token trägt
    (z.B. nach einem Reload/F5) und noch kein Nutzer im session_state steckt."""
    if st.session_state.get("auth_user"):
        return
    token = st.query_params.get("session")
    if not token:
        return
    user = db.get_user_by_session_token(token)
    if user:
        st.session_state["auth_user"] = user
        st.session_state["session_token"] = token
    else:
        # Token abgelaufen/ungültig -> aus der URL entfernen
        st.query_params.clear()


def render_login_gate() -> bool:
    """Login-Button / Bann-Meldung in der Sidebar.

    Rückgabewert:
        True  -> Nutzer ist eingeloggt und NICHT gesperrt -> App darf rendern
        False -> Nutzer ist ausgeloggt ODER gesperrt -> main() sollte returnen
    """
    db.init_db()
    db.delete_expired_sessions()
    _handle_oauth_callback()
    _restore_session_from_url()

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
                elif user["is_supporter"]:
                    st.caption("🧡 Supporter")
            if st.button("Ausloggen", use_container_width=True):
                token = st.session_state.get("session_token")
                if token:
                    db.delete_session(token)
                st.session_state.pop("auth_user", None)
                st.session_state.pop("session_token", None)
                st.query_params.clear()
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

    if not user["is_approved"]:
        st.warning(
            "⏳ Dein Account wartet noch auf Freigabe durch einen Admin oder Supporter. "
            "Schau gleich nochmal vorbei – sobald du freigegeben bist, hast du automatisch Zugriff."
        )
        return False

    return True


def render_admin_dashboard() -> None:
    """Moderations-Bereich: „Freigaben“ (neue User freischalten) für Admin + Supporter,
    volle Nutzerverwaltung (Admin-/Supporter-Vergabe, Sperren) nur für Admins.
    Rendert sich nur, wenn der eingeloggte Nutzer is_admin ODER is_supporter hat."""
    user = st.session_state.get("auth_user")
    if not user or not (user.get("is_admin") or user.get("is_supporter")):
        return

    # ---- Freigaben: neue, noch nicht freigeschaltete User (Admin + Supporter) ----
    pending = db.get_pending_users()
    with st.expander(f"🟢 Freigaben ({len(pending)} wartend)", expanded=bool(pending)):
        if not pending:
            st.caption("Aktuell wartet niemand auf Freigabe.")
        else:
            for u in pending:
                c_img, c_name, c_ok, c_ban = st.columns([1, 4, 2, 2])
                with c_img:
                    if u.get("profile_image_url"):
                        st.image(u["profile_image_url"], width=36)
                with c_name:
                    st.markdown(f"**{u['twitch_username']}**")
                    st.caption(f"Letzter Login: {u.get('last_login') or '–'}")
                with c_ok:
                    if st.button("✅ Freigeben", key=f"approve_{u['id']}", use_container_width=True):
                        db.set_approved(u["id"], True)
                        st.rerun()
                with c_ban:
                    if st.button("🚫 Bannen", key=f"reject_{u['id']}", use_container_width=True):
                        db.set_banned(u["id"], True)
                        st.rerun()

    # ---- Volle Nutzerverwaltung (Admin/Supporter/Gesperrt/Freigegeben) nur für Admins ----
    if not user.get("is_admin"):
        return

    with st.expander("🛠️ Admin-Dashboard: Nutzerverwaltung", expanded=False):
        users = db.get_all_users()
        if not users:
            st.caption("Noch keine Nutzer registriert.")
            return

        st.caption(f"{len(users)} registrierte Nutzer – Häkchen setzen und speichern:")

        df = pd.DataFrame(users)[
            ["id", "twitch_username", "twitch_id", "last_login", "is_admin", "is_supporter",
             "is_approved", "is_banned"]
        ].rename(columns={
            "id": "ID",
            "twitch_username": "Twitch-Name",
            "twitch_id": "Twitch-ID",
            "last_login": "Letzter Login",
            "is_admin": "Admin",
            "is_supporter": "Supporter",
            "is_approved": "Freigegeben",
            "is_banned": "Gesperrt",
        })
        for col in ("Admin", "Supporter", "Freigegeben", "Gesperrt"):
            df[col] = df[col].astype(bool)

        edited = st.data_editor(
            df,
            column_config={
                "ID": st.column_config.NumberColumn(disabled=True),
                "Twitch-Name": st.column_config.TextColumn(disabled=True),
                "Twitch-ID": st.column_config.TextColumn(disabled=True),
                "Letzter Login": st.column_config.TextColumn(disabled=True),
                "Admin": st.column_config.CheckboxColumn(help="Adminrechte vergeben/entziehen"),
                "Supporter": st.column_config.CheckboxColumn(help="Supporter-Rechte vergeben/entziehen"),
                "Freigegeben": st.column_config.CheckboxColumn(help="Zugriff freischalten/entziehen"),
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
                if bool(row["Supporter"]) != bool(orig["is_supporter"]):
                    db.set_supporter(uid, bool(row["Supporter"]))
                    changed += 1
                if bool(row["Freigegeben"]) != bool(orig["is_approved"]):
                    db.set_approved(uid, bool(row["Freigegeben"]))
                    changed += 1
                if bool(row["Gesperrt"]) != bool(orig["is_banned"]):
                    db.set_banned(uid, bool(row["Gesperrt"]))
                    changed += 1
            if changed:
                st.success(f"{changed} Änderung(en) gespeichert.")
                st.rerun()
            else:
                st.info("Keine Änderungen erkannt.")
