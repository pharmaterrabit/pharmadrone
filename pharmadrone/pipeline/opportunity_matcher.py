"""Deterministic Phase 1 opportunity matching for PharmaTune/PharmaDrone.

This module is intentionally small and read-only. It does not call the web,
LLMs, or databases. It only matches a user query against opportunities already
created by the existing evidence pipeline.

Patch v1.2 tightens dissolution/release-performance matching so dosage-form
descriptors such as "extended release" or "immediate release" are treated as
background only unless the evidence states an actual dissolution or
release-performance problem.
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
MATCH_SCOPE_LABEL = "Matched against currently indexed PharmaTune evidence. Use Generate/Refresh to add new signals."
TECH_CERTAINTY_NOTE = (
    "Potential relevance only — not proof that the company needs this technology. "
    "Requires validation before outreach."
)

MATCH_DIRECT = "Direct match"
MATCH_RELATED = "Strong related match"
MATCH_WEAK = "Weak/background match"
MATCH_DESCRIPTOR_ONLY = "Background dosage-form descriptor only"
_STRENGTH_SCORE = {
    MATCH_DIRECT: 4,
    MATCH_RELATED: 3,
    MATCH_WEAK: 1,
    MATCH_DESCRIPTOR_ONLY: 1,
}


def _norm(text: Any) -> str:
    """Lower-case, punctuation-light text for deterministic phrase matching."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+/.-]+", " ", str(text).lower())).strip()


def _contains_any(text: str, terms: list[str] | tuple[str, ...]) -> bool:
    t = _norm(text)
    return any(_norm(term) in t for term in terms if term)


def _first_hit(text: str, terms: list[str] | tuple[str, ...]) -> str | None:
    t = _norm(text)
    # Prefer the most specific phrase when several terms match the same text.
    for term in sorted((x for x in terms if x), key=lambda x: len(_norm(x)), reverse=True):
        nt = _norm(term)
        if nt and nt in t:
            return term
    return None


