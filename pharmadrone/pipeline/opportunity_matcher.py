"""Deterministic Phase 1 opportunity matching for PharmaTune/PharmaDrone.

This module is intentionally small and read-only. It does not call the web,
LLMs, or databases. It only matches a user query against opportunities already
created by the existing evidence pipeline.
"""
from __future__ import annotations

import json
import re
from typing import Any

NO_MATCH_MESSAGE = (
    "No strong evidence-backed matches found in the current evidence set. "
    "Try broadening the problem, technology, region, or source coverage."
)
EMPTY_EVIDENCE_MESSAGE = (
    "Run Generate first to create evidence-backed opportunities, then use the matcher."
)
MATCH_SCOPE_LABEL = "Matched from existing evidence"
TECH_CERTAINTY_NOTE = (
    "Potential relevance only — not proof that the company needs this technology. "
    "Requires validation before outreach."
)


def _norm(text: Any) -> str:
    """Lower-case, punctuation-light text for simple deterministic matching."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+/.-]+", " ", str(text).lower())).strip()


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(_norm(term) in text for term in terms if term)


PROBLEM_RULES: dict[str, dict[str, Any]] = {
    "dissolution": {
        "label": "Dissolution / release performance",
        "terms": [
            "dissolution", "release", "drug release", "failed release", "out of specification",
            "oos", "subpotent", "tablet performance", "capsule performance",
        ],
        "solution_types": [
            "dissolution testing",
            "formulation optimisation",
            "particle-size control",
            "solid-state characterisation",
            "analytical/QC testing",
            "batch variability investigation",
        ],
        "partner_categories": [
            "formulation development CDMO",
            "dissolution testing specialist",
            "analytical/QC laboratory",
            "solid-state / particle-engineering specialist",
            "CMC troubleshooting consultant",
        ],
        "safe_bd_action": (
            "Validate whether the evidence indicates a product-specific dissolution or release signal; "
            "if yes, frame outreach around diagnostic support and formulation troubleshooting, not a claimed fix."
        ),
    },
    "stability": {
        "label": "Stability / shelf-life / degradation",
        "terms": [
            "stability", "unstable", "degradation", "degrade", "shelf life", "expiry",
            "storage", "temperature excursion", "humidity", "photostability",
        ],
        "solution_types": [
            "stability testing",
            "packaging review",
            "excipient compatibility",
            "formulation robustness",
            "storage condition review",
        ],
        "partner_categories": [
            "ICH stability testing laboratory",
            "formulation development CDMO",
            "packaging compatibility specialist",
            "excipient compatibility laboratory",
            "CMC / quality consultant",
        ],
        "safe_bd_action": (
            "Check whether the evidence points to a stability-related product problem, then propose a validation-led "
            "stability and formulation robustness discussion."
        ),
    },
    "sterility": {
        "label": "Sterility / contamination control",
        "terms": [
            "sterility", "sterile", "aseptic", "contamination", "microbial", "microbiology",
            "particulate matter", "bacteria", "fungal", "endotoxin", "environmental monitoring",
        ],
        "solution_types": [
            "sterile manufacturing",
            "aseptic processing",
            "contamination control",
            "microbiology testing",
            "facility remediation",
        ],
        "partner_categories": [
            "sterile manufacturing CDMO",
            "aseptic processing consultant",
            "microbiology testing laboratory",
            "contamination-control specialist",
            "GMP facility remediation consultant",
        ],
        "safe_bd_action": (
            "Treat as a quality-critical signal. Outreach should focus on assessment and remediation capability, "
            "not assumptions about the exact contamination source."
        ),
    },
    "impurity": {
        "label": "Impurity / degradation product / specification issue",
        "terms": [
            "impurity", "impurities", "nitrosamine", "degradation product", "related substance",
            "failed specification", "specification", "assay", "content uniformity", "contaminant",
        ],
        "solution_types": [
            "analytical testing",
            "impurity profiling",
            "supplier qualification",
            "process optimisation",
            "nitrosamine / degradation pathway review where relevant",
        ],
        "partner_categories": [
            "analytical/QC laboratory",
            "impurity profiling specialist",
            "process chemistry / CMC consultant",
            "supplier qualification specialist",
            "nitrosamine risk-assessment laboratory where relevant",
        ],
        "safe_bd_action": (
            "Confirm the specific impurity or failed attribute from the source record, then frame the action around "
            "analytical confirmation and root-cause investigation."
        ),
    },

    "solid_state": {
        "label": "Solid-state / polymorph / particle attributes",
        "terms": [
            "solid-state", "solid state", "polymorph", "crystallinity", "amorphous",
            "crystal form", "particle size", "particle-size", "solid form", "form conversion",
            "polymorph conversion", "salt form", "cocrystal",
        ],
        "solution_types": [
            "solid-state characterisation",
            "polymorph screening",
            "crystallinity / amorphous-content assessment",
            "particle-size distribution testing",
            "form-selection and stability support",
        ],
        "partner_categories": [
            "solid-state characterisation laboratory",
            "particle-engineering specialist",
            "polymorph screening provider",
            "analytical/QC laboratory",
            "CMC troubleshooting consultant",
        ],
        "safe_bd_action": (
            "Use the evidence as a solid-state or particle-attribute signal only. Validate the direct record before "
            "linking it to dissolution, stability, or manufacturability. "
        ),
    },
    "bioavailability": {
        "label": "Bioavailability / absorption / exposure",
        "terms": [
            "bioavailability", "absorption", "exposure", "pharmacokinetic", "pk", "auc", "cmax",
            "poor solubility", "low solubility", "bcs ii", "bcs iv", "oral performance",
        ],
        "solution_types": [
            "solubility enhancement",
            "particle engineering",
            "lipid formulation",
            "amorphous solid dispersion",
            "permeability / absorption strategy",
        ],
        "partner_categories": [
            "bioavailability-enhancement technology provider",
            "particle-engineering specialist",
            "lipid formulation CDMO",
            "amorphous solid dispersion specialist",
            "biopharmaceutics / PK consultant",
        ],
        "safe_bd_action": (
            "Use the evidence as a problem signal only; validate the formulation, PK, and clinical context before proposing "
            "a specific enabling technology."
        ),
    },
    "packaging_container_closure": {
        "label": "Packaging / container-closure",
        "terms": [
            "packaging", "container closure", "container-closure", "closure", "seal", "leak",
            "extractables", "leachables", "moisture", "oxygen", "blister", "vial", "syringe",
        ],
        "solution_types": [
            "packaging compatibility",
            "container-closure testing",
            "extractables/leachables",
            "moisture/oxygen protection",
            "stability support",
        ],
        "partner_categories": [
            "container-closure testing laboratory",
            "extractables/leachables specialist",
            "pharmaceutical packaging supplier",
            "stability testing laboratory",
            "packaging engineering consultant",
        ],
        "safe_bd_action": (
            "Confirm the packaging or container-closure failure mode first, then position support around testing and risk "
            "reduction rather than product-specific certainty."
        ),
    },
    "manufacturing_variability": {
        "label": "Manufacturing variability / CMC troubleshooting",
        "terms": [
            "manufacturing", "process", "batch", "variability", "scale-up", "scale up", "gmp",
            "quality system", "deviation", "failed batch", "production", "cmc", "process validation",
        ],
        "solution_types": [
            "process optimisation",
            "batch investigation",
            "scale-up support",
            "CMC troubleshooting",
            "quality systems review",
        ],
        "partner_categories": [
            "CMC troubleshooting consultant",
            "process development CDMO",
            "GMP quality systems consultant",
            "scale-up specialist",
            "manufacturing investigation team",
        ],
        "safe_bd_action": (
            "Treat this as a manufacturing or CMC signal requiring validation. Outreach should offer diagnostic support, "
            "not assert a known internal cause."
        ),
    },
}


TECH_RULES: dict[str, dict[str, Any]] = {
    "particle_engineering": {
        "label": "Particle engineering technology",
        "terms": [
            "particle engineering", "particle", "particle size", "micronization", "micronisation",
            "nanoparticle", "spray drying", "supercritical", "sas", "msas", "crystal engineering",
        ],
        "problem_categories": ["dissolution", "bioavailability", "solid_state", "stability", "manufacturing_variability"],
        "why_fit": (
            "Particle engineering may have potential relevance where evidence-backed signals mention dissolution, "
            "bioavailability, solid-state behaviour, particle-size sensitivity, or oral solid dose performance."
        ),
        "safe_outreach_angle": (
            "Use a diagnostic angle: ask whether particle-size, solid-state, or dissolution performance has been evaluated. "
            "Do not claim the technology will solve the product problem."
        ),
    },
    "solubility_enhancement": {
        "label": "Solubility enhancement technology",
        "terms": [
            "solubility enhancement", "solubil", "poor solubility", "low solubility", "asd",
            "amorphous solid dispersion", "cyclodextrin", "lipid formulation", "nanoformulation",
        ],
        "problem_categories": ["bioavailability", "dissolution"],
        "why_fit": (
            "Solubility enhancement may be relevant to evidence-backed signals involving poor solubility, low oral exposure, "
            "dissolution failure, or BCS II/IV-like development challenges."
        ),
        "safe_outreach_angle": (
            "Frame as formulation feasibility support and request validation data before proposing a specific platform."
        ),
    },
    "solid_state_characterisation": {
        "label": "Solid-state characterisation",
        "terms": [
            "solid-state", "solid state", "polymorph", "crystal", "crystallinity", "amorphous",
            "xrpd", "dsc", "tga", "ftir", "raman", "salt form", "cocrystal",
        ],
        "problem_categories": ["dissolution", "solid_state", "stability", "manufacturing_variability"],
        "why_fit": (
            "Solid-state characterisation may be relevant where signals involve polymorph control, crystallinity changes, "
            "amorphous conversion, dissolution variability, or stability concerns."
        ),
        "safe_outreach_angle": (
            "Offer characterisation and risk-mapping support; avoid presenting solid-state behaviour as confirmed unless directly evidenced."
        ),
    },
    "analytical_qc_service": {
        "label": "Analytical/QC service",
        "terms": [
            "analytical", "qc", "quality control", "testing service", "method development",
            "method validation", "assay", "specification", "impurity profiling", "dissolution testing",
        ],
        "problem_categories": ["impurity", "dissolution", "stability", "manufacturing_variability"],
        "why_fit": (
            "Analytical/QC services may be relevant to evidence-backed signals involving failed specifications, impurity issues, "
            "dissolution testing, assay/content uniformity, or stability failures."
        ),
        "safe_outreach_angle": (
            "Position the outreach around independent testing, confirmation, and investigation support."
        ),
    },
    "formulation_cdmo": {
        "label": "Formulation CDMO",
        "terms": [
            "formulation cdmo", "cdmo", "formulation development", "reformulation",
            "drug delivery", "dosage form", "oral solid", "topical", "injectable formulation",
        ],
        "problem_categories": ["dissolution", "stability", "bioavailability", "manufacturing_variability"],
        "why_fit": (
            "A formulation CDMO may have potential relevance where existing evidence indicates formulation optimisation, "
            "dissolution, stability, bioavailability, or lifecycle reformulation needs."
        ),
        "safe_outreach_angle": (
            "Frame as a feasibility and troubleshooting conversation, not as proof that outsourcing or reformulation is required."
        ),
    },
}


def classify_problem_query(query: str) -> tuple[str | None, dict[str, Any] | None]:
    q = _norm(query)
    if not q:
        return None, None
    best_key = None
    best_hits = 0
    for key, rule in PROBLEM_RULES.items():
        hits = sum(1 for term in rule["terms"] if _norm(term) in q)
        if _norm(key).replace("_", " ") in q:
            hits += 2
        if hits > best_hits:
            best_key, best_hits = key, hits
    return (best_key, PROBLEM_RULES[best_key]) if best_key else (None, None)


def classify_technology_query(query: str) -> tuple[str | None, dict[str, Any] | None]:
    q = _norm(query)
    if not q:
        return None, None
    best_key = None
    best_hits = 0
    for key, rule in TECH_RULES.items():
        hits = sum(1 for term in rule["terms"] if _norm(term) in q)
        if _norm(key).replace("_", " ") in q:
            hits += 2
        if hits > best_hits:
            best_key, best_hits = key, hits
    return (best_key, TECH_RULES[best_key]) if best_key else (None, None)


def _parse_opp(row: dict[str, Any]) -> dict[str, Any]:
    data = {}
    raw = row.get("data_json")
    if raw:
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
    merged = {**row, **data}
    return merged


def prepare_existing_opportunities(
    opportunity_rows: list[dict[str, Any]] | None,
    evidence_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Normalise DB rows into matcher-ready records."""
    opportunity_rows = opportunity_rows or []
    evidence_rows = evidence_rows or []
    evidence_by_opp: dict[str, list[dict[str, Any]]] = {}
    for e in evidence_rows:
        evidence_by_opp.setdefault(str(e.get("opportunity_id", "")), []).append(e)

    records: list[dict[str, Any]] = []
    for row in opportunity_rows:
        opp = _parse_opp(row)
        oid = str(opp.get("id") or row.get("id") or "")
        evidence = opp.get("evidence") or evidence_by_opp.get(oid, []) or []
        if not isinstance(evidence, list):
            evidence = []
        opp["evidence"] = evidence
        records.append(opp)
    return records


