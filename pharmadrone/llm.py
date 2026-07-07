"""Configurable LLM access.

Provider is chosen with env vars (no provider is mandatory):

    LLM_PROVIDER = openrouter | groq | openai | gemini   (default: openrouter)
    LLM_MODEL    = <model string for that provider>       (optional; sensible default)

Keys (only the one for the SELECTED provider is needed):
    OPENROUTER_API_KEY, GROQ_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY

OpenRouter / Groq / OpenAI share the OpenAI-compatible chat-completions API, so
they use one code path. Gemini uses its own endpoint. Every call reports token
usage to the CostTracker.
"""
from __future__ import annotations
import json
import os
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential
from . import settings

# name -> (endpoint, key env var, default cheap model, openai_compatible?)
PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions",
                   "OPENROUTER_API_KEY",
                   "meta-llama/llama-3.3-70b-instruct:free", True),
    "groq": ("https://api.groq.com/openai/v1/chat/completions",
             "GROQ_API_KEY", "llama-3.1-8b-instant", True),
    "openai": ("https://api.openai.com/v1/chat/completions",
               "OPENAI_API_KEY", "gpt-4o-mini", True),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/models/"
               "{model}:generateContent", "GEMINI_API_KEY",
               "gemini-2.0-flash", False),
}


class LLMError(RuntimeError):
    pass


def active_provider() -> str:
    return settings.env("LLM_PROVIDER", "openrouter").strip().lower() or "openrouter"


def active_model(provider: str | None = None) -> str:
    provider = provider or active_provider()
    default = PROVIDERS.get(provider, (None, None, "", None))[2]
    return settings.env("LLM_MODEL", default) or default


def _key_name(provider: str) -> str:
    return PROVIDERS[provider][1]


def check_config() -> tuple[bool, str]:
    """(ok, message). Fails clearly if the selected provider key is missing."""
    provider = active_provider()
    if provider not in PROVIDERS:
        return False, (f"LLM_PROVIDER='{provider}' is not supported. "
                       f"Choose one of: {', '.join(PROVIDERS)}.")
    key_name = _key_name(provider)
    if not settings.env(key_name):
        return False, (f"LLM_PROVIDER is '{provider}' but {key_name} is not set. "
                       f"Add {key_name} to your environment, or switch LLM_PROVIDER.")
    return True, f"Provider '{provider}' · model '{active_model(provider)}' · key set."


# --------------------------------------------------------------------------
@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
def _openai_compatible(url, key, model, prompt, cost, provider, temperature) -> str:
    headers = {"Authorization": f"Bearer {key}"}
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://pharmadrone.local"
        headers["X-Title"] = "PharmaDrone"
    payload = {"model": model, "temperature": temperature,
               "messages": [{"role": "user", "content": prompt}]}
    with httpx.Client(timeout=120) as c:
        r = c.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    usage = data.get("usage", {}) or {}
    if cost is not None:
        cost.add_llm(provider, usage.get("prompt_tokens", 0),
                     usage.get("completion_tokens", 0))
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise LLMError(f"Unexpected {provider} response: {json.dumps(data)[:400]}")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
def _gemini(url_tpl, key, model, prompt, cost, temperature) -> str:
    url = url_tpl.format(model=model)
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"temperature": temperature,
                                    "maxOutputTokens": 4096}}
    with httpx.Client(timeout=120) as c:
        r = c.post(url, params={"key": key}, json=payload)
        r.raise_for_status()
        data = r.json()
    usage = data.get("usageMetadata", {}) or {}
    if cost is not None:
        cost.add_llm("gemini", usage.get("promptTokenCount", 0),
                     usage.get("candidatesTokenCount", 0))
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise LLMError(f"Unexpected Gemini response: {json.dumps(data)[:400]}")


def complete(prompt: str, cost=None, temperature: float = 0.2) -> str:
    """Send a prompt to the configured provider. Raises LLMError if misconfigured."""
    ok, msg = check_config()
    if not ok:
        raise LLMError(msg)
    provider = active_provider()
    url, key_name, _default, oai = PROVIDERS[provider]
    key = settings.env(key_name)
    model = active_model(provider)
    if oai:
        return _openai_compatible(url, key, model, prompt, cost, provider, temperature)
    return _gemini(url, key, model, prompt, cost, temperature)


def complete_json(prompt: str, cost=None):
    """Force-parse a JSON reply. Strips markdown fences if present."""
    raw = complete(prompt + "\n\nRespond ONLY with valid JSON. No prose, no markdown.",
                   cost, temperature=0.1)
    txt = raw.strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1]
        txt = txt[4:] if txt.lower().startswith("json") else txt
        txt = txt.rsplit("```", 1)[0] if "```" in txt else txt
    txt = txt.strip()
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        for oc, cc in (("{", "}"), ("[", "]")):
            i, j = txt.find(oc), txt.rfind(cc)
            if i != -1 and j != -1:
                try:
                    return json.loads(txt[i:j + 1])
                except json.JSONDecodeError:
                    continue
        raise LLMError(f"Could not parse JSON from LLM: {raw[:300]}")
