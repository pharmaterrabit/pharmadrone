"""Application shell and role-safe Customer / Analyst navigation."""
from __future__ import annotations

import streamlit as st

from pharmadrone import auth, db
from pharmadrone.storage import DatabaseConfigurationError, DatabaseUnavailableError
from . import pages, theme

NAV = {
    "DISCOVER":["Overview","Opportunity Explorer","Companies","Products","Technologies"],
    "INTELLIGENCE":["Research & Innovation","Regulatory Signals","Deals & Funding","Patents"],
    "WORKFLOW":["My Workspace","Saved Lists","Alerts","Human Validation","Case Studies","Pharmaceutical Memory"],
    "PLATFORM":["Data Sources","System Health","Settings"],
}

HIDDEN_ROUTE_PARENT = {"Opportunity Detail": "Opportunity Explorer"}
HIDDEN_ROUTE_PARENT["Company Detail"] = "Companies"
HIDDEN_ROUTE_PARENT["Regulatory Detail"] = "Regulatory Signals"
HIDDEN_ROUTE_PARENT["Patent Detail"] = "Patents"
HIDDEN_ROUTE_PARENT["Research Detail"] = "Research & Innovation"
HIDDEN_ROUTE_PARENT["Deal Detail"] = "Deals & Funding"
NAV_OPTIONS = [page for group in NAV.values() for page in group]
NAVIGATION_KEY = "navigation_page"
PENDING_NAVIGATION_KEY = "_pending_navigation_page"


@st.cache_data(ttl=30, show_spinner=False)
def _database_status() -> dict:
    """Bound shell health checks so navigation does not re-query Neon twice."""
    return db.database_status()


def _navigate(page: str) -> None:
    st.session_state["page"] = page
    st.session_state[PENDING_NAVIGATION_KEY] = HIDDEN_ROUTE_PARENT.get(page, page)
    st.rerun()


def _sync_navigation() -> None:
    """Copy a sidebar click to the active route before Streamlit reruns."""
    selected = st.session_state[NAVIGATION_KEY]
    current = st.session_state.get("page", "Overview")
    if HIDDEN_ROUTE_PARENT.get(current) != selected:
        st.session_state["page"] = selected


def _sidebar(principal: dict) -> str:
    with st.sidebar:
        st.markdown('<div class="pt-brand"><div class="pt-mark">P</div><div><b>PharmaTune</b><small>Intelligence Platform</small></div></div>',unsafe_allow_html=True)
        current=st.session_state.get("page","Overview")
        visible_current=current if current in NAV_OPTIONS else HIDDEN_ROUTE_PARENT.get(current,"Overview")
        pending = st.session_state.pop(PENDING_NAVIGATION_KEY, None)
        if pending in NAV_OPTIONS:
            st.session_state[NAVIGATION_KEY] = pending
        elif NAVIGATION_KEY not in st.session_state:
            st.session_state[NAVIGATION_KEY] = visible_current
        selected=st.radio(
            "Navigation",
            NAV_OPTIONS,
            key=NAVIGATION_KEY,
            on_change=_sync_navigation,
            label_visibility="collapsed",
        )
        st.markdown("---")
        role_label = "Read-only executive" if principal.get("role") == "read_only_executive" else "Analyst workspace"
        st.markdown(theme.badge(role_label,"blue"),unsafe_allow_html=True)
        st.caption(str(principal.get("display_name") or "Authenticated customer"))
        st.caption("Evidence-backed opportunity signals. Human validation required.")
        if st.button("Sign out", use_container_width=True):
            auth.sign_out()
        return selected


def run(principal: dict | None = None) -> None:
    theme.inject(); principal = principal or auth.require_password()
    if principal.get("role") not in {"analyst_reviewer", "read_only_executive"}:
        st.error("This account is assigned to an administration workspace.")
        st.stop()
    st.session_state["customer_principal"] = dict(principal)
    try:
        status=_database_status()
    except (DatabaseConfigurationError,DatabaseUnavailableError,RuntimeError) as exc:
        st.error("PharmaTune cannot connect to its durable database. Production remains closed rather than using a disposable fallback.")
        st.caption(str(exc)); st.stop()
    selected=_sidebar(principal)
    page=st.session_state.get("page",selected)
    routes={
        "Overview":pages.overview,
        "Opportunity Explorer":lambda:pages.explorer(_navigate),
        "Opportunity Detail":lambda:pages.opportunity_detail(_navigate),
        "Companies":lambda:pages.entity_page("Companies","Evidence-governed organisations, linked products and signals, and weekly-reviewed contact routes.","company",_navigate),
        "Company Detail":lambda:pages.company_detail(_navigate),
        "Products":lambda:pages.entity_page("Products","Products represented in the current live opportunity index.","product"),
        "Technologies":pages.technology_profile,
        "My Workspace":lambda:pages.customer_workspace(principal,_navigate),
        "Saved Lists":lambda:pages.saved_lists(principal,_navigate),
        "Alerts":lambda:pages.customer_alerts(principal,_navigate),
        "Human Validation":pages.validation,
        "Case Studies":lambda:pages.case_studies(principal,_navigate),
        "Pharmaceutical Memory":pages.pharmaceutical_memory,
        "Data Sources":pages.sources,
        "System Health":pages.health,
        "Research & Innovation":lambda:pages.research_innovation(_navigate),
        "Research Detail":lambda:pages.research_detail(_navigate),
        "Regulatory Signals":lambda:pages.regulatory_signals(_navigate),
        "Regulatory Detail":lambda:pages.regulatory_detail(_navigate),
        "Deals & Funding":lambda:pages.deals_funding(_navigate),
        "Deal Detail":lambda:pages.deal_detail(_navigate),
        "Patents":lambda:pages.patents(_navigate),
        "Patent Detail":lambda:pages.patent_detail(_navigate),
        "Settings":lambda:pages.customer_settings(principal),
    }
    routes.get(page,pages.overview)()
    st.markdown(f'<div class="pt-mono" style="margin-top:40px;border-top:1px solid rgba(151,168,205,.13);padding-top:14px">PharmaTune · PostgreSQL schema v{status.get("schema_version",0)} · Human validation required for every opportunity signal</div>',unsafe_allow_html=True)
