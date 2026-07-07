"""Crossref works search — no key required.  Docs: https://api.crossref.org/"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error
from .. import settings

NAME = "Crossref"
BASE = "https://api.crossref.org/works"


def search(term: str, max_results: int = 10) -> ConnectorResult:
    params = {"query": term, "rows": min(max_results, 25)}
    if settings.env("CONTACT_EMAIL"):
        params["mailto"] = settings.env("CONTACT_EMAIL")
    try:
        data = get_json(BASE, params)
    except Exception as e:
        return ConnectorResult(NAME, term, ok=False, error=describe_error(e))
    out = []
    for it in data.get("message", {}).get("items", [])[:max_results]:
        title = " ".join(it.get("title", []) or [])
        journal = " ".join(it.get("container-title", []) or [])
        doi = it.get("DOI", "")
        raw = f"{title}. {journal}. {it.get('abstract','')[:1500]}"
        out.append(record("paper", NAME, doi, title,
                          f"https://doi.org/{doi}" if doi else "", raw))
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)
