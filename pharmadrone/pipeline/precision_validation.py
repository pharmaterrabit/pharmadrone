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
            ent.get("trial_relevance_context"), ent.get("discovery_topic"),
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


def _trial_registry_blob(record: dict[str, Any]) -> str:
    """Registry facts only; excludes query/discovery-topic labels.

    This prevents a search topic such as ``bioequivalence`` from becoming
    evidence that the study itself is a bioequivalence/formulation study.
    """
    vals: list[Any] = [record.get("product"), record.get("molecule")]
    for e in _evidence(record):
        vals.extend([e.get("title"), e.get("raw_text"), e.get("english_summary")])
        ent = e.get("entities") or {}
        if isinstance(ent, dict):
            vals.extend([
                ent.get("intervention_names"), ent.get("intervention_type"),
                ent.get("conditions"), ent.get("why_stopped"), ent.get("phase"),
                ent.get("overall_status"), ent.get("study_type"),
            ])
    data = _load(record.get("data_json"))
    if isinstance(data, dict):
        # Only known registry-fact keys are collected. Deliberately exclude
        # discovery_topic and trial_relevance_context.
        for d in _walk_dicts(data):
            vals.extend([
                d.get("title"), d.get("briefTitle"), d.get("officialTitle"),
                d.get("briefSummary"), d.get("detailedDescription"),
                d.get("intervention_names"), d.get("intervention_type"),
                d.get("conditions"), d.get("why_stopped"), d.get("phase"),
            ])
    return _norm(" ".join(str(x) for x in vals if x))


def _is_trial(record: dict[str, Any]) -> bool:
    text = _norm(f"{record.get('source_type')} {record.get('source_id')}")
    return "trial" in text or str(record.get("source_id") or "").upper().startswith("NCT")


def _is_recall(record: dict[str, Any]) -> bool:
    return "recall" in _norm(record.get("source_type"))


def _is_shortage(record: dict[str, Any]) -> bool:
    return "shortage" in _norm(record.get("source_type"))


def classify_problem(record: dict[str, Any]) -> tuple[str, str]:
    blob = _norm(" ".join([
        str(record.get("problem_category") or ""), str(record.get("problem_signal") or ""),
        source_problem_text(record), str(record.get("product") or ""),
    ]))
    # Most specific first.
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
    if any(x in blob for x in ("expiry", "shelf life", "shelf-life")) and any(x in blob for x in ("data", "support", "stability")):
        return "stability issue", "shelf-life / stability-support issue"
    if any(x in blob for x in ("transdermal", "adhesion", "shear")) and any(x in blob for x in ("release", "delivery", "rate")):
        return "delivery-system issue", "delivery-system / release-rate issue"
    if any(x in blob for x in ("sterility", "lack of sterility", "contamination", "endotoxin", "microbial")):
        return "sterility/contamination issue", "sterility / contamination issue"
    if any(x in blob for x in ("bioequivalence", "relative bioavailability", "food effect", "fed/fasted", "fed versus fasted", "fed vs fasted")):
        return "bioavailability / PK context", "bioequivalence / relative-bioavailability / food-effect signal"
    if any(x in blob for x in ("tablet versus capsule", "tablet vs capsule", "formulation comparison", "dosage form comparison", "oral suspension", "modified release", "targeted release", "extended release", "immediate release", "topical", "transdermal", "inhaled", "inhalation", "prefilled syringe", "vial")):
        return "formulation / delivery context", "formulation / dosage-form / delivery comparison"
    if any(x in blob for x in ("stability", "degradation")):
        return "stability issue", "stability / degradation issue"
    if any(x in blob for x in ("impurit", "related substance")):
        return "impurity issue", "impurity issue"
    if _is_shortage(record):
        if "discontinu" in blob:
            return "discontinuation signal", "official discontinuation / availability signal"
        if any(x in blob for x in ("manufactur", "quality", "facility", "production delay")):
            return "manufacturing / supply signal", "manufacturing / quality supply signal"
        return "supply / availability signal", "supply / availability signal"
    broad = str(record.get("problem_category") or "unspecified product/problem signal")
    return broad, broad


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
    product_source_blob = _norm(" ".join([
        str(record.get("product") or ""), str(record.get("molecule") or ""),
        source_problem_text(record),
    ]))
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



