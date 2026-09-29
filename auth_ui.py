# -*- coding: utf-8 -*-
"""
auth_ui.py
==========
Benutzeroberfläche für Twitch-Login, Nutzersessions, Berechtigungsprüfungen
und das Moderations-Dashboard inkl. Massen-Freigabe-Entzug.
"""

import os
import urllib.parse
import requests
import streamlit as st

import db

# Statische IDs für den Initial-Admin und Bootstrap-Supporter (optional über Umgebungsvariablen)
BOOTSTRAP_ADMIN_TWITCH_IDS = [
    x.strip() for x in os.getenv("BOOTSTRAP_ADMIN_TWITCH_IDS", "").split(",") if x.strip()
]
BOOTSTRAP_SUPPORTER_TWITCH_IDS = [
    x.strip() for x in os.getenv("BOOTSTRAP_SUPPORTER_TWITCH_IDS", "").split(",") if x.strip()
]


def init_auth():
    """Initialisiert die Datenbank-Tabellen beim Aufruf."""
    db.init_db()


def get_twitch_login_url() -> str:
    """Generiert die Twitch OAuth Login URL."""
    client_id = os.getenv("TWITCH_CLIENT_ID", "")
    redirect_uri = os.getenv("TWITCH_REDIRECT_URI", "")
    scope = "user:read:email"
    
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
    }
    return f"https://id.twitch.tv/oauth2/authorize?{urllib.parse.urlencode(params)}"


def handle_twitch_callback() -> bool:
    """Verarbeitet den OAuth Code von Twitch nach der Weiterleitung."""
    query_params = st.query_params
    code = query_params.get("code")
    
    if not code:
        return False

    client_id = os.getenv("TWITCH_CLIENT_ID", "")
    client_secret = os.getenv("TWITCH_CLIENT_SECRET", "")
    redirect_uri = os.getenv("TWITCH_REDIRECT_URI", "")

    # Token bei Twitch anfordern
    token_url = "https://id.twitch.tv/oauth2/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }

    try:
        res = requests.post(token_url, data=data, timeout=10)
        res_data = res.json()
        access_token = res_data.get("access_token")

        if not access_token:
            st.error("Twitch-Authentifizierung fehlgeschlagen.")
            return False

        # Benutzerdaten von Twitch abrufen
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {access_token}",
        }
        user_res = requests.get("https://api.twitch.tv/helix/users", headers=headers, timeout=10)
        user_data = user_res.json().get("data", [])[0]

        twitch_id = str(user_data["id"])
        twitch_username = user_data["display_name"]
        profile_image_url = user_data.get("profile_image_url", "")

        # User in DB anlegen oder laden
        user = db.get_or_create_user(twitch_id, twitch_username, profile_image_url)

        # Bootstrap-Rollen zuteilen falls konfiguriert
        if twitch_id in BOOTSTRAP_ADMIN_TWITCH_IDS:
            db.set_admin(user["id"], True)
            db.set_approved(user["id"], True)
            user = db.get_user_by_id(user["id"])

        if twitch_id in BOOTSTRAP_SUPPORTER_TWITCH_IDS:
            db.set_supporter(user["id"], True)

        # Session-Token erstellen und speichern
        session_token = db.create_session(twitch_id)
        st.session_state["session_token"] = session_token
        st.session_state["current_user"] = user

        # Query Parameter bereinigen
        st.query_params.clear()
        return True

    except Exception as e:
        st.error(f"Fehler bei der Anmeldung: {e}")
        return False


def get_current_user():
    """Gibt den aktuell eingeloggten Nutzer zurück oder None."""
    if "current_user" in st.session_state and st.session_state["current_user"]:
        # Aktualisierten Status aus der DB laden
        user = db.get_user_by_id(st.session_state["current_user"]["id"])
        st.session_state["current_user"] = user
        if user:
            db.touch_last_seen(user["id"])
        return user

    token = st.session_state.get("session_token")
    if token:
        user = db.get_user_by_session_token(token)
        if user:
            st.session_state["current_user"] = user
            db.touch_last_seen(user["id"])
            return user

    return None


def logout():
    """Meldet den aktuellen Benutzer ab."""
    token = st.session_state.get("session_token")
    if token:
        db.delete_session(token)
    st.session_state.pop("session_token", None)
    st.session_state.pop("current_user", None)
    st.rerun()


def render_login_button():
    """Rendert den Twitch Login Button."""
    url = get_twitch_login_url()
    st.markdown(
        f"""
        <a href="{url}" target="_self" style="
            display: inline-block;
            background-color: #9146FF;
            color: white;
            padding: 10px 20px;
            text-decoration: none;
            font-weight: bold;
            border-radius: 5px;
        ">
            🟣 Mit Twitch anmelden
        </a>
        """,
        unsafe_allow_html=fTrue,
    )


def render_pending_approval_screen():
    """Warteraum für Nutzer, die noch keine Freigabe haben."""
    st.title("⏳ Account wartet auf Freigabe")
    st.warning("Dein Account wurde registriert, muss aber noch von einem Moderator oder Admin freigeschaltet werden.")
    st.info("Bitte gedulde dich einen Moment. Sobald du freigeschaltet wurdest, kannst du die Anwendung nutzen.")
    if st.button("Logout / Abmelden"):
        logout()


def render_banned_screen():
    """Sperrbildschirm für gesperrte Nutzer."""
    st.title("🚫 Account gesperrt")
    st.error("Dein Account wurde gesperrt. Du hast keinen Zugriff mehr auf diese Anwendung.")
    if st.button("Logout / Abmelden"):
        logout()


