"""Shared helpers for connectors.

Every connector's `search()` returns a ConnectorResult so that failures are
VISIBLE (never silently swallowed). The pipeline collects these into a per-source
coverage summary and surfaces any error text in the dashboard and CLI.

Each evidence record has this shape:

    {
      "source_type": "trial|label|paper|patent|web|company",
      "source_name": "ClinicalTrials.gov",
      "record_id":  "NCT01234567",
      "title":      "...",
      "url":        "https://...",
      "language":   "en",
      "raw_text":   "abstract / snippet used for LLM extraction",
      "date_accessed": "2026-07-07",
    }

Anti-hallucination: connectors only pass through what the API returns. If a
field is absent it stays empty — never guessed.
"""
from __future__ import annotations
import datetime
from dataclasses import dataclass, field
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

USER_AGENT = "PharmaDrone/1.0 (BD research; contact set in .env)"


def _retryable_http_error(exc: Exception) -> bool:
    """Retry transient connector failures only; do not hammer APIs on 4xx rejections."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError))


@dataclass
class ConnectorResult:
    """Outcome of one connector call. `ok=False` means the source FAILED."""
    source: str
    query: str
    ok: bool
    count: int = 0
    error: str | None = None
    records: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def today() -> str:
    return datetime.date.today().isoformat()


def describe_error(e: Exception) -> str:
    """Turn an exception into a short, human-readable reason."""
    import httpx as _h
    if isinstance(e, _h.HTTPStatusError):
        code = e.response.status_code
        hints = {
            401: "unauthorized — check the API key in .env",
            403: "forbidden — key missing/invalid or rate-limited",
            404: "endpoint not found — the API URL may have changed",
            429: "rate limit hit — slow down or reduce queries",
            432: "query rejected by API — try a shorter/sanitised query",
        }
        return f"HTTP {code}: {hints.get(code, 'server rejected the request')}"
    if isinstance(e, _h.TimeoutException):
        return "timeout — source did not respond (check your connection)"
    if isinstance(e, _h.ConnectError):
        return "connection failed — no internet or the host is unreachable"
    if isinstance(e, ValueError):  # includes JSONDecodeError
        return "response was not valid JSON (endpoint may have changed)"
    return f"{type(e).__name__}: {e}"


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), retry=retry_if_exception(_retryable_http_error), reraise=True)
def get_json(url: str, params: dict | None = None, headers: dict | None = None) -> dict:
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        r = c.get(url, params=params or {}, headers=h)
        r.raise_for_status()
        return r.json()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), retry=retry_if_exception(_retryable_http_error), reraise=True)
def post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    h = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    with httpx.Client(timeout=60, follow_redirects=True) as c:
        r = c.post(url, json=payload, headers=h)
        r.raise_for_status()
        return r.json()


def record(source_type, source_name, record_id, title, url, raw_text,
           language="en", source_category=None, entities=None):
    """entities: optional dict of deterministic fields the API already gave us
    (company, product, trial_id, dosage_form, event_type) — used by the
    candidate-discovery step so opportunity candidates don't depend solely on
    the LLM successfully parsing free text.
    """
    return {
        "source_type": source_type,
        "source_category": source_category or _CATEGORY.get(source_type, "news"),
        "source_name": source_name,
        "record_id": record_id or "",
        "title": (title or "").strip(),
        "url": url or "",
        "language": language,
        "raw_text": (raw_text or "").strip()[:4000],
        "date_accessed": today(),
        "entities": entities or {},
    }


# Coarse category mapping (the failure layer refines web hits by domain).
_CATEGORY = {
    "recall": "regulatory", "enforcement": "regulatory", "label": "regulatory",
    "trial": "trial", "paper": "publication", "web": "news",
    "company": "company", "conference": "conference", "patent": "patent",
}
