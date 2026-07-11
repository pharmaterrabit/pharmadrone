"""Checkpoint 6A deterministic precision and external-eligibility annotations.

Read-only helpers. They never call APIs/LLMs, mutate scores/stable IDs, or
confirm root cause/customer need. Annotations are derived from already stored
public-source evidence and are intended for human validation.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any
from urllib.parse import unquote, urlparse

SIGNAL_A = "A"
SIGNAL_B = "B"
SIGNAL_C = "C"
SIGNAL_D = "D"

_VERIFIED = {"verified_direct", "verified_secondary"}
_OFFICIAL_HOSTS = ("fda.gov", "clinicaltrials.gov", "nih.gov", "gov.uk", "europa.eu", "canada.ca", "tga.gov.au")

TRIAL_TIER_A_SIGNAL_CODES = {
    "formulation_comparison",
    "tablet_vs_capsule",
    "liquid_formulation_pk",
    "relative_bioavailability",
    "bioequivalence",
    "food_effect_fed_fasted",
    "release_profile_comparison",
    "delivery_device_comparison",
    "dosage_form_or_route_comparison",
    "delivery_optimisation",
}

# Manually audited corrections are keyed by source type + stable official source
# ID, never by a CSV row number. They add review metadata only; they do not
# rewrite source facts, scores, stable IDs, reports or root-cause conclusions.
_AUDIT_CORRECTIONS = {
    ("fda recall", "D-0386-2024"): {
        "company_identity_mismatch": True,
        "company_match_warning": True,
        "company_match_warning_note": (
            "Manual audit correction for FDA recall D-0386-2024: unresolved "
            "company/manufacturer attribution mismatch; verify technical ownership before external use."
        ),
        "company_role_note": (
            "Manual audit correction keyed to FDA recall D-0386-2024: audited evidence identified "
            "a company/manufacturer attribution concern."
        ),
        "product_type_warning": (
            "unapproved-product / regulatory-status concern; internal validation only"
        ),
        "audit_correction_note": (
            "Manual audit correction for D-0386-2024: company/manufacturer attribution and "
            "unapproved-product/regulatory-status concerns require external exclusion."
        ),
        "manual_audit_status": "manual audit correction applied",
        "external_exclusion_reason": (
            "manual audit correction: unresolved company/manufacturer attribution mismatch; "
            "unapproved-product / regulatory-status concern"
        ),
    },
}


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "").lower()).strip()


def _load(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    if not v:
        return None
    try:
        return json.loads(str(v))
    except Exception:
        return None


def _walk_dicts(v: Any):
    if isinstance(v, dict):
        yield v
        for x in v.values():
            yield from _walk_dicts(x)
    elif isinstance(v, list):
        for x in v:
            yield from _walk_dicts(x)


def _evidence(record: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for value in (record.get("evidence"), _load(record.get("data_json"))):
        if isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            ev = value.get("evidence")
            if isinstance(ev, list):
                out.extend(x for x in ev if isinstance(x, dict))
    # deterministic de-duplication
    seen, clean = set(), []
    for e in out:
        key = (str(e.get("record_id") or ""), str(e.get("url") or ""), str(e.get("title") or ""))
        if key in seen:
            continue
        seen.add(key); clean.append(e)
    return clean


def _entities(record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for e in _evidence(record):
        ent = e.get("entities") or {}
        if isinstance(ent, dict):
            rows.append(ent)
    data = _load(record.get("data_json"))
    if isinstance(data, dict):
        for d in _walk_dicts(data):
            if any(k in d for k in ("recall_fields", "source_event_id", "why_stopped", "shortage_reason")):
                rows.append(d)
    return rows


def _first(*values: Any) -> str:
    for v in values:
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v if x)
        if str(v or "").strip():
            return str(v).strip()
    return ""


def source_problem_text(record: dict[str, Any]) -> str:
    bits: list[str] = []
    for ent in _entities(record):
        rf = ent.get("recall_fields") or {}
        bits.extend(str(x) for x in (
            rf.get("reason_for_recall"), ent.get("event_reason"), ent.get("reason_text"),
            ent.get("why_stopped"), ent.get("shortage_reason"), ent.get("issue_category"),
        ) if x)
    for e in _evidence(record):
        bits.extend(str(x) for x in (e.get("raw_text"), e.get("english_summary"), e.get("title")) if x)
    if not bits:
        bits.extend(str(record.get(k) or "") for k in ("problem_signal", "problem_category", "product", "molecule"))
    # preserve human-readable source wording but cap size
    return " | ".join(dict.fromkeys(x.strip() for x in bits if x.strip()))[:5000]


def _trial_blob(record: dict[str, Any]) -> str:
    """Broad internal trial text, including stored discovery context."""
    vals = [source_problem_text(record), record.get("product"), record.get("molecule"), record.get("problem_category")]
    for ent in _entities(record):
        vals.extend([ent.get("intervention_names"), ent.get("intervention_type"), ent.get("conditions"), ent.get("why_stopped")])
    return _norm(" ".join(str(x) for x in vals if x))


def _collect_trial_structured_text(value: Any) -> list[str]:
    """Collect only known ClinicalTrials.gov registry fact fields.

    Query/discovery metadata, PharmaTune interpretations, reports and seller-fit
    text are deliberately ignored. This keeps Tier-A classification grounded in
    official study structure rather than the search topic that found the study.
    """
    out: list[str] = []
    direct_keys = {
        "officialtitle", "brieftitle", "briefsummary", "detaileddescription",
        "interventionnames", "interventiondescriptions", "interventionothernames",
        "armlabels", "armdescriptions", "primaryoutcomes", "secondaryoutcomes",
        "otheroutcomes", "conditions", "whystopped", "phase",
    }
    if isinstance(value, dict):
        for key, child in value.items():
            k = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if k in direct_keys:
                if isinstance(child, (str, int, float)):
                    out.append(str(child))
                elif isinstance(child, list):
                    for item in child:
                        if isinstance(item, (str, int, float)):
                            out.append(str(item))
                        elif isinstance(item, dict):
                            for subkey in ("name", "description", "label", "measure", "timeFrame", "otherNames"):
                                sub = item.get(subkey)
                                if isinstance(sub, list):
                                    out.extend(str(x) for x in sub if x)
                                elif sub:
                                    out.append(str(sub))
            # Official API module structures.
            if k == "interventions" and isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        for subkey in ("name", "description", "otherNames"):
                            sub = item.get(subkey)
                            if isinstance(sub, list):
                                out.extend(str(x) for x in sub if x)
                            elif sub:
                                out.append(str(sub))
            elif k == "armgroups" and isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        out.extend(str(item.get(x)) for x in ("label", "description") if item.get(x))
            elif k in {"primaryoutcomes", "secondaryoutcomes", "otheroutcomes"} and isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        out.extend(str(item.get(x)) for x in ("measure", "description", "timeFrame") if item.get(x))
            out.extend(_collect_trial_structured_text(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_collect_trial_structured_text(child))
    return out


def _trial_registry_blob(record: dict[str, Any]) -> str:
    """Registry facts only; excludes query/discovery labels and interpretations."""
    vals: list[Any] = [record.get("product"), record.get("molecule")]
    for e in _evidence(record):
        vals.extend([e.get("title"), e.get("raw_text")])
        ent = e.get("entities") or {}
        if isinstance(ent, dict):
            vals.extend(_collect_trial_structured_text(ent))
            vals.extend([
                ent.get("intervention_names"), ent.get("intervention_type"),
                ent.get("conditions"), ent.get("why_stopped"), ent.get("phase"),
                ent.get("overall_status"), ent.get("study_type"),
            ])
    data = _load(record.get("data_json"))
    if isinstance(data, dict):
        vals.extend(_collect_trial_structured_text(data))
    return _norm(" ".join(str(x) for x in vals if x))


def _is_trial(record: dict[str, Any]) -> bool:
    text = _norm(f"{record.get('source_type')} {record.get('source_id')}")
    return "trial" in text or str(record.get("source_id") or "").upper().startswith("NCT")


def _is_recall(record: dict[str, Any]) -> bool:
    return "recall" in _norm(record.get("source_type"))


def _is_shortage(record: dict[str, Any]) -> bool:
    return "shortage" in _norm(record.get("source_type"))


def _recall_reason_text(record: dict[str, Any]) -> str:
    reasons: list[str] = []
    for ent in _entities(record):
        rf = ent.get("recall_fields") or {}
        for value in (rf.get("reason_for_recall"), ent.get("event_reason"), ent.get("reason_text")):
            if value:
                reasons.append(str(value))
    if not reasons:
        for e in _evidence(record):
            ent = e.get("entities") or {}
            if isinstance(ent, dict) and ent.get("event_reason"):
                reasons.append(str(ent.get("event_reason")))
    return " | ".join(dict.fromkeys(x.strip() for x in reasons if x.strip()))


def _classify_problem_blob(blob: str, *, shortage: bool = False, fallback: str = "") -> tuple[str, str]:
    # Most specific source-supported categories first. The returned pair is
    # always internally consistent because both fields come from one rule.
    if any(x in blob for x in ("nitrosamine", "ndsri", "n-nitroso")):
        return "impurity issue", "nitrosamine / NDSRI impurity"
    if "dissolution" in blob:
        return "dissolution failure", "dissolution OOS / dissolution failure"
    if any(x in blob for x in ("subpotent", "superpotent", "assay out of specification", "assay oos", "potency")):
        return "assay/potency issue", "assay OOS / potency issue"
    if ("degrad" in blob and any(x in blob for x in ("impurit", "oos", "out of specification", "stability station"))):
        return "impurity issue", "impurity / degradation issue"
    if any(x in blob for x in ("particulate", "visible particle", "foreign particle", "crystal", "precipitat")):
        return "particulate / precipitation issue", "particulate / crystal / precipitation issue"
    if any(x in blob for x in ("particle size", "particle-size", "micronized", "micronised")):
        return "particle-size issue", "particle-size / micronized API issue"
    # Sterility/contamination outranks shelf-life when the official reason states it.
    if any(x in blob for x in ("sterility", "lack of sterility", "contamination", "endotoxin", "microbial")):
        return "sterility/contamination issue", "sterility / contamination issue"
    if any(x in blob for x in ("expiry", "shelf life", "shelf-life")) and any(x in blob for x in ("data", "support", "stability", "expiration")):
        return "stability issue", "shelf-life / stability-support issue"
    if any(x in blob for x in ("transdermal", "adhesion", "shear")) and any(x in blob for x in ("release", "delivery", "rate")):
        return "delivery-system issue", "delivery-system / release-rate issue"
    if any(x in blob for x in ("bioequivalence", "relative bioavailability", "food effect", "fed/fasted", "fed versus fasted", "fed vs fasted")):
        return "bioavailability / PK context", "bioequivalence / relative-bioavailability / food-effect signal"
    if any(x in blob for x in (
        "tablet versus capsule", "tablet vs capsule", "capsule versus tablet", "capsule vs tablet",
        "formulation comparison", "dosage form comparison", "oral suspension", "liquid formulation",
        "modified release", "targeted release", "extended release", "immediate release", "topical",
        "transdermal", "inhaled", "inhalation", "prefilled syringe", "vial",
    )):
        return "formulation / delivery context", "formulation / dosage-form / delivery comparison"
    if any(x in blob for x in ("stability", "degradation")):
        return "stability issue", "stability / degradation issue"
    if any(x in blob for x in ("impurit", "related substance")):
        return "impurity issue", "impurity issue"
    if shortage:
        if "discontinu" in blob:
            return "discontinuation signal", "official discontinuation / availability signal"
        if any(x in blob for x in ("manufactur", "quality", "facility", "production delay")):
            return "manufacturing / supply signal", "manufacturing / quality supply signal"
        return "supply / availability signal", "supply / availability signal"
    clean_fallback = fallback or "unspecified product/problem signal"
    return clean_fallback, clean_fallback



_TRIAL_FIELD_MAP = {
    "officialtitle": "official_title",
    "brieftitle": "brief_title",
    "briefsummary": "brief_summary",
    "detaileddescription": "detailed_description",
    "interventiondescription": "intervention_description",
    "interventiondescriptions": "intervention_description",
    "interventionname": "intervention_name",
    "interventionnames": "intervention_name",
    "armlabel": "arm_label",
    "armlabels": "arm_label",
    "armdescription": "arm_description",
    "armdescriptions": "arm_description",
    "primaryoutcome": "primary_outcome",
    "primaryoutcomes": "primary_outcome",
    "secondaryoutcome": "secondary_outcome",
    "secondaryoutcomes": "secondary_outcome",
    "otheroutcome": "other_outcome",
    "otheroutcomes": "other_outcome",
}

_TRIAL_FIELD_PRIORITY = {
    "official_title": 0,
    "brief_title": 1,
    "brief_summary": 2,
    "detailed_description": 3,
    "intervention_description": 4,
    "arm_label": 5,
    "arm_description": 6,
    "primary_outcome": 7,
    "secondary_outcome": 8,
    "other_outcome": 9,
    "intervention_name": 10,
}


def _flatten_trial_field(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, (str, int, float)):
        text = re.sub(r"\s+", " ", str(value)).strip()
        if text:
            out.append(text)
    elif isinstance(value, list):
        for item in value:
            out.extend(_flatten_trial_field(item))
    elif isinstance(value, dict):
        for key in ("name", "description", "label", "measure", "timeFrame", "otherNames"):
            if key in value:
                out.extend(_flatten_trial_field(value.get(key)))
    return out


def _collect_trial_fields(value: Any, out: list[tuple[str, str]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            norm_key = re.sub(r"[^a-z0-9]", "", str(key).lower())
            canonical = _TRIAL_FIELD_MAP.get(norm_key)
            if canonical:
                for text in _flatten_trial_field(child):
                    out.append((canonical, text))
            # Official API module arrays may not use the flattened keys above.
            if norm_key == "interventions" and isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        out.extend(("intervention_name", x) for x in _flatten_trial_field(item.get("name")))
                        out.extend(("intervention_description", x) for x in _flatten_trial_field(item.get("description")))
            elif norm_key == "armgroups" and isinstance(child, list):
                for item in child:
                    if isinstance(item, dict):
                        out.extend(("arm_label", x) for x in _flatten_trial_field(item.get("label")))
                        out.extend(("arm_description", x) for x in _flatten_trial_field(item.get("description")))
            _collect_trial_fields(child, out)
    elif isinstance(value, list):
        for child in value:
            _collect_trial_fields(child, out)


def trial_registry_fields(record: dict[str, Any]) -> list[tuple[str, str]]:
    """Return attributable registry fields only; never discovery topics/reports."""
    rows: list[tuple[str, str]] = []
    for evidence in _evidence(record):
        if "trial" not in _norm(f"{evidence.get('source_type')} {evidence.get('source_name')}"):
            continue
        if evidence.get("title"):
            rows.append(("brief_title", str(evidence.get("title"))))
        entities = evidence.get("entities") or {}
        if isinstance(entities, dict):
            _collect_trial_fields(entities, rows)
    data = _load(record.get("data_json"))
    if isinstance(data, dict):
        _collect_trial_fields(data, rows)
    # Deterministic de-duplication and ordering by attributable field priority.
    seen: set[tuple[str, str]] = set()
    clean: list[tuple[str, str]] = []
    for field, text in rows:
        compact = re.sub(r"\s+", " ", str(text)).strip()
        key = (field, compact.lower())
        if compact and key not in seen:
            seen.add(key)
            clean.append((field, compact))
    clean.sort(key=lambda item: (_TRIAL_FIELD_PRIORITY.get(item[0], 99), item[1].lower()))
    return clean


def _short_evidence_text(text: str, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _comparison_pair(text: str, left: tuple[str, ...], right: tuple[str, ...]) -> bool:
    l = "|".join(re.escape(x) for x in left)
    r = "|".join(re.escape(x) for x in right)
    connector = r"(?:vs\.?|versus|compared\s+(?:with|to)|comparison\s+(?:with|of|between)|and)"
    return bool(re.search(rf"(?:{l}).{{0,90}}{connector}.{{0,90}}(?:{r})|(?:{r}).{{0,90}}{connector}.{{0,90}}(?:{l})", text))


def clinical_trial_signal_trace(record: dict[str, Any]) -> dict[str, str]:
    """Return a specific attributable Tier-A trial signal, or an empty dict.

    Generic PK, oral/tablet/capsule, safety, efficacy, exposure or isolated
    bioavailability wording is intentionally insufficient.
    """
    if not _is_trial(record):
        return {}
    fields = trial_registry_fields(record)
    matches: list[dict[str, str]] = []
    for field, original in fields:
        text = _norm(original)
        title_or_objective = field in {"official_title", "brief_title", "brief_summary", "detailed_description"}
        code = reason = broad = specific = ""

        if _comparison_pair(text, ("tablet", "tablets"), ("capsule", "capsules")) or re.search(r"tablet(?:s)?\s+and\s+capsule(?:s)?\s+formulation", text):
            code = "tablet_vs_capsule"
            reason = "tablet-versus-capsule formulation comparison"
            broad = "formulation / delivery context"
            specific = "tablet-versus-capsule formulation comparison"
        elif _comparison_pair(text, ("prefilled syringe", "pre-filled syringe", "syringe"), ("vial", "vials")):
            code = "delivery_device_comparison"
            reason = "prefilled-syringe-versus-vial delivery comparison"
            broad = "formulation / delivery context"
            specific = "delivery-device / presentation comparison"
        elif _comparison_pair(text, ("immediate release", "immediate-release"), ("modified release", "modified-release", "targeted release", "targeted-release", "extended release", "extended-release", "delayed release", "delayed-release")):
            code = "release_profile_comparison"
            reason = "immediate-versus-modified/targeted-release comparison"
            broad = "delivery-system issue"
            specific = "release-profile / delivery-system comparison"
        elif re.search(r"(?:compare|comparison|comparative|versus|vs\.)[^.]{0,100}(?:formulation|dosage form|delivery system|route)", text) or re.search(r"(?:two|different|alternative)\s+(?:drug\s+)?formulations", text):
            code = "formulation_comparison"
            reason = "explicit formulation or dosage-form comparison"
            broad = "formulation / delivery context"
            specific = "formulation / dosage-form comparison"
        elif "liquid formulation" in text or "oral liquid formulation" in text or "liquid dosage form" in text:
            if any(x in text for x in ("pharmacokinetic", "pharmacokinetics", "food effect", "fed", "fasted", "bioavailability", "compare", "comparison")):
                code = "liquid_formulation_pk"
                reason = "liquid-formulation PK/formulation assessment"
                broad = "formulation / delivery context"
                specific = "liquid-formulation PK / formulation assessment"
        elif title_or_objective and re.search(r"\brelative bioavailability\b|\bcomparative bioavailability\b", text):
            code = "relative_bioavailability"
            reason = "explicit relative-bioavailability assessment"
            broad = "bioavailability / PK context"
            specific = "relative-bioavailability assessment"
        elif title_or_objective and re.search(r"\bbioequivalence\b|\bbio-equivalence\b", text):
            code = "bioequivalence"
            reason = "explicit bioequivalence assessment"
            broad = "bioavailability / PK context"
            specific = "bioequivalence assessment"
        elif re.search(r"\bfood[- ]effect\b|\beffect of food\b|\bfed\s*(?:versus|vs\.?|and)\s*fasted\b|\bfasted\s*(?:versus|vs\.?|and)\s*fed\b", text):
            code = "food_effect_fed_fasted"
            reason = "explicit food-effect / fed-versus-fasted assessment"
            broad = "bioavailability / PK context"
            specific = "food-effect / fed-fasted PK assessment"
        elif _comparison_pair(text, ("oral", "intravenous", "subcutaneous", "intramuscular", "topical", "transdermal", "inhaled", "inhalation"), ("oral", "intravenous", "subcutaneous", "intramuscular", "topical", "transdermal", "inhaled", "inhalation")):
            code = "dosage_form_or_route_comparison"
            reason = "explicit route or dosage-form comparison"
            broad = "formulation / delivery context"
            specific = "route / dosage-form comparison"
        elif title_or_objective and any(x in text for x in ("oral insulin delivery", "transdermal delivery", "inhaled delivery", "inhalation delivery")) and any(x in text for x in ("assess", "evaluate", "compare", "optim", "delivery")):
            code = "delivery_optimisation"
            reason = "explicit absorption/delivery optimisation study"
            broad = "formulation / delivery context"
            specific = "drug-delivery / absorption optimisation"

        if code:
            matches.append({
                "clinical_trial_signal_code": code,
                "clinical_trial_signal_reason": reason,
                "clinical_trial_evidence_field": field,
                "clinical_trial_evidence_text": _short_evidence_text(original),
                "broad_problem_category": broad,
                "specific_problem_subcategory": specific,
            })

    # A field can contain both a formulation comparison and a food-effect or
    # relative-bioavailability objective. Preserve that attributable support
    # instead of allowing the first matched code to hide the second signal.
    existing_codes = {m["clinical_trial_signal_code"] for m in matches}
    for field, original in fields:
        text = _norm(original)
        title_or_objective = field in {"official_title", "brief_title", "brief_summary", "detailed_description"}
        if "food_effect_fed_fasted" not in existing_codes and re.search(
            r"\bfood[- ]effect\b|\beffect of food\b|\bfed\s*(?:versus|vs\.?|and)\s*fasted\b|\bfasted\s*(?:versus|vs\.?|and)\s*fed\b", text
        ):
            matches.append({
                "clinical_trial_signal_code": "food_effect_fed_fasted",
                "clinical_trial_signal_reason": "explicit food-effect / fed-versus-fasted assessment",
                "clinical_trial_evidence_field": field,
                "clinical_trial_evidence_text": _short_evidence_text(original),
                "broad_problem_category": "bioavailability / PK context",
                "specific_problem_subcategory": "food-effect / fed-fasted PK assessment",
            })
            existing_codes.add("food_effect_fed_fasted")
        if title_or_objective and "relative_bioavailability" not in existing_codes and re.search(
            r"\brelative bioavailability\b|\bcomparative bioavailability\b", text
        ):
            matches.append({
                "clinical_trial_signal_code": "relative_bioavailability",
                "clinical_trial_signal_reason": "explicit relative-bioavailability assessment",
                "clinical_trial_evidence_field": field,
                "clinical_trial_evidence_text": _short_evidence_text(original),
                "broad_problem_category": "bioavailability / PK context",
                "specific_problem_subcategory": "relative-bioavailability assessment",
            })
            existing_codes.add("relative_bioavailability")

    if not matches:
        return {}

    priority = {
        "tablet_vs_capsule": 0,
        "liquid_formulation_pk": 1,
        "formulation_comparison": 2,
        "release_profile_comparison": 3,
        "delivery_device_comparison": 4,
        "relative_bioavailability": 5,
        "bioequivalence": 6,
        "food_effect_fed_fasted": 7,
        "dosage_form_or_route_comparison": 8,
        "delivery_optimisation": 9,
    }
    matches.sort(key=lambda row: (priority.get(row["clinical_trial_signal_code"], 99), _TRIAL_FIELD_PRIORITY.get(row["clinical_trial_evidence_field"], 99)))
    primary = dict(matches[0])
    # Add explicit supporting food-effect/relative-BA trace when present.
    support = [m for m in matches[1:] if m["clinical_trial_signal_code"] in {"food_effect_fed_fasted", "relative_bioavailability", "bioequivalence"}]
    if support:
        chosen = support[0]
        primary["clinical_trial_signal_reason"] += "; " + chosen["clinical_trial_signal_reason"]
        primary["clinical_trial_evidence_field"] += "; " + chosen["clinical_trial_evidence_field"]
        primary["clinical_trial_evidence_text"] += " | " + chosen["clinical_trial_evidence_text"]
    return primary


def _audit_key(record: dict[str, Any]) -> tuple[str, str]:
    source = _norm(record.get("source_type"))
    if "recall" in source or "enforcement" in source:
        source = "fda recall"
    elif "trial" in source or str(record.get("source_id") or "").upper().startswith("NCT"):
        source = "clinicaltrials.gov trial"
    return source, str(record.get("source_id") or "").strip().upper()


def audit_correction(record: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(_AUDIT_CORRECTIONS.get(_audit_key(record), {}))

def classify_problem(record: dict[str, Any]) -> tuple[str, str]:
    if _is_recall(record):
        reason = _norm(_recall_reason_text(record))
        if reason:
            return _classify_problem_blob(reason, fallback="other FDA recall issue")
        fallback = str(record.get("problem_category") or record.get("problem_signal") or "other FDA recall issue")
        return _classify_problem_blob(_norm(fallback), fallback=fallback)

    if _is_trial(record):
        trace = clinical_trial_signal_trace(record)
        if trace:
            return trace["broad_problem_category"], trace["specific_problem_subcategory"]
        registry = _trial_registry_blob(record)
        if any(x in registry for x in ("pharmacokinetic", "pharmacokinetics", "exposure", "absorption", "bioavailability")):
            return "development / PK context", "general PK / clinical-pharmacology context"
        return "clinical development context", "ordinary efficacy / dose-development context"

    blob = _norm(" ".join([
        str(record.get("problem_category") or ""), str(record.get("problem_signal") or ""),
        source_problem_text(record), str(record.get("product") or ""),
    ]))
    return _classify_problem_blob(
        blob,
        shortage=_is_shortage(record),
        fallback=str(record.get("problem_category") or "unspecified product/problem signal"),
    )


_D_FATAL = (
    "hygiene kit", "hygiene product", "toothbrush", "oral care kit", "oral-care kit",
    "oral hygiene", "dental hygiene", "dental kit", "dental sample", "dental product",
    "sample kit", "sample box", "sample boxes", "carifree",
    "biospecimen", "specimen collection", "blood sample", "serum", "plasma",
    "tissue", "biopsy", "diagnostic test", "diagnostic-only", "standard of care",
    "no intervention",
)



def _seller_allows_api_scope(seller_profile: str) -> bool:
    profile = _norm(seller_profile)
    return any(x in profile for x in (
        "api work", "api services", "api development", "drug substance",
        "raw material", "raw-material", "excipient", "bulk pharmaceutical",
    ))


def product_type_diagnostics(record: dict[str, Any], seller_profile: str = "") -> tuple[str, bool, str]:
    blob = _trial_blob(record)
    registry_blob = _trial_registry_blob(record) if _is_trial(record) else blob
    product = _norm(record.get("product"))
    fatal: list[str] = []

    if any(x in blob for x in (
        "carifree", "dental hygiene", "dental sample", "dental kit",
        "dental product", "oral care kit", "oral-care kit", "oral hygiene",
        "toothbrush", "hygiene kit", "hygiene product", "sample box",
        "sample boxes", "sample kit",
    )):
        fatal.append("dental hygiene/sample kit")
    if any(x in blob for x in (
        "biospecimen", "specimen collection", "blood sample", "serum",
        "plasma", "tissue", "biopsy",
    )):
        fatal.append("specimen/sample-only record")
    if any(x in blob for x in ("diagnostic test", "diagnostic-only")):
        fatal.append("diagnostic-only record")
    if any(x in blob for x in ("standard of care", "no intervention")):
        fatal.append("standard-care/no-intervention record")

    # Placebo-only is fatal; named active product plus a placebo comparator is
    # retained with a caveat.
    placebo = "placebo" in registry_blob
    active_tokens = [x for x in product.split() if x not in {"placebo", "matching", "control", "comparator"}]
    if placebo and (
        product in {"placebo", "matching placebo", "placebo control", "placebo comparator"}
        or not active_tokens
    ):
        fatal.append("placebo-only intervention")

    warnings: list[str] = []
    if placebo and not any("placebo-only" in x for x in fatal):
        warnings.append("placebo comparator present; active intervention retained")

    if any(x in blob for x in (
        "dietary supplement", "nutraceutical", "red ginseng", "herbal supplement",
    )):
        warnings.append("dietary supplement / nutraceutical context")

    # Raw-material/API status is based on product/source wording, not a generic
    # mention of an active ingredient elsewhere in the record. Particle
    # engineering alone does not opt a seller into API/raw-material records.
    product_parts: list[str] = [str(record.get("product") or ""), str(record.get("molecule") or "")]
    for ent in _entities(record):
        rf = ent.get("recall_fields") or {}
        product_parts.extend(str(x) for x in (
            rf.get("product_description"), ent.get("product"), ent.get("product_short"),
            ent.get("product_type"), ent.get("dosage_form"),
        ) if x)
    product_source_blob = _norm(" ".join(product_parts))
    api_context = any(x in product_source_blob for x in (
        "bulk pharmaceutical chemical", "bulk pharmaceutical ingredient",
        "bulk drug substance", "bulk raw material", "api-only",
        "active pharmaceutical ingredient", "pharmaceutical excipient",
    ))
    if api_context and not _seller_allows_api_scope(seller_profile):
        warnings.append("bulk raw material / API / excipient context")

    vague_exact = {
        "", "drug", "study drug", "investigational product", "intervention",
        "treatment", "study medication", "investigational drug",
    }
    vague_pattern = bool(re.search(
        r"^(administration of )?(investigation|investigational|study) of .{0,35}drug$",
        product,
    )) or any(x in product for x in (
        "administration of investigation of",
        "administration of investigational",
        "investigation of eurofarma drug",
        "unnamed investigational drug",
    ))
    if _is_trial(record) and (product in vague_exact or vague_pattern):
        fatal.append("vague intervention")
    elif not product and not _is_shortage(record):
        fatal.append("non-product record / missing product")

    unapproved = any(x in blob for x in (
        "unapproved drug", "unapproved new drug", "unapproved product",
        "marketed without an approved", "without an approved nda",
        "without approved nda", "without an approved application",
        "regulatory status concern",
    ))
    if unapproved:
        warnings.append("unapproved-product / regulatory-status concern; internal validation only")

    fatal = list(dict.fromkeys(fatal))
    warnings = list(dict.fromkeys(warnings))
    all_labels = fatal + warnings
    return "; ".join(all_labels), bool(fatal), "; ".join(fatal)



_COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
    "company", "co", "plc", "gmbh", "ag", "sa", "pharmaceuticals", "pharma",
}


def _company_tokens(value: str) -> set[str]:
    return {x for x in re.findall(r"[a-z0-9]+", _norm(value)) if x not in _COMPANY_SUFFIXES}


def _company_equivalent(a: str, b: str) -> bool:
    if not a or not b:
        return False
    na, nb = _norm(a), _norm(b)
    if na == nb or na in nb or nb in na:
        return True
    ta, tb = _company_tokens(a), _company_tokens(b)
    return bool(ta and tb and (ta == tb or len(ta & tb) >= max(1, min(len(ta), len(tb)) - 1)))


def _extract_role_names(raw_blob: str, pattern: str) -> list[str]:
    values = re.findall(pattern, raw_blob, flags=re.I)
    clean: list[str] = []
    for value in values:
        name = re.sub(r"\s+", " ", str(value)).strip(" ,:-")
        name = re.split(r"\b(?:product|reason|classification|status|recall|lot|distributed|marketed|manufactured)\s*:", name, maxsplit=1, flags=re.I)[0].strip(" ,:-")
        if name and name not in clean:
            clean.append(name)
    return clean


def company_role_diagnostics(record: dict[str, Any]) -> dict[str, Any]:
    target = _first(record.get("target_company"), record.get("company"))
    source = ""
    manufacturers: list[str] = []
    distributors: list[str] = []
    repackagers: list[str] = []
    for ent in _entities(record):
        rf = ent.get("recall_fields") or {}
        source = source or _first(rf.get("recalling_firm"), ent.get("sponsor"), ent.get("company"), ent.get("company_name"))
        for key in ("manufacturer_name", "manufacturer", "manufactured_by", "technical_manufacturer"):
            value = ent.get(key) or rf.get(key)
            if value:
                manufacturers.extend(value if isinstance(value, list) else [value])
        for key in ("distributor", "distributed_by"):
            value = ent.get(key) or rf.get(key)
            if value:
                distributors.extend(value if isinstance(value, list) else [value])
        for key in ("repackager", "relabeler", "packager"):
            value = ent.get(key) or rf.get(key)
            if value:
                repackagers.extend(value if isinstance(value, list) else [value])
    source = source or target
    raw_blob = source_problem_text(record) + " " + str(record.get("product") or "")

    manufactured_for = _extract_role_names(raw_blob, r"manufactured\s+for\s*[:\-]?\s*([^.;|]{2,100})")
    marketed_by = _extract_role_names(raw_blob, r"marketed\s+by\s*[:\-]?\s*([^.;|]{2,100})")
    # Parse explicit contract-manufacturing relationships before the generic
    # manufacturer pattern so role transitions are not captured as company names.
    for manufacturer, owner in re.findall(
        r"manufactured\s+by\s*[:\-]?\s*(.{2,100}?)\s+for\s+(.{2,100}?)(?=[.;|]|$)", raw_blob, flags=re.I
    ):
        manufacturers.append(manufacturer)
        manufactured_for.append(owner)
    for owner, manufacturer in re.findall(
        r"manufactured\s+for\s*[:\-]?\s*(.{2,100}?)\s+by\s+(.{2,100}?)(?=[.;|]|$)", raw_blob, flags=re.I
    ):
        manufactured_for.append(owner)
        manufacturers.append(manufacturer)
    manufacturers += _extract_role_names(raw_blob, r"manufactured\s+by\s*[:\-]?\s*([^.;|]{2,100})")
    distributors += _extract_role_names(raw_blob, r"distributed\s+by\s*[:\-]?\s*([^.;|]{2,100})")
    repackagers += _extract_role_names(raw_blob, r"(?:repackaged|relabeled|relabelled|packaged)\s+by\s*[:\-]?\s*([^.;|]{2,100})")

    def unique(values: list[str]) -> list[str]:
        out: list[str] = []
        for value in values:
            name = re.sub(r"\s+", " ", str(value)).strip(" ,:-")
            name = re.split(
                r"\s+(?:and\s+)?(?:distributed|marketed|manufactured|repackaged|relabeled|relabelled|packaged)\s+(?:by|for)\s+",
                name, maxsplit=1, flags=re.I,
            )[0].strip(" ,:-")
            if name and name not in out:
                out.append(name)
        return out

    manufacturers, distributors, repackagers = unique(manufacturers), unique(distributors), unique(repackagers)
    manufactured_for, marketed_by = unique(manufactured_for), unique(marketed_by)

    target_is_owner = _is_trial(record) and _company_equivalent(target, source)
    target_is_owner = target_is_owner or any(_company_equivalent(target, x) for x in manufactured_for + marketed_by)
    technical_differs = any(not _company_equivalent(target, x) for x in manufacturers)
    target_distributor = any(_company_equivalent(target, x) for x in distributors)
    target_repackager = any(_company_equivalent(target, x) for x in repackagers)
    distributor_only = (target_distributor or target_repackager) and not target_is_owner

    source_differs = bool(source and target and not _company_equivalent(source, target))
    # A different contract manufacturer/source is a role difference when the
    # source explicitly says the product was manufactured for/marketed by target.
    identity_mismatch = source_differs and not target_is_owner
    role_difference = technical_differs or source_differs or bool(manufacturers or manufactured_for or marketed_by or distributors or repackagers)

    notes: list[str] = []
    if manufacturers:
        notes.append("named technical manufacturer: " + "; ".join(manufacturers[:3]))
    if manufactured_for:
        notes.append("manufactured-for relationship: " + "; ".join(manufactured_for[:3]))
    if marketed_by:
        notes.append("marketed-by relationship: " + "; ".join(marketed_by[:3]))
    if distributors:
        notes.append("named distributor: " + "; ".join(distributors[:3]))
    if repackagers:
        notes.append("named repackager/packager: " + "; ".join(repackagers[:3]))

    warning = ""
    if identity_mismatch:
        warning = f"official source company '{source}' differs from PharmaTune target company '{target}' and the role relationship is unresolved"
    elif distributor_only:
        warning = "target appears to be distributor/repackager/packager only; technical product ownership is unresolved"
        if technical_differs:
            warning += "; named technical manufacturer differs from the target company"

    result = {
        "source_company": source,
        "target_company": target,
        "company_role_note": "; ".join(notes),
        "company_match_warning": bool(warning),
        "company_match_warning_note": warning,
        "company_identity_mismatch": identity_mismatch,
        "company_role_difference": role_difference,
        "technical_manufacturer_differs": technical_differs,
        "target_is_product_owner_or_sponsor": target_is_owner,
        "target_is_distributor_or_repackager_only": distributor_only,
    }
    correction = audit_correction(record)
    if correction:
        for key in (
            "company_identity_mismatch", "company_match_warning", "company_match_warning_note",
            "company_role_note", "audit_correction_note",
        ):
            if key in correction:
                if key == "company_role_note" and result.get(key):
                    result[key] = result[key] + "; " + str(correction[key])
                else:
                    result[key] = correction[key]
    return result


def company_diagnostics(record: dict[str, Any]) -> tuple[str, str, bool, str]:
    details = company_role_diagnostics(record)
    return (
        str(details.get("source_company") or ""),
        str(details.get("company_role_note") or ""),
        bool(details.get("company_match_warning")),
        str(details.get("company_match_warning_note") or ""),
    )


def extract_stored_official_url(record: dict[str, Any]) -> str:
    """Return a stored official URL only; never search or fabricate one."""
    urls: list[str] = []
    for key in ("official_source_url", "source_url", "url"):
        value = record.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(value)
    for e in _evidence(record):
        for key in ("official_source_url", "url", "source_url"):
            value = e.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                urls.append(value)
        ent = e.get("entities") or {}
        value = ent.get("official_source_url") if isinstance(ent, dict) else None
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            urls.append(value)
    links = _load(record.get("evidence_links_json"))
    if isinstance(links, list):
        for value in links:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                urls.append(value)
            elif isinstance(value, dict):
                for key in ("url", "official_source_url"):
                    v = value.get(key)
                    if isinstance(v, str) and v.startswith(("http://", "https://")):
                        urls.append(v)
    for url in dict.fromkeys(urls):
        if _official(url):
            return url
    return ""


def _official(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return any(host == x or host.endswith("." + x) for x in _OFFICIAL_HOSTS)


def verification_diagnostics(record: dict[str, Any], official_url: str) -> tuple[str, str, str]:
    existing = _norm(record.get("source_id_verification_status"))
    if existing in _VERIFIED:
        return existing, _first(record.get("verification_method"), "stored structured/previous verification; manual audit status separate"), _first(record.get("source_id_verification_note"), "Existing structured-source verification retained; this does not imply human audit.")
    source_id = str(record.get("source_id") or "").strip()
    if not official_url:
        return "not_verified", "no stored official URL", "No official source URL was stored for deterministic verification."
    if not _official(official_url):
        return "official_url_present_not_checked", "stored non-official/secondary URL", "A secondary URL is present but has not been verified as a reliable source-ID match."
    decoded = _norm(unquote(official_url))
    sid = _norm(source_id)
    if sid and sid not in {"unknown-source", "unknown source"}:
        # Detect a conflicting identifier when the official URL clearly contains one.
        if sid.upper().startswith("NCT"):
            found = re.findall(r"nct\d{8}", decoded)
            if found and sid not in found:
                return "source_id_mismatch", "official URL contains a different NCT ID", f"Stored source ID {source_id} does not match {found[0].upper()} in the official URL."
        if re.fullmatch(r"d-?\d+-?\d+", sid):
            found = re.findall(r"d-?\d+-?\d+", decoded)
            if found and sid.replace("-", "") not in {x.replace("-", "") for x in found}:
                return "source_id_mismatch", "official URL contains a different recall ID", f"Stored source ID {source_id} does not match the recall identifier in the official URL."
        sid_parts = {sid, sid.replace('"', ''), sid.replace("-", "")}
        url_compact = decoded.replace("-", "")
        if any(x and (x in decoded or x.replace("-", "") in url_compact) for x in sid_parts):
            return "verified_direct", "structured official-source URL contains source ID (not a human audit)", "The structured official-source URL matches the indexed source identifier; manual audit remains pending."
        return "official_url_present_not_checked", "stored official URL; ID not embedded", "Official URL is present but the source ID was not deterministically matched in the URL."
    return "official_url_present_not_checked", "stored official URL", "Official URL is present; source identifier requires manual confirmation."

def _explicit_trial_a_reason(blob: str) -> str:
    """Backward-compatible helper; generic blob matching no longer assigns Tier A."""
    return ""


def classify_signal(record: dict[str, Any], seller_profile: str = "") -> tuple[str, str, str]:
    broad, specific = classify_problem(record)
    warning, fatal, fatal_reason = product_type_diagnostics(record, seller_profile)
    if fatal:
        return SIGNAL_D, "unsuitable / remove", fatal_reason or warning

    if _is_trial(record):
        trace = clinical_trial_signal_trace(record)
        if trace and trace.get("clinical_trial_signal_code") in TRIAL_TIER_A_SIGNAL_CODES:
            return SIGNAL_A, "explicit attributable formulation / bioavailability / delivery signal", trace["clinical_trial_signal_reason"]

        registry = _trial_registry_blob(record)
        if any(x in registry for x in (
            "pharmacokinetic", "pharmacokinetics", "bioavailability", "exposure",
            "absorption", "dose escalation", "maximum tolerated dose", "safety",
        )):
            return (
                SIGNAL_B,
                "development / PK context; not an explicit formulation-comparison signal",
                "registry contains general clinical-pharmacology/PK development context without an approved attributable Tier-A comparison",
            )
        if any(x in registry for x in ("efficacy", "oncology", "tumor", "tumour", "cancer")):
            return (
                SIGNAL_C,
                "weak/general clinical-development signal",
                "ordinary efficacy/development trial without explicit formulation, bioavailability, food-effect, dosage-form, delivery or product-performance comparison",
            )
        return SIGNAL_C, "weak/general trial signal", "no approved attributable formulation/PK/delivery comparison was identified"

    strong_broad = {
        "dissolution failure", "impurity issue", "assay/potency issue",
        "particulate / precipitation issue", "particle-size issue",
        "stability issue", "delivery-system issue",
        "sterility/contamination issue", "formulation / delivery context",
        "bioavailability / PK context",
    }
    if broad in strong_broad:
        return SIGNAL_A, "strong product / formulation signal", specific
    if _is_shortage(record):
        if "manufacturing / quality" in specific or "discontinuation" in specific:
            return SIGNAL_B, "supply / manufacturing / discontinuation context", specific
        return SIGNAL_C, "weak/general supply signal", specific
    if _is_recall(record):
        return SIGNAL_C, "general regulatory recall signal", specific
    return SIGNAL_C, "weak/general signal", specific



def annotate_record(record: dict[str, Any], *, seller_profile: str = "", official_source_url: str = "") -> dict[str, Any]:
    out = deepcopy(record)
    trial_trace = clinical_trial_signal_trace(out) if _is_trial(out) else {}
    broad, specific = classify_problem(out)
    tier, signal_type, signal_reason = classify_signal(out, seller_profile)
    product_warning, product_fatal, product_exclusion = product_type_diagnostics(out, seller_profile)
    company = company_role_diagnostics(out)
    correction = audit_correction(out)

    # Apply audited product/regulatory warning without fabricating source facts.
    if correction.get("product_type_warning"):
        product_warning = "; ".join(dict.fromkeys(x for x in (product_warning, correction["product_type_warning"]) if x))
        product_warning = product_warning.replace("bulk raw material / API / excipient context; ", "") if _audit_key(out) == ("fda recall", "D-0386-2024") else product_warning
        if _audit_key(out) == ("fda recall", "D-0386-2024") and "bulk raw material / API / excipient context" in product_warning:
            product_warning = product_warning.replace("bulk raw material / API / excipient context", "").strip("; ")

    target_company = str(company.get("target_company") or _first(out.get("target_company"), out.get("company")))
    url = official_source_url or extract_stored_official_url(out) or _first(out.get("official_source_url"), out.get("url"))
    verification, method, verification_note = verification_diagnostics(out, url)
    fit = _norm(out.get("seller_fit_strength") or out.get("fit_strength"))
    fit_specific = fit in {"strong fit", "moderate fit"} and "weak" not in fit
    strong_b = tier == SIGNAL_B and signal_type.startswith("strong ")
    if not fit and seller_profile:
        profile = _norm(seller_profile)
        seller_relevant = {
            "dissolution failure": ("dissolution", "formulation", "solubility", "particle"),
            "impurity issue": ("impurity", "analytical", "qc", "formulation"),
            "assay/potency issue": ("assay", "analytical", "qc"),
            "particulate / precipitation issue": ("particle", "precipitation", "formulation", "analytical"),
            "particle-size issue": ("particle", "api", "formulation"),
            "stability issue": ("stability", "formulation", "analytical"),
            "delivery-system issue": ("delivery", "formulation"),
            "formulation / delivery context": ("formulation", "delivery", "solubility", "particle"),
            "bioavailability / PK context": ("bioavailability", "solubility", "formulation", "particle"),
            "development / PK context": ("bioavailability", "formulation", "clinical pharmacology"),
        }
        fit_specific = any(term in profile for term in seller_relevant.get(broad, ()))

    exclusion: list[str] = []
    if verification not in _VERIFIED:
        exclusion.append("source ID is not directly/secondarily verified")

    if _is_trial(out):
        code = trial_trace.get("clinical_trial_signal_code", "")
        attributable = all(trial_trace.get(k) for k in (
            "clinical_trial_signal_reason", "clinical_trial_evidence_field", "clinical_trial_evidence_text",
        ))
        if tier != SIGNAL_A or code not in TRIAL_TIER_A_SIGNAL_CODES or not attributable:
            exclusion.append("ClinicalTrials record lacks an approved attributable Tier-A formulation/bioavailability/delivery signal")
    elif tier == SIGNAL_B and not strong_b:
        exclusion.append("signal tier B is contextual and not a strong external-use B signal")
    elif tier not in {SIGNAL_A, SIGNAL_B}:
        exclusion.append(f"signal tier {tier} is not A/strong B")

    if product_fatal:
        exclusion.append(product_exclusion or "unsuitable product/intervention type")
    if product_warning and any(x in _norm(product_warning) for x in (
        "supplement", "nutraceutical", "bulk raw", "api / excipient",
        "unapproved", "regulatory-status", "dental", "hygiene", "sample",
        "vague intervention",
    )):
        exclusion.append(product_warning)

    # Only unresolved identity mismatch or distributor/repackager-only status
    # blocks external use. Normal contract-manufacturer role differences do not.
    if company.get("company_identity_mismatch"):
        exclusion.append(str(company.get("company_match_warning_note") or "unresolved material company identity mismatch"))
    if company.get("target_is_distributor_or_repackager_only"):
        exclusion.append(str(company.get("company_match_warning_note") or "target is distributor/repackager/packager only and technical ownership is unresolved"))

    if not fit_specific:
        exclusion.append("seller fit is weak/background or unavailable")
    evidence_boundary = _norm(out.get("what_evidence_does_not_prove"))
    if evidence_boundary and not any(x in evidence_boundary for x in ("does not prove", "not proof", "requires validation", "no product-specific root cause")):
        exclusion.append("evidence boundary wording requires review")
    if correction.get("external_exclusion_reason"):
        exclusion.append(str(correction["external_exclusion_reason"]))

    eligible = not exclusion

    out.update({
        "signal_tier": tier,
        "signal_type": signal_type,
        "signal_reason": signal_reason,
        "broad_problem_category": broad,
        "specific_problem_subcategory": specific,
        "source_problem_text": source_problem_text(out),
        "source_company": company.get("source_company", ""),
        "target_company": target_company,
        "company_role_note": company.get("company_role_note", ""),
        "company_match_warning": bool(company.get("company_match_warning")),
        "company_match_warning_note": company.get("company_match_warning_note", ""),
        "company_identity_mismatch": bool(company.get("company_identity_mismatch")),
        "company_role_difference": bool(company.get("company_role_difference")),
        "technical_manufacturer_differs": bool(company.get("technical_manufacturer_differs")),
        "target_is_product_owner_or_sponsor": bool(company.get("target_is_product_owner_or_sponsor")),
        "target_is_distributor_or_repackager_only": bool(company.get("target_is_distributor_or_repackager_only")),
        "product_owner_warning": company.get("company_match_warning_note") if company.get("company_match_warning") else company.get("company_role_note", ""),
        "product_type_warning": product_warning,
        "official_source_url": url,
        "official_source_verified": verification in _VERIFIED,
        "source_record_present": bool(url) or bool(out.get("source_id")) or _is_trial(out) or _is_recall(out) or _is_shortage(out),
        "source_id_verified_by_structured_source": verification in _VERIFIED,
        "manual_audit_status": _first(correction.get("manual_audit_status"), out.get("manual_verdict"), "pending manual audit"),
        "verification_method": method,
        "source_id_verification_status": verification,
        "source_id_verification_note": verification_note,
        "external_case_study_eligible": eligible,
        "exclusion_reason": "; ".join(dict.fromkeys(x for x in exclusion if x)),
        "clinical_trial_signal_code": trial_trace.get("clinical_trial_signal_code", "") if _is_trial(out) else "",
        "clinical_trial_signal_reason": trial_trace.get("clinical_trial_signal_reason", signal_reason if _is_trial(out) else ""),
        "clinical_trial_evidence_field": trial_trace.get("clinical_trial_evidence_field", "") if _is_trial(out) else "",
        "clinical_trial_evidence_text": trial_trace.get("clinical_trial_evidence_text", "") if _is_trial(out) else "",
        "audit_correction_note": correction.get("audit_correction_note", ""),
    })
    return out

