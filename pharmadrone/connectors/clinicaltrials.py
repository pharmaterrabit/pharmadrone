"""ClinicalTrials.gov API v2 — no key required.

Checkpoint 5A.1 keeps strict medicinal-intervention gates, bounded token
pagination, explicit rejection diagnostics, and a cautious technical discovery
context. Trial status is a registry fact; a stopped study is not a product
failure unless the registry's stated reason directly supports that conclusion.
"""
from __future__ import annotations

from collections import Counter
import re
from .base import get_json, record, ConnectorResult, describe_error

NAME = "ClinicalTrials.gov"
BASE = "https://clinicaltrials.gov/api/v2/studies"
STOPPED_STATUSES = ["TERMINATED", "WITHDRAWN", "SUSPENDED"]

_ALLOWED_INTERVENTION_TYPES = {"DRUG", "BIOLOGICAL", "COMBINATION_PRODUCT"}
_SPECIMEN_OR_DIAGNOSTIC_TERMS = {
    "blood sample", "serum", "plasma", "tissue", "biopsy", "biospecimen",
    "specimen collection", "diagnostic test", "blood draw", "sample collection",
    "pharmacokinetic sampling", "laboratory test",
}
_CONTROL_ONLY_EXACT = {
    "placebo", "matching placebo", "placebo comparator", "standard of care",
    "no intervention", "observation", "usual care", "best supportive care",
}
_PRODUCT_PROBLEM_TERMS = (
    "formulation", "bioavailability", "bioequivalence", "solubility", "dissolution",
    "pharmacokinetic", "food effect", "drug delivery", "modified release",
    "extended release", "delayed release", "stability", "precipitation",
    "manufacturing", "drug supply", "supply issue", "product quality",
    "dose delivery", "absorption",
)

_TOPIC_CONTEXTS = (
    (("bioavailability", "relative bioavailability"), "bioavailability / exposure context"),
    (("bioequivalence", "pharmacokinetic", "bridging"), "bioequivalence / pharmacokinetic context"),
    (("food effect",), "food-effect context"),
    (("solubility", "dissolution"), "solubility / dissolution context"),
    (("modified release", "extended release", "delayed release"), "modified-release formulation context"),
    (("topical", "transdermal"), "topical / transdermal delivery context"),
    (("inhaled", "inhalation"), "inhalation delivery context"),
    (("injectable", "long acting injectable"), "injectable formulation context"),
    (("pediatric formulation", "paediatric formulation"), "paediatric formulation context"),
    (("fixed dose combination",), "fixed-dose combination context"),
    (("formulation", "reformulation", "drug product"), "formulation / drug-product context"),
    (("drug delivery",), "drug-delivery context"),
)


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        # DateStruct and similar v2 structures commonly expose a date field.
        if value.get("date"):
            return _clean(value.get("date"))
        return " ".join(_clean(v) for v in value.values() if _clean(v))
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
        usable.append({
            "name": name,
            "type": kind,
            "description": _clean(item.get("description")),
            "other_names": [_clean(x) for x in (item.get("otherNames") or []) if _clean(x)],
        })
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


def _topic_context(topic: str) -> str:
    text = _norm(topic)
    for terms, label in _TOPIC_CONTEXTS:
        if any(term in text for term in terms):
            return label
    return "drug-product development context" if text else ""


def _study_context_blob(study: dict, interventions: list[dict]) -> str:
    ps = study.get("protocolSection", {}) or {}
    ident = ps.get("identificationModule", {}) or {}
    desc = ps.get("descriptionModule", {}) or {}
    cond = ps.get("conditionsModule", {}) or {}
    outcomes = ps.get("outcomesModule", {}) or {}
    outcome_text = []
    for key in ("primaryOutcomes", "secondaryOutcomes", "otherOutcomes"):
        for row in outcomes.get(key, []) or []:
            outcome_text.extend([_clean(row.get("measure")), _clean(row.get("description"))])
    return _norm(" ".join([
        _clean(ident.get("briefTitle")), _clean(ident.get("officialTitle")),
        _clean(desc.get("briefSummary")), _clean(desc.get("detailedDescription")),
        _clean(cond.get("conditions")), _clean(cond.get("keywords")),
        " ".join(x.get("name", "") for x in interventions),
        " ".join(x.get("description", "") for x in interventions),
        " ".join(outcome_text),
    ]))


