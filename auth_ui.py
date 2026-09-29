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

    # TAB 2: Übersicht aller Benutzer (Einzelne Freigabe verwalten/entziehen)
    with tab_objects[1]:
        st.subheader("Übersicht aller Konten")
        for u in users:
            role_label = "Admin" if u.get("is_admin") else ("Supporter" if u.get("is_supporter") else "Normaler User")
            status_label = "🟢 Freigegeben" if u.get("is_approved") else "🔴 Gesperrt / Nicht freigegeben"
            
            with st.expander(f"{u['twitch_username']} — [{role_label}] — {status_label}"):
                col_info, col_actions = st.columns([2, 2])

                with col_info:
                    if u.get("profile_image_url"):
                        st.image(u["profile_image_url"], width=60)
                    st.write(f"**Twitch-ID:** {u['twitch_id']}")
                    st.write(f"**Letzter Login:** {u['last_login']}")

                with col_actions:
                    st.write("**Freigabe-Status verwalten:**")
                    
                    # Verhindere, dass Admins sich selbst die Freigabe entziehen
                    is_self_admin = bool(u.get("is_admin") and u["id"] == current_user["id"])

                    if u.get("is_approved"):
                        if st.button(
                            "🚫 Freigabe entziehen",
                            key=f"btn_revoke_{u['id']}",
                            type="secondary",
                            disabled=is_self_admin,
                        ):
                            db.toggle_user_approval(u["id"], False)
                            st.warning(f"Freigabe für {u['twitch_username']} wurde entzogen.")
                            st.rerun()
                    else:
                        if st.button(
                            "✅ Freigeben",
                            key=f"btn_approve_{u['id']}",
                            type="primary",
                        ):
                            db.toggle_user_approval(u["id"], True)
                            st.success(f"{u['twitch_username']} wurde freigegeben.")
                            st.rerun()

                    st.divider()

                    # Supporter-Status anpassen (Nur Admins)
                    if current_user.get("is_admin"):
                        is_sup = bool(u.get("is_supporter"))
                        new_sup = st.checkbox(
                            "Supporter-Rechte gewähren",
                            value=is_sup,
                            key=f"check_sup_{u['id']}",
                            disabled=bool(u.get("is_admin")),
                        )
                        if new_sup != is_sup:
                            db.toggle_user_supporter(u["id"], new_sup)
                            st.rerun()

    # TAB 3: Massen-Aktionen (Nur für Admins)
    if current_user.get("is_admin") and len(tab_objects) > 2:
        with tab_objects[2]:
            st.subheader("🚨 Massen-Aktionen & Zurücksetzen")

            # Option 1: Nur normale User sperren
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

            # Option 2: Alle sperren (inkl. Supporter)
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
