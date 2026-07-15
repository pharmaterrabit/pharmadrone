"""Role-gated administration shell kept separate from customer navigation."""
from __future__ import annotations

import streamlit as st

from pharmadrone import admin, auth
from pharmatune_ui import theme
from . import pages

PLATFORM_NAV = (
    "Platform Overview", "Organisations", "Users", "Workspaces", "Roles & Permissions",
    "Source Connectors", "Scheduled Jobs", "Data Ingestion", "Failed Jobs & Retries",
    "API Usage & Costs", "Database & Backups", "Audit & Security Logs", "Feature Flags",
    "System Configuration",
)


@st.cache_data(ttl=15, show_spinner=False)
def _snapshot(role: str, display_name: str, organisation_id: str) -> dict:
    """Reuse one bounded administration snapshot during warm navigation."""
    return admin.snapshot({
        "role": role,
        "display_name": display_name,
        "organisation_id": organisation_id,
    })


def run(principal: dict) -> None:
    theme.inject()
    role = principal.get("role")
    if role not in {admin.PLATFORM_ADMIN, admin.WORKSPACE_ADMIN}:
        st.error("Administration access is not assigned to this account.")
        st.stop()
    with st.sidebar:
        st.markdown('<div class="pt-brand"><div class="pt-mark">P</div><div><b>PharmaTune</b><small>Administration</small></div></div>', unsafe_allow_html=True)
        if role == admin.WORKSPACE_ADMIN:
            selected = "Workspace Administration"
            st.markdown(theme.badge("Workspace Admin", "blue"), unsafe_allow_html=True)
            st.caption("Scoped to one organisation. Global operations are hidden server-side.")
        else:
            st.markdown(theme.badge("Platform Admin", "violet"), unsafe_allow_html=True)
            selected = st.radio("Administration navigation", PLATFORM_NAV, label_visibility="collapsed")
            st.caption("Internal platform operations. Credentials and secret values are never displayed.")
        st.markdown("---")
        if st.button("Sign out", use_container_width=True):
            auth.sign_out()
    try:
        state = _snapshot(
            str(principal.get("role") or ""),
            str(principal.get("display_name") or ""),
            str(principal.get("organisation_id") or ""),
        )
    except Exception as exc:
        st.error("Administration data could not be loaded safely.")
        st.caption(str(exc)); st.stop()
    routes = {
        "Workspace Administration": pages.workspace_administration,
        "Platform Overview": pages.platform_overview,
        "Organisations": pages.organisations,
        "Users": pages.users,
        "Workspaces": pages.workspaces,
        "Roles & Permissions": pages.roles,
        "Source Connectors": pages.connectors,
        "Scheduled Jobs": pages.jobs,
        "Data Ingestion": pages.ingestion,
        "Failed Jobs & Retries": pages.failed_jobs,
        "API Usage & Costs": pages.usage,
        "Database & Backups": pages.database_backups,
        "Audit & Security Logs": pages.audit_logs,
        "Feature Flags": pages.feature_flags,
        "System Configuration": pages.system_configuration,
    }
    routes[selected](principal, state)
    st.markdown('<div class="pt-mono" style="margin-top:40px;border-top:1px solid rgba(151,168,205,.13);padding-top:14px">PharmaTune Administration · role-scoped server-side · secrets hidden</div>', unsafe_allow_html=True)
