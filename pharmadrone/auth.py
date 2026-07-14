"""Server-side password gate and role resolution for PharmaTune.

The password lives ONLY in the server-side config value APP_PASSWORD (a real env
var on Render/Railway, or a Streamlit Community Cloud secret). It is compared in
Python on the server; it is never sent to the browser or embedded in any
frontend JavaScript. If APP_PASSWORD is unset, the app runs but warns loudly —
so a cloud deploy is never accidentally left open.
"""
from __future__ import annotations
import hmac
import streamlit as st
from . import settings


def _configured_credentials() -> list[dict[str, str]]:
    credentials = []
    for env_name, role, label, org_env in (
        ("PLATFORM_ADMIN_PASSWORD", "platform_admin", "Platform Administrator", ""),
        ("WORKSPACE_ADMIN_PASSWORD", "workspace_admin", "Workspace Administrator", "WORKSPACE_ADMIN_ORGANISATION_ID"),
        ("APP_PASSWORD", "analyst_reviewer", "Analyst / Reviewer", ""),
    ):
        password = settings.env(env_name, "")
        if password:
            credentials.append({
                "password": password, "role": role, "display_name": label,
                "organisation_id": settings.env(org_env, "") if org_env else "",
            })
    return credentials


def require_password() -> dict[str, str]:
    credentials = _configured_credentials()

    if not credentials:
        st.warning("⚠ No APP_PASSWORD set — this instance is UNPROTECTED. "
                   "Set APP_PASSWORD as an environment variable (Render/Railway) "
                   "or a Streamlit secret before sharing the URL.")
        return {"role": "analyst_reviewer", "display_name": "Unprotected local user", "organisation_id": ""}

    principal = st.session_state.get("auth_principal")
    if principal:
        return dict(principal)

    st.title("PharmaTune")
    st.caption("Private intelligence platform — sign in to continue.")
    entered = st.text_input("Password", type="password")
    if st.button("Enter"):
        matches = [item for item in credentials if hmac.compare_digest(entered, item["password"])]
        if len(matches) == 1:
            selected = {k: v for k, v in matches[0].items() if k != "password"}
            if selected["role"] == "workspace_admin" and not selected.get("organisation_id"):
                st.error("Workspace administrator access is not provisioned with an organisation ID.")
                st.stop()
            st.session_state["auth_principal"] = selected
            st.session_state["auth_ok"] = True
            st.rerun()
        elif len(matches) > 1:
            st.error("This access configuration is ambiguous. Administrator passwords must be distinct.")
        else:
            st.error("Incorrect password.")
    st.stop()


def sign_out() -> None:
    st.session_state.pop("auth_ok", None)
    st.session_state.pop("auth_principal", None)
    st.rerun()