def _topic_locally_supported(topic: str, blob: str) -> bool:
    """Require at least one meaningful topic token in stored registry content."""
    topic_n = _norm(topic)
    if not topic_n:
        return True
    strong_phrases = [
        "bioavailability", "bioequivalence", "pharmacokinetic", "food effect",
        "solubility", "dissolution", "modified release", "extended release",
        "delayed release", "formulation", "reformulation", "drug delivery",
        "topical", "transdermal", "inhaled", "inhalation", "injectable",
        "fixed dose combination", "pediatric", "paediatric", "bridging",
    ]
    requested = [p for p in strong_phrases if p in topic_n]
    if not requested:
        requested = [x for x in topic_n.split() if len(x) >= 6]
    return any(term in blob for term in requested)


def _row(study: dict, discovery_topic: str = ""):
    ps = study.get("protocolSection", {}) or {}
    ident = ps.get("identificationModule", {}) or {}
    status = ps.get("statusModule", {}) or {}
    design = ps.get("designModule", {}) or {}
    description = ps.get("descriptionModule", {}) or {}
    arms_module = ps.get("armsInterventionsModule", {}) or {}
    outcomes_module = ps.get("outcomesModule", {}) or {}
    conditions = ps.get("conditionsModule", {}) or {}
    sponsor_mod = ps.get("sponsorCollaboratorsModule", {}) or {}
    sponsor = sponsor_mod.get("leadSponsor", {}) or {}
    contacts_module = ps.get("contactsLocationsModule", {}) or {}
    locations = contacts_module.get("locations", []) or []
    public_contacts = []
    for contact in contacts_module.get("centralContacts", []) or []:
        name = _clean((contact or {}).get("name"))
        if name:
            public_contacts.append({
                "name": name,
                "role": _clean((contact or {}).get("role")),
                "phone": _clean((contact or {}).get("phone")),
                "email": _clean((contact or {}).get("email")),
                "source_scope": "central study contact",
            })
    for location in locations:
        for contact in (location or {}).get("contacts", []) or []:
            name = _clean((contact or {}).get("name"))
            if name:
                public_contacts.append({
                    "name": name,
                    "role": _clean((contact or {}).get("role")),
                    "phone": _clean((contact or {}).get("phone")),
                    "email": _clean((contact or {}).get("email")),
                    "source_scope": _clean((location or {}).get("facility")) or "study location contact",
                })

    nct = _clean(ident.get("nctId"))
    title = _clean(ident.get("briefTitle"))
    official_title = _clean(ident.get("officialTitle"))
    brief_summary = _clean(description.get("briefSummary"))
    detailed_description = _clean(description.get("detailedDescription"))
    study_type = _clean(design.get("studyType")).upper()
    overall = _clean(status.get("overallStatus")).upper()
    why_stopped = _clean(status.get("whyStopped"))
    sponsor_name = _clean(sponsor.get("name"))
    phases = [_clean(x) for x in (design.get("phases", []) or []) if _clean(x)]
    intervention_rows = _usable_interventions(study)
    intervention_names = [x["name"] for x in intervention_rows]
    intervention_types = [x["type"] for x in intervention_rows]
    intervention_descriptions = [x.get("description", "") for x in intervention_rows if x.get("description")]
    intervention_other_names = [name for x in intervention_rows for name in (x.get("other_names") or []) if name]
    arm_labels = [_clean((x or {}).get("label")) for x in (arms_module.get("armGroups", []) or []) if _clean((x or {}).get("label"))]
    arm_descriptions = [_clean((x or {}).get("description")) for x in (arms_module.get("armGroups", []) or []) if _clean((x or {}).get("description"))]
    primary_outcomes = [
        " | ".join(y for y in (_clean((x or {}).get("measure")), _clean((x or {}).get("description"))) if y)
        for x in (outcomes_module.get("primaryOutcomes", []) or [])
    ]
    secondary_outcomes = [
        " | ".join(y for y in (_clean((x or {}).get("measure")), _clean((x or {}).get("description"))) if y)
        for x in (outcomes_module.get("secondaryOutcomes", []) or [])
    ]
    product = intervention_names[0] if intervention_names else ""
    problem = _trial_problem(why_stopped)
    technical_context = _topic_context(discovery_topic)
    context_blob = _study_context_blob(study, intervention_rows)
    context_supported = _topic_locally_supported(discovery_topic, context_blob)
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
    event_type = overall.lower() if overall in STOPPED_STATUSES else None
    official_url = f"https://clinicaltrials.gov/study/{nct}"
    rec = record(
        "trial", NAME, nct, title, official_url, raw_text,
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
            "trial_signal_category": "stopped trial / development signal",
            "trial_relevance_context": technical_context if context_supported else None,
            "technical_context_supported": bool(context_supported),
            "discovery_topic": discovery_topic or None,
            "intervention_type": intervention_types[0] if intervention_types else None,
            "intervention_names": intervention_names,
            "phase": phases,
            "sponsor": sponsor_name or None,
            "conditions": conditions.get("conditions", []) or [],
            "brief_title": title or None,
            "official_title": official_title or None,
            "brief_summary": brief_summary or None,
            "detailed_description": detailed_description or None,
            "intervention_descriptions": intervention_descriptions,
            "intervention_other_names": intervention_other_names,
            "arm_labels": arm_labels,
            "arm_descriptions": arm_descriptions,
            "primary_outcomes": [x for x in primary_outcomes if x],
            "secondary_outcomes": [x for x in secondary_outcomes if x],
            "last_update_date": _clean(
                status.get("lastUpdatePostDateStruct")
                or status.get("lastUpdatePostDate")
                or status.get("studyFirstPostDateStruct")
            ) or None,
            "regulator": "ClinicalTrials.gov",
            "country": countries[0] if len(countries) == 1 else None,
            "countries": countries,
            "contacts": public_contacts,
            "official_source_url": official_url,
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
        "context_supported": context_supported,
    }


