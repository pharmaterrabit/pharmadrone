"""ClinicalTrials.gov API v2 — no key required.

Checkpoint 5A adds bounded token pagination and strict medicinal-intervention
quality gates. Trial status remains a registry fact; it is not treated as a
product failure unless the registry's stated reason directly supports a
formulation/product problem.
"""
from __future__ import annotations

import re
from .base import get_json, record, ConnectorResult, describe_error

NAME = "ClinicalTrials.gov"
BASE = "https://clinicaltrials.gov/api/v2/studies"
STOPPED_STATUSES = ["TERMINATED", "WITHDRAWN", "SUSPENDED", "NO_LONGER_AVAILABLE"]

_ALLOWED_INTERVENTION_TYPES = {"DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT"}
_SPECIMEN_OR_DIAGNOSTIC_TERMS = {
    "blood sample", "serum", "plasma", "tissue", "biopsy", "biospecimen",
    "specimen collection", "diagnostic test", "blood draw", "sample collection",
}
_CONTROL_ONLY_EXACT = {
    "placebo", "matching placebo", "placebo comparator", "standard of care",
    "no intervention", "observation", "usual care",
}
_PRODUCT_PROBLEM_TERMS = (
    "formulation", "bioavailability", "bioequivalence", "solubility", "dissolution",
    "pharmacokinetic", "food effect", "drug delivery", "modified release",
    "extended release", "delayed release", "stability", "precipitation",
    "manufacturing", "drug supply", "supply issue", "product quality",
    "dose delivery", "absorption",
)


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(x) for x in value if x is not None)
    return " ".join(str(value).split()).strip()


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _is_control_only(name: str) -> bool:
    text = _norm(name)
    if not text:
        return True
    if text in _CONTROL_ONLY_EXACT:
        return True
    if any(term in text for term in _SPECIMEN_OR_DIAGNOSTIC_TERMS):
        return True
    # "placebo alone" is excluded, while a named drug-plus-placebo intervention
    # is not discarded merely because the descriptor contains the word placebo.
    if text.startswith("placebo ") or text.endswith(" placebo"):
        non_control = text.replace("placebo", "").replace("comparator", "").strip(" +-/")
        return not non_control
    return False


def _usable_interventions(study: dict) -> list[dict]:
    ps = study.get("protocolSection", {}) or {}
    raw = (ps.get("armsInterventionsModule", {}) or {}).get("interventions", []) or []
    usable = []
    for item in raw:
        name = _clean(item.get("name"))
        kind = _clean(item.get("type")).upper()
        if not name or _is_control_only(name):
            continue
        if kind not in _ALLOWED_INTERVENTION_TYPES:
            continue
        usable.append({"name": name, "type": kind, "description": _clean(item.get("description"))})
    # Prefer medicinal DRUG records while retaining legitimate biological/
    # combination-product studies when no drug intervention exists.
    drugs = [x for x in usable if x["type"] == "DRUG"]
    return drugs or usable


def _trial_problem(why_stopped: str) -> str | None:
    text = _norm(why_stopped)
    if not text:
        return None
    for term in _PRODUCT_PROBLEM_TERMS:
        if term in text:
            return term
    return None


