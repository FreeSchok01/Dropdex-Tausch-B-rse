import streamlit as st
import db


def render_user_management(current_user: dict) -> None:
    """Rendert die Benutzeroberfläche zur Rechte- und Freigabeverwaltung."""
    st.header("⚙️ Benutzerverwaltung & Moderation")

    # Überprüfung, ob der zugreifende User Admin oder Supporter ist
    if not current_user.get("is_admin") and not current_user.get("is_supporter"):
        st.error("Zugriff verweigert. Du besitzt keine Berechtigung für dieses Menü.")
        return

    users = db.get_all_users()

    # Admin sieht alle 3 Tabs, Supporter nur die ersten beiden
    if current_user.get("is_admin"):
        tabs = ["⏳ Warten auf Freigabe", "👥 Alle Benutzer", "🚨 Massen-Aktionen & Sicherheit"]
    else:
        tabs = ["⏳ Warten auf Freigabe", "👥 Alle Benutzer"]

    tab_objects = st.tabs(tabs)

    # TAB 1: Ausstehende Freigaben
    with tab_objects[0]:
        st.subheader("Ausstehende Account-Freigaben")
        pending_users = [u for u in users if not u.get("is_approved")]

        if not pending_users:
            st.info("Keine Benutzer warten derzeit auf eine Freigabe.")
        else:
            for u in pending_users:
                col1, col2, col3 = st.columns([1, 3, 2])
                with col1:
                    if u.get("profile_image_url"):
                        st.image(u["profile_image_url"], width=40)
                    else:
                        st.write("👤")
                with col2:
                    st.write(f"**{u['twitch_username']}** (ID: {u['id']})")
                with col3:
                    if st.button(
                        "✅ Freigeben", key=f"approve_pending_{u['id']}", type="primary"
                    ):
                        db.toggle_user_approval(u["id"], True)
                        st.success(f"{u['twitch_username']} wurde freigegeben!")
                        st.rerun()

    # TAB 2: Übersicht aller Benutzer
    with tab_objects[1]:
        st.subheader("Übersicht aller Konten")
        for u in users:
            with st.expander(
                f"{u['twitch_username']} "
                f"({'Admin' if u.get('is_admin') else 'Supporter' if u.get('is_supporter') else 'Normaler User'})"
            ):
                col_info, col_actions = st.columns([2, 2])

                with col_info:
                    if u.get("profile_image_url"):
                        st.image(u["profile_image_url"], width=60)
                    st.write(f"**Twitch-ID:** {u['twitch_id']}")
                    st.write(f"**Letzter Login:** {u['last_login']}")

                with col_actions:
                    # Status: Freigabe
                    is_app = bool(u.get("is_approved"))
                    new_app = st.checkbox(
                        "Account freigegeben",
                        value=is_app,
                        key=f"check_app_{u['id']}",
                        disabled=bool(u.get("is_admin")),  # Admins können sich nicht selbst sperren
                    )
                    if new_app != is_app:
                        db.toggle_user_approval(u["id"], new_app)
                        st.rerun()

                    # Status: Supporter (Nur Admins können Supporter vergeben)
                    if current_user.get("is_admin"):
                        is_sup = bool(u.get("is_supporter"))
                        new_sup = st.checkbox(
                            "Supporter-Rechte",
                            value=is_sup,
                            key=f"check_sup_{u['id']}",
                            disabled=bool(u.get("is_admin")),
                        )
                        if new_sup != is_sup:
                            db.toggle_user_supporter(u["id"], new_sup)
                            st.rerun()

    # TAB 3: Massen-Aktionen (Nur für Admins sichtbar)
    if current_user.get("is_admin") and len(tab_objects) > 2:
        with tab_objects[2]:
            st.subheader("🚨 Massen-Aktionen & Zurücksetzen")

            # Option A: Nur normale User sperren
            st.markdown("### 1. Freigabe nur von normalen Usern entziehen")
            st.info(
                "Entzieht allen normalen Benutzern die Freigabe. "
                "**Admins und Supporter behalten ihre Freigabe.**"
            )
            confirm_user_revoke = st.checkbox(
                "Ich bestätige, dass ich allen normalen Usern die Freigabe entziehen möchte.",
                key="confirm_user_revoke",
            )
            if st.button(
                "❌ Freigabe aller normalen User entziehen",
                type="secondary",
                disabled=not confirm_user_revoke,
            ):
                count = db.revoke_all_approvals_except_admins_and_supporters()
                st.success(
                    f"Erfolgreich! Bei {count} normalen User-Accounts wurde die Freigabe entzogen."
                )
                st.rerun()

            st.divider()

            # Option B: Alle sperren (inkl. Supporter)
            st.markdown("### 2. Freigabe von ALLEN entziehen (inkl. Supporter)")
            st.warning(
                "Achtung: Diese Aktion entzieht **allen** Benutzern und Supportern auf einmal die Freigabe. "
                "Ausgenommen sind lediglich Admins."
            )
            confirm_mass_revoke = st.checkbox(
                "Ich bestätige, dass ich allen Usern und Supportern die Freigabe entziehen möchte.",
                key="confirm_mass_revoke",
            )
            if st.button(
                "💥 JETZT ALLE FREIGABEN ENTZIEHEN (inkl. Supporter)",
                type="primary",
                disabled=not confirm_mass_revoke,
            ):
                count = db.revoke_all_approvals_except_admins()
                st.success(
                    f"Erfolgreich! Bei {count} Accounts wurde die Freigabe entzogen."
                )
                st.rerun()
