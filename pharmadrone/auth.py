"""Password gate for the dashboard.

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


def require_password() -> None:
    pw = settings.env("APP_PASSWORD", "")

    if not pw:
        st.warning("⚠ No APP_PASSWORD set — this instance is UNPROTECTED. "
                   "Set APP_PASSWORD as an environment variable (Render/Railway) "
                   "or a Streamlit secret before sharing the URL.")
        return

    if st.session_state.get("auth_ok"):
        return

    st.title("PharmaDrone")
    st.caption("Private intelligence dashboard — sign in to continue.")
    entered = st.text_input("Password", type="password")
    if st.button("Enter"):
        if hmac.compare_digest(entered, pw):
            st.session_state["auth_ok"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()
