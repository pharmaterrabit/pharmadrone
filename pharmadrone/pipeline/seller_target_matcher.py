"""Deterministic seller-to-target matching for Phase 3C.

This module is deliberately read-only: it matches a seller/company capability
profile against already indexed PharmaTune opportunity records. It does not call
external APIs, LLMs, or mutate scores/storage.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from .. import db
from . import opportunity_index
from .opportunity_matcher import prepare_existing_opportunities, _common_match_metadata

MATCH_SCOPE_LABEL = "Matched against currently indexed PharmaTune evidence. Use Generate/Refresh to add new signals."
NO_TARGETS_MESSAGE = (
    "No evidence-backed target opportunities matched this seller profile in the current opportunity index. "
    "Try broadening capability categories, region, evidence-quality filters, or Generate/Refresh to add new signals."
)
EMPTY_INDEX_MESSAGE = "Run Generate first to create indexed PharmaTune evidence, then use seller-to-target matching."
FIT_NOTE = (
    "Seller Fit Strength reflects technical/capability fit only, not commercial readiness. "
    "It is a deterministic capability-match label and does not replace or modify the stored Opportunity Score."
)
PREVIEW_MESSAGE = (
    "Full report not generated yet. This is an indexed opportunity preview. Use Continue previous queue or Generate/Refresh to create a full report."
)

FIT_STRONG = "Strong fit"
FIT_MODERATE = "Moderate fit"
FIT_WEAK = "Weak/background fit"

CAPABILITY_RULES: dict[str, dict[str, Any]] = {
    "particle_engineering": {
        "label": "particle engineering",
        "terms": ["particle engineering", "particle-size", "particle size", "micronization", "milling", "spray drying", "solid-state"],
        "problem_terms": [
            "dissolution failure", "dissolution", "poor solubility", "low solubility", "solubility",
            "low bioavailability", "bioavailability", "food effect", "dose burden", "formulation challenge",
            "particle-size", "particle size", "solid-state", "solid state", "polymorph", "oral solid",
        ],
        "direct_problem_categories": ["dissolution", "bioavailability", "solid_state"],
        "why": "Particle engineering may be relevant where indexed evidence indicates dissolution, solubility, bioavailability, solid-state, or oral solid-dose performance signals.",
        "angle": "Lead with a validation-led particle-size/solid-state feasibility discussion, not a claim that the target company needs this technology.",
    },
    "solubility_enhancement": {
        "label": "solubility enhancement",
        "terms": ["solubility enhancement", "poor solubility", "amorphous solid dispersion", "asd", "lipid formulation", "bcs ii", "bcs iv"],
        "problem_terms": [
            "poor solubility", "low solubility", "solubility", "low bioavailability", "bioavailability",
            "dissolution failure", "dissolution", "food effect", "dose burden", "formulation challenge",
        ],
        "direct_problem_categories": ["dissolution", "bioavailability"],
        "why": "Solubility-enhancement capabilities may be relevant to indexed dissolution, solubility, oral exposure, or bioavailability problem signals.",
        "angle": "Frame outreach around formulation feasibility and evidence review before proposing a specific enabling platform.",
    },
    "bioavailability_enhancement": {
        "label": "bioavailability enhancement",
        "terms": ["bioavailability enhancement", "bioavailability", "absorption", "permeability", "pk", "exposure"],
        "problem_terms": ["low bioavailability", "bioavailability", "absorption", "low exposure", "poor solubility", "dissolution", "food effect"],
        "direct_problem_categories": ["bioavailability", "dissolution"],
        "why": "Bioavailability-enhancement support may be relevant when public evidence indicates low exposure, poor solubility, dissolution, or absorption-related challenges.",
        "angle": "Position as exploratory biopharmaceutics/formulation support requiring validation data.",
    },
    "formulation_cdmo": {
        "label": "formulation CDMO",
        "terms": ["formulation cdmo", "cdmo", "formulation development", "reformulation", "drug delivery", "dosage form"],
        "problem_terms": [
            "dissolution failure", "dissolution", "stability issue", "stability", "formulation challenge", "precipitation",
            "bioavailability", "topical delivery", "topical formulation", "lifecycle reformulation",
        ],
        "direct_problem_categories": ["dissolution", "stability", "bioavailability", "topical_delivery"],
        "why": "A formulation CDMO may be relevant where indexed evidence points to dissolution, stability, bioavailability, precipitation, topical delivery, or formulation robustness problems.",
        "angle": "Use a feasibility/troubleshooting angle and ask for validation data before discussing reformulation.",
    },
    "dissolution_testing": {
        "label": "dissolution testing",
        "terms": ["dissolution testing", "dissolution", "release testing", "ivrt", "release profile"],
        "problem_terms": ["dissolution failure", "failed dissolution", "dissolution specification", "release profile", "drug release above", "drug release below"],
        "direct_problem_categories": ["dissolution"],
        "why": "Dissolution/release testing may be relevant where the indexed problem signal is explicitly dissolution or release-performance related.",
        "angle": "Offer independent testing and method/problem confirmation rather than a proposed fix.",
    },
    "analytical_qc_testing": {
        "label": "analytical/QC testing",
        "terms": ["analytical", "qc", "quality control", "testing", "method validation", "impurity profiling", "assay"],
        "problem_terms": [
            "dissolution failure", "dissolution", "impurity", "assay", "potency", "stability", "batch variability",
            "quality issue", "failed specification", "content uniformity",
        ],
        "direct_problem_categories": ["dissolution", "impurity", "stability", "manufacturing_variability"],
        "why": "Analytical/QC services may be relevant to indexed failed-specification, impurity, dissolution, assay/potency, stability, or batch-variability signals.",
        "angle": "Frame as independent testing, confirmation, and investigation support.",
    },
    "stability_troubleshooting": {
        "label": "stability troubleshooting",
        "terms": ["stability troubleshooting", "stability", "degradation", "shelf life", "excipient compatibility"],
        "problem_terms": ["stability issue", "stability", "degradation", "shelf life", "storage", "precipitation", "formulation robustness"],
        "direct_problem_categories": ["stability"],
        "why": "Stability troubleshooting may be relevant to indexed shelf-life, degradation, storage, or formulation robustness signals.",
        "angle": "Position as stability-risk mapping and validation support, not proof of the root cause.",
    },
    "impurity_investigation": {
        "label": "impurity investigation",
        "terms": ["impurity", "impurity investigation", "nitrosamine", "degradation pathway", "related substances"],
        "problem_terms": ["impurity", "nitrosamine", "degradation product", "related substance", "contaminant", "assay", "failed specification"],
        "direct_problem_categories": ["impurity", "stability"],
        "why": "Impurity investigation may be relevant to indexed impurity, nitrosamine, degradation-product, or failed-specification signals.",
        "angle": "Start with analytical confirmation and pathway investigation; do not assert a root cause from public evidence alone.",
    },
    "sterile_manufacturing_support": {
        "label": "sterile manufacturing support",
        "terms": ["sterile manufacturing", "aseptic", "contamination control", "microbiology", "injectable"],
        "problem_terms": ["sterility", "sterile", "contamination", "aseptic", "microbial", "injectable", "endotoxin", "particulate matter"],
        "direct_problem_categories": ["sterility"],
        "why": "Sterile manufacturing support may be relevant where indexed evidence indicates sterility, contamination, aseptic-processing, or injectable quality signals.",
        "angle": "Use a GMP remediation and contamination-control support angle, requiring direct validation.",
    },
    "topical_delivery_technology": {
        "label": "topical delivery technology",
        "terms": ["topical", "skin delivery", "transdermal", "semi-solid", "cream", "gel", "ointment"],
        "problem_terms": ["topical", "skin delivery", "semi-solid", "cream", "gel", "ointment", "permeation", "dermal"],
        "direct_problem_categories": ["topical_delivery", "formulation"],
        "why": "Topical delivery technologies may be relevant to indexed topical formulation, skin delivery, or semi-solid product signals.",
        "angle": "Frame as topical formulation feasibility and performance assessment, not proof of product need.",
    },
    "drug_device_inhalation_delivery": {
        "label": "drug-device / inhalation delivery",
        "terms": ["drug-device", "device", "inhalation", "inhaler", "aerosol", "dose delivery"],
        "problem_terms": ["delivery limitation", "inhalation", "inhaler", "device", "dose delivery", "delivery variability"],
        "direct_problem_categories": ["delivery"],
        "why": "Drug-device or inhalation delivery support may be relevant when indexed evidence indicates delivery limitation or dose-delivery variability.",
        "angle": "Approach as delivery-performance validation support, not an assumed product failure.",
    },
    "packaging_container_closure_support": {
        "label": "packaging / container-closure support",
        "terms": ["packaging", "container closure", "container-closure", "extractables", "leachables", "moisture", "oxygen"],
        "problem_terms": ["packaging", "container closure", "container-closure", "leak", "extractables", "leachables", "moisture", "oxygen", "stability"],
        "direct_problem_categories": ["packaging_container_closure", "stability"],
        "why": "Packaging/container-closure support may be relevant to indexed packaging, leakage, extractables/leachables, or packaging-linked stability signals.",
        "angle": "Suggest compatibility and container-closure assessment only where the public evidence supports that line of validation.",
    },
}

DISPLAY_CATEGORIES = [rule["label"] for rule in CAPABILITY_RULES.values()]
LABEL_TO_KEY = {rule["label"]: key for key, rule in CAPABILITY_RULES.items()}

_EVIDENCE_RANK = {
    "Tier 1 / high": 1,
    "Tier 2 / moderate": 2,
    "Tier 3 / limited": 3,
    "Tier 4 / weak": 4,
    "not checked": 5,
    "Any": 99,
}


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+/.-]+", " ", str(value).lower())).strip()


def _contains(text: str, term: str) -> bool:
    return _norm(term) in _norm(text)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_as_text(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_as_text(v) for v in value)
    return str(value)


def _split_lines(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    return [x.strip() for x in re.split(r"[\n;,]+", str(value)) if x.strip()]


def _record_text(opp: dict[str, Any]) -> str:
    parts = [
        opp.get("problem_category"), opp.get("problem_signal"), opp.get("failure_signal"),
        opp.get("event_reason"), opp.get("failure_reason"), opp.get("company"), opp.get("product"),
        opp.get("molecule"), opp.get("generic_name"), opp.get("brand_name"), opp.get("source_type"),
    ]
    for e in opp.get("evidence", []) or []:
        if isinstance(e, dict):
            parts.extend([e.get("title"), e.get("supports"), e.get("english_summary")])
            ent = e.get("entities") or {}
            rf = ent.get("recall_fields") or {}
            parts.extend([ent.get("event_reason"), ent.get("dosage_form"), rf.get("reason_for_recall"), rf.get("product_description")])
    return _norm(" ".join(_as_text(x) for x in parts if x))


def _product_dosage_text(opp: dict[str, Any]) -> str:
    parts = [opp.get("product"), opp.get("molecule"), opp.get("generic_name"), opp.get("brand_name"), opp.get("dosage_form")]
    for e in opp.get("evidence", []) or []:
        if isinstance(e, dict):
            ent = e.get("entities") or {}
            rf = ent.get("recall_fields") or {}
            parts.extend([ent.get("product"), ent.get("product_short"), ent.get("dosage_form"), rf.get("product_description")])
    return _norm(" ".join(_as_text(x) for x in parts if x))


def _clean_problem_category(value: Any) -> str:
    return opportunity_index.clean_problem_category(value) or str(value or "")


def _category_key_from_text(value: Any) -> str:
    text = _norm(value)
    if "dissolution" in text or "release performance" in text:
        return "dissolution"
    if "bioavailability" in text or "solubility" in text or "food effect" in text:
        return "bioavailability"
    if "stability" in text or "degradation" in text or "shelf life" in text:
        return "stability"
    if "impurit" in text or "nitrosamine" in text or "assay" in text or "potency" in text:
        return "impurity"
    if "sterility" in text or "contamination" in text or "aseptic" in text:
        return "sterility"
    if "packag" in text or "container closure" in text or "leachable" in text or "extractable" in text:
        return "packaging_container_closure"
    if "manufactur" in text or "batch" in text or "scale" in text or "quality" in text:
        return "manufacturing_variability"
    if "topical" in text or "skin" in text or "semi solid" in text or "cream" in text or "ointment" in text or "gel" in text:
        return "topical_delivery"
    if "polymorph" in text or "solid state" in text or "solid-state" in text or "particle size" in text:
        return "solid_state"
    if "delivery" in text or "inhal" in text or "device" in text:
        return "delivery"
    return ""


def classify_seller_capabilities(selected: list[str] | None, description: str = "") -> list[str]:
    keys: list[str] = []
    for item in selected or []:
        key = LABEL_TO_KEY.get(item) or LABEL_TO_KEY.get(str(item).strip().lower()) or str(item).strip().lower().replace(" ", "_")
        if key in CAPABILITY_RULES and key not in keys:
            keys.append(key)
    blob = _norm(description)
    for key, rule in CAPABILITY_RULES.items():
        if key in keys:
            continue
        if any(_norm(term) in blob for term in rule.get("terms", [])):
            keys.append(key)
    return keys


def _normalise_min_quality(label: str | None) -> int:
    if not label or label == "Any":
        return 99
    if "tier 1" in _norm(label):
        return 1
    if "tier 2" in _norm(label):
        return 2
    if "tier 3" in _norm(label):
        return 3
    if "tier 4" in _norm(label):
        return 4
    return 99


def _evidence_rank(record: dict[str, Any]) -> int:
    label = str(record.get("best_evidence_tier") or record.get("evidence_quality") or "not checked").strip()
    return _EVIDENCE_RANK.get(label, 5)


def _passes_quality(record: dict[str, Any], min_quality: str | None) -> bool:
    required = _normalise_min_quality(min_quality)
    if required == 99:
        return True
    return _evidence_rank(record) <= required


def _matches_region(record: dict[str, Any], regions: list[str] | str | None) -> bool:
    prefs = _split_lines(regions)
    prefs = [p for p in prefs if _norm(p) not in {"any", "all", ""}]
    if not prefs:
        return True
    region = _norm(record.get("region") or "")
    return any(_norm(pref) in region or region in _norm(pref) for pref in prefs)


def _dosage_match(record: dict[str, Any], dosage_focus: list[str] | str | None) -> tuple[bool, str]:
    foci = [f for f in _split_lines(dosage_focus) if _norm(f) not in {"any", "all"}]
    if not foci:
        return True, ""
    text = _product_dosage_text(record)
    for focus in foci:
        f = _norm(focus)
        if f and (f in text or text in f):
            return True, focus
        synonyms = {
            "oral solid": ["tablet", "capsule", "caplet", "oral solid", "solid dosage"],
            "injectable": ["injection", "injectable", "vial", "syringe", "infusion", "intravenous"],
            "topical": ["cream", "ointment", "gel", "topical", "dermal", "transdermal"],
            "inhalation": ["inhaler", "inhalation", "aerosol"],
        }.get(f, [])
        if any(s in text for s in synonyms):
            return True, focus
    return False, ""


def _capability_match(record: dict[str, Any], capability_key: str) -> tuple[int, list[str], str]:
    rule = CAPABILITY_RULES[capability_key]
    text = _record_text(record)
    pcat = _clean_problem_category(record.get("problem_category") or record.get("problem_signal"))
    pkey = _category_key_from_text(pcat)
    hits: list[str] = []
    score = 0
    if pkey and pkey in rule.get("direct_problem_categories", []):
        score += 5
        hits.append(f"problem category = {pcat}")
    for term in sorted(rule.get("problem_terms", []), key=len, reverse=True):
        if _contains(text, term):
            score += 2
            hits.append(term)
            if len(hits) >= 4:
                break
    reason = rule.get("why", "This seller capability may be relevant to the indexed problem signal.")
    return score, hits, reason


def _problem_interest_score(record: dict[str, Any], problem_signals: list[str] | str | None) -> tuple[int, list[str]]:
    interests = _split_lines(problem_signals)
    if not interests:
        return 0, []
    text = _record_text(record)
    hits = [p for p in interests if _norm(p) and _norm(p) in text]
    return (2 * len(hits), hits[:4])


def _lead_status(record: dict[str, Any]) -> str:
    status = db.normalize_status_label(record.get("lead_status") or "") or "needs validation"
    s = _norm(status)
    if "monitor" in s:
        return "monitor only"
    if "outreach" in s and "ready" in s:
        return "outreach-ready"
    if "low" in s or "archive" in s:
        return "low priority / archive"
    return "needs validation"


def _risk_readiness(record: dict[str, Any], fit_strength: str) -> str:
    status = _lead_status(record)
    if status == "monitor only":
        return "monitor only"
    if status == "low priority / archive":
        return "low priority / archive"
    if fit_strength == FIT_STRONG and status == "outreach-ready":
        return "outreach-ready candidate"
    return "needs validation"


def _is_not_checked_label(value: Any) -> bool:
    label = _norm(value or "not checked")
    return label in {"", "not checked", "unchecked", "unknown", "not available"}


def _max_fit_strength(record: dict[str, Any]) -> str:
    """Return the strongest allowed seller-fit label for the evidence maturity.

    This is a display-only trust cap. It does not change matching, queue status,
    enrichment, Opportunity Score, or any stored lead classification.
    """
    coverage = int(record.get("source_coverage_count") or 0)
    has_report = bool(int(record.get("has_full_report") or 0))
    lead_status = _lead_status(record)
    evidence_quality = record.get("evidence_quality") or "not checked"
    best_tier = record.get("best_evidence_tier") or "not checked"
    evidence_rank = _evidence_rank(record)
    cap = FIT_STRONG

    if not has_report and _is_not_checked_label(evidence_quality):
        cap = FIT_MODERATE

    if coverage == 0 and _is_not_checked_label(best_tier):
        cap = FIT_MODERATE

    if lead_status == "monitor only" and evidence_rank > 2:
        cap = FIT_MODERATE

    if lead_status == "low priority / archive":
        # Low-priority/archive leads should never be presented as a strong seller target.
        # If they also lack evidence maturity, keep them as weak/background only.
        if evidence_rank > 2 or (coverage == 0 and _is_not_checked_label(best_tier)):
            cap = FIT_WEAK
        else:
            cap = FIT_MODERATE

    return cap


def _apply_fit_cap(strength: str, cap: str) -> str:
    order = {FIT_WEAK: 0, FIT_MODERATE: 1, FIT_STRONG: 2}
    if not strength:
        return strength
    if order.get(strength, 0) > order.get(cap, 2):
        return cap
    return strength


def _fit_strength(raw_score: int, record: dict[str, Any]) -> str:
    if raw_score <= 0:
        return ""
    coverage = int(record.get("source_coverage_count") or 0)
    has_report = bool(int(record.get("has_full_report") or 0))
    evidence_rank = _evidence_rank(record)
    lead_status = _lead_status(record)
    adjusted = raw_score
    if evidence_rank <= 1:
        adjusted += 2
    elif evidence_rank == 2:
        adjusted += 1
    if coverage > 1:
        adjusted += 1
    if has_report:
        adjusted += 1
    if lead_status == "monitor only":
        adjusted -= 2
    if adjusted >= 8:
        strength = FIT_STRONG
    elif adjusted >= 4:
        strength = FIT_MODERATE
    else:
        strength = FIT_WEAK
    return _apply_fit_cap(strength, _max_fit_strength(record))


def _evidence_proves(record: dict[str, Any]) -> str:
    source_type = _norm(record.get("source_type") or "")
    corr = str(record.get("corroboration_status") or "direct source only")
    bits: list[str] = []
    if "recall" in source_type or "regulator" in _norm(corr):
        bits.append("public regulatory evidence indicates that a product/company event or recall signal was indexed")
    if "trial" in source_type or "clinicaltrials" in source_type:
        bits.append("ClinicalTrials.gov context can confirm trial status and stated registry facts only")
    if str(record.get("label_context_status") or "") == "label context available":
        bits.append("label context can support molecule, dosage-form, route, or manufacturer/distributor context")
    if str(record.get("literature_context_status") or "") == "literature context found":
        bits.append("literature context can support scientific plausibility or solution-fit background")
    if not bits:
        bits.append("public evidence indicates an indexed opportunity signal that requires validation")
    return "; ".join(bits) + "."


def _evidence_does_not_prove(record: dict[str, Any]) -> str:
    return (
        "It does not prove the target company needs the seller's technology, does not prove current commercial urgency, "
        "and does not confirm a product-specific root cause unless a direct official source states it. Label context is not defect/root-cause evidence; literature is not product-specific proof."
    )


def _validation_questions(capability_labels: list[str], record: dict[str, Any]) -> list[str]:
    pcat = _clean_problem_category(record.get("problem_category")) or "the indexed problem"
    return [
        f"Is {pcat} still relevant/current, or was it historical and closed?",
        "What direct evidence, batch data, CMC data, or performance data confirms the problem mechanism?",
        f"Where could {', '.join(capability_labels[:2]) or 'the seller capability'} support validation, troubleshooting, or feasibility work?",
        "Who owns the relevant CMC, quality, formulation, or lifecycle-management decision internally?",
    ]


def _safe_outreach_wording(seller_name: str, capability_labels: list[str], record: dict[str, Any]) -> str:
    seller = seller_name or "Our team"
    caps = ", ".join(capability_labels[:3]) or "relevant technical support"
    pcat = _clean_problem_category(record.get("problem_category")) or "the indexed public signal"
    return (
        f"{seller} may be relevant to public evidence indicating {pcat}. We would position this as a validation-led discussion around {caps}, "
        "not as a claim that the company needs a specific technology or that root cause is confirmed."
    )


def _bd_angle(capability_labels: list[str], record: dict[str, Any]) -> str:
    caps = ", ".join(capability_labels[:3]) or "technical support"
    pcat = _clean_problem_category(record.get("problem_category")) or "the indexed problem signal"
    return f"Explore whether {caps} could support validation, troubleshooting, or feasibility work around {pcat}; requires target-side confirmation."


def _status(record: dict[str, Any], key: str, default: str = "not checked") -> str:
    return str(db.normalize_status_label(record.get(key) or default) or default)


def _base_match_record(record: dict[str, Any]) -> dict[str, Any]:
    meta = _common_match_metadata(record)
    product = meta.get("short_product") or record.get("product") or ""
    return {
        **meta,
        "target_company": record.get("company") or "",
        "company": record.get("company") or "",
        "product": product,
        "molecule": record.get("molecule") or record.get("generic_name") or "",
        "problem_category": _clean_problem_category(record.get("problem_category") or record.get("problem_signal")),
        "source_type": record.get("source_type") or meta.get("source_type") or "",
        "source_id": record.get("source_id") or meta.get("source_id") or "",
        "region": record.get("region") or "",
        "opportunity_score": record.get("score") if record.get("score") is not None else record.get("opportunity_score"),
        "grade": record.get("grade") or "",
        "lead_status": _lead_status(record),
        "queue_status": record.get("queue_status") or "",
        "has_full_report": bool(int(record.get("has_full_report") or 0) or record.get("report_md")),
        "evidence_quality": record.get("evidence_quality") or "not checked",
        "best_evidence_tier": record.get("best_evidence_tier") or record.get("evidence_quality") or "not checked",
        "corroboration_status": _status(record, "corroboration_status", "direct source only"),
        "official_followup_status": _status(record, "official_followup_status", "not checked"),
        "label_context_status": _status(record, "label_context_status", "not checked"),
        "clinical_trial_context_status": _status(record, "clinical_trial_context_status", "not checked"),
        "literature_context_status": _status(record, "literature_context_status", "not checked"),
        "source_coverage_count": int(record.get("source_coverage_count") or 0),
        "report_path": record.get("report_path") or meta.get("report_path") or "",
        "stored_report_md": record.get("report_md") or meta.get("stored_report_md") or "",
    }


def match_seller_to_targets(
    seller_name: str,
    seller_description: str,
    capability_categories: list[str] | None,
    indexed_records: list[dict[str, Any]] | None,
    *,
    problem_signals: list[str] | str | None = None,
    dosage_focus: list[str] | str | None = None,
    region_preference: list[str] | str | None = None,
    min_evidence_quality: str | None = "Any",
    include_monitor_only: bool = False,
    max_targets: int = 10,
    include_weak: bool = False,
) -> dict[str, Any]:
    rows = indexed_records or []
    if not rows:
        return {"status": "empty", "message": EMPTY_INDEX_MESSAGE, "matches": []}

    capability_keys = classify_seller_capabilities(capability_categories, seller_description)
    if not capability_keys:
        return {
            "status": "no_profile",
            "message": "Select at least one seller technology/service category or describe the seller capability clearly.",
            "matches": [],
        }
    capability_labels = [CAPABILITY_RULES[k]["label"] for k in capability_keys]

    records = prepare_existing_opportunities(rows, [])
    matches: list[dict[str, Any]] = []
    hidden_weak = 0
    hidden_monitor = 0
    hidden_quality = 0
    hidden_region = 0

    for record in records:
        if str(record.get("queue_status") or "") in {"archived", "rejected"}:
            continue
        lead_status = _lead_status(record)
        if not include_monitor_only and lead_status == "monitor only":
            hidden_monitor += 1
            continue
        if not _passes_quality(record, min_evidence_quality):
            hidden_quality += 1
            continue
        if not _matches_region(record, region_preference):
            hidden_region += 1
            continue

        dosage_ok, dosage_hit = _dosage_match(record, dosage_focus)
        if not dosage_ok:
            # Modality focus is a soft filter to avoid obviously irrelevant targets.
            continue

        raw_score = 0
        matched_caps: list[str] = []
        why_parts: list[str] = []
        hit_terms: list[str] = []
        for key in capability_keys:
            s, hits, why = _capability_match(record, key)
            if s > 0:
                raw_score += s
                matched_caps.append(CAPABILITY_RULES[key]["label"])
                why_parts.append(why)
                hit_terms.extend(hits)

        p_bonus, p_hits = _problem_interest_score(record, problem_signals)
        raw_score += p_bonus
        hit_terms.extend(p_hits)
        if dosage_hit:
            raw_score += 1
            hit_terms.append(f"dosage/modality focus = {dosage_hit}")
        if raw_score <= 0:
            continue

        fit_strength = _fit_strength(raw_score, record)
        if fit_strength == FIT_WEAK and not include_weak:
            hidden_weak += 1
            continue

        base = _base_match_record(record)
        matched_caps = matched_caps or capability_labels
        why = why_parts[0] if why_parts else "This seller capability may be relevant to the indexed problem signal."
        if hit_terms:
            why += " Match basis: " + "; ".join(dict.fromkeys(str(h) for h in hit_terms[:5])) + "."
        if lead_status == "monitor only":
            why += " This remains monitor only because public evidence appears historical/limited unless stronger current evidence is found."

        result = {
            **base,
            "seller_name": seller_name or "",
            "seller_capability": "; ".join(matched_caps),
            "fit_strength": fit_strength,
            "seller_fit_score_raw": raw_score,
            "why_fit": why,
            "what_evidence_proves": _evidence_proves(record),
            "what_evidence_does_not_prove": _evidence_does_not_prove(record),
            "safe_bd_angle": _bd_angle(matched_caps, record),
            "recommended_bd_angle": _bd_angle(matched_caps, record),
            "validation_questions": _validation_questions(matched_caps, record),
            "safe_outreach_wording": _safe_outreach_wording(seller_name, matched_caps, record),
            "risk_readiness_label": _risk_readiness(record, fit_strength),
            "preview_message": PREVIEW_MESSAGE if not base.get("has_full_report") else "",
        }
        matches.append(result)

    fit_order = {FIT_STRONG: 0, FIT_MODERATE: 1, FIT_WEAK: 2}
    matches.sort(key=lambda m: (
        fit_order.get(m.get("fit_strength"), 9),
        -int(m.get("source_coverage_count") or 0),
        -int(m.get("opportunity_score") or 0),
        0 if m.get("has_full_report") else 1,
        m.get("risk_readiness_label") == "monitor only",
    ))
    matches = matches[: max(1, int(max_targets or 10))]

    if not matches:
        return {
            "status": "no_matches",
            "message": NO_TARGETS_MESSAGE,
            "matches": [],
            "hidden_weak_count": hidden_weak,
            "hidden_monitor_count": hidden_monitor,
            "hidden_quality_count": hidden_quality,
            "hidden_region_count": hidden_region,
            "seller_capabilities": capability_labels,
        }

    return {
        "status": "ok",
        "message": f"Found {len(matches)} target opportunity match(es) from indexed PharmaTune evidence.",
        "matches": matches,
        "hidden_weak_count": hidden_weak,
        "hidden_monitor_count": hidden_monitor,
        "hidden_quality_count": hidden_quality,
        "hidden_region_count": hidden_region,
        "seller_capabilities": capability_labels,
        "fit_note": FIT_NOTE,
    }


def export_seller_target_matches_csv(result: dict[str, Any]) -> bytes:
    fields = [
        "seller_name", "seller_capability", "target_company", "product", "molecule",
        "problem_category", "source_type", "source_id", "region", "opportunity_score",
        "grade", "lead_status", "fit_strength", "why_fit", "evidence_quality",
        "best_evidence_tier", "corroboration_status", "official_followup_status",
        "label_context_status", "clinical_trial_context_status", "literature_context_status",
        "has_full_report", "report_path", "safe_bd_angle", "validation_questions",
    ]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fields)
    writer.writeheader()
    for match in result.get("matches", []) or []:
        row = {k: match.get(k, "") for k in fields}
        row["has_full_report"] = "yes" if match.get("has_full_report") else "no"
        if isinstance(row.get("validation_questions"), list):
            row["validation_questions"] = " | ".join(row["validation_questions"])
        for key in list(row.keys()):
            if key.endswith("_status") or key in {"corroboration_status", "lead_status"}:
                row[key] = db.normalize_status_label(row.get(key)) or ""
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig")