def _evidence_text(evidence: list[dict[str, Any]]) -> str:
    bits: list[str] = []
    fields = (
        "title", "english_summary", "raw_text", "supports", "does_not_prove", "source_type",
        "source_name", "source_category", "record_id", "url",
    )
    for e in evidence:
        if isinstance(e, dict):
            bits.extend(str(e.get(f, "")) for f in fields if e.get(f))
            entities = e.get("entities")
            if isinstance(entities, dict):
                bits.extend(str(v) for v in entities.values() if v)
    return _norm(" ".join(bits))


def _opp_text(opp: dict[str, Any]) -> str:
    fields = (
        "company", "parent_company", "product", "generic_name", "brand_name", "dev_code",
        "indication", "therapeutic_area", "region", "stage", "problem_signal",
        "report_type", "signal_status", "discovery_method", "discovery_reason", "event_type",
        "event_reason", "failure_signal", "root_cause", "confirmed_fact", "interpretation",
        "root_cause_summary", "solution_fit", "report_md",
    )
    return _norm(" ".join(str(opp.get(f, "")) for f in fields if opp.get(f)))


def _match_category_score(opp: dict[str, Any], category_key: str) -> tuple[int, list[str]]:
    rule = PROBLEM_RULES[category_key]
    opp_text = _opp_text(opp)
    ev_text = _evidence_text(opp.get("evidence", []))
    terms = rule["terms"]
    hits: list[str] = []
    score = 0
    for term in terms:
        t = _norm(term)
        if not t:
            continue
        in_opp = t in opp_text
        in_ev = t in ev_text
        if in_ev:
            score += 3
            hits.append(term)
        elif in_opp:
            score += 1
            hits.append(term)
    # Evidence text is weighted higher because the UI promises existing evidence matches.
    return score, sorted(set(hits))