def render_moderation_dashboard(current_user: dict):
    """Vollständiges Moderations- und Admin-Dashboard."""
    if not (current_user.get("is_admin") or current_user.get("is_supporter")):
        st.error("Du hast keine Berechtigung für das Moderations-Dashboard.")
        return

    st.title("🛡️ Moderation & Verwaltung")

    tabs = ["⏳ Offene Freigaben", "👥 Benutzerverwaltung"]
    if current_user.get("is_admin"):
        tabs.append("🚨 Massen-Aktionen & Sicherheit")

    tab_objects = st.tabs(tabs)

    # TAB 1: Offene Freigaben
    with tab_objects[0]:
        st.subheader("Ausstehende Nutzer-Freigaben")
        pending = db.get_pending_users()

        if not pending:
            st.success("Aktuell warten keine neuen Nutzer auf eine Freigabe.")
        else:
            for u in pending:
                col_info, col_btn1, col_btn2 = st.columns([3, 1, 1])
                with col_info:
                    st.write(f"**{u['twitch_username']}** (ID: `{u['twitch_id']}`)")
                    st.caption(f"Registriert/Letzter Login: {u['last_login'] or 'Unbekannt'}")
                with col_btn1:
                    if st.button("✅ Freigeben", key=f"approve_{u['id']}", type="primary"):
                        db.set_approved(u['id'], True)
                        st.success(f"{u['twitch_username']} wurde freigegeben.")
                        st.rerun()
                with col_btn2:
                    if st.button("🚫 Sperren", key=f"ban_pending_{u['id']}"):
                        db.set_banned(u['id'], True)
                        st.warning(f"{u['twitch_username']} wurde gesperrt.")
                        st.rerun()
                st.divider()

    # TAB 2: Benutzerverwaltung
    with tab_objects[1]:
        st.subheader("Alle registrierten Benutzer")
        all_users = db.get_all_users()

        search = st.text_input("🔍 Benutzer suchen", placeholder="Twitch-Name...").strip().lower()
        if search:
            all_users = [u for u in all_users if search in u["twitch_username"].lower()]

        for u in all_users:
            col_avatar, col_details, col_actions = st.columns([1, 4, 3])

            with col_avatar:
                if u.get("profile_image_url"):
                    st.image(u["profile_image_url"], width=50)
                else:
                    st.write("👤")

            with col_details:
                st.write(f"**{u['twitch_username']}** (ID: {u['id']})")
                
                # Rollen-Badges
                badges = []
                if u["is_admin"]:
                    badges.append("🔴 Admin")
                if u["is_supporter"]:
                    badges.append("🟢 Supporter")
                if u["is_approved"]:
                    badges.append("✅ Freigegeben")
                else:
                    badges.append("⏳ Wartet auf Freigabe")
                if u["is_banned"]:
                    badges.append("🚫 Gesperrt")
                st.caption(" | ".join(badges))

            with col_actions:
                # Buttons zur Steuerung (Admins dürfen alles, Supporter begrenzte Rechte)
                if not u["is_admin"] or current_user.get("is_admin"):
                    
                    # Freigabe Umschalten
                    if u["is_approved"]:
                        if st.button("❌ Freigabe entziehen", key=f"revoke_app_{u['id']}"):
                            db.set_approved(u['id'], False)
                            st.rerun()
                    else:
                        if st.button("✅ Freigeben", key=f"grant_app_{u['id']}"):
                            db.set_approved(u['id'], True)
                            st.rerun()

                    # Nur Admins dürfen Supporter/Admin-Rollen vergeben
                    if current_user.get("is_admin"):
                        # Supporter Toggle
                        is_supp = bool(u["is_supporter"])
                        new_supp = st.checkbox("Supporter", value=is_supp, key=f"chk_supp_{u['id']}")
                        if new_supp != is_supp:
                            db.set_supporter(u['id'], new_supp)
                            st.rerun()

                        # Admin Toggle
                        is_adm = bool(u["is_admin"])
                        new_adm = st.checkbox("Admin", value=is_adm, key=f"chk_adm_{u['id']}")
                        if new_adm != is_adm:
                            db.set_admin(u['id'], new_adm)
                            st.rerun()

                    # Sperre Toggle
                    if u["is_banned"]:
                        if st.button("Entsperren", key=f"unban_{u['id']}"):
                            db.set_banned(u['id'], False)
                            st.rerun()
                    else:
                        if st.button("🚫 Sperren", key=f"ban_{u['id']}"):
                            db.set_banned(u['id'], True)
                            st.rerun()

            st.divider()

    # TAB 3: Massen-Aktionen (Nur Admins)
    if current_user.get("is_admin") and len(tab_objects) > 2:
        with tab_objects[2]:
            st.subheader("🚨 Massen-Aktionen & Zurücksetzen")
            st.warning(
                "Achtung: Die folgende Aktion entzieht **allen** Benutzern und Supportern auf einmal die Freigabe. "
                "Ausgenommen sind lediglich Admins. Alle betroffenen Nutzer müssen anschließend neu freigegeben werden."
            )

            confirm = st.checkbox(
                "Ich bestätige, dass ich allen Usern und Supportern die Freigabe entziehen möchte.",
                key="confirm_mass_revoke",
            )

            if st.button("💥 JETZT ALLE FREIGABEN ENTZIEHEN", type="primary", disabled=not confirm):
                count = db.revoke_all_approvals_except_admins()
                st.success(f"Erfolgreich! Bei {count} Accounts wurde die Freigabe entzogen.")
                st.rerun()
