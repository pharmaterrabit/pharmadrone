"""Central config: loads .env and the Technology Profile YAML."""
from __future__ import annotations
import os
from pathlib import Path
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "technology_profile.yaml"
REPORTS_DIR = ROOT / "reports"
DB_PATH = ROOT / "pharmadrone.db"

load_dotenv(ROOT / ".env")


def env(key: str, default: str = "") -> str:
    """Read a config value from, in order: real environment variables (Render,
    Railway, your local .env via python-dotenv), then Streamlit Community
    Cloud's st.secrets (its canonical secrets mechanism — not guaranteed to be
    mirrored into os.environ on every Streamlit version), then the default.
    """
    val = os.getenv(key)
    if val:
        return val
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return default


def load_profile() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_profile(profile: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(profile, f, sort_keys=False, allow_unicode=True)


def active_regions(profile: dict) -> list[dict]:
    return [r for r in profile.get("regions", []) if r.get("active")]


def enabled_sources(profile: dict) -> list[str]:
    return [k for k, v in profile.get("sources", {}).items() if v.get("enabled")]


# Which env var holds each provider's key
PROVIDER_KEY_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def llm_provider() -> str:
    return (env("LLM_PROVIDER", "openrouter") or "openrouter").lower()


def llm_key_env() -> str | None:
    return PROVIDER_KEY_ENV.get(llm_provider())


def llm_key_present() -> bool:
    e = llm_key_env()
    return bool(env(e)) if e else False


def llm_status() -> dict:
    """For the dashboard: provider, model, whether its key is present."""
    p = llm_provider()
    m = env("LLM_MODEL", "") or "(provider default)"
    return {"provider": p, "model": m, "key_env": llm_key_env(),
            "key_present": llm_key_present(),
            "valid_provider": p in PROVIDER_KEY_ENV}


HAS_TAVILY = bool(env("TAVILY_API_KEY"))