def _source_summary(evidence: list[dict[str, Any]]) -> str:
    seen: list[str] = []
    for e in evidence:
        if not isinstance(e, dict):
            continue
        src = e.get("source_name") or e.get("source_type") or e.get("source_category")
        if src and src not in seen:
            seen.append(str(src))
    return ", ".join(seen[:4]) or "Stored opportunity evidence"


def _confirmed_fact(opp: dict[str, Any]) -> str:
    for key in ("confirmed_fact", "failure_event_summary", "event_reason", "problem_signal"):
        val = opp.get(key)
        if val:
            return str(val)
    evidence = opp.get("evidence", [])
    for e in evidence:
        if isinstance(e, dict):
            for key in ("supports", "english_summary", "title"):
                val = e.get(key)
                if val:
                    return str(val)
    return "Evidence-backed product/company signal stored by the existing pipeline."


def _target_label(opp: dict[str, Any]) -> str:
    company = opp.get("company") or "Unknown company"
    product = opp.get("product") or opp.get("brand_name") or opp.get("generic_name") or "Unknown product"
    return f"{company} — {product}"


def _lead_status(opp: dict[str, Any], match_score: int) -> str:
    grade = str(opp.get("grade") or "").upper()
    confidence = _norm(opp.get("confidence") or "")
    ev_count = int(opp.get("evidence_count") or len(opp.get("evidence", [])) or 0)
    score = int(opp.get("score") or 0)
    if match_score >= 6 and ev_count >= 2 and grade in {"A", "B"} and confidence != "low":
        return "outreach-ready"
    if match_score >= 3 and ev_count >= 1 and (grade in {"A", "B", "C"} or score >= 30):
        return "needs validation"
    if match_score >= 1 and ev_count >= 1:
        return "monitor only"
    return "low priority / archive"


