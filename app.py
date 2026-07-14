"""PharmaTune role-gated product entry point — Checkpoint 6D-B."""
import streamlit as st

st.set_page_config(page_title="PharmaTune Intelligence Platform", page_icon="🧬", layout="wide", initial_sidebar_state="expanded")

from pharmadrone import admin, auth

principal = auth.require_password()
if principal.get("role") in {admin.PLATFORM_ADMIN, admin.WORKSPACE_ADMIN}:
    from pharmatune_admin.app import run
else:
    from pharmatune_ui.app import run

run(principal)
"""PharmaTune role-gated product entry point — Checkpoint 6D-B."""
import streamlit as st

st.set_page_config(page_title="PharmaTune Intelligence Platform", page_icon="🧬", layout="wide", initial_sidebar_state="expanded")

from pharmadrone import admin, auth

principal = auth.require_password()
if principal.get("role") in {admin.PLATFORM_ADMIN, admin.WORKSPACE_ADMIN}:
    from pharmatune_admin.app import run
else:
    from pharmatune_ui.app import run

run(principal)