def _row(study: dict):
    ps = study.get("protocolSection", {}) or {}
    ident = ps.get("identificationModule", {}) or {}
    status = ps.get("statusModule", {}) or {}
    design = ps.get("designModule", {}) or {}
    conditions = ps.get("conditionsModule", {}) or {}
    sponsor_mod = ps.get("sponsorCollaboratorsModule", {}) or {}
    sponsor = sponsor_mod.get("leadSponsor", {}) or {}
    locations = (ps.get("contactsLocationsModule", {}) or {}).get("locations", []) or []

    nct = _clean(ident.get("nctId"))
    title = _clean(ident.get("briefTitle"))
    study_type = _clean(design.get("studyType") or ident.get("studyType")).upper()
    overall = _clean(status.get("overallStatus")).upper()
    why_stopped = _clean(status.get("whyStopped"))
    sponsor_name = _clean(sponsor.get("name"))
    phases = [_clean(x) for x in (design.get("phases", []) or []) if _clean(x)]
    intervention_rows = _usable_interventions(study)
    intervention_names = [x["name"] for x in intervention_rows]
    intervention_types = [x["type"] for x in intervention_rows]
    product = intervention_names[0] if intervention_names else ""
    problem = _trial_problem(why_stopped)
    countries = []
    for loc in locations:
        country = _clean((loc or {}).get("country"))
        if country and country not in countries:
            countries.append(country)

    raw_text = (
        f"Title: {title}. Sponsor: {sponsor_name}. Study type: {study_type}. "
        f"Status: {overall}. Phase: {', '.join(phases)}. "
        f"Conditions: {_clean(conditions.get('conditions'))}. "
        f"Interventions: {', '.join(intervention_names)}."
        + (f" WhyStopped: {why_stopped}." if why_stopped else " Stated stop reason: not available.")
    )
    event_type = overall.lower() if overall in STOPPED_STATUSES else ("stopped" if why_stopped else None)
    rec = record(
        "trial", NAME, nct, title, f"https://clinicaltrials.gov/study/{nct}", raw_text,
        source_category="trial",
        entities={
            "company": sponsor_name or None,
            "product": product or None,
            "trial_id": nct or None,
            "nct_id": nct or None,
            "source_event_id": nct or None,
            "study_type": study_type or None,
            "overall_status": overall or None,
            "event_type": event_type,
            "why_stopped": why_stopped or None,
            "stated_reason_available": bool(why_stopped),
            "product_problem_supported": bool(problem),
            "direct_problem_evidence": bool(problem),
            "issue_category": problem,
            "intervention_type": intervention_types[0] if intervention_types else None,
            "intervention_names": intervention_names,
            "phase": phases,
            "sponsor": sponsor_name or None,
            "conditions": conditions.get("conditions", []) or [],
            "last_update_date": _clean(status.get("lastUpdatePostDate") or status.get("studyFirstPostDate")) or None,
            "regulator": "ClinicalTrials.gov",
            "country": countries[0] if len(countries) == 1 else None,
            "countries": countries,
            "official_source_url": f"https://clinicaltrials.gov/study/{nct}",
        },
    )
    if countries:
        rec["region_hint"] = countries[0] if len(countries) == 1 else ", ".join(countries[:3])
    return rec, {
        "why_stopped": why_stopped,
        "overall": overall,
        "sponsor": sponsor_name,
        "interventions": intervention_rows,
        "study_type": study_type,
        "problem": problem,
        "nct": nct,
    }


def _eligible(meta: dict) -> bool:
    return bool(
        meta.get("nct")
        and meta.get("overall") in STOPPED_STATUSES
        and meta.get("sponsor")
        and meta.get("interventions")
        and meta.get("study_type") == "INTERVENTIONAL"
    )


def search(term: str, max_results: int = 10) -> ConnectorResult:
    try:
        data = get_json(BASE, {
            "query.term": term,
            "pageSize": min(max_results, 50),
            "format": "json",
        })
    except Exception as exc:
        return ConnectorResult(NAME, term, ok=False, error=describe_error(exc))
    out = []
    for study in data.get("studies", []) or []:
        rec, meta = _row(study)
        if meta.get("nct") and meta.get("interventions"):
            out.append(rec)
        if len(out) >= max_results:
            break
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)


def discover_stopped(term: str = "", max_results: int = 15, statuses=None, *,
                     page_size: int | None = None, max_pages: int = 1) -> ConnectorResult:
    """Return bounded, deduplicated stopped interventional medicinal studies."""
    statuses = statuses or STOPPED_STATUSES
    page_size = max(1, min(int(page_size or min(max_results, 50)), 100))
    max_pages = max(1, int(max_pages))
    max_results = max(0, int(max_results))
    if max_results == 0:
        return ConnectorResult(NAME, term or "stopped-trials", ok=True, count=0, records=[])

    unique: dict[str, dict] = {}
    next_token = ""
    pages_run = 0
    query_label = term or "stopped-trials"
    try:
        for _page in range(max_pages):
            remaining = max_results - len(unique)
            if remaining <= 0:
                break
            params = {
                "filter.overallStatus": "|".join(statuses),
                "pageSize": min(page_size, remaining),
                "format": "json",
                "sort": "LastUpdatePostDate:desc",
            }
            if term:
                params["query.term"] = term
            if next_token:
                params["pageToken"] = next_token
            data = get_json(BASE, params)
            pages_run += 1
            studies = data.get("studies", []) or []
            if not studies:
                break
            for study in studies:
                rec, meta = _row(study)
                if not _eligible(meta):
                    continue
                unique.setdefault(meta["nct"].upper(), rec)
                if len(unique) >= max_results:
                    break
            next_token = _clean(data.get("nextPageToken"))
            if not next_token:
                break
    except Exception as exc:
        return ConnectorResult(NAME, query_label, ok=False, error=describe_error(exc))

    records = list(unique.values())
    return ConnectorResult(
        NAME, query_label, ok=True, count=len(records), records=records,
        warnings=[f"bounded pagination: {pages_run} page(s), {len(records)} eligible unique NCT record(s)"],
    )