def company_diagnostics(record: dict[str, Any]) -> tuple[str, str, bool, str]:
    target = _first(record.get("target_company"), record.get("company"))
    source = ""
    role_notes: list[str] = []
    for ent in _entities(record):
        rf = ent.get("recall_fields") or {}
        source = source or _first(
            rf.get("recalling_firm"), ent.get("sponsor"), ent.get("company"),
            ent.get("company_name"), ent.get("manufacturer_name"),
        )
    source = source or target
    raw_blob = source_problem_text(record) + " " + str(record.get("product") or "")
    blob = _norm(raw_blob)
    role_terms = []
    for term in (
        "distributed by", "distributor", "repackaged by", "repackager",
        "relabeler", "packaged by", "packager", "manufactured by", "manufacturer",
    ):
        if term in blob:
            role_terms.append(term)
    if role_terms:
        role_notes.append(
            "source/target company role requires validation: "
            + ", ".join(dict.fromkeys(role_terms))
        )

    named_owners: list[str] = []
    for pat in (
        r"manufactured by\s*[:\-]?\s*([^.;|]{3,100})",
        r"manufacturer\s*[:\-]\s*([^.;|]{3,100})",
        r"distributed by\s*[:\-]?\s*([^.;|]{3,100})",
    ):
        for match in re.findall(pat, raw_blob, flags=re.I):
            clean = re.sub(r"\s+", " ", match).strip(" ,:-")
            if clean and clean not in named_owners:
                named_owners.append(clean)
    if named_owners:
        role_notes.append("named manufacturer/distributor in source text: " + "; ".join(named_owners[:3]))

    mismatch = bool(source and target and _norm(source) != _norm(target))
    named_mismatch = any(
        _norm(owner) not in {_norm(target), _norm(source)}
        and _norm(target) not in _norm(owner)
        and _norm(owner) not in _norm(target)
        for owner in named_owners
    )
    warning = ""
    if mismatch:
        warning = (
            f"official source company/manufacturer '{source}' differs from "
            f"PharmaTune target company '{target}'"
        )
    elif named_mismatch:
        warning = (
            "source text names a manufacturer/distributor that differs from the "
            "PharmaTune target company; technical owner requires validation"
        )
    elif role_terms:
        warning = (
            "company role may be distributor/repackager/relabeler/packager rather "
            "than technical manufacturer/sponsor; product owner requires validation"
        )
    return source, "; ".join(role_notes), bool(warning), warning



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
    patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("formulation comparison", (
            "formulation comparison", "comparison of formulations",
            "comparison of two formulations", "compare two formulations",
            "comparing two formulations", "different formulations",
            "two formulations of", "formulations of asasantin",
        )),
        ("relative bioavailability", (
            "relative bioavailability", "comparative bioavailability",
            "bioavailability comparison",
        )),
        ("food-effect / fed-fasted PK", (
            "food effect", "food-effect", "fed/fasted", "fed versus fasted",
            "fed vs fasted", "fasted and fed", "effect of food",
        )),
        ("bioequivalence", ("bioequivalence", "bio-equivalence")),
        ("dosage-form comparison", (
            "tablet vs capsule", "tablet versus capsule", "capsule vs tablet",
            "capsule versus tablet", "dosage form comparison",
            "oral suspension", "prefilled syringe vs vial",
            "prefilled syringe versus vial", "syringe vs vial",
            "syringe versus vial",
        )),
        ("modified/release-delivery comparison", (
            "modified release", "modified-release", "targeted release",
            "targeted-release", "extended release versus", "immediate release versus",
            "extended-release versus", "immediate-release versus",
        )),
        ("drug-delivery/formulation development", (
            "oral insulin", "transdermal delivery", "topical delivery",
            "inhaled delivery", "inhalation delivery", "cyclodextrin",
        )),
    )
    for label, terms in patterns:
        if any(term in blob for term in terms):
            return label
    return ""


