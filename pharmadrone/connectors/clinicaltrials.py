"""ClinicalTrials.gov API v2 — no key required.
Docs: https://clinicaltrials.gov/data-api/api

Two entry points:
  - search(term):            generic keyword search (kept for the generic path)
  - discover_stopped():      EVENT-FIRST discovery — filters by overallStatus
                             (TERMINATED/WITHDRAWN/SUSPENDED/NO_LONGER_AVAILABLE)
                             and only keeps trials that carry a whyStopped reason
                             or a concrete drug/sponsor, so we build candidates
                             from real stopped trials rather than generic hits.
"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error

NAME = "ClinicalTrials.gov"
BASE = "https://clinicaltrials.gov/api/v2/studies"

STOPPED_STATUSES = ["TERMINATED", "WITHDRAWN", "SUSPENDED", "NO_LONGER_AVAILABLE"]


def _row(study):
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
    overall = status.get("overallStatus", "")
    raw = (f"Title: {ident.get('briefTitle','')}. Sponsor: {sponsor.get('name','')}. "
           f"Status: {overall}. Phase: {phases}. "
           f"Conditions: {', '.join(cond.get('conditions', []))}. "
           f"Interventions: {', '.join(interventions)}."
           + (f" WhyStopped: {why_stopped}." if why_stopped else ""))
    event = None
    if overall in STOPPED_STATUSES:
        event = overall.lower()
    elif why_stopped:
        event = "stopped"
    return record("trial", NAME, nct, ident.get("briefTitle", ""),
                  f"https://clinicaltrials.gov/study/{nct}", raw,
                  entities={
                      "company": sponsor.get("name") or None,
                      "product": (interventions[0] if interventions else None),
                      "trial_id": nct,
                      "dosage_form": None,
                      "event_type": event,
                      "why_stopped": why_stopped or None,
                  }), why_stopped, overall, sponsor.get("name"), interventions


def search(term: str, max_results: int = 10) -> ConnectorResult:
    try:
        data = get_json(BASE, {"query.term": term,
                               "pageSize": min(max_results, 50), "format": "json"})
    except Exception as e:
        return ConnectorResult(NAME, term, ok=False, error=describe_error(e))
    out = [_row(s)[0] for s in data.get("studies", [])[:max_results]]
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)


def discover_stopped(term: str = "", max_results: int = 15,
                     statuses=None) -> ConnectorResult:
    """EVENT-FIRST: return trials with a stopped status. `term` optionally
    narrows by topic (e.g. 'bioavailability', 'drug supply'); leave blank to get
    all recently-stopped trials. Only trials with a whyStopped reason OR a named
    drug+sponsor become usable candidates downstream."""
    statuses = statuses or STOPPED_STATUSES
    params = {
        "filter.overallStatus": "|".join(statuses),
        "pageSize": min(max_results, 50),
        "format": "json",
        "sort": "LastUpdatePostDate:desc",
    }
    if term:
        params["query.term"] = term
    try:
        data = get_json(BASE, params)
    except Exception as e:
        return ConnectorResult(NAME, term or "stopped-trials", ok=False,
                               error=describe_error(e))
    out = []
    for s in data.get("studies", [])[:max_results]:
        rec, why, overall, sponsor_name, interventions = _row(s)
        # keep only trials that are genuinely a stopped-event with a target
        if overall in STOPPED_STATUSES and (why or sponsor_name or interventions):
            out.append(rec)
    return ConnectorResult(NAME, term or "stopped-trials", ok=True,
                           count=len(out), records=out)
