"""Streamlit host page for the standalone PharmaDrone AI workspace."""
from __future__ import annotations

from urllib.parse import urlparse

import streamlit as st
import streamlit.components.v1 as components

from pharmadrone import settings

from . import theme


EMBED_HEIGHT = 960


def configured_app_url() -> str:
    """Return only an explicitly configured HTTP(S) standalone-app URL."""
    value = settings.env("PHARMADRONE_AI_URL", "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return value


def render_page() -> None:
    theme.page_header(
        "PharmaDrone AI",
        "Evidence-grounded pharmaceutical business-development assistant, embedded in PharmaTune.",
        "AI Workspace",
    )
    app_url = configured_app_url()
    if not app_url:
        st.warning(
            "PharmaDrone AI is not configured for this deployment. "
            "Set PHARMADRONE_AI_URL to the running standalone application URL."
        )
        return
    st.caption(
        "The embedded workspace uses its own PharmaDrone AI account. "
        "Register once inside the panel; no default password is created."
    )
    st.link_button("Open PharmaDrone AI in a new tab", app_url)
    components.iframe(app_url, height=EMBED_HEIGHT, scrolling=True)
