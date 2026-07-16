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


def _authorships(work: dict) -> tuple[list[dict], list[dict]]:
    authors = []
    institution_map: dict[str, dict] = {}
    for authorship in work.get("authorships", []) or []:
        author = authorship.get("author") or {}
        linked = []
        for institution in authorship.get("institutions", []) or []:
            name = str(institution.get("display_name") or "").strip()
            if not name:
                continue
            key = str(institution.get("id") or institution.get("ror") or name).casefold()
            institution_map[key] = {
                "name": name, "openalex_id": institution.get("id") or "", "ror_id": institution.get("ror") or "",
                "country_code": institution.get("country_code") or "", "organisation_type": institution.get("type") or "",
                "official_url": institution.get("homepage_url") or "",
            }
            linked.append(key)
        authors.append({
            "name": author.get("display_name") or "", "openalex_id": author.get("id") or "",
            "orcid": author.get("orcid") or "", "institution_keys": linked,
        })
    return [item for item in authors if item["name"]], list(institution_map.values())


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
        authors, institutions = _authorships(w)
        location = w.get("primary_location") or {}
        source = location.get("source") or {}
        open_access = w.get("open_access") or {}
        out.append(record("paper", NAME, doi or w.get("id", ""), w.get("title", ""),
                          w.get("doi") or w.get("id", ""), raw, entities={
                              "doi": doi, "openalex_id": w.get("id") or "", "publication_title": w.get("title") or "",
                              "publication_year": w.get("publication_year") or "", "publication_date": w.get("publication_date") or "",
                              "publication_type": w.get("type") or "", "journal": source.get("display_name") or "",
                              "abstract": abstract, "citation_count": int(w.get("cited_by_count") or 0),
                              "open_access": bool(open_access.get("is_oa")), "open_access_status": open_access.get("oa_status") or "",
                              "authors": authors, "institutions": institutions,
                              "grants": [{
                                  "funder": (grant.get("funder_display_name") or ""),
                                  "funder_id": (grant.get("funder") or ""),
                                  "award_id": (grant.get("award_id") or ""),
                              } for grant in (w.get("grants", []) or []) if grant.get("funder_display_name") or grant.get("award_id")],
                          }))
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)
