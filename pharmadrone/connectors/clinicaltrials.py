"""ClinicalTrials.gov API v2 — no key required.
Docs: https://clinicaltrials.gov/data-api/api
"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error

NAME = "ClinicalTrials.gov"
BASE = "https://clinicaltrials.gov/api/v2/studies"


def search(term: str, max_results: int = 10) -> ConnectorResult:
    try:
        data = get_json(BASE, {"query.term": term,
                               "pageSize": min(max_results, 50), "format": "json"})
    except Exception as e:
        return ConnectorResult(NAME, term, ok=False, error=describe_error(e))
    out = []
    for study in data.get("studies", [])[:max_results]:
        ps = study.get("protocolSection", {})
        ident = ps.get("identificationModule", {})
        status = ps.get("statusModule", {})
        design = ps.get("designModule", {})
        cond = ps.get("conditionsModule", {})
        sponsor = ps.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {})
        interventions = [i.get("name", "") for i in
                         ps.get("armsInterventionsModule", {}).get("interventions", [])]
        nct = ident.get("nctId", "")
        phases = ", ".join(design.get("phases", []) or [])
        why_stopped = status.get("whyStopped", "")
        raw = (f"Title: {ident.get('briefTitle','')}. Sponsor: {sponsor.get('name','')}. "
               f"Status: {status.get('overallStatus','')}. Phase: {phases}. "
               f"Conditions: {', '.join(cond.get('conditions', []))}. "
               f"Interventions: {', '.join(interventions)}."
               + (f" WhyStopped: {why_stopped}." if why_stopped else ""))
        out.append(record("trial", NAME, nct, ident.get("briefTitle", ""),
                          f"https://clinicaltrials.gov/study/{nct}", raw))
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)
