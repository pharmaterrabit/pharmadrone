"""Application shell and role-safe Customer / Analyst navigation."""
from __future__ import annotations

import streamlit as st

from pharmadrone import auth, db
from pharmadrone.storage import DatabaseConfigurationError, DatabaseUnavailableError
from . import pages, theme

NAV = {
    "DISCOVER":["Overview","Opportunity Explorer","Companies","Products","Technologies"],
    "INTELLIGENCE":["Research & Innovation","Regulatory Signals","Deals & Funding","Patents"],
    "WORKFLOW":["Human Validation","Case Studies"],
    "PLATFORM":["Data Sources","System Health","Settings"],
}

HIDDEN_ROUTE_PARENT = {"Opportunity Detail": "Opportunity Explorer"}


@st.cache_data(ttl=30, show_spinner=False)
def _database_status() -> dict:
    """Bound shell health checks so navigation does not re-query Neon twice."""
    return db.database_status()


def _navigate(page: str) -> None:
    st.session_state["page"] = page
    st.rerun()


def _sidebar() -> str:
    with st.sidebar:
        st.markdown('<div class="pt-brand"><div class="pt-mark">P</div><div><b>PharmaTune</b><small>Intelligence Platform</small></div></div>',unsafe_allow_html=True)
        current=st.session_state.get("page","Overview")
        options=[p for group in NAV.values() for p in group]
        visible_current=current if current in options else HIDDEN_ROUTE_PARENT.get(current,"Overview")
        selected=st.radio("Navigation",options,index=options.index(visible_current),label_visibility="collapsed")
        st.markdown("---")
        st.markdown(theme.badge("Analyst workspace","blue"),unsafe_allow_html=True)
        st.caption("Evidence-backed opportunity signals. Human validation required.")
        return selected


def run(principal: dict | None = None) -> None:
    theme.inject(); principal = principal or auth.require_password()
    if principal.get("role") not in {"analyst_reviewer", "read_only_executive"}:
        st.error("This account is assigned to an administration workspace.")
        st.stop()
    try:
        status=_database_status()
    except (DatabaseConfigurationError,DatabaseUnavailableError,RuntimeError) as exc:
        st.error("PharmaTune cannot connect to its durable database. Production remains closed rather than using a disposable fallback.")
        st.caption(str(exc)); st.stop()
    selected=_sidebar()
    current=st.session_state.get("page","Overview")
    if HIDDEN_ROUTE_PARENT.get(current) == selected:
        page=current
    else:
        if selected!=current:
            st.session_state["page"]=selected
        page=st.session_state.get("page","Overview")
    routes={
        "Overview":pages.overview,
        "Opportunity Explorer":lambda:pages.explorer(_navigate),
        "Opportunity Detail":lambda:pages.opportunity_detail(_navigate),
        "Companies":lambda:pages.entity_page("Companies","Organisations represented in the current live opportunity index.","company"),
        "Products":lambda:pages.entity_page("Products","Products represented in the current live opportunity index.","product"),
        "Technologies":pages.technology_profile,
        "Human Validation":pages.validation,
        "Case Studies":lambda:pages.case_studies(principal,_navigate),
        "Data Sources":pages.sources,
        "System Health":pages.health,
        "Research & Innovation":lambda:pages.placeholder("Research & Innovation","Future university, research-group and technology-transfer intelligence."),
        "Regulatory Signals":lambda:pages.placeholder("Regulatory Signals","Future evidence-aware global regulatory intelligence beyond current FDA coverage."),
        "Deals & Funding":lambda:pages.placeholder("Deals & Funding","Future company, partnership, financing and commercial-signal intelligence."),
        "Patents":lambda:pages.placeholder("Patents","Future patent-family, ownership and technology-position intelligence."),
        "Settings":lambda:pages.placeholder("Settings","Workspace preferences will be introduced with authenticated workspace administration."),
    }
    routes.get(page,pages.overview)()
    st.markdown(f'<div class="pt-mono" style="margin-top:40px;border-top:1px solid rgba(151,168,205,.13);padding-top:14px">PharmaTune · PostgreSQL schema v{status.get("schema_version",0)} · Human validation required for every opportunity signal</div>',unsafe_allow_html=True)
