"""Tavily web search — needs TAVILY_API_KEY. Company sites, pipeline pages, press
releases, multilingual trade-press discovery.  Docs: https://docs.tavily.com/
"""
from __future__ import annotations
from .base import post_json, record, ConnectorResult, describe_error
from .. import settings

NAME = "Web (Tavily)"
URL = "https://api.tavily.com/search"


def search(query: str, max_results: int = 6, cost=None) -> ConnectorResult:
    key = settings.env("TAVILY_API_KEY")
    if not key:
        return ConnectorResult(NAME, query, ok=False,
                               error="TAVILY_API_KEY missing — add it to .env")
    payload = {"api_key": key, "query": query, "max_results": max_results,
               "search_depth": "advanced", "include_answer": False}
    try:
        data = post_json(URL, payload)
    except Exception as e:
        return ConnectorResult(NAME, query, ok=False, error=describe_error(e))
    if cost is not None:
        cost.add_search(1, note=query[:60])
    out = [record("web", NAME, r.get("url", ""), r.get("title", ""),
                  r.get("url", ""), r.get("content", ""))
           for r in data.get("results", [])[:max_results]]
    return ConnectorResult(NAME, query, ok=True, count=len(out), records=out)
