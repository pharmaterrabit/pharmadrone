"""Deterministic evidence quality scoring for Phase 3A.

Evidence quality is separate from the Opportunity Score. A high-quality source
can confirm an event, but it does not confirm root cause unless the text directly
supports root-cause language.
"""
from __future__ import annotations

import re
from typing import Any


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").lower()).strip()


def _blob(evidence: dict[str, Any]) -> str:
    ent = evidence.get("entities") or {}
    rf = ent.get("recall_fields") or {}
    return _norm(" ".join(str(x or "") for x in (
        evidence.get("source_type"), evidence.get("source_category"), evidence.get("source_name"),
        evidence.get("title"), evidence.get("url"), evidence.get("raw_text"), evidence.get("english_summary"),
        evidence.get("supports"), rf.get("reason_for_recall"), rf.get("recall_number"), ent.get("event_reason"),
    )))


def source_quality_tier(evidence: dict[str, Any]) -> int:
    text = _blob(evidence)
    source_type = _norm(evidence.get("source_type"))
    source_category = _norm(evidence.get("source_category"))
    source_name = _norm(evidence.get("source_name"))
    url = _norm(evidence.get("url"))

    if source_type in {"recall", "enforcement", "label", "trial"}:
        return 1
    if "openfda" in source_name or "clinicaltrials" in source_name or "regulator" in source_category:
        return 1
    if any(x in text for x in ("warning letter", "inspection", "form 483", "official company statement")):
        return 1
    if source_type in {"paper", "patent"} or source_category in {"publication", "patent"}:
        return 2
    if any(x in source_name for x in ("europe pmc", "openalex", "crossref", "pubmed")):
        return 2
    if any(x in url for x in (".edu", "ac.uk", "technologytransfer", "tto", "patents.google", "worldwide.espacenet")):
        return 2
    if source_category in {"company"} or any(x in url for x in ("/pipeline", "/products", "/press", "/news", "investors")):
        return 2
    if source_category in {"news"} or source_type == "web":
        return 3
    if any(x in text for x in ("blog", "forum", "generic drug information", "wikipedia")):
        return 4
    return 3


def evidence_relevance(evidence: dict[str, Any]) -> str:
    text = _blob(evidence)
    if any(x in text for x in ("recall", "warning letter", "terminated", "withdrawn", "failed dissolution", "failed specification", "sterility", "impurity", "degradation")):
        return "high"
    if any(x in text for x in ("dissolution", "stability", "bioavailability", "polymorph", "particle size", "formulation", "quality")):
        return "medium"
    return "low"


def support_flags(evidence: dict[str, Any]) -> dict[str, bool]:
    text = _blob(evidence)
    supports_event = any(x in text for x in ("recall", "terminated", "withdrawn", "failed", "warning letter", "inspection", "drug alert"))
    supports_root_cause = any(x in text for x in (
        "root cause", "cause was", "attributed to", "due to", "inspection finding", "form 483", "warning letter"
    )) and supports_event
    supports_solution_fit = any(x in text for x in (
        "dissolution testing", "particle size", "solid-state", "polymorph", "formulation", "stability testing", "analytical", "qc", "bioavailability"
    ))
    supports_commercial_relevance = any(x in text for x in (
        "partnership", "license", "licensing", "collaboration", "pipeline", "launch", "shortage", "multiple lots", "ongoing"
    ))
    return {
        "supports_event": supports_event,
        "supports_root_cause": supports_root_cause,
        "supports_solution_fit": supports_solution_fit,
        "supports_commercial_relevance": supports_commercial_relevance,
    }


def annotate_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    tier = source_quality_tier(evidence)
    flags = support_flags(evidence)
    return {
        **evidence,
        "source_quality_tier": tier,
        "evidence_relevance": evidence_relevance(evidence),
        **flags,
    }


def summarise_evidence(evidence_items: list[dict[str, Any]]) -> dict[str, Any]:
    annotated = [annotate_evidence(e) for e in evidence_items if isinstance(e, dict)]
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for e in annotated:
        counts[int(e.get("source_quality_tier") or 4)] += 1

    regulator_confirmed = any(e.get("source_quality_tier") == 1 and e.get("supports_event") for e in annotated)
    company_confirmed = any((e.get("source_category") == "company" or "company" in _blob(e)) and e.get("supports_event") for e in annotated)
    literature_supported = any(e.get("source_quality_tier") == 2 and e.get("source_type") == "paper" for e in annotated)
    external_corroboration_found = len({e.get("url") or e.get("record_id") or e.get("title") for e in annotated if e}) > 1

    if company_confirmed:
        corroboration_status = "company-confirmed"
    elif regulator_confirmed:
        corroboration_status = "regulator-confirmed"
    elif literature_supported:
        corroboration_status = "literature-supported"
    elif external_corroboration_found:
        corroboration_status = "externally corroborated"
    elif annotated:
        corroboration_status = "direct source only"
    else:
        corroboration_status = "no corroboration found"

    if counts[1] > 0:
        evidence_quality = "Tier 1 / high"
    elif counts[2] > 0:
        evidence_quality = "Tier 2 / moderate"
    elif counts[3] > 0:
        evidence_quality = "Tier 3 / limited"
    else:
        evidence_quality = "Tier 4 / weak"

    return {
        "annotated_evidence": annotated,
        "evidence_quality": evidence_quality,
        "source_coverage_count": len(annotated),
        "tier1_count": counts[1],
        "tier2_count": counts[2],
        "tier3_count": counts[3],
        "tier4_count": counts[4],
        "regulator_confirmed": regulator_confirmed,
        "company_confirmed": company_confirmed,
        "literature_supported": literature_supported,
        "external_corroboration_found": external_corroboration_found,
        "corroboration_status": corroboration_status,
    }
