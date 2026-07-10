"""Official/context enrichment helpers for Phase 3B.

This module enriches existing opportunity_index records only. It does not
change Opportunity Score and it never infers root cause from label/literature
context. Any API/source failures are recorded in source_health_events for
developer/debug views; user-facing status values are clean and non-technical.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .. import db, settings
from ..connectors import clinicaltrials, crossref, europepmc, openalex, openfda, tavily_search
from . import evidence_quality, query_safety, source_health


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _load_data(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(row.get("data_json") or "{}")
    except Exception:
        return {}


def _lead(lead: dict[str, Any]) -> dict[str, Any]:
    data = _load_data(lead)
    return {**data, **lead}


def _text_blob(*values: Any) -> str:
    parts: list[str] = []
    for v in values:
        if isinstance(v, dict):
            parts.extend(str(x or "") for x in v.values())
        elif isinstance(v, list):
            parts.extend(str(x or "") for x in v)
        else:
            parts.append(str(v or ""))
    return _norm(" ".join(parts))


def _evidence_items(lead: dict[str, Any]) -> list[dict[str, Any]]:
    ev = lead.get("evidence") or []
    return ev if isinstance(ev, list) else []


def _primary_terms(lead: dict[str, Any]) -> dict[str, str]:
    merged = _lead(lead)
    ev_text = _text_blob(*_evidence_items(merged))
    product = str(merged.get("product") or merged.get("short_product") or "").strip()
    molecule = str(merged.get("molecule") or merged.get("generic_name") or "").strip()
    company = str(merged.get("company") or "").strip()
    problem = str(merged.get("problem_category") or merged.get("problem_signal") or "").strip()
    source_id = str(merged.get("source_id") or "").strip()
    if not molecule:
        # Very conservative fallback: use product only as the label/literature term.
        molecule = product
    return {
        "company": company,
        "product": product,
        "molecule": molecule,
        "problem": problem,
        "source_id": source_id,
        "source_type": str(merged.get("source_type") or ""),
        "ev_text": ev_text,
    }


def _is_fda_or_regulatory_lead(lead: dict[str, Any]) -> bool:
    terms = _primary_terms(lead)
    blob = _text_blob(terms, lead)
    return any(x in blob for x in ("fda", "openfda", "recall", "enforcement", "d-", "regulatory"))


def _is_trial_lead(lead: dict[str, Any]) -> bool:
    """Return True only for explicit ClinicalTrials/NCT leads.

    FDA recall/enforcement records can contain generic words such as
    "trial" or connector/source-status metadata, which should not trigger a
    ClinicalTrials.gov enrichment attempt. Deeper trial context is only valid
    when the indexed record itself is a trial source or an NCT identifier is
    present.
    """
    terms = _primary_terms(lead)
    source_type = _norm(terms.get("source_type"))
    if "clinicaltrials" in source_type or source_type in {"trial", "clinical trial", "clinical trial registry"}:
        return True
    return bool(_nct_id(lead))


def _nct_id(lead: dict[str, Any]) -> str:
    blob = _text_blob(_primary_terms(lead), lead, *_evidence_items(_lead(lead)))
    m = re.search(r"\bNCT\d{8}\b", blob, re.I)
    return m.group(0).upper() if m else ""


def _record_health(conn, *, run_id: str, stable_lead_id: str, source_name: str,
                   source_type: str, query: str = "", sanitized_query: str = "",
                   status: str = "skipped", failure_reason: str = "",
                   retrieved_count: int = 0, accepted_count: int = 0, rejected_count: int = 0) -> None:
    db.save_source_health_event(conn, {
        "run_id": run_id,
        "stable_lead_id": stable_lead_id,
        "source_name": source_name,
        "source_type": source_type,
        "query": query,
        "sanitized_query": sanitized_query,
        "status": status,
        "failure_reason": failure_reason,
        "retrieved_count": retrieved_count,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
    })


def _event_from_result(conn, res: Any, *, run_id: str, stable_lead_id: str,
                       source_name: str | None = None, source_type: str | None = None,
                       sanitized_query: str = "", accepted_count: int | None = None,
                       rejected_count: int = 0) -> None:
    event = source_health.event_from_connector_result(
        res,
        run_id=run_id,
        stable_lead_id=stable_lead_id,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        sanitized_query=sanitized_query,
    )
    if source_name:
        event["source_name"] = source_name
    if source_type:
        event["source_type"] = source_type
    db.save_source_health_event(conn, event)


def _official_followup_match(rec: dict[str, Any], lead: dict[str, Any]) -> bool:
    url = _norm(rec.get("url"))
    text = _text_blob(rec.get("title"), rec.get("raw_text"), rec.get("english_summary"), rec.get("url"))
    terms = _primary_terms(lead)
    if "fda.gov" not in url and "fda" not in text:
        return False
    if not any(x in text for x in ("warning letter", "inspection", "form 483", "recall follow-up", "quality")):
        return False
    anchors = [terms.get("company"), terms.get("product"), terms.get("molecule"), terms.get("source_id")]
    return any(a and _norm(a) in text for a in anchors)


def enrich_fda_official_followup(lead: dict[str, Any], conn, *, run_id: str = "", use_web: bool = True, cost=None) -> dict[str, Any]:
    stable_id = lead.get("stable_lead_id") or ""
    if not _is_fda_or_regulatory_lead(lead):
        _record_health(conn, run_id=run_id, stable_lead_id=stable_id,
                       source_name="FDA official follow-up", source_type="regulatory",
                       status="skipped", failure_reason="not FDA/regulatory lead")
        return {"status": "skipped - not FDA/regulatory lead", "count": 0, "evidence": []}

    if not use_web or not settings.env("TAVILY_API_KEY"):
        _record_health(conn, run_id=run_id, stable_lead_id=stable_id,
                       source_name="FDA official follow-up", source_type="regulatory",
                       status="skipped", failure_reason="Tavily unavailable for official follow-up search")
        return {"status": "official follow-up source unavailable", "count": 0, "evidence": []}

    accepted: list[dict[str, Any]] = []
    failures = 0
    queries = query_safety.fda_official_followup_queries(lead, max_queries=2)
    for query in queries:
        safe_q = query_safety.sanitize_tavily_query(query, max_chars=170)
        res = tavily_search.search(safe_q, max_results=3, cost=cost)
        matches = [r for r in (res.records or []) if _official_followup_match(r, lead)] if res.ok else []
        for rec in matches:
            rec.update({
                "source_type": "web",
                "source_category": "regulatory",
                "source_name": "FDA official follow-up",
                "enrichment_track": "official_followup",
                "supports": "official follow-up context; requires direct reading before root-cause claims",
                "does_not_prove": "does not confirm root cause unless the official page directly states it",
                "supports_official_followup": True,
            })
        _event_from_result(
            conn, res, run_id=run_id, stable_lead_id=stable_id,
            source_name="FDA official follow-up", source_type="regulatory",
            sanitized_query=safe_q, accepted_count=len(matches),
            rejected_count=max(0, int(getattr(res, "count", 0) or 0) - len(matches)),
        )
        if not res.ok:
            failures += 1
        accepted.extend(matches)
        if accepted:
            break

    if accepted:
        return {"status": "official follow-up found", "count": len(accepted), "evidence": accepted}
    if failures >= len(queries) and queries:
        return {"status": "official follow-up source unavailable", "count": 0, "evidence": []}
    return {"status": "no official follow-up found", "count": 0, "evidence": []}


def _label_match(rec: dict[str, Any], lead: dict[str, Any]) -> bool:
    terms = _primary_terms(lead)
    text = _text_blob(rec.get("title"), rec.get("raw_text"), rec.get("entities"))
    anchors = [_norm(terms.get("molecule")), _norm(terms.get("product"))]
    anchors = [a for a in anchors if a and len(a) >= 4]
    return bool(anchors and any(a in text for a in anchors))


def enrich_fda_label_context(lead: dict[str, Any], conn, *, run_id: str = "") -> dict[str, Any]:
    stable_id = lead.get("stable_lead_id") or ""
    terms = _primary_terms(lead)
    query = query_safety.label_context_query(lead)
    if not query:
        _record_health(conn, run_id=run_id, stable_lead_id=stable_id,
                       source_name="openFDA (Drug Label)", source_type="regulatory",
                       status="skipped", failure_reason="no product/molecule available")
        return {"status": "skipped - no product/molecule", "evidence": []}
    res = openfda.search(query, max_results=3)
    matches = [r for r in (res.records or []) if _label_match(r, lead)] if res.ok else []
    for rec in matches:
        rec.update({
            "enrichment_track": "label_context",
            "supports": "FDA label/product context only",
            "does_not_prove": "label context does not prove recall root cause or company need",
            "supports_product_context": True,
        })
    _event_from_result(
        conn, res, run_id=run_id, stable_lead_id=stable_id,
        source_name="openFDA (Drug Label)", source_type="regulatory",
        sanitized_query=query, accepted_count=len(matches),
        rejected_count=max(0, int(getattr(res, "count", 0) or 0) - len(matches)),
    )
    if not res.ok:
        return {"status": "label source unavailable", "evidence": []}
    if matches:
        return {"status": "label context available", "evidence": matches}
    return {"status": "label context not found", "evidence": []}


def _trial_match(rec: dict[str, Any], lead: dict[str, Any], nct: str) -> bool:
    if nct and _norm(rec.get("record_id")) == _norm(nct):
        return True
    terms = _primary_terms(lead)
    text = _text_blob(rec.get("title"), rec.get("raw_text"), rec.get("entities"))
    anchors = [_norm(terms.get("product")), _norm(terms.get("molecule")), _norm(terms.get("company"))]
    return any(a and len(a) >= 4 and a in text for a in anchors)


def enrich_clinical_trial_context(lead: dict[str, Any], conn, *, run_id: str = "") -> dict[str, Any]:
    stable_id = lead.get("stable_lead_id") or ""
    if not _is_trial_lead(lead):
        _record_health(conn, run_id=run_id, stable_lead_id=stable_id,
                       source_name="ClinicalTrials.gov", source_type="clinical trial registry",
                       status="skipped", failure_reason="not trial lead")
        return {"status": "skipped - not trial lead", "evidence": []}
    nct = _nct_id(lead)
    query = nct or query_safety.trial_context_query(lead)
    if not query:
        return {"status": "trial context not found", "evidence": []}
    res = clinicaltrials.search(query, max_results=3)
    matches = [r for r in (res.records or []) if _trial_match(r, lead, nct)] if res.ok else []
    for rec in matches:
        rec.update({
            "enrichment_track": "clinical_trial_context",
            "supports": "ClinicalTrials.gov trial-status context",
            "does_not_prove": "trial status does not prove product failure unless the trial record directly states a product/formulation reason",
            "supports_trial_context": True,
        })
    _event_from_result(
        conn, res, run_id=run_id, stable_lead_id=stable_id,
        source_name="ClinicalTrials.gov", source_type="clinical trial registry",
        sanitized_query=query, accepted_count=len(matches),
        rejected_count=max(0, int(getattr(res, "count", 0) or 0) - len(matches)),
    )
    if not res.ok:
        return {"status": "trial source unavailable", "evidence": []}
    if matches:
        return {"status": "trial status confirmed", "evidence": matches}
    return {"status": "trial context not found", "evidence": []}


PROBLEM_TERMS = {
    "dissolution": ["dissolution", "drug release", "release profile", "bioavailability", "formulation"],
    "stability": ["stability", "degradation", "shelf life", "impurity"],
    "impurity": ["impurity", "degradation", "nitrosamine", "contaminant"],
    "sterility": ["sterility", "microbiology", "contamination", "aseptic"],
    "bioavailability": ["bioavailability", "solubility", "absorption", "formulation"],
}


def _literature_match(rec: dict[str, Any], lead: dict[str, Any]) -> bool:
    terms = _primary_terms(lead)
    text = _text_blob(rec.get("title"), rec.get("raw_text"), rec.get("english_summary"))
    anchors = [_norm(terms.get("molecule")), _norm(terms.get("product"))]
    anchors = [a for a in anchors if a and len(a) >= 4]
    if not anchors or not any(a in text for a in anchors):
        return False
    p = _norm(terms.get("problem"))
    problem_words = []
    for key, vals in PROBLEM_TERMS.items():
        if key in p:
            problem_words.extend(vals)
    problem_words.extend(["formulation", "pharmaceutical", "delivery", "quality"])
    return any(w in text for w in problem_words)


def enrich_literature_context(lead: dict[str, Any], conn, *, run_id: str = "") -> dict[str, Any]:
    stable_id = lead.get("stable_lead_id") or ""
    query = query_safety.literature_context_query(lead)
    if not query:
        _record_health(conn, run_id=run_id, stable_lead_id=stable_id,
                       source_name="Literature context", source_type="literature",
                       status="skipped", failure_reason="no molecule/product/problem terms available")
        return {"status": "no relevant literature context found", "evidence": []}

    accepted: list[dict[str, Any]] = []
    failures = 0
    connectors = [europepmc, openalex, crossref]
    for connector in connectors:
        res = connector.search(query, max_results=2)
        matches = [r for r in (res.records or []) if _literature_match(r, lead)] if res.ok else []
        for rec in matches:
            rec.update({
                "enrichment_track": "literature_context",
                "supports": "scientific/literature context only; may support plausibility or solution-fit",
                "does_not_prove": "general literature does not confirm product-specific root cause",
                "supports_scientific_plausibility": True,
            })
        _event_from_result(
            conn, res, run_id=run_id, stable_lead_id=stable_id,
            source_name=getattr(connector, "NAME", "Literature"), source_type="literature",
            sanitized_query=query, accepted_count=len(matches),
            rejected_count=max(0, int(getattr(res, "count", 0) or 0) - len(matches)),
        )
        if not res.ok:
            failures += 1
        accepted.extend(matches)
        if len(accepted) >= 3:
            break

    if accepted:
        return {"status": "literature context found", "evidence": accepted[:3]}
    if failures == len(connectors):
        return {"status": "literature source unavailable", "evidence": []}
    return {"status": "no relevant literature context found", "evidence": []}


def enrich_official_context(lead: dict[str, Any], conn, *, run_id: str = "", use_web: bool = True, cost=None) -> dict[str, Any]:
    """Run small Phase 3B context enrichment tracks for one indexed lead."""
    official = enrich_fda_official_followup(lead, conn, run_id=run_id, use_web=use_web, cost=cost)
    label = enrich_fda_label_context(lead, conn, run_id=run_id)
    trial = enrich_clinical_trial_context(lead, conn, run_id=run_id)
    lit = enrich_literature_context(lead, conn, run_id=run_id)

    evidence = []
    for result in (official, label, trial, lit):
        evidence.extend(result.get("evidence") or [])
    summary = evidence_quality.summarise_evidence(evidence)
    official_source_count = len((official.get("evidence") or []) + (label.get("evidence") or []) + (trial.get("evidence") or []))
    literature_source_count = len(lit.get("evidence") or [])
    best = summary.get("evidence_quality") or "not checked"
    if not evidence:
        best = "not checked"
    return {
        "official_followup_status": official.get("status") or "not checked",
        "official_followup_count": int(official.get("count") or len(official.get("evidence") or [])),
        "label_context_status": label.get("status") or "not checked",
        "clinical_trial_context_status": trial.get("status") or "not checked",
        "literature_context_status": lit.get("status") or "not checked",
        "best_evidence_tier": best,
        "official_source_count": official_source_count,
        "literature_source_count": literature_source_count,
        "evidence": evidence,
        "no_product_specific_root_cause_confirmed": True,
    }


def compact_context_label(enriched: dict[str, Any]) -> str:
    parts = []
    for key in ("official_followup_status", "label_context_status", "clinical_trial_context_status", "literature_context_status"):
        value = enriched.get(key)
        if value and value not in {"not checked", "skipped - not trial lead", "skipped - no product/molecule", "skipped - not FDA/regulatory lead"}:
            parts.append(str(value))
    parts.append("no product-specific root cause confirmed")
    return " · ".join(parts)
