"""PharmaTune Customer / Analyst Platform — Checkpoint 6D-A."""
import streamlit as st

st.set_page_config(page_title="PharmaTune Intelligence Platform", page_icon="🧬", layout="wide", initial_sidebar_state="expanded")

from pharmatune_ui.app import run

run()