def classify_signal(record: dict[str, Any], seller_profile: str = "") -> tuple[str, str, str]:
    broad, specific = classify_problem(record)
    broad_blob = _trial_blob(record)
    warning, fatal, fatal_reason = product_type_diagnostics(record, seller_profile)
    if fatal:
        return SIGNAL_D, "unsuitable / remove", fatal_reason or warning

    if _is_trial(record):
        registry_blob = _trial_registry_blob(record)
        explicit_a = _explicit_trial_a_reason(registry_blob)
        if explicit_a:
            return SIGNAL_A, "strong explicit formulation / PK / delivery signal", explicit_a

        # PK/development context is Tier B, but not automatically external-ready.
        # Discovery-topic labels cannot promote a study to Tier A.
        dev = (
            "pharmacokinetic", "pharmacokinetics", "bioavailability",
            "drug delivery", "formulation", "absorption", "bridging",
        )
        if any(x in registry_blob for x in dev):
            if any(x in registry_blob for x in (
                "dose escalation", "maximum tolerated dose", "oncology",
                "tumor", "tumour", "cancer", "carboplatin", "etoposide",
            )):
                return (
                    SIGNAL_B,
                    "development / PK context; not a specific formulation-performance signal",
                    "oncology/combination or dose-development trial with PK context only",
                )
            return SIGNAL_B, "development / PK / bioavailability context", specific

        if any(x in registry_blob for x in (
            "dose escalation", "efficacy", "oncology", "maximum tolerated dose",
            "tumor", "tumour", "cancer",
        )):
            return (
                SIGNAL_C, "weak/general clinical-development signal",
                "ordinary efficacy/dose-escalation trial without explicit formulation, bioavailability, food-effect, dosage-form, delivery, or product-performance signal",
            )
        return SIGNAL_C, "weak/general trial signal", specific

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
    broad, specific = classify_problem(out)
    tier, signal_type, signal_reason = classify_signal(out, seller_profile)
    product_warning, product_fatal, product_exclusion = product_type_diagnostics(out, seller_profile)
    source_company, role_note, company_warning_bool, company_warning = company_diagnostics(out)
    target_company = _first(out.get("target_company"), out.get("company"))
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
        }
        fit_specific = any(term in profile for term in seller_relevant.get(broad, ()))
    exclusion: list[str] = []
    if verification not in _VERIFIED:
        exclusion.append("source ID is not directly/secondarily verified")
    if tier == SIGNAL_B and not strong_b:
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
    if company_warning_bool:
        exclusion.append(company_warning)
    if not fit_specific:
        exclusion.append("seller fit is weak/background or unavailable")
    evidence_boundary = _norm(out.get("what_evidence_does_not_prove"))
    if evidence_boundary and not any(x in evidence_boundary for x in ("does not prove", "not proof", "requires validation", "no product-specific root cause")):
        exclusion.append("evidence boundary wording requires review")
    eligible = not exclusion

    out.update({
        "signal_tier": tier,
        "signal_type": signal_type,
        "signal_reason": signal_reason,
        "broad_problem_category": broad,
        "specific_problem_subcategory": specific,
        "source_problem_text": source_problem_text(out),
        "source_company": source_company,
        "target_company": target_company,
        "company_role_note": role_note,
        "company_match_warning": company_warning_bool,
        "company_match_warning_note": company_warning,
        "product_owner_warning": company_warning if company_warning_bool else role_note,
        "product_type_warning": product_warning,
        "official_source_url": url,
        "official_source_verified": verification in _VERIFIED,
        "source_record_present": bool(url) or bool(out.get("source_id")) or _is_trial(out) or _is_recall(out) or _is_shortage(out),
        "source_id_verified_by_structured_source": verification in _VERIFIED,
        "manual_audit_status": _first(out.get("manual_verdict"), "pending manual audit"),
        "verification_method": method,
        "source_id_verification_status": verification,
        "source_id_verification_note": verification_note,
        "external_case_study_eligible": eligible,
        "exclusion_reason": "; ".join(dict.fromkeys(x for x in exclusion if x)),
        "clinical_trial_signal_reason": signal_reason if _is_trial(out) else "",
    })
    return out
