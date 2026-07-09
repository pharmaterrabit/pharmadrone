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

import re
import httpx

from .base import record, ConnectorResult, describe_error, USER_AGENT
from .. import settings

NAME = "Web (Tavily)"
URL = "https://api.tavily.com/search"
TIMEOUT_SECONDS = float(settings.env("TAVILY_TIMEOUT_SECONDS", "25") or "25")


def _sanitize_query(query: str) -> str:
    """Reduce search-engine syntax that Tavily may reject.

    Examples:
    - `site:fda.gov recall dissolution tablet` -> `fda.gov recall dissolution tablet`
    - quoted firm/product strings -> unquoted strings
    - `OR` operators / parentheses -> plain terms
    """
    q = str(query or "").strip()
    q = re.sub(r"\bsite:\s*", "", q, flags=re.I)
    q = q.replace('"', " ").replace("'", " ")
    q = re.sub(r"[(){}\[\]]", " ", q)
    q = re.sub(r"\bOR\b|\bAND\b", " ", q, flags=re.I)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:220]


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


def search(query: str, max_results: int = 6, cost=None) -> ConnectorResult:
    key = settings.env("TAVILY_API_KEY")
    if not key:
        return ConnectorResult(NAME, query, ok=False,
                               error="TAVILY_API_KEY missing — add it to .env")

    original_query = str(query or "").strip()
    sanitized_query = _sanitize_query(original_query)
    warnings: list[str] = []

    def payload_for(q: str) -> dict:
        return {
            "api_key": key,
            "query": q,
            "max_results": max_results,
            "search_depth": "advanced",
            "include_answer": False,
        }

    try:
        data = _post_tavily(payload_for(original_query))
        used_query = original_query
    except Exception as first_error:
        first_reason = describe_error(first_error)
        # Tavily sometimes rejects search-engine operators such as `site:`. For
        # that specific class of failure, retry once with a shorter/sanitised
        # query. Do not retry arbitrary 4xx indefinitely.
        if sanitized_query and sanitized_query != original_query and _is_query_rejection(first_error):
            warnings.append(
                f"Tavily rejected original query {original_query!r}: {first_reason}. "
                f"Retried with sanitised query {sanitized_query!r}."
            )
            try:
                data = _post_tavily(payload_for(sanitized_query))
                used_query = sanitized_query
            except Exception as second_error:
                second_reason = describe_error(second_error)
                return ConnectorResult(
                    NAME,
                    original_query,
                    ok=False,
                    error=(
                        f"Tavily rejected/failed query. Original {original_query!r}: {first_reason}; "
                        f"sanitised {sanitized_query!r}: {second_reason}"
                    ),
                    warnings=warnings,
                )
        else:
            return ConnectorResult(NAME, original_query, ok=False, error=first_reason)

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
    )
