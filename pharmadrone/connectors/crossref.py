"""Crossref works search — no key required.  Docs: https://api.crossref.org/"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error
from .. import settings

NAME = "Crossref"
BASE = "https://api.crossref.org/works"


def _date(parts: dict) -> str:
    values = (parts or {}).get("date-parts") or []
    if not values:
        return ""
    return "-".join(str(value).zfill(2) for value in values[0])


def _authors(item: dict) -> list[dict]:
    output = []
    for author in item.get("author", []) or []:
        name = " ".join(x for x in (author.get("given"), author.get("family")) if x).strip()
        output.append({
            "name": name, "orcid": str(author.get("ORCID") or "").replace("https://orcid.org/", ""),
            "affiliations": [str(row.get("name") or "").strip() for row in author.get("affiliation", []) or [] if str(row.get("name") or "").strip()],
        })
    return [row for row in output if row["name"]]


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
                          f"https://doi.org/{doi}" if doi else "", raw, entities={
                              "doi": doi, "publication_title": title, "journal": journal,
                              "publication_date": _date(it.get("published-print") or it.get("published-online") or it.get("issued") or {}),
                              "publication_year": ((_date(it.get("published-print") or it.get("published-online") or it.get("issued") or {})).split("-") or [""])[0],
                              "publication_type": it.get("type") or "", "publisher": it.get("publisher") or "",
                              "abstract": it.get("abstract") or "", "citation_count": int(it.get("is-referenced-by-count") or 0),
                              "authors": _authors(it),
                          }))
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)