# ---------------------------------------------------------------------------
# Rule maps shown to the user. Matching strictness is implemented below in
# CATEGORY_MATCH_PROFILES so these user-facing lists can remain business useful.
# ---------------------------------------------------------------------------
PROBLEM_RULES: dict[str, dict[str, Any]] = {
    "dissolution": {
        "label": "Dissolution / release performance",
        "terms": [
            "dissolution", "failed dissolution", "dissolution specification",
            "drug release", "release performance", "dissolution testing",
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
            "Validate whether the evidence indicates a product-specific dissolution or release-performance signal; "
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
            "failed specification", "failed specifications", "assay", "content uniformity", "contaminant",
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
            "linking it to dissolution, stability, or manufacturability."
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
        # Keep this strict: do not include broad stability/manufacturing unless a
        # particle-size, solid-state, dissolution, bioavailability, or oral-solid
        # signal is directly present.
        "problem_categories": ["dissolution", "bioavailability", "solid_state"],
        "why_fit": (
            "Particle engineering may have potential relevance where evidence-backed problem signals mention dissolution, "
            "bioavailability, particle-size sensitivity, solid-state behaviour, polymorph issues, or oral solid-dose performance."
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
        "problem_categories": ["dissolution", "solid_state", "stability"],
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
        "problem_categories": ["dissolution", "stability", "bioavailability"],
        "why_fit": (
            "A formulation CDMO may have potential relevance where existing evidence indicates formulation optimisation, "
            "dissolution, stability, bioavailability, or lifecycle reformulation needs."
        ),
        "safe_outreach_angle": (
            "Frame as a feasibility and troubleshooting conversation, not as proof that outsourcing or reformulation is required."
        ),
    },
}

_DISSOLUTION_DIRECT_TERMS = (
    "failed dissolution specifications", "failed dissolution specification",
    "dissolution failure", "failed dissolution", "fails dissolution",
    "dissolution specification", "dissolution specifications",
    "dissolution out of specification", "dissolution oos",
    "drug release above specification", "drug release below specification",
    "drug release above specifications", "drug release below specifications",
    "drug release specification failure", "drug release specifications failure",
    "failed drug release", "release-rate failure", "release rate failure",
    "root-cause category dissolution", "dissolution/release performance",
    "release-performance failure", "drug-release performance failure",
    "dissolution",
)
# Strong related terms must describe an actual dissolution/release-performance
# problem or test. They must not be plain dosage-form descriptors.
_DISSOLUTION_RELATED_TERMS = (
    "dissolution testing", "dissolution test", "dissolution specification",
    "dissolution specifications", "dissolution profile out of specification",
    "dissolution profile out-of-specification", "failed dissolution",
    "drug release above specification", "drug release below specification",
    "drug release above specifications", "drug release below specifications",
    "release-rate failure", "release rate failure",
    "modified-release performance issue", "modified release performance issue",
    "extended-release performance failure", "extended release performance failure",
    "in vitro release failure", "release profile out of specification",
    "release profile out-of-specification", "release performance failure",
    "drug-release performance issue", "drug release performance issue",
    "oral solid-dose performance problem", "oral solid dose performance problem",
    "oral solid-dose release performance problem", "oral solid dose release performance problem",
)
_DISSOLUTION_WEAK_TERMS = (
    "release", "oos", "out of specification", "specification", "specifications", "qc",
    "batch release", "press release", "failed specifications", "failed specification",
)
_DISSOLUTION_DESCRIPTOR_ONLY_TERMS = (
    "extended release", "extended-release", "immediate release", "immediate-release",
    "modified release", "modified-release", "delayed release", "delayed-release",
    "controlled release", "controlled-release", "sustained release", "sustained-release",
    "oral", "tablet", "tablets", "capsule", "capsules",
)
_INJECTABLE_TERMS = (
    "injection", "injectable", "injectables", "vial", "syringe", "infusion", "iv ",
    "intravenous", "parenteral", "ampule", "ampoule", "prefilled syringe",
)
_ORAL_SOLID_TERMS = (
    "tablet", "tablets", "capsule", "capsules", "caplet", "caplets", "oral solid",
    "solid dose", "solid dosage", "immediate release", "extended release", "modified release",
)

CATEGORY_MATCH_PROFILES: dict[str, dict[str, tuple[str, ...]]] = {
    "dissolution": {
        "direct": _DISSOLUTION_DIRECT_TERMS,
        "related": _DISSOLUTION_RELATED_TERMS,
        "weak": _DISSOLUTION_WEAK_TERMS,
        "descriptor_only": _DISSOLUTION_DESCRIPTOR_ONLY_TERMS,
    },
    "stability": {
        "direct": ("stability", "unstable", "degradation", "degradation product", "shelf life", "shelf-life", "expiry", "storage condition"),
        "related": ("temperature excursion", "humidity", "photostability", "accelerated stability", "storage"),
        "weak": ("expired", "date", "storage"),
    },
    "sterility": {
        "direct": ("sterility", "sterile", "aseptic", "microbial contamination", "contamination", "endotoxin"),
        "related": ("microbiology", "environmental monitoring", "particulate matter", "bacteria", "fungal"),
        "weak": ("clean", "quality"),
    },
    "impurity": {
        "direct": ("impurity", "impurities", "nitrosamine", "degradation product", "related substance", "contaminant", "assay failure", "failed assay", "content uniformity"),
        "related": ("failed specification", "failed specifications", "out of specification", "oos", "supplier qualification", "process impurity"),
        "weak": ("specification", "specifications", "qc"),
    },
    "solid_state": {
        "direct": ("solid-state", "solid state", "polymorph", "crystallinity", "amorphous", "crystal form", "particle size", "particle-size", "solid form", "form conversion"),
        "related": ("salt form", "cocrystal", "xrpd", "dsc", "tga", "particle-size distribution"),
        "weak": ("particles", "solid"),
    },
    "bioavailability": {
        "direct": ("bioavailability", "low bioavailability", "poor bioavailability", "poor solubility", "low solubility", "bcs ii", "bcs iv"),
        "related": ("absorption", "oral exposure", "low exposure", "pharmacokinetic", "auc", "cmax", "oral performance"),
        "weak": ("pk", "exposure"),
    },
    "packaging_container_closure": {
        "direct": ("packaging", "container closure", "container-closure", "closure", "seal", "leak", "extractables", "leachables"),
        "related": ("moisture", "oxygen", "blister", "vial", "syringe", "container"),
        "weak": ("label", "package"),
    },
    "manufacturing_variability": {
        "direct": ("manufacturing", "process validation", "batch variability", "failed batch", "scale-up", "scale up", "cgmp", "cGMP"),
        "related": ("deviation", "quality system", "production", "cmc", "process optimisation", "process optimization"),
        "weak": ("batch", "process", "gmp"),
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
    # Phase 2 opportunity_index metadata must survive even if data_json was
    # created before those fields existed. These fields drive freshness and queue
    # display; they do not change matching strictness.
    for key in (
        "stable_lead_id", "first_seen_at", "last_seen_at", "last_updated_at",
        "last_checked_at", "novelty_status", "queue_status", "has_full_report",
        "report_path", "report_opportunity_id", "source_id", "lead_status",
        "enrichment_status", "corroboration_status", "evidence_quality",
        "source_coverage_count", "last_enrichment_check", "tier1_count", "tier2_count",
        "tier3_count", "tier4_count", "regulator_confirmed", "company_confirmed",
        "literature_supported", "external_corroboration_found",
    ):
        if key in row and row.get(key) not in (None, ""):
            merged[key] = row.get(key)
    return merged




def _clean_problem_category_label(value: Any) -> str:
    raw = str(value or "").strip()
    text = _norm(raw)
    if not text:
        return ""
    if "impurit" in text or "nitrosamine" in text or "related substance" in text:
        return "impurity issue"
    if "dissolution" in text:
        return "dissolution failure"
    if "stability" in text or "degradation" in text:
        return "stability issue"
    if "sterility" in text or "contamination" in text:
        return "sterility issue"
    if "bioavailability" in text or "solubility" in text:
        return "bioavailability issue"
    return raw

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
        if opp.get("problem_category"):
            opp["problem_category"] = _clean_problem_category_label(opp.get("problem_category"))
        records.append(opp)
    return records


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_stringify(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_stringify(v) for v in value)
    return str(value)


def _labelled_opp_fields(opp: dict[str, Any]) -> list[tuple[str, str]]:
    """Structured fields that should drive direct matches.

    This deliberately excludes broad report_md text so a generic generated report
    section cannot create a Direct or Related match by itself.
    """
    labels = {
        "problem_signal": "stored problem_signal",
        "problem_category": "stored problem_category",
        "failure_signal": "failure-signal problem classification",
        "failure_reason": "failure-signal stated reason",
        "event_reason": "confirmed stated reason",
        "failure_event_summary": "confirmed event summary",
        "confirmed_fact": "confirmed fact",
        "root_cause": "root-cause section category",
        "root_cause_summary": "root-cause section summary",
        "solution_fit": "solution-fit section",
    }
    out: list[tuple[str, str]] = []
    for key, label in labels.items():
        text = _stringify(opp.get(key))
        if text:
            out.append((label, text))
    return out


def _labelled_recall_reason_fields(opp: dict[str, Any]) -> list[tuple[str, str]]:
    """Structured recall/event reasons from evidence.

    openFDA recall reason is the strongest field for a regulatory product problem
    signal. DB evidence rows may not include nested entities, but data_json does.
    """
    out: list[tuple[str, str]] = []
    for e in opp.get("evidence", []) or []:
        if not isinstance(e, dict):
            continue
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        if rf.get("reason_for_recall"):
            out.append(("FDA recall reason", str(rf["reason_for_recall"])))
        if ent.get("event_reason"):
            out.append(("evidence event reason", str(ent["event_reason"])))
        # For DB-only evidence, supports often carries the recall reason.
        if e.get("source_type") == "recall" and e.get("supports"):
            out.append(("recall evidence supports", str(e["supports"])))
    return out


def _labelled_evidence_fields(opp: dict[str, Any]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for e in opp.get("evidence", []) or []:
        if not isinstance(e, dict):
            continue
        for key, label in (
            ("title", "evidence title"),
            ("english_summary", "evidence summary"),
            ("supports", "evidence supports"),
            ("raw_text", "evidence raw text"),
        ):
            if e.get(key):
                out.append((label, str(e[key])))
    return out


def _broad_background_text(opp: dict[str, Any]) -> str:
    """Text allowed only for weak/background matches."""
    fields = (
        "company", "parent_company", "product", "generic_name", "brand_name",
        "indication", "therapeutic_area", "region", "stage", "discovery_reason",
        "interpretation", "report_md",
    )
    return " ".join(str(opp.get(f, "")) for f in fields if opp.get(f))


def _product_dosage_text(opp: dict[str, Any]) -> str:
    bits = [
        opp.get("product"), opp.get("brand_name"), opp.get("generic_name"),
        opp.get("dosage_form"),
    ]
    for e in opp.get("evidence", []) or []:
        if not isinstance(e, dict):
            continue
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        bits.extend([
            ent.get("product"), ent.get("product_short"), ent.get("dosage_form"),
            rf.get("product_description"),
        ])
    return _norm(" ".join(str(x) for x in bits if x))


def _looks_injectable(opp: dict[str, Any]) -> bool:
    text = _product_dosage_text(opp)
    return _contains_any(text, _INJECTABLE_TERMS)


def _looks_oral_solid(opp: dict[str, Any]) -> bool:
    text = _product_dosage_text(opp)
    return _contains_any(text, _ORAL_SOLID_TERMS)


def _category_profile(category_key: str) -> dict[str, tuple[str, ...]]:
    return CATEGORY_MATCH_PROFILES.get(category_key, {
        "direct": tuple(PROBLEM_RULES[category_key]["terms"]),
        "related": tuple(PROBLEM_RULES[category_key]["terms"]),
        "weak": tuple(),
    })


def _make_reason(strength: str, field_label: str, hit: str, field_text: str) -> str:
    # Keep the reason short and auditable. For exact problem_signal, show the value.
    clean_text = " ".join(str(field_text).split())
    if len(clean_text) > 120:
        clean_text = clean_text[:117].rsplit(" ", 1)[0] + "…"
    if field_label == "stored problem_signal":
        return f"{strength}: stored problem_signal = {clean_text}"
    if field_label == "stored problem_category":
        return f"{strength}: stored problem_category = {clean_text}"
    if strength == MATCH_DESCRIPTOR_ONLY:
        return f"{strength}: {field_label} contains dosage-form descriptor '{hit}' only"
    return f"{strength}: {field_label} contains {hit}"


def _match_problem_category(opp: dict[str, Any], category_key: str) -> dict[str, Any] | None:
    """Return one Direct/Strong related/Weak match for an opportunity/category.

    Priority order:
      1) structured problem fields and recall reasons for Direct matches
      2) specific problem/performance evidence for Strong related matches
      3) broad generic/background text as hidden weak matches only

    Dissolution is intentionally strict: dosage-form descriptors such as
    "extended release" and "immediate release" are background only unless the
    text also states an actual dissolution or release-performance failure.
    """
    profile = _category_profile(category_key)
    structured = _labelled_opp_fields(opp)
    recall_reasons = _labelled_recall_reason_fields(opp)
    evidence = _labelled_evidence_fields(opp)

    direct_terms = profile["direct"]
    related_terms = profile["related"]
    weak_terms = profile.get("weak", tuple())
    descriptor_terms = profile.get("descriptor_only", tuple())

    # Direct: structured fields first, including recall reasons. Evidence summaries
    # cannot create a Direct match by themselves.
    for field_label, text in structured + recall_reasons:
        hit = _first_hit(text, direct_terms)
        if hit:
            return {
                "strength": MATCH_DIRECT,
                "score": 100,
                "terms": sorted({hit}),
                "reason": _make_reason(MATCH_DIRECT, field_label, hit, text),
                "source_field": field_label,
            }

    # Dissolution/injectable guardrail: injectables should not appear for a
    # dissolution-failure search unless a structured field or recall/event reason
    # directly states dissolution or release-performance failure. Generic evidence
    # wording such as "extended release" is not enough.
    if category_key == "dissolution" and _looks_injectable(opp):
        return None

    # Strong related: structured, recall, or focused evidence snippets only.
    # For dissolution, related terms are restricted to actual performance/problem
    # phrases such as dissolution testing or release-profile OOS.
    for field_label, text in structured + recall_reasons + evidence:
        hit = _first_hit(text, related_terms)
        if hit:
            return {
                "strength": MATCH_RELATED,
                "score": 70,
                "terms": sorted({hit}),
                "reason": _make_reason(MATCH_RELATED, field_label, hit, text),
                "source_field": field_label,
            }

    # Descriptor-only/background: hidden by default. "Extended release" and
    # "immediate release" are dosage-form descriptors, not evidence of a failure.
    if category_key == "dissolution":
        for field_label, text in structured + recall_reasons + evidence + [("broad generated report/background text", _broad_background_text(opp))]:
            hit = _first_hit(text, descriptor_terms)
            if hit:
                return {
                    "strength": MATCH_DESCRIPTOR_ONLY,
                    "score": 10,
                    "terms": sorted({hit}),
                    "reason": _make_reason(MATCH_DESCRIPTOR_ONLY, field_label, hit, text),
                    "source_field": field_label,
                }

    # Weak/background: hidden by default. Generic terms live here only.
    broad = _broad_background_text(opp)
    for field_label, text in evidence + [("broad generated report/background text", broad)]:
        hit = _first_hit(text, weak_terms)
        if hit:
            return {
                "strength": MATCH_WEAK,
                "score": 15,
                "terms": sorted({hit}),
                "reason": _make_reason(MATCH_WEAK, field_label, hit, text),
                "source_field": field_label,
            }
    return None

def _source_summary(evidence: list[dict[str, Any]]) -> str:
    seen: list[str] = []
    for e in evidence:
        if not isinstance(e, dict):
            continue
        src = e.get("source_name") or e.get("source_type") or e.get("source_category")
        if src and src not in seen:
            seen.append(str(src))
    return ", ".join(seen[:4]) or "Stored opportunity evidence"


def _source_type_label(evidence: list[dict[str, Any]]) -> str:
    """Short user-facing source type summary for BD lead cards."""
    labels = {
        "recall": "FDA recall",
        "trial": "ClinicalTrials.gov trial",
        "web": "web",
        "company": "company/web",
        "paper": "scientific paper",
        "patent": "patent",
        "label": "drug label",
    }
    seen: list[str] = []
    for e in evidence or []:
        if not isinstance(e, dict):
            continue
        stype = _norm(e.get("source_type") or "")
        label = labels.get(stype, stype or _norm(e.get("source_category") or ""))
        if label and label not in seen:
            seen.append(label)
    return ", ".join(seen[:3]) or "stored evidence"


def _clean_product_text(text: Any) -> str:
    t = " ".join(str(text or "").replace("\n", " ").split())
    if not t:
        return ""
    t = re.sub(r"\bNDC(?:s)?\s*[:#]?\s*[0-9-]+(?:\s*(?:,|and)\s*[0-9-]+)*", "", t, flags=re.I)
    t = re.sub(r"\bUPC\s*[:#]?\s*[0-9-]+", "", t, flags=re.I)
    t = re.sub(r"\bLot(?:s)?\s*[:#]?\s*[A-Za-z0-9, -]+", "", t, flags=re.I)
    t = re.split(
        r"\b(?:packaged in|packaged as|bottle[s]? of|unit dose|carton[s]? of|blister[s]? of|case[s]? of|recall number|quantity|lot number|expiration)\b",
        t,
        maxsplit=1,
        flags=re.I,
    )[0]
    t = re.split(r"[;|]", t, maxsplit=1)[0]
    t = re.sub(r"\s*,\s*", ", ", t).strip(" ,.-")
    return t


def _short_product_name(opp: dict[str, Any]) -> str:
    """Readable product label without NDC/package clutter.

    Recall product names often include package size, NDC, lot, quantity, and
    strength. For the matcher card, keep the drug/product and dosage-form phrase
    where possible and push the long description to details.
    """
    candidates = [
        opp.get("product"),
        opp.get("brand_name"),
        opp.get("generic_name"),
    ]
    for e in opp.get("evidence", []) or []:
        if not isinstance(e, dict):
            continue
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        candidates.extend([ent.get("product_short"), ent.get("product"), rf.get("product_description")])

    product = next((_clean_product_text(x) for x in candidates if _clean_product_text(x)), "Unknown product")

    # Prefer a name ending at the dosage-form word. This turns long FDA package
    # descriptions into labels such as "Nitrofurantoin Capsules".
    form_capture = re.search(
        r"^(.{2,95}?\b(?:capsules?|tablets?|caplets?|injections?|solution|suspension|cream|ointment|gel|patches|vials?|syringes?)\b)",
        product,
        flags=re.I,
    )
    if form_capture:
        product = form_capture.group(1)

    # Remove trailing strength/pack descriptors after the main name when the
    # dosage form was not captured cleanly.
    product = re.sub(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|kg|ml|mL|%)\b.*$", "", product, flags=re.I).strip(" ,.-")
    product = re.sub(r"\bUSP\b", "", product, flags=re.I).strip(" ,.-")
    product = re.sub(r"\s{2,}", " ", product)
    if len(product) > 80:
        product = product[:77].rsplit(" ", 1)[0] + "…"
    return product or "Unknown product"


def _long_product_description(opp: dict[str, Any]) -> str:
    details: list[str] = []
    for key in ("product", "brand_name", "generic_name", "dev_code"):
        val = opp.get(key)
        if val and str(val) not in details:
            details.append(str(val))
    for e in opp.get("evidence", []) or []:
        if not isinstance(e, dict):
            continue
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        for val in (rf.get("product_description"), e.get("title"), e.get("supports")):
            if val and str(val) not in details:
                details.append(str(val))
    return "\n\n".join(details[:6]) or "No additional product description stored."


def _display_title(opp: dict[str, Any]) -> str:
    company = opp.get("company") or "Unknown company"
    return f"{company} — {_short_product_name(opp)}"


def _last_generated_date(opp: dict[str, Any]) -> str:
    if int(opp.get("has_full_report") or 0):
        return str(opp.get("created_at") or opp.get("date_generated") or opp.get("generated_at") or opp.get("last_updated_at") or "")
    return ""


def _source_freshness(opp: dict[str, Any]) -> str:
    lead_status = _norm(opp.get("lead_status") or "")
    novelty = _norm(opp.get("novelty_status") or "")
    if "monitor" in lead_status:
        return "monitor only"
    if novelty == "updated":
        return "updated"
    if novelty == "new":
        return "current"
    return "current" if opp.get("last_checked_at") else "stale"


def _confirmed_fact(opp: dict[str, Any]) -> str:
    for key in ("confirmed_fact", "failure_event_summary", "event_reason", "problem_signal"):
        val = opp.get(key)
        if val:
            return str(val)
    for _label, reason in _labelled_recall_reason_fields(opp):
        if reason:
            return str(reason)
    for e in opp.get("evidence", []) or []:
        if isinstance(e, dict):
            for key in ("supports", "english_summary", "title"):
                val = e.get(key)
                if val:
                    return str(val)
    return "Evidence-backed product/company signal stored by the existing pipeline."


def _target_label(opp: dict[str, Any]) -> str:
    return _display_title(opp)


def _common_match_metadata(opp: dict[str, Any]) -> dict[str, Any]:
    evidence = opp.get("evidence", []) or []
    has_full_report = int(opp.get("has_full_report") or (1 if opp.get("report_md") else 0) or 0)
    return {
        "opportunity_id": opp.get("stable_lead_id") or opp.get("id") or "",
        "stable_lead_id": opp.get("stable_lead_id") or opp.get("id") or "",
        "short_product": _short_product_name(opp),
        "display_title": _display_title(opp),
        "long_product_description": _long_product_description(opp),
        "source_type": opp.get("source_type") or _source_type_label(evidence),
        "source_id": opp.get("source_id") or "",
        "first_seen_at": opp.get("first_seen_at") or "",
        "last_seen_at": opp.get("last_seen_at") or "",
        "last_checked_at": opp.get("last_checked_at") or "",
        "last_updated_at": opp.get("last_updated_at") or "",
        "source_freshness": _source_freshness(opp),
        "novelty_status": opp.get("novelty_status") or "",
        "queue_status": opp.get("queue_status") or "",
        "has_full_report": bool(has_full_report),
        "report_path": opp.get("report_path") or "",
        "last_generated_date": _last_generated_date(opp),
        "stored_report_md": opp.get("report_md") or "",
        "enrichment_status": opp.get("enrichment_status") or "enrichment not checked",
        "corroboration_status": opp.get("corroboration_status") or "direct source only",
        "evidence_quality": opp.get("evidence_quality") or "not checked",
        "source_coverage_count": opp.get("source_coverage_count") or 0,
        "last_enrichment_check": opp.get("last_enrichment_check") or "",
        "tier1_count": opp.get("tier1_count") or 0,
        "tier2_count": opp.get("tier2_count") or 0,
        "tier3_count": opp.get("tier3_count") or 0,
        "tier4_count": opp.get("tier4_count") or 0,
    }


def _normalise_lead_status(value: Any) -> str | None:
    """Return one of the user-facing lead status labels, if recognised."""
    raw = _norm(value)
    if not raw:
        return None
    if "outreach" in raw and "ready" in raw:
        return "outreach-ready"
    if "needs" in raw and "validation" in raw:
        return "needs validation"
    if "monitor" in raw:
        return "monitor only"
    if "low" in raw and ("priority" in raw or "archive" in raw):
        return "low priority / archive"
    return None


def _stored_report_lead_status(opp: dict[str, Any]) -> str | None:
    """Prefer the status already written in the stored full report.

    The matcher card and the expandable report must not disagree.  Existing
    reports expose this as, for example:
    **Lead classification:** **Monitor only**
    """
    report = str(opp.get("report_md") or opp.get("stored_report_md") or "")
    if not report:
        return None
    patterns = (
        r"\*\*Lead classification:\*\*\s*\*\*([^*]+)\*\*",
        r"Lead classification:\*{0,2}\s*\*\*([^*]+)\*\*",
        r"Lead classification:\*{0,2}\s*([^\n—-]+)",
        r"\*\*Lead status:\*\*\s*\*\*([^*]+)\*\*",
        r"Lead status:\*{0,2}\s*\*\*([^*]+)\*\*",
        r"Lead status:\*{0,2}\s*([^\n—-]+)",
    )
    for pat in patterns:
        m = re.search(pat, report, flags=re.I)
        if m:
            status = _normalise_lead_status(m.group(1))
            if status:
                return status
    return None


def _status_text_blob(opp: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "status", "stage", "problem_signal", "problem_category", "event_reason",
        "failure_event_summary", "confirmed_fact", "root_cause", "root_cause_summary",
        "discovery_reason", "interpretation",
    ):
        if opp.get(key):
            parts.append(_stringify(opp.get(key)))
    for label, text in _labelled_recall_reason_fields(opp):
        parts.append(f"{label}: {text}")
    for e in opp.get("evidence", []) or []:
        if not isinstance(e, dict):
            continue
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        parts.extend(_stringify(x) for x in (
            e.get("title"), e.get("supports"), e.get("english_summary"),
            ent.get("event_type"), ent.get("event_reason"),
            rf.get("status"), rf.get("classification"), rf.get("product_quantity"),
            rf.get("distribution_pattern"), rf.get("reason_for_recall"),
        ) if x)
    return _norm(" ".join(parts))


def _recall_status_meta(opp: dict[str, Any]) -> dict[str, bool]:
    blob = _status_text_blob(opp)
    terminated = any(x in blob for x in (
        "terminated", "recall terminated", "completed", "status terminated"
    ))
    lot_specific = any(x in blob for x in (
        "one lot", "single lot", "one batch", "single batch", "lot #",
        "lot number", "lot numbers", " lot ", " lot:"
    ))
    old_or_scope_unclear = terminated or any(x in blob for x in (
        "current relevance", "requires validation", "not established", "unclear",
        "historical", "archive"
    ))
    root_confirmed = any(x in blob for x in (
        "confirmed root cause", "root cause confirmed", "confirmed underlying root cause"
    )) and "not publicly confirmed" not in blob
    repeated_or_current = any(x in blob for x in (
        "repeated", "recurring", "multiple lots", "multiple batches", "ongoing",
        "active recall", "not terminated"
    )) and not terminated
    return {
        "terminated": terminated,
        "lot_specific": lot_specific,
        "old_or_scope_unclear": old_or_scope_unclear,
        "root_confirmed": root_confirmed,
        "repeated_or_current": repeated_or_current,
    }


def _lead_status(opp: dict[str, Any], strength: str) -> str:
    """Lead status used by matcher cards and CSV export.

    Priority order:
      1. use the lead classification already present in the stored report;
      2. otherwise apply the same conservative recall-readiness logic;
      3. only then fall back to score/grade heuristics.
    """
    report_status = _stored_report_lead_status(opp)
    if report_status:
        return report_status
    explicit_status = _normalise_lead_status(opp.get("lead_status"))
    if explicit_status:
        return explicit_status

    meta = _recall_status_meta(opp)
    if meta["terminated"] and meta["lot_specific"] and not meta["root_confirmed"]:
        return "monitor only"
    if strength in {MATCH_WEAK, MATCH_DESCRIPTOR_ONLY}:
        return "monitor only"
    if meta["old_or_scope_unclear"] and not meta["repeated_or_current"]:
        return "needs validation"

    grade = str(opp.get("grade") or "").upper()
    confidence = _norm(opp.get("confidence") or "")
    ev_count = int(opp.get("evidence_count") or len(opp.get("evidence", [])) or 0)
    score = int(opp.get("score") or 0)
    if (
        strength == MATCH_DIRECT
        and meta["repeated_or_current"]
        and ev_count >= 2
        and grade in {"A", "B"}
        and confidence != "low"
    ):
        return "outreach-ready"
    if strength in {MATCH_DIRECT, MATCH_RELATED} and ev_count >= 1 and (grade in {"A", "B", "C"} or score >= 30):
        return "needs validation"
    return "low priority / archive"


def _confidence(opp: dict[str, Any], strength: str) -> str:
    existing = _norm(opp.get("confidence") or "")
    if strength == MATCH_DIRECT:
        return existing if existing in {"high", "medium-high", "medium"} else "medium"
    if strength == MATCH_RELATED:
        return "low-medium"
    return "low"


def _problem_match_row(
    query: str,
    category_key: str,
    rule: dict[str, Any],
    opp: dict[str, Any],
    match: dict[str, Any],
) -> dict[str, Any]:
    target = _target_label(opp)
    strength = match["strength"]
    return {
        **_common_match_metadata(opp),
        "match_scope": MATCH_SCOPE_LABEL,
        "match_strength": strength,
        "match_reason": match["reason"],
        "searched_problem": query,
        "matched_problem_category": rule["label"],
        "matching_product_problem_lead": target,
        "company": opp.get("company") or "",
        "product": opp.get("product") or opp.get("brand_name") or opp.get("generic_name") or "",
        "evidence_source": _source_summary(opp.get("evidence", [])),
        "confirmed_fact": _confirmed_fact(opp),
        "interpretation_hypothesis": (
            f"{strength}: the current stored evidence has a {rule['label']} signal. "
            "This is an opportunity hypothesis, not a confirmed root cause or proof that a specific technology is needed."
        ),
        "likely_solution_types": rule["solution_types"],
        "possible_partner_categories": rule["partner_categories"],
        "confidence": _confidence(opp, strength),
        "lead_status": _lead_status(opp, strength),
        "safe_bd_action": rule["safe_bd_action"],
        "match_terms": match.get("terms", []),
        "match_score": match.get("score", 0),
        "grade": opp.get("grade") or "",
        "opportunity_score": opp.get("score") or "",
        "evidence_count": opp.get("evidence_count") or len(opp.get("evidence", [])) or 0,
    }


def match_problem_to_solutions(
    query: str,
    opportunity_rows: list[dict[str, Any]] | None,
    evidence_rows: list[dict[str, Any]] | None = None,
    limit: int = 10,
    include_weak: bool = False,
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
    hidden_weak_count = 0
    for opp in records:
        match = _match_problem_category(opp, category_key)
        if not match:
            continue
        if match["strength"] in {MATCH_WEAK, MATCH_DESCRIPTOR_ONLY} and not include_weak:
            hidden_weak_count += 1
            continue
        matches.append(_problem_match_row(query, category_key, rule, opp, match))

    matches.sort(
        key=lambda m: (
            _STRENGTH_SCORE.get(m["match_strength"], 0),
            int(m.get("evidence_count") or 0),
            int(m.get("opportunity_score") or 0),
        ),
        reverse=True,
    )
    if not matches:
        return {
            "status": "no_matches",
            "message": NO_MATCH_MESSAGE,
            "searched_problem": query,
            "matched_problem_category": rule["label"],
            "likely_solution_types": rule["solution_types"],
            "possible_partner_categories": rule["partner_categories"],
            "safe_bd_action": rule["safe_bd_action"],
            "hidden_weak_count": hidden_weak_count,
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
        "hidden_weak_count": hidden_weak_count,
        "matches": matches[:limit],
    }


def _tech_match_row(
    query: str,
    tech_key: str,
    tech_rule: dict[str, Any],
    opp: dict[str, Any],
    category_matches: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    relevant_labels = [PROBLEM_RULES[k]["label"] for k in tech_rule["problem_categories"]]
    best_strength = max((m["strength"] for _k, m in category_matches), key=lambda s: _STRENGTH_SCORE.get(s, 0))
    term_hits = sorted({term for _k, m in category_matches for term in m.get("terms", [])})
    reasons = [f"{PROBLEM_RULES[k]['label']}: {m['reason']}" for k, m in category_matches]
    return {
        **_common_match_metadata(opp),
        "match_scope": MATCH_SCOPE_LABEL,
        "match_strength": best_strength,
        "match_reason": "; ".join(reasons[:3]),
        "searched_technology": query,
        "technology_category": tech_rule["label"],
        "relevant_problem_categories": relevant_labels,
        "matching_product_company_lead": _target_label(opp),
        "company": opp.get("company") or "",
        "product": opp.get("product") or opp.get("brand_name") or opp.get("generic_name") or "",
        "why_this_technology_may_fit": (
            f"{tech_rule['why_fit']} Current match strength: {best_strength}. "
            f"Matched against indexed evidence via: {'; '.join(reasons[:2])}."
        ),
        "evidence_strength": _confidence(opp, best_strength),
        "confidence": _confidence(opp, best_strength),
        "lead_status": _lead_status(opp, best_strength),
        "safe_outreach_angle": tech_rule["safe_outreach_angle"] + " " + TECH_CERTAINTY_NOTE,
        "confirmed_fact": _confirmed_fact(opp),
        "evidence_source": _source_summary(opp.get("evidence", [])),
        "matched_problem_categories": [PROBLEM_RULES[k]["label"] for k, _m in category_matches],
        "match_terms": term_hits,
        "match_score": max(m.get("score", 0) for _k, m in category_matches),
        "grade": opp.get("grade") or "",
        "opportunity_score": opp.get("score") or "",
        "evidence_count": opp.get("evidence_count") or len(opp.get("evidence", [])) or 0,
    }


def match_technology_to_targets(
    query: str,
    opportunity_rows: list[dict[str, Any]] | None,
    evidence_rows: list[dict[str, Any]] | None = None,
    limit: int = 10,
    include_weak: bool = False,
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
    hidden_weak_count = 0
    for opp in records:
        category_matches: list[tuple[str, dict[str, Any]]] = []
        for category_key in tech_rule["problem_categories"]:
            match = _match_problem_category(opp, category_key)
            if not match:
                continue
            if match["strength"] in {MATCH_WEAK, MATCH_DESCRIPTOR_ONLY} and not include_weak:
                hidden_weak_count += 1
                continue
            category_matches.append((category_key, match))
        if category_matches:
            matches.append(_tech_match_row(query, tech_key, tech_rule, opp, category_matches))

    matches.sort(
        key=lambda m: (
            _STRENGTH_SCORE.get(m["match_strength"], 0),
            int(m.get("evidence_count") or 0),
            int(m.get("opportunity_score") or 0),
        ),
        reverse=True,
    )
    relevant_labels = [PROBLEM_RULES[k]["label"] for k in tech_rule["problem_categories"]]
    if not matches:
        return {
            "status": "no_matches",
            "message": NO_MATCH_MESSAGE,
            "searched_technology": query,
            "technology_category": tech_rule["label"],
            "relevant_problem_categories": relevant_labels,
            "why_this_technology_may_fit": tech_rule["why_fit"],
            "hidden_weak_count": hidden_weak_count,
            "matches": [],
        }
    return {
        "status": "ok",
        "message": MATCH_SCOPE_LABEL,
        "searched_technology": query,
        "technology_category": tech_rule["label"],
        "relevant_problem_categories": relevant_labels,
        "why_this_technology_may_fit": tech_rule["why_fit"],
        "hidden_weak_count": hidden_weak_count,
        "matches": matches[:limit],
    }
