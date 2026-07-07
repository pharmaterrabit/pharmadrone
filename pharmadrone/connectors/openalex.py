"""OpenAlex works search — no key required.  Docs: https://docs.openalex.org/"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error
from .. import settings

NAME = "OpenAlex"
BASE = "https://api.openalex.org/works"


def _reconstruct_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    positions = [(i, w) for w, idxs in inv.items() for i in idxs]
    positions.sort()
    return " ".join(w for _, w in positions)[:2000]


def search(term: str, max_results: int = 10) -> ConnectorResult:
    params = {"search": term, "per-page": min(max_results, 25)}
    if settings.env("CONTACT_EMAIL"):
        params["mailto"] = settings.env("CONTACT_EMAIL")
    try:
        data = get_json(BASE, params)
    except Exception as e:
        return ConnectorResult(NAME, term, ok=False, error=describe_error(e))
    out = []
    for w in data.get("results", [])[:max_results]:
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        abstract = _reconstruct_abstract(w.get("abstract_inverted_index"))
        raw = f"{w.get('title','')} ({w.get('publication_year','')}). {abstract}"
        out.append(record("paper", NAME, doi or w.get("id", ""), w.get("title", ""),
                          w.get("doi") or w.get("id", ""), raw))
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)
