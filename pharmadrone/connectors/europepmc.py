"""Europe PMC REST — no key required. Covers PubMed metadata + OA links.
Docs: https://europepmc.org/RestfulWebService
"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error

NAME = "Europe PMC"
BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _authors(row: dict) -> list[dict]:
    output = []
    for author in (row.get("authorList") or {}).get("author", []) or []:
        affiliations = []
        for detail in (author.get("authorAffiliationDetailsList") or {}).get("authorAffiliation", []) or []:
            value = str(detail.get("affiliation") or "").strip()
            if value and value not in affiliations:
                affiliations.append(value)
        identifiers = (author.get("authorIdList") or {}).get("authorId", []) or []
        orcid = next((str(item.get("value") or "") for item in identifiers if str(item.get("type") or "").upper() == "ORCID"), "")
        output.append({
            "name": str(author.get("fullName") or "").strip(), "orcid": orcid,
            "affiliations": affiliations,
        })
    return [item for item in output if item["name"]]


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
        journal = r.get("journalInfo") or {}
        out.append(record("paper", NAME, rid, r.get("title", ""), url, raw, entities={
            "doi": doi, "pmid": pmid, "pmcid": pmcid, "publication_title": r.get("title", ""),
            "journal": r.get("journalTitle") or (journal.get("journal") or {}).get("title") or "",
            "publication_year": r.get("pubYear") or "", "publication_date": r.get("firstPublicationDate") or "",
            "abstract": r.get("abstractText") or "", "authors": _authors(r),
            "publication_type": ", ".join((r.get("pubTypeList") or {}).get("pubType", []) or []),
            "open_access": str(r.get("isOpenAccess") or "").upper() == "Y",
            "citation_count": int(r.get("citedByCount") or 0),
        }))
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)
