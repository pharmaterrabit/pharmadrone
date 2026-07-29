"""Tavily web search — needs TAVILY_API_KEY. Company sites, pipeline pages, press
releases, multilingual trade-press discovery. Docs: https://docs.tavily.com/

Reliability note:
Tavily can reject some advanced search-engine operators (notably some `site:`
patterns) with non-standard 4xx responses. This connector therefore tries the
original query once, then a sanitised fallback query once. The run never blocks
on Tavily failure; failures are returned as ConnectorResult errors and surfaced
in Source Coverage.
"""
from __future__ import annotations

import httpx
import re

from ..pipeline.query_safety import sanitize_tavily_query
from .base import record, ConnectorResult, describe_error, USER_AGENT
from .. import settings

NAME = "Web (Tavily)"
URL = "https://api.tavily.com/search"
TIMEOUT_SECONDS = float(settings.env("TAVILY_TIMEOUT_SECONDS", "25") or "25")


def _sanitize_query(query: str) -> str:
    q = sanitize_tavily_query(query, max_chars=80)
    q = re.sub(r"https?://\S+|www\.\S+", " ", q, flags=re.I)
    q = re.sub(r"\b[\w.-]+\.(?:com|org|int|gov|uk)(?:/\S*)?\b", " ", q, flags=re.I)
    q = re.sub(r"\b(?:site|inurl|intitle):\S+", " ", q, flags=re.I)
    q = re.sub(r"[^\w\s-]", " ", q)
    return re.sub(r"\s+", " ", q).strip()[:80]


def _post_tavily(payload: dict) -> dict:
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = client.post(URL, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def _is_query_rejection(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {400, 422, 432}
    return False


def search(
    query: str,
    max_results: int = 6,
    cost=None,
    *,
    include_domains: list[str] | None = None,
) -> ConnectorResult:
    key = settings.env("TAVILY_API_KEY")
    if not key:
        return ConnectorResult(NAME, query, ok=False,
                               error="TAVILY_API_KEY missing — add it to .env")

    original_query = str(query or "").strip()
    sanitized_query = _sanitize_query(original_query)
    warnings: list[str] = []

    def payload_for(q: str) -> dict:
        payload = {
            "api_key": key,
            "query": q,
            "max_results": max_results,
            "search_depth": "advanced",
            "include_answer": False,
        }
        if include_domains:
            payload["include_domains"] = list(dict.fromkeys(include_domains))
        return payload

    attempts: list[tuple[str, list[str] | None]] = [(original_query, include_domains)]
    if include_domains:
        attempts.append((original_query, None))
    if sanitized_query and sanitized_query != original_query:
        attempts.append((sanitized_query, None))

    data = None
    used_query = original_query
    last_error = ""
    rejected = False
    for attempt_index, (attempt_query, attempt_domains) in enumerate(attempts):
        try:
            request_payload = payload_for(attempt_query)
            if not attempt_domains:
                request_payload.pop("include_domains", None)
            data = _post_tavily(request_payload)
            if data is not None:
                if attempt_index:
                    warnings.append("Tavily query was simplified after provider rejection.")
                used_query = attempt_query
                break
        except Exception as exc:
            last_error = describe_error(exc)
            rejected = _is_query_rejection(exc)
            if not rejected or attempt_index == len(attempts) - 1:
                break

    if data is None:
        return ConnectorResult(
            NAME,
            original_query,
            ok=False,
            error=last_error,
            warnings=warnings,
            stats={"rejected": rejected, "attempts": len(attempts)},
        )

    if cost is not None:
        cost.add_search(1, note=used_query[:60])

    out = [record("web", NAME, r.get("url", ""), r.get("title", ""),
                  r.get("url", ""), r.get("content", ""))
           for r in data.get("results", [])[:max_results]]
    for rec in out:
        rec["query_text"] = used_query
        if used_query != original_query:
            rec["original_query_text"] = original_query
            rec["query_sanitized"] = True

    return ConnectorResult(
        NAME,
        original_query,
        ok=True,
        count=len(out),
        records=out,
        warnings=warnings,
        stats={"rejected": False, "attempts": len(attempts)},
    )