def _confidence(opp: dict[str, Any], match_score: int) -> str:
    existing = _norm(opp.get("confidence") or "")
    if match_score >= 6 and existing in {"high", "medium-high", "medium"}:
        return existing or "medium"
    if match_score >= 6:
        return "medium"
    if match_score >= 3:
        return "low-medium"
    return "low"


def _problem_match_row(
    query: str,
    category_key: str,
    rule: dict[str, Any],
    opp: dict[str, Any],
    match_score: int,
    hits: list[str],
) -> dict[str, Any]:
    target = _target_label(opp)
    return {
        "match_scope": MATCH_SCOPE_LABEL,
        "searched_problem": query,
        "matched_problem_category": rule["label"],
        "matching_product_problem_lead": target,
        "company": opp.get("company") or "",
        "product": opp.get("product") or opp.get("brand_name") or opp.get("generic_name") or "",
        "evidence_source": _source_summary(opp.get("evidence", [])),
        "confirmed_fact": _confirmed_fact(opp),
        "interpretation_hypothesis": (
            f"The stored evidence contains terms consistent with {rule['label']} "
            f"({', '.join(hits[:5])}). This is an opportunity hypothesis, not a confirmed root cause."
        ),
        "likely_solution_types": rule["solution_types"],
        "possible_partner_categories": rule["partner_categories"],
        "confidence": _confidence(opp, match_score),
        "lead_status": _lead_status(opp, match_score),
        "safe_bd_action": rule["safe_bd_action"],
        "match_terms": hits,
        "match_score": match_score,
        "grade": opp.get("grade") or "",
        "opportunity_score": opp.get("score") or "",
        "evidence_count": opp.get("evidence_count") or len(opp.get("evidence", [])) or 0,
    }