def _rejection_reason(meta: dict) -> str | None:
    if not meta.get("nct"):
        return "missing NCT ID"
    if meta.get("overall") not in STOPPED_STATUSES:
        return "status is not terminated/withdrawn/suspended"
    if meta.get("study_type") != "INTERVENTIONAL":
        return "not an interventional study"
    if not meta.get("sponsor"):
        return "missing lead sponsor"
    if not meta.get("interventions"):
        return "no usable medicinal intervention"
    if not meta.get("context_supported"):
        return "query topic not supported by stored registry text"
    return None


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
    rejected = Counter()
    for study in data.get("studies", []) or []:
        rec, meta = _row(study, term)
        reason = _rejection_reason(meta)
        if reason:
            rejected[reason] += 1
            continue
        out.append(rec)
        if len(out) >= max_results:
            break
    return ConnectorResult(
        NAME, term, ok=True, count=len(out), records=out,
        stats={
            "query_count": 1,
            "successful_queries": 1,
            "failed_queries": 0,
            "raw_results": len(data.get("studies", []) or []),
            "records_rejected": sum(rejected.values()),
            "rejection_reasons": dict(rejected),
        },
    )


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
    rejected = Counter()
    next_token = ""
    pages_run = 0
    raw_results = 0
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
            raw_results += len(studies)
            if not studies:
                break
            for study in studies:
                rec, meta = _row(study, term)
                reason = _rejection_reason(meta)
                if reason:
                    rejected[reason] += 1
                    continue
                unique.setdefault(meta["nct"].upper(), rec)
                if len(unique) >= max_results:
                    break
            next_token = _clean(data.get("nextPageToken"))
            if not next_token:
                break
    except Exception as exc:
        return ConnectorResult(
            NAME, query_label, ok=False, error=describe_error(exc),
            stats={
                "query_count": 1,
                "successful_queries": 0,
                "failed_queries": 1,
                "raw_results": raw_results,
                "records_rejected": sum(rejected.values()),
                "rejection_reasons": dict(rejected),
            },
        )

    records = list(unique.values())
    return ConnectorResult(
        NAME, query_label, ok=True, count=len(records), records=records,
        warnings=[
            f"bounded pagination: {pages_run} page(s), {len(records)} eligible unique NCT record(s), "
            f"{sum(rejected.values())} record(s) rejected by medicinal-intervention/topic gates"
        ],
        stats={
            "query_count": 1,
            "successful_queries": 1,
            "failed_queries": 0,
            "raw_results": raw_results,
            "unique_records": len(records),
            "records_rejected": sum(rejected.values()),
            "rejection_reasons": dict(rejected),
            "pages_run": pages_run,
            "source_event_ids": [r.get("record_id") for r in records],
        },
    )
