"""Europe PMC REST — no key required. Covers PubMed metadata + OA links.
Docs: https://europepmc.org/RestfulWebService
"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error

NAME = "Europe PMC"
BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def search(term: str, max_results: int = 10) -> ConnectorResult:
    try:
        data = get_json(BASE, {"query": term, "format": "json",
                               "pageSize": min(max_results, 25), "resultType": "core"})
    except Exception as e:
        return ConnectorResult(NAME, term, ok=False, error=describe_error(e))
    out = []
    for r in data.get("resultList", {}).get("result", [])[:max_results]:
        pmid, pmcid, doi = r.get("pmid", ""), r.get("pmcid", ""), r.get("doi", "")
        rid = pmcid or pmid or doi
        url = (f"https://europepmc.org/article/{r.get('source','MED')}/{pmid}" if pmid
               else (f"https://doi.org/{doi}" if doi else ""))
        raw = (f"{r.get('title','')}. {r.get('journalTitle','')} "
               f"{r.get('pubYear','')}. {r.get('abstractText','')}")
        out.append(record("paper", NAME, rid, r.get("title", ""), url, raw))
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)