def match_problem_to_solutions(
    query: str,
    opportunity_rows: list[dict[str, Any]] | None,
    evidence_rows: list[dict[str, Any]] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    records = prepare_existing_opportunities(opportunity_rows, evidence_rows)
    if not records:
        return {"status": "empty", "message": EMPTY_EVIDENCE_MESSAGE, "matches": []}

    category_key, rule = classify_problem_query(query)
    if not category_key or not rule:
        return {
            "status": "no_rule",
            "message": "No rule-based problem category matched this query. Try a broader term such as dissolution, stability, sterility, impurity, bioavailability, packaging, or manufacturing variability.",
            "matches": [],
        }

    matches: list[dict[str, Any]] = []
    for opp in records:
        score, hits = _match_category_score(opp, category_key)
        if score >= 3:  # require at least one evidence-level hit or several weaker stored-field hits
            matches.append(_problem_match_row(query, category_key, rule, opp, score, hits))

    matches.sort(key=lambda m: (m["match_score"], int(m.get("evidence_count") or 0), int(m.get("opportunity_score") or 0)), reverse=True)
    if not matches:
        return {
            "status": "no_matches",
            "message": NO_MATCH_MESSAGE,
            "searched_problem": query,
            "matched_problem_category": rule["label"],
            "likely_solution_types": rule["solution_types"],
            "possible_partner_categories": rule["partner_categories"],
            "safe_bd_action": rule["safe_bd_action"],
            "matches": [],
        }
    return {
        "status": "ok",
        "message": MATCH_SCOPE_LABEL,
        "searched_problem": query,
        "matched_problem_category": rule["label"],
        "likely_solution_types": rule["solution_types"],
        "possible_partner_categories": rule["partner_categories"],
        "safe_bd_action": rule["safe_bd_action"],
        "matches": matches[:limit],
    }


def _tech_match_row(
    query: str,
    tech_key: str,
    tech_rule: dict[str, Any],
    opp: dict[str, Any],
    category_hits: list[str],
    match_score: int,
    term_hits: list[str],
) -> dict[str, Any]:
    relevant_labels = [PROBLEM_RULES[k]["label"] for k in tech_rule["problem_categories"]]
    return {
        "match_scope": MATCH_SCOPE_LABEL,
        "searched_technology": query,
        "technology_category": tech_rule["label"],
        "relevant_problem_categories": relevant_labels,
        "matching_product_company_lead": _target_label(opp),
        "company": opp.get("company") or "",
        "product": opp.get("product") or opp.get("brand_name") or opp.get("generic_name") or "",
        "why_this_technology_may_fit": (
            f"{tech_rule['why_fit']} The stored evidence matched: {', '.join(term_hits[:6]) or ', '.join(category_hits)}. "
            f"{TECH_CERTAINTY_NOTE}"
        ),
        "evidence_strength": _confidence(opp, match_score),
        "confidence": _confidence(opp, match_score),
        "lead_status": _lead_status(opp, match_score),
        "safe_outreach_angle": tech_rule["safe_outreach_angle"] + " " + TECH_CERTAINTY_NOTE,
        "confirmed_fact": _confirmed_fact(opp),
        "evidence_source": _source_summary(opp.get("evidence", [])),
        "matched_problem_categories": [PROBLEM_RULES[k]["label"] for k in category_hits],
        "match_terms": term_hits,
        "match_score": match_score,
        "grade": opp.get("grade") or "",
        "opportunity_score": opp.get("score") or "",
        "evidence_count": opp.get("evidence_count") or len(opp.get("evidence", [])) or 0,
    }


def match_technology_to_targets(
    query: str,
    opportunity_rows: list[dict[str, Any]] | None,
    evidence_rows: list[dict[str, Any]] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    records = prepare_existing_opportunities(opportunity_rows, evidence_rows)
    if not records:
        return {"status": "empty", "message": EMPTY_EVIDENCE_MESSAGE, "matches": []}

    tech_key, tech_rule = classify_technology_query(query)
    if not tech_key or not tech_rule:
        return {
            "status": "no_rule",
            "message": "No rule-based technology category matched this query. Try particle engineering, solubility enhancement, solid-state characterisation, analytical/QC service, or formulation CDMO.",
            "matches": [],
        }

    matches: list[dict[str, Any]] = []
    for opp in records:
        total = 0
        category_hits: list[str] = []
        term_hits: list[str] = []
        for category_key in tech_rule["problem_categories"]:
            score, hits = _match_category_score(opp, category_key)
            if score >= 3:
                total += score
                category_hits.append(category_key)
                term_hits.extend(hits)
        if total >= 3:
            matches.append(_tech_match_row(query, tech_key, tech_rule, opp, category_hits, total, sorted(set(term_hits))))

    matches.sort(key=lambda m: (m["match_score"], int(m.get("evidence_count") or 0), int(m.get("opportunity_score") or 0)), reverse=True)
    relevant_labels = [PROBLEM_RULES[k]["label"] for k in tech_rule["problem_categories"]]
    if not matches:
        return {
            "status": "no_matches",
            "message": NO_MATCH_MESSAGE,
            "searched_technology": query,
            "technology_category": tech_rule["label"],
            "relevant_problem_categories": relevant_labels,
            "why_this_technology_may_fit": tech_rule["why_fit"] + " " + TECH_CERTAINTY_NOTE,
            "matches": [],
        }
    return {
        "status": "ok",
        "message": MATCH_SCOPE_LABEL,
        "searched_technology": query,
        "technology_category": tech_rule["label"],
        "relevant_problem_categories": relevant_labels,
        "why_this_technology_may_fit": tech_rule["why_fit"] + " " + TECH_CERTAINTY_NOTE,
        "matches": matches[:limit],
    }
