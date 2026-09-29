import streamlit as st
import db


def render_user_management(current_user: dict) -> None:
    """Rendert die Benutzeroberfläche zur Rechte- und Freigabeverwaltung direkt im Dashboard."""
    st.header("⚙️ Benutzerverwaltung & Moderation")

    # Sicherheitsprüfung: Nur Admins oder Supporter dürfen diese Seite sehen
    if not current_user.get("is_admin") and not current_user.get("is_supporter"):
        st.error("❌ Zugriff verweigert. Du besitzt keine Berechtigung für dieses Menü.")
        return

    users = db.get_all_users()

    # Admin sieht alle 3 Tabs im Web-Dashboard, Supporter nur die ersten zwei
    if current_user.get("is_admin"):
        tabs = ["⏳ Warten auf Freigabe", "👥 Alle Benutzer verwalten", "🚨 Massen-Aktionen & Sicherheit"]
    else:
        tabs = ["⏳ Warten auf Freigabe", "👥 Alle Benutzer verwalten"]

    tab_objects = st.tabs(tabs)

    # -------------------------------------------------------------------
    # TAB 1: Ausstehende Freigaben
    # -------------------------------------------------------------------
    with tab_objects[0]:
        st.subheader("Ausstehende Account-Freigaben")
        pending_users = [u for u in users if not bool(u.get("is_approved"))]

        if not pending_users:
            st.info("Aktuell warten keine Benutzer auf eine Freigabe.")
        else:
            st.write(f"Es warten **{len(pending_users)}** Benutzer auf Aktivierung:")
            
            for u in pending_users:
                c1, c2, c3, c4 = st.columns([1, 3, 3, 2])
                with c1:
                    if u.get("profile_image_url"):
                        st.image(u["profile_image_url"], width=40)
                    else:
                        st.write("👤")
                with c2:
                    st.write(f"**{u['twitch_username']}**")
                with c3:
                    st.write(f"ID: `{u['twitch_id']}`")
                with c4:
                    if st.button("✅ Jetzt Freigeben", key=f"app_pend_{u['id']}", type="primary"):
                        db.toggle_user_approval(u["id"], True)
                        st.success(f"{u['twitch_username']} freigegeben!")
                        st.rerun()
                st.divider()

    # -------------------------------------------------------------------
    # TAB 2: Übersicht aller Benutzer mit allen Buttons
    # -------------------------------------------------------------------
    with tab_objects[1]:
        st.subheader("Direkt-Verwaltung aller Konten")

        if not users:
            st.info("Keine Benutzer in der Datenbank gefunden.")
        else:
            # Tabellen-Kopfzeile
            head_col1, head_col2, head_col3, head_col4 = st.columns([2, 2, 3, 3])
            with head_col1:
                st.markdown("**Benutzer**")
            with head_col2:
                st.markdown("**Rolle & Status**")
            with head_col3:
                st.markdown("**Freigabe-Aktion**")
            with head_col4:
                st.markdown("**Supporter-Aktion**")
            st.divider()

            for u in users:
                is_approved = bool(u.get("is_approved"))
                is_supporter = bool(u.get("is_supporter"))
                is_admin = bool(u.get("is_admin"))

                c1, c2, c3, c4 = st.columns([2, 2, 3, 3])

                # Spalte 1: Name & Bild
                with c1:
                    st.write(f"**{u['twitch_username']}**")
                    st.caption(f"ID: {u['twitch_id']}")

                # Spalte 2: Rollen-Badges
                with c2:
                    if is_admin:
                        st.markdown("🔴 **Admin**")
                    elif is_supporter:
                        st.markdown("⭐ **Supporter**")
                    else:
                        st.markdown("👤 **Normaler User**")

                    if is_approved:
                        st.caption("🟢 Freigegeben")
                    else:
                        st.caption("🔴 Gesperrt")

                # Spalte 3: FREIGABE ENTZIEHEN / ERTEILEN BUTTON
                with c3:
                    is_self_admin = is_admin and (u["id"] == current_user["id"])

                    if is_approved:
                        if st.button(
                            "🚫 Freigabe entziehen",
                            key=f"btn_revoke_{u['id']}",
                            type="secondary",
                            disabled=is_self_admin,
                        ):
                            db.toggle_user_approval(u["id"], False)
                            st.warning(f"Freigabe für {u['twitch_username']} wurde entzogen!")
                            st.rerun()
                    else:
                        if st.button(
                            "✅ Freigeben",
                            key=f"btn_approve_{u['id']}",
                            type="primary",
                        ):
                            db.toggle_user_approval(u["id"], True)
                            st.success(f"{u['twitch_username']} wurde freigegeben!")
                            st.rerun()

                # Spalte 4: SUPPORTER BERECHTIGUNG ÄNDERN
                with c4:
                    if current_user.get("is_admin"):
                        if is_admin:
                            st.caption("Admins unantastbar")
                        elif is_supporter:
                            if st.button(
                                "⬇️ Supporter entfernen",
                                key=f"btn_rem_sup_{u['id']}",
                            ):
                                db.toggle_user_supporter(u["id"], False)
                                st.rerun()
                        else:
                            if st.button(
                                "⭐ Zum Supporter machen",
                                key=f"btn_add_sup_{u['id']}",
                            ):
                                db.toggle_user_supporter(u["id"], True)
                                st.rerun()

                st.divider()

    # -------------------------------------------------------------------
    # TAB 3: Massen-Aktionen (Nur Admins)
    # -------------------------------------------------------------------
    if current_user.get("is_admin") and len(tab_objects) > 2:
        with tab_objects[2]:
            st.subheader("🚨 Massen-Aktionen & Zurücksetzen")

            # 1. Nur normale User sperren
            st.markdown("### 1. Freigabe nur von normalen Usern entziehen")
            st.info("Entzieht allen normalen Benutzern die Freigabe. **Admins und Supporter behalten ihre Freigabe.**")
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
                st.success(f"Erfolgreich! Bei {count} normalen User-Accounts wurde die Freigabe entzogen.")
                st.rerun()

            st.divider()

            # 2. Alle sperren (inkl. Supporter)
            st.markdown("### 2. Freigabe von ALLEN entziehen (inkl. Supporter)")
            st.warning("Achtung: Diese Aktion entzieht **allen** Benutzern und Supportern auf einmal die Freigabe.")
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
                st.success(f"Erfolgreich! Bei {count} Accounts wurde die Freigabe entzogen.")
                st.rerun()
