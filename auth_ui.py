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

from typing import Any, Dict, Set
import html as html_lib

import streamlit as st

import db
import maintenance
import twitch_auth

# ---------------------------------------------------------------------------
# Twitch-IDs (nicht Usernamen!), die beim ersten Login automatisch
# is_admin = True bekommen. So kommst du selbst initial ins Admin-Dashboard,
# ohne direkt in der SQLite-Datei herumzueditieren.
# Deine Twitch-ID findest du z.B. über https://streamscharts.com/tools/convert-username
# ---------------------------------------------------------------------------
BOOTSTRAP_ADMIN_TWITCH_IDS: Set[str] = {
     "171478372",
     "133037208",
}

# ---------------------------------------------------------------------------
# Twitch-IDs, die beim ersten Login automatisch is_supporter = True bekommen
# (gleiches Muster wie BOOTSTRAP_ADMIN_TWITCH_IDS oben, nur für den Rang
# Supporter statt Admin). Einfach die Twitch-ID hier eintragen.
# ---------------------------------------------------------------------------
BOOTSTRAP_SUPPORTER_TWITCH_IDS: Set[str] = {
     # "DEINE_SUPPORTER_TWITCH_ID_HIER",
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
    if user["twitch_id"] in BOOTSTRAP_SUPPORTER_TWITCH_IDS and not user["is_supporter"]:
        db.set_supporter(user["id"], True)
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


def _render_center_login() -> None:
    """Login-Button mittig im Hauptbereich (zusätzlich zur Sidebar).
    Wichtig, falls die Sidebar auf dem Gerät/Browser des Nutzers zugeklappt
    oder der Öffnen-Button gerade nicht sichtbar ist - dann kommt man trotzdem
    ohne die Sidebar zum Login."""
    login_url = twitch_auth.get_login_url()
    st.markdown("<div style='height: 10vh;'></div>", unsafe_allow_html=True)
    col_l, col_mid, col_r = st.columns([1, 1.4, 1])
    with col_mid:
        st.markdown(
            "<div style='text-align:center; font-size:2.4rem;'>👋</div>",
            unsafe_allow_html=True,
        )
        st.markdown(
            "<h3 style='text-align:center; margin-top:0;'>Anmeldung erforderlich</h3>"
            "<p style='text-align:center; color:#a2a4bd;'>"
            "Melde dich mit Twitch an, um die Tauschbörse zu nutzen.</p>",
            unsafe_allow_html=True,
        )
        st.link_button(
            "🟣 Mit Twitch anmelden",
            login_url,
            type="primary",
            use_container_width=True,
        )


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
        st.markdown('<div class="side-nav-label">ACCOUNT</div>', unsafe_allow_html=True)
        if user:
            if user.get("profile_image_url"):
                avatar_html = f'<img class="account-avatar" src="{html_lib.escape(user["profile_image_url"])}" alt="">'
            else:
                avatar_html = '<div class="account-avatar account-avatar--fallback">👤</div>'
            if user["is_admin"]:
                badge_html = '<div class="account-badge account-badge--admin">🛡️ Admin</div>'
            elif user["is_supporter"]:
                badge_html = '<div class="account-badge account-badge--supporter">🧡 Supporter</div>'
            else:
                badge_html = ""
            st.markdown(
                f'<div class="account-card">{avatar_html}'
                f'<div class="account-meta">'
                f'<div class="account-name">{html_lib.escape(user["twitch_username"])}</div>'
                f'{badge_html}</div></div>',
                unsafe_allow_html=True,
            )
            if user["is_admin"]:
                maint_now = maintenance.is_maintenance_mode()
                maint_on = st.toggle(
                    "🚧 Wartungsmodus", value=maint_now, key="maintenance_toggle",
                    help="An: nur du (Admin) kommst noch rein, alle anderen sehen "
                         "eine Wartungsseite.",
                )
                if maint_on != maint_now:
                    maintenance.set_maintenance_mode(maint_on)
                    st.rerun()
            if st.button("🚪  Ausloggen", key="btn_logout", use_container_width=True):
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
        st.markdown('<hr class="side-divider">', unsafe_allow_html=True)

    if not user:
        _render_center_login()
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

    # ---- Wartungsmodus: nur Admins kommen durch, alle anderen sehen nur diese
    # Meldung statt der eigentlichen App (siehe maintenance.py + Toggle oben). ----
    if maintenance.is_maintenance_mode() and not user["is_admin"]:
        st.markdown("<div style='height: 12vh;'></div>", unsafe_allow_html=True)
        col_l, col_mid, col_r = st.columns([1, 1.4, 1])
        with col_mid:
            st.markdown(
                "<div style='text-align:center; font-size:2.4rem;'>🚧</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<h3 style='text-align:center; margin-top:0;'>Seite ist gerade in Wartungsarbeit</h3>"
                "<p style='text-align:center; color:#a2a4bd;'>"
                "Wir basteln gerade an der Tauschbörse. Schau in Kürze wieder vorbei!</p>",
                unsafe_allow_html=True,
            )
        return False

    return True


ADMIN_CSS = """
<style>
.admin-shell { display:flex; gap:0; border-radius:18px; overflow:hidden;
    border:1px solid #2a2c45; margin: 6px 0 26px 0; background:#0d0e18; }
.admin-nav {
    background: linear-gradient(180deg, #1c1030 0%, #120c1f 100%);
    padding: 18px 12px; min-width: 190px; border-right: 1px solid #2a2c45;
}
.admin-nav-title { font-weight:800; color:#eceef8; font-size:0.95rem;
    padding: 4px 6px 14px 6px; }
.admin-col { padding: 20px 22px; }
.admin-col-right { border-left: 1px solid #2a2c45; min-width: 260px; max-width: 300px; }
.admin-header-row { display:flex; align-items:center; justify-content:space-between;
    margin-bottom: 14px; }
.admin-header-row h3 { margin:0; font-size:1.15rem; color:#eceef8; }
.admin-count { color:#8b8d9e; font-size:0.82rem; margin-left:8px; }
.admin-row {
    display:flex; align-items:center; gap:12px; padding:9px 10px; border-radius:10px;
    border:1px solid transparent;
}
.admin-row:hover { background: rgba(255,255,255,0.03); border-color:#2a2c45; }
.admin-avatar { width:34px; height:34px; border-radius:50%; object-fit:cover;
    border:1px solid #2a2c45; flex-shrink:0; background:#1c1d2c; }
.admin-name { font-weight:700; color:#e7e7ef; font-size:0.92rem; }
.admin-sub { color:#7d7f97; font-size:0.72rem; }
.admin-badge {
    display:inline-block; padding:2px 9px; border-radius:6px; font-size:0.68rem;
    font-weight:800; white-space:nowrap; text-transform:uppercase; letter-spacing:0.02em;
}
.admin-badge-admin { background: rgba(245,158,11,0.18); color:#f59e0b; border:1px solid #f59e0b; }
.admin-badge-supporter { background: rgba(192,38,211,0.18); color:#d94ded; border:1px solid #c026d3; }
.admin-badge-viewer { background: rgba(156,163,175,0.18); color:#b7bac4; border:1px solid #9ca3af; }
.admin-badge-approved { background: rgba(34,197,94,0.18); color:#34d399; border:1px solid #22c55e; }
.admin-badge-pending { background: rgba(59,130,246,0.18); color:#5b9bff; border:1px solid #3b82f6; }
.admin-badge-banned { background: rgba(239,68,68,0.18); color:#f87171; border:1px solid #ef4444; }
.admin-detail-card { text-align:center; padding: 6px 4px 18px 4px; }
.admin-detail-card img { width:64px; height:64px; border-radius:50%; object-fit:cover;
    border:2px solid #2a2c45; margin-bottom:8px; }
.admin-detail-name { font-weight:800; font-size:1.05rem; color:#eceef8; }
.admin-info-label { color:#7d7f97; font-size:0.72rem; text-transform:uppercase;
    letter-spacing:0.04em; margin-top:12px; }
.admin-info-value { color:#e2e3f2; font-size:0.9rem; font-weight:600; margin-top:2px; }
.admin-empty { color:#7d7f97; font-size:0.85rem; padding: 20px 6px; text-align:center; }
.admin-stats-row { display:flex; gap:14px; margin: 10px 0 18px 0; }
.admin-stat {
    flex:1; background: linear-gradient(180deg, #1c1030 0%, #120c1f 100%);
    border:1px solid #2a2c45; border-radius:14px; padding: 14px 18px;
}
.admin-stat-label { color:#8b8d9e; font-size:0.78rem; text-transform:uppercase;
    letter-spacing:0.04em; margin-bottom:4px; }
.admin-stat-value { color:#eceef8; font-size:1.6rem; font-weight:800; }
.admin-stat-value.online { color:#3ecf72; }
.admin-stat-value.offline { color:#9ca3af; }
</style>
"""


def _rank(u: Dict[str, Any]) -> int:
    """Rang-Stufe für den Vergleich: Admin > Supporter > Zuschauer."""
    if u.get("is_admin"):
        return 3
    if u.get("is_supporter"):
        return 2
    return 1


def _can_ban(actor: Dict[str, Any], target: Dict[str, Any]) -> bool:
    """Admins dürfen jeden sperren/entsperren (außer sich selbst). Supporter dürfen das nur bei
    Nutzern, die im Rang UNTER ihnen stehen – also weder Admin noch Supporter sind."""
    if actor.get("id") == target.get("id"):
        return False
    if actor.get("is_admin"):
        return True
    return _rank(target) < _rank(actor)


def _rang_badge_html(u: Dict[str, Any]) -> str:
    if u.get("is_admin"):
        return '<span class="admin-badge admin-badge-admin">🛡️ Admin</span>'
    if u.get("is_supporter"):
        return '<span class="admin-badge admin-badge-supporter">🧡 Supporter</span>'
    return '<span class="admin-badge admin-badge-viewer">Zuschauer</span>'


def _status_badge_html(u: Dict[str, Any]) -> str:
    if u.get("is_banned"):
        return '<span class="admin-badge admin-badge-banned">🚫 Gesperrt</span>'
    if not u.get("is_approved"):
        return '<span class="admin-badge admin-badge-pending">⏳ Wartet</span>'
    return '<span class="admin-badge admin-badge-approved">✅ Freigegeben</span>'


def _online_dot_html(u: Dict[str, Any]) -> str:
    """Kleiner grüner/grauer Punkt für den Online-Status in der Admin-Nutzerliste,
    basierend auf db.is_user_online() (gleiche Schwelle wie im Chat)."""
    if db.is_user_online(u.get("last_seen")):
        return '<span title="online" style="color:#3ecf72;">🟢</span>'
    return '<span title="offline" style="color:#6b6d80;">⚪</span>'


def render_admin_dashboard() -> None:
    """Moderations-Dashboard im Stil „Nutzerübersicht / Freigaben / Gesperrt“ mit
    Detail-Panel rechts. Freigeben+Bannen für Admin + Supporter, volle Rechteverwaltung
    (Admin-/Supporter-Vergabe) nur für Admins. Rendert sich nur, wenn der eingeloggte
    Nutzer is_admin ODER is_supporter hat."""
    user = st.session_state.get("auth_user")
    if not user or not (user.get("is_admin") or user.get("is_supporter")):
        return

    is_full_admin = bool(user.get("is_admin"))
    st.markdown(ADMIN_CSS, unsafe_allow_html=True)

    st.markdown('<div class="section-title">🛠️ Moderations-Dashboard</div>', unsafe_allow_html=True)

    # ---- Statistik-Zeile: wie viele Profile gibt es insgesamt und wie viele davon sind
    # GERADE (siehe db.ONLINE_THRESHOLD_SECONDS) online bzw. offline. ----
    all_users_stats = db.get_all_users()
    online_n = sum(1 for u in all_users_stats if db.is_user_online(u.get("last_seen")))
    total_n = len(all_users_stats)
    st.markdown(
        '<div class="admin-stats-row">'
        f'<div class="admin-stat"><div class="admin-stat-label">👥 Profile hinterlegt</div>'
        f'<div class="admin-stat-value">{total_n}</div></div>'
        f'<div class="admin-stat"><div class="admin-stat-label">🟢 Gerade online</div>'
        f'<div class="admin-stat-value online">{online_n}</div></div>'
        f'<div class="admin-stat"><div class="admin-stat-label">⚪ Offline</div>'
        f'<div class="admin-stat-value offline">{total_n - online_n}</div></div>'
        '</div>',
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        all_users = db.get_all_users()
        pending_count = sum(1 for u in all_users if not u["is_approved"] and not u["is_banned"])
        banned_count = sum(1 for u in all_users if u["is_banned"])

        # ---- Navigation: Admins UND Supporter sehen alle 3 Bereiche (inkl. voller
        # Nutzerübersicht) – wer davon wen sperren/entsperren darf, regelt _can_ban() unten. ----
        sections = [
            ("overview", "👥 Nutzerübersicht"), ("pending", f"🟢 Freigaben ({pending_count})"),
            ("banned", f"🚫 Gesperrt ({banned_count})"),
        ]
        active = st.session_state.get("admin_nav_section", sections[0][0])
        if active not in dict(sections):
            active = sections[0][0]

        nav_col, list_col, detail_col = st.columns([1, 2.6, 1.3])

        with nav_col:
            st.markdown('<div class="admin-nav-title">Bereich</div>', unsafe_allow_html=True)
            for key, label in sections:
                if st.button(
                    label, key=f"admin_nav_{key}", use_container_width=True,
                    type="primary" if key == active else "secondary",
                ):
                    st.session_state["admin_nav_section"] = key
                    st.session_state.pop("admin_page", None)
                    st.rerun()

        # ---- Liste je nach aktivem Bereich filtern ----
        if active == "pending":
            filtered = [u for u in all_users if not u["is_approved"] and not u["is_banned"]]
            title = "Freigaben"
        elif active == "banned":
            filtered = [u for u in all_users if u["is_banned"]]
            title = "Gesperrte User"
        else:
            filtered = all_users
            title = "Aktuelle User"

        with list_col:
            st.markdown(
                f'<div class="admin-header-row"><h3>{title}</h3>'
                f'<span class="admin-count">{len(filtered)} gesamt</span></div>',
                unsafe_allow_html=True,
            )
            search = st.text_input(
                "User suchen", key="admin_search", placeholder="🔍 User suchen …",
                label_visibility="collapsed",
            )
            if search.strip():
                needle = search.strip().lower()
                filtered = [u for u in filtered if needle in u["twitch_username"].lower()]

            if not filtered:
                st.markdown('<div class="admin-empty">Keine User in diesem Bereich.</div>', unsafe_allow_html=True)
            else:
                page_size = 10
                total_pages = max(1, (len(filtered) + page_size - 1) // page_size)
                page = min(st.session_state.get("admin_page", 1), total_pages)
                start = (page - 1) * page_size
                page_users = filtered[start:start + page_size]

                selected_id = st.session_state.get("admin_selected_user_id")
                if page_users and selected_id not in {u["id"] for u in page_users} and selected_id not in {u["id"] for u in all_users}:
                    st.session_state["admin_selected_user_id"] = page_users[0]["id"]
                    selected_id = page_users[0]["id"]
                elif selected_id is None and page_users:
                    st.session_state["admin_selected_user_id"] = page_users[0]["id"]
                    selected_id = page_users[0]["id"]

                for u in page_users:
                    c_img, c_name, c_view, c_ok, c_ban = st.columns([0.6, 2.6, 0.7, 0.7, 0.7])
                    with c_img:
                        if u.get("profile_image_url"):
                            st.markdown(
                                f'<img class="admin-avatar" src="{html_lib.escape(u["profile_image_url"])}" />',
                                unsafe_allow_html=True,
                            )
                    with c_name:
                        st.markdown(
                            f'<div class="admin-name">{_online_dot_html(u)} {html_lib.escape(u["twitch_username"])}</div>'
                            f'<div style="margin-top:2px;">{_rang_badge_html(u)} {_status_badge_html(u)}</div>',
                            unsafe_allow_html=True,
                        )
                    with c_view:
                        if st.button("👁️", key=f"admin_view_{u['id']}", help="Details anzeigen"):
                            st.session_state["admin_selected_user_id"] = u["id"]
                            st.rerun()
                    with c_ok:
                        # Freigeben (unbanned + nicht freigegeben) darf jeder Moderator; Entbannen
                        # (is_banned) nur, wenn der Rang des Ziels unter dem eigenen liegt.
                        show_ok = (not u["is_approved"] and not u["is_banned"]) or \
                            (u["is_banned"] and _can_ban(user, u))
                        if show_ok:
                            if st.button("✅", key=f"admin_approve_{u['id']}", help="Freigeben / entbannen"):
                                db.set_approved(u["id"], True)
                                db.set_banned(u["id"], False)
                                st.rerun()
                    with c_ban:
                        if not u["is_banned"] and _can_ban(user, u):
                            if st.button("🚫", key=f"admin_ban_{u['id']}", help="Bannen"):
                                db.set_banned(u["id"], True)
                                st.rerun()

                if total_pages > 1:
                    p_prev, p_info, p_next = st.columns([1, 3, 1])
                    with p_prev:
                        if st.button("‹", key="admin_page_prev", disabled=page <= 1, use_container_width=True):
                            st.session_state["admin_page"] = page - 1
                            st.rerun()
                    with p_info:
                        st.markdown(
                            f'<div style="text-align:center; color:#8b8d9e; font-size:0.82rem; padding-top:6px;">'
                            f'Zeige {start + 1}–{min(start + page_size, len(filtered))} von {len(filtered)}'
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                    with p_next:
                        if st.button("›", key="admin_page_next", disabled=page >= total_pages, use_container_width=True):
                            st.session_state["admin_page"] = page + 1
                            st.rerun()

        # ---- Detail-Panel rechts für den ausgewählten User ----
        with detail_col:
            selected_id = st.session_state.get("admin_selected_user_id")
            selected = next((u for u in all_users if u["id"] == selected_id), None)
            if not selected:
                st.markdown('<div class="admin-empty">Wähle links einen User aus.</div>', unsafe_allow_html=True)
            else:
                avatar = selected.get("profile_image_url") or ""
                st.markdown(
                    '<div class="admin-detail-card">'
                    + (f'<img src="{html_lib.escape(avatar)}" />' if avatar else "")
                    + f'<div class="admin-detail-name">{html_lib.escape(selected["twitch_username"])}</div>'
                    + f'<div style="margin-top:6px;">{_status_badge_html(selected)}</div>'
                    + "</div>",
                    unsafe_allow_html=True,
                )
                st.markdown('<div class="admin-info-label">Rang</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="admin-info-value">{_rang_badge_html(selected)}</div>', unsafe_allow_html=True)
                st.markdown('<div class="admin-info-label">Letzter Login</div>', unsafe_allow_html=True)
                st.markdown(
                    f'<div class="admin-info-value">{html_lib.escape(selected.get("last_login") or "–")}</div>',
                    unsafe_allow_html=True,
                )
                st.markdown('<div class="admin-info-label">Twitch-ID</div>', unsafe_allow_html=True)
                st.markdown(f'<div class="admin-info-value">{html_lib.escape(selected["twitch_id"])}</div>', unsafe_allow_html=True)

                st.markdown("<div style='margin-top:18px;'></div>", unsafe_allow_html=True)

                if selected["is_banned"]:
                    if _can_ban(user, selected):
                        if st.button("✅ Entbannen", key="admin_detail_unban", use_container_width=True, type="primary"):
                            db.set_banned(selected["id"], False)
                            st.rerun()
                    else:
                        st.caption("Dieser Nutzer steht in deinem Rang oder darüber – du kannst ihn nicht entbannen.")
                else:
                    if not selected["is_approved"]:
                        if st.button("✅ Freigeben", key="admin_detail_approve", use_container_width=True, type="primary"):
                            db.set_approved(selected["id"], True)
                            st.rerun()
                    if _can_ban(user, selected):
                        if st.button("🚫 Bannen", key="admin_detail_ban", use_container_width=True):
                            db.set_banned(selected["id"], True)
                            st.rerun()
                    elif selected["id"] != user["id"]:
                        st.caption("Dieser Nutzer steht in deinem Rang oder darüber – du kannst ihn nicht bannen.")

                if is_full_admin and selected["id"] != user["id"]:
                    st.markdown("<div style='margin-top:10px;'></div>", unsafe_allow_html=True)
                    if selected.get("is_admin"):
                        if st.button("🛡️ Admin entziehen", key="admin_detail_unadmin", use_container_width=True):
                            db.set_admin(selected["id"], False)
                            st.rerun()
                    else:
                        if st.button("🛡️ Zum Admin machen", key="admin_detail_admin", use_container_width=True):
                            db.set_admin(selected["id"], True)
                            st.rerun()
                    if selected.get("is_supporter"):
                        if st.button("🧡 Supporter entziehen", key="admin_detail_unsupp", use_container_width=True):
                            db.set_supporter(selected["id"], False)
                            st.rerun()
                    else:
                        if st.button("🧡 Zum Supporter machen", key="admin_detail_supp", use_container_width=True):
                            db.set_supporter(selected["id"], True)
                            st.rerun()
