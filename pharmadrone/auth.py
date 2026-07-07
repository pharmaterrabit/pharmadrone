"""Password gate for the dashboard.

The password lives ONLY in the server-side env var APP_PASSWORD. It is compared
in Python on the server; it is never sent to the browser or embedded in any
frontend JavaScript. If APP_PASSWORD is unset, the app runs but warns loudly —
so a cloud deploy is never accidentally left open.
"""
from __future__ import annotations
import hmac
import os
import streamlit as st


def require_password() -> None:
    pw = os.getenv("APP_PASSWORD", "")

    if not pw:
        st.warning("⚠ No APP_PASSWORD set — this instance is UNPROTECTED. "
                   "Set APP_PASSWORD in your host's environment variables before "
                   "sharing the URL.")
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
