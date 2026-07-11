"""Checkpoint 4: deterministic 100-target internal validation study.

This module is deliberately read-only. It selects from existing indexed and
already-enriched PharmaTune opportunity records, reuses the deterministic
seller-to-target matcher, and produces audit-ready CSV/Markdown exports.

It does not call APIs, require an LLM, change Opportunity Scores, mutate stable
lead IDs, or write to the opportunity index.
"""
from __future__ import annotations

import csv
import io
import json
import re
from collections import Counter
from copy import deepcopy
from statistics import mean
from typing import Any
from urllib.parse import urlparse

from .. import db
from . import seller_target_matcher

DEFAULT_VALIDATION_TITLE = "PharmaTune 100-target validation study"
DEFAULT_SELLER_SERVICE_PROFILE = "Specialist formulation / drug-product technology provider"
DEFAULT_CAPABILITIES = [
    "particle engineering",
    "solubility enhancement",
    "formulation CDMO",
    "dissolution testing",
    "analytical/QC testing",
    "stability troubleshooting",
    "impurity investigation",
]
DEFAULT_PROBLEM_SIGNALS = [
    "dissolution failure",
    "poor solubility",
    "low bioavailability",
    "stability issue",
    "impurity issue",
    "assay/potency issue",
    "formulation challenge",
    "precipitation",
    "sterility/contamination issue",
    "packaging/container closure issue",
]

MIN_EVIDENCE_OPTIONS = [
    "Any",
    "Tier 1 / high",
    "Tier 2 / moderate",
    "Tier 3 / limited",
    "Tier 4 / weak",
]

MANUAL_VERDICT_OPTIONS = ["PASS", "PARTIAL", "FAIL", "REMOVE FROM CASE STUDY"]

CSV_FIELDS = [
    "validation_rank",
    "target_company",
    "product",
    "molecule",
    "problem_category",
    "source_type",
    "source_id",
    "region",
    "opportunity_score",
    "grade",
    "lead_status",
    "queue_status",
    "evidence_quality",
    "best_evidence_tier",
    "direct_source_evidence_status",
    "corroboration_status",
    "official_followup_status",
    "label_context_status",
    "clinical_trial_context_status",
    "literature_context_status",
    "source_coverage_count",
    "seller_fit_strength",
    "seller_capability_match",
    "why_fit",
    "what_evidence_proves",
    "what_evidence_does_not_prove",
    "safe_bd_angle",
    "validation_questions",
    "has_full_report",
    "report_path",
    "official_source_found",
    "company_matches_source",
    "product_matches_source",
    "source_id_matches_source",
    "problem_signal_matches_source",
    "status_date_classification_matches",
    "root_cause_confirmed_by_source",
    "pharmatune_fit_reasonable",
    "manual_verdict",
    "auditor_notes",
    "official_source_url",
    "audit_date",
    "auditor_name",
]

_FIT_RANK = {
    seller_target_matcher.FIT_STRONG: 0,
    seller_target_matcher.FIT_MODERATE: 1,
    seller_target_matcher.FIT_WEAK: 2,
}

_OFFICIAL_HOST_SUFFIXES = (
    "fda.gov",
    "clinicaltrials.gov",
    "nih.gov",
    "ema.europa.eu",
    "europa.eu",
    "gov.uk",
    "mhra.gov.uk",
    "sfda.gov.sa",
    "canada.ca",
    "tga.gov.au",
    "who.int",
)


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+/._:-]+", " ", str(value).lower())).strip()


def _safe_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _norm(value) in {"1", "true", "yes", "y"}


def _normalise_lead_status(value: Any) -> str:
    label = db.normalize_status_label(value) or "needs validation"
    text = _norm(label)
    if "monitor" in text:
        return "monitor only"
    if "low" in text or "archive" in text:
        return "low priority / archive"
    if "outreach" in text and "ready" in text:
        return "outreach-ready"
    return "needs validation"


def _quality_rank_value(value: Any) -> int:
    label = _norm(value or "not checked")
    if "tier 1" in label:
        return 1
    if "tier 2" in label:
        return 2
    if "tier 3" in label:
        return 3
    if "tier 4" in label:
        return 4
    return 5


def _quality_rank(record: dict[str, Any]) -> int:
    return _quality_rank_value(record.get("best_evidence_tier") or record.get("evidence_quality") or "not checked")


def _required_quality_rank(label: str | None) -> int:
    if not label or _norm(label) in {"any", "all"}:
        return 99
    return _quality_rank_value(label)


def _passes_quality(record: dict[str, Any], minimum: str | None) -> bool:
    required = _required_quality_rank(minimum)
    return required == 99 or _quality_rank(record) <= required


def _is_enriched(record: dict[str, Any]) -> bool:
    label = _norm(record.get("enrichment_status") or "not checked")
    return label not in {"", "not checked", "enrichment not checked", "unchecked", "unknown"}


def _is_truly_hidden_or_rejected(record: dict[str, Any]) -> bool:
    """Exclude storage/workflow-hidden records, not lead-status low-priority rows."""
    novelty = _norm(record.get("novelty_status") or "")
    queue = _norm(record.get("queue_status") or "")
    if novelty in {"archived", "rejected", "rejected / hidden", "hidden"}:
        return True
    if queue in {"archived", "rejected", "hidden"}:
        return True
    return False


def _matches_region(record: dict[str, Any], regions: list[str] | str | None) -> bool:
    if not regions:
        return True
    if isinstance(regions, str):
        selected = [x.strip() for x in re.split(r"[;,\n]+", regions) if x.strip()]
    else:
        selected = [str(x).strip() for x in regions if str(x).strip()]
    selected = [x for x in selected if _norm(x) not in {"any", "all", "all regions"}]
    if not selected:
        return True
    region = _norm(record.get("region") or "")
    return any(_norm(item) in region or region in _norm(item) for item in selected)


def _record_key(record: dict[str, Any]) -> str:
    return str(
        record.get("stable_lead_id")
        or "|".join(
            _norm(record.get(k) or "")
            for k in ("company", "target_company", "product", "problem_category", "source_id")
        )
    )


def _company_key(record: dict[str, Any]) -> str:
    return _norm(record.get("target_company") or record.get("company") or "unknown company")


def _record_lookup(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_record_key(r): r for r in records}


def _match_original(match: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    original = lookup.get(_record_key(match))
    if original:
        return original
    # Stable IDs should normally resolve. This deterministic fallback avoids
    # inventing records while supporting older rows that lack stable IDs.
    company = _company_key(match)
    product = _norm(match.get("product") or match.get("short_product") or "")
    source_id = _norm(match.get("source_id") or "")
    for candidate in lookup.values():
        if _company_key(candidate) != company:
            continue
        c_product = _norm(candidate.get("product") or "")
        c_source = _norm(candidate.get("source_id") or "")
        if source_id and c_source == source_id:
            return candidate
        if product and (product in c_product or c_product in product):
            return candidate
    return {}


def _load_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _walk_urls(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"url", "source_url", "official_source_url", "link", "href"} and isinstance(item, str):
                if item.startswith(("http://", "https://")):
                    urls.append(item)
            urls.extend(_walk_urls(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            urls.extend(_walk_urls(item))
    elif isinstance(value, str):
        # Evidence links JSON may already have been decoded to a plain URL list.
        if value.startswith(("http://", "https://")):
            urls.append(value)
    return urls


def _is_official_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
    except Exception:
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _OFFICIAL_HOST_SUFFIXES)


def extract_official_source_url(record: dict[str, Any]) -> str:
    """Return a stored official URL only; never search or fabricate one."""
    candidates: list[str] = []
    for key in ("official_source_url", "source_url", "url"):
        value = record.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            candidates.append(value)
    source_id = str(record.get("source_id") or "")
    if source_id.startswith(("http://", "https://")):
        candidates.append(source_id)
    candidates.extend(_walk_urls(_load_json(record.get("evidence_links_json"))))
    candidates.extend(_walk_urls(_load_json(record.get("data_json"))))
    candidates.extend(_walk_urls(record.get("evidence") or []))

    seen: set[str] = set()
    official = []
    for url in candidates:
        clean = str(url).strip()
        if clean in seen:
            continue
        seen.add(clean)
        if _is_official_url(clean):
            official.append(clean)
    if not official:
        return ""

    source_type = _norm(record.get("source_type") or "")
    if "trial" in source_type or "clinicaltrials" in source_type:
        for url in official:
            if "clinicaltrials.gov" in _norm(url):
                return url
    if "fda" in source_type or "recall" in source_type or "regulat" in source_type:
        for url in official:
            if "fda.gov" in _norm(url):
                return url
    return official[0]


def _has_official_source_evidence(record: dict[str, Any]) -> bool:
    source_type = _norm(record.get("source_type") or "")
    corroboration = _norm(record.get("corroboration_status") or "")
    if _safe_int(record.get("official_source_count")) > 0:
        return True
    if _as_bool(record.get("regulator_confirmed")):
        return True
    if any(token in source_type for token in ("fda", "regulator", "recall", "clinicaltrials", "trial registry")):
        return True
    if any(token in corroboration for token in ("regulator confirmed", "regulator-confirmed", "company confirmed", "company-confirmed")):
        return True
    return bool(extract_official_source_url(record))


def direct_source_evidence_status(record: dict[str, Any]) -> str:
    """Display official-source presence separately from enrichment quality.

    A preview can legitimately have an official FDA/registry source while its
    Phase 3 enrichment quality remains ``not checked``. This label prevents the
    validation UI from implying that no authoritative evidence exists.
    """
    if _has_official_source_evidence(record):
        return "official direct source - not enriched" if not _is_enriched(record) else "official direct source - enriched"
    return "no official direct source identified"


def _rank_key(record: dict[str, Any]) -> tuple[Any, ...]:
    lead_status = _normalise_lead_status(record.get("lead_status"))
    return (
        0 if _has_official_source_evidence(record) else 1,
        0 if _is_enriched(record) else 1,
        0 if _as_bool(record.get("has_full_report")) else 1,
        _quality_rank(record),
        _FIT_RANK.get(record.get("fit_strength") or record.get("seller_fit_strength"), 9),
        -_safe_int(record.get("source_coverage_count")),
        -_safe_int(record.get("opportunity_score") if record.get("opportunity_score") is not None else record.get("score")),
        1 if lead_status == "low priority / archive" else 0,
        _company_key(record),
        _norm(record.get("product") or ""),
        _norm(record.get("source_id") or ""),
        _norm(record.get("stable_lead_id") or ""),
    )


def _select_ranked(
    eligible: list[dict[str, Any]],
    limit: int,
    prefer_unique_companies: bool,
) -> list[dict[str, Any]]:
    ordered = sorted(eligible, key=_rank_key)
    limit = max(1, min(int(limit or 100), 100))
    if not prefer_unique_companies:
        return [dict(x) for x in ordered[:limit]]

    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    used_companies: set[str] = set()
    for record in ordered:
        company = _company_key(record)
        if company in used_companies:
            continue
        selected.append(dict(record))
        selected_keys.add(_record_key(record))
        used_companies.add(company)
        if len(selected) >= limit:
            return selected
    for record in ordered:
        if len(selected) >= limit:
            break
        if _record_key(record) in selected_keys:
            continue
        selected.append(dict(record))
        selected_keys.add(_record_key(record))
    return selected


def _validation_questions_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple, set)):
        return " | ".join(str(x) for x in value if str(x).strip())
    return ""


def _validation_row(record: dict[str, Any], rank: int) -> dict[str, Any]:
    return {
        "validation_rank": rank,
        "target_company": record.get("target_company") or record.get("company") or "",
        "product": record.get("product") or record.get("short_product") or "",
        "molecule": record.get("molecule") or "",
        "problem_category": record.get("problem_category") or "",
        "source_type": record.get("source_type") or "",
        "source_id": record.get("source_id") or "",
        "region": record.get("region") or "",
        "opportunity_score": record.get("opportunity_score") if record.get("opportunity_score") is not None else record.get("score", ""),
        "grade": record.get("grade") or "",
        "lead_status": _normalise_lead_status(record.get("lead_status")),
        "queue_status": record.get("queue_status") or "",
        "evidence_quality": record.get("evidence_quality") or "not checked",
        "best_evidence_tier": record.get("best_evidence_tier") or record.get("evidence_quality") or "not checked",
        "direct_source_evidence_status": direct_source_evidence_status(record),
        "corroboration_status": db.normalize_status_label(record.get("corroboration_status") or "direct source only") or "direct source only",
        "official_followup_status": db.normalize_status_label(record.get("official_followup_status") or "not checked") or "not checked",
        "label_context_status": db.normalize_status_label(record.get("label_context_status") or "not checked") or "not checked",
        "clinical_trial_context_status": db.normalize_status_label(record.get("clinical_trial_context_status") or "not checked") or "not checked",
        "literature_context_status": db.normalize_status_label(record.get("literature_context_status") or "not checked") or "not checked",
        "source_coverage_count": _safe_int(record.get("source_coverage_count")),
        "seller_fit_strength": record.get("fit_strength") or record.get("seller_fit_strength") or seller_target_matcher.FIT_WEAK,
        "seller_capability_match": record.get("seller_capability") or record.get("seller_capability_match") or "",
        "why_fit": record.get("why_fit") or "Possible technical/capability fit; requires validation.",
        "what_evidence_proves": record.get("what_evidence_proves") or "Public evidence indicates an indexed product/problem signal.",
        "what_evidence_does_not_prove": record.get("what_evidence_does_not_prove") or (
            "The evidence does not prove current customer need, commercial urgency, or a product-specific root cause."
        ),
        "safe_bd_angle": record.get("safe_bd_angle") or record.get("recommended_bd_angle") or "Validation-led discussion only.",
        "validation_questions": _validation_questions_text(record.get("validation_questions")),
        "has_full_report": _as_bool(record.get("has_full_report")),
        "report_path": record.get("report_path") or "",
        # Manual audit columns deliberately start blank. No automatic verdict is assigned.
        "official_source_found": "",
        "company_matches_source": "",
        "product_matches_source": "",
        "source_id_matches_source": "",
        "problem_signal_matches_source": "",
        "status_date_classification_matches": "",
        "root_cause_confirmed_by_source": "",
        "pharmatune_fit_reasonable": "",
        "manual_verdict": "",
        "auditor_notes": "",
        "official_source_url": extract_official_source_url(record),
        "audit_date": "",
        "auditor_name": "",
        # Internal regression/metric fields omitted from CSV.
        "stable_lead_id": record.get("stable_lead_id") or "",
        "enrichment_status": record.get("enrichment_status") or "enrichment not checked",
        "official_source_count": _safe_int(record.get("official_source_count")),
    }


def _counter_dict(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(v or "not specified" for v in values).items(), key=lambda item: (-item[1], item[0])))


def calculate_metrics(
    indexed_records: list[dict[str, Any]],
    eligible_records: list[dict[str, Any]],
    selected_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    scores = [_safe_float(r.get("opportunity_score")) for r in selected_rows]
    scores = [x for x in scores if x is not None]
    evidence = [str(r.get("best_evidence_tier") or r.get("evidence_quality") or "not checked") for r in selected_rows]
    readiness = [_normalise_lead_status(r.get("lead_status")) for r in selected_rows]
    fits = [str(r.get("seller_fit_strength") or seller_target_matcher.FIT_WEAK) for r in selected_rows]
    companies = {_company_key(r) for r in selected_rows if _company_key(r)}
    return {
        "total_indexed_records_reviewed": len(indexed_records),
        "eligible_records_available": len(eligible_records),
        "total_selected": len(selected_rows),
        "unique_companies_selected": len(companies),
        "full_reports_count": sum(1 for r in selected_rows if r.get("has_full_report")),
        "preview_only_count": sum(1 for r in selected_rows if not r.get("has_full_report")),
        "enriched_count": sum(1 for r in selected_rows if _is_enriched(r)),
        "tier1_high_count": sum(1 for x in evidence if "tier 1" in _norm(x)),
        "tier2_count": sum(1 for x in evidence if "tier 2" in _norm(x)),
        "not_checked_count": sum(1 for x in evidence if not _norm(x) or "not checked" in _norm(x)),
        "monitor_only_count": sum(1 for x in readiness if x == "monitor only"),
        "needs_validation_count": sum(1 for x in readiness if x == "needs validation"),
        "low_priority_archive_count": sum(1 for x in readiness if x == "low priority / archive"),
        "outreach_ready_count": sum(1 for x in readiness if x == "outreach-ready"),
        "strong_fit_count": sum(1 for x in fits if x == seller_target_matcher.FIT_STRONG),
        "moderate_fit_count": sum(1 for x in fits if x == seller_target_matcher.FIT_MODERATE),
        "weak_background_fit_count": sum(1 for x in fits if x == seller_target_matcher.FIT_WEAK),
        "source_type_breakdown": _counter_dict([str(r.get("source_type") or "not specified") for r in selected_rows]),
        "problem_category_breakdown": _counter_dict([str(r.get("problem_category") or "not specified") for r in selected_rows]),
        "average_opportunity_score": round(mean(scores), 1) if scores else None,
        "number_requiring_manual_audit": len(selected_rows),
        "number_with_official_source_urls_available": sum(1 for r in selected_rows if r.get("official_source_url")),
        "official_direct_source_records_available": sum(
            1 for r in selected_rows
            if (
                bool(r.get("official_source_url"))
                or str(r.get("direct_source_evidence_status") or "").lower().startswith("official direct source")
                or any(
                    token in _norm(r.get("source_type") or "")
                    for token in ("fda", "regulator", "recall", "clinicaltrials", "trial registry")
                )
            )
        ),
        "evidence_strength_distribution": _counter_dict(evidence),
        "readiness_distribution": _counter_dict(readiness),
        "seller_fit_distribution": _counter_dict(fits),
    }


def _build_limitations(
    indexed_count: int,
    eligible_count: int,
    selected: list[dict[str, Any]],
    requested_limit: int,
    prefer_unique_companies: bool,
) -> list[str]:
    limitations: list[str] = []
    if eligible_count < 100:
        limitations.append(
            f"Only {eligible_count} eligible indexed records are available. Generate/Refresh or expand sources before running a full 100-target validation study."
        )
    if len(selected) < requested_limit:
        limitations.append(
            f"The selected filters produced {len(selected)} target(s), below the requested maximum of {requested_limit}; no records were invented or duplicated to fill the study."
        )
    unique_companies = len({_company_key(r) for r in selected if _company_key(r)})
    if prefer_unique_companies and unique_companies < len(selected):
        limitations.append(
            f"The study contains {unique_companies} unique companies across {len(selected)} opportunities because the eligible index did not contain enough distinct companies."
        )
    preview_count = sum(1 for r in selected if not r.get("has_full_report"))
    if preview_count:
        limitations.append(f"{preview_count} selected record(s) are indexed previews without a generated full report.")
    not_checked = sum(
        1 for r in selected
        if "not checked" in _norm(r.get("best_evidence_tier") or r.get("evidence_quality") or "not checked")
    )
    if not_checked:
        limitations.append(f"{not_checked} selected record(s) have evidence quality/enrichment that is not checked.")
    monitor = sum(1 for r in selected if _normalise_lead_status(r.get("lead_status")) == "monitor only")
    if monitor:
        limitations.append(
            f"{monitor} selected record(s) remain monitor only; inclusion supports classification testing and does not establish current commercial urgency."
        )
    low_priority = sum(1 for r in selected if _normalise_lead_status(r.get("lead_status")) == "low priority / archive")
    if low_priority:
        limitations.append(
            f"{low_priority} selected record(s) are labelled low priority / archive and are retained for internal false-positive and weak-signal validation."
        )
    limitations.extend([
        "Seller Fit Strength is a deterministic technical/capability-fit label, not proof of commercial readiness or customer need.",
        "Label and literature context cannot confirm a product-specific root cause; trial termination is not treated as product failure unless the registry directly states a relevant reason.",
        "The study uses the current local SQLite opportunity index and existing enrichment only; it is not a complete global dataset or production SaaS validation database.",
        "All selected records require human audit before any external use.",
    ])
    return limitations


def build_validation_study(
    indexed_records: list[dict[str, Any]] | None,
    *,
    validation_title: str = DEFAULT_VALIDATION_TITLE,
    seller_service_profile: str = DEFAULT_SELLER_SERVICE_PROFILE,
    capability_categories: list[str] | None = None,
    problem_signals: list[str] | None = None,
    region_filter: list[str] | str | None = None,
    include_monitor_only: bool = True,
    include_preview_only: bool = True,
    include_low_priority_archive: bool = True,
    minimum_evidence_quality: str = "Any",
    maximum_targets: int = 100,
    prefer_unique_companies: bool = True,
    require_full_report: bool = False,
    require_enrichment: bool = False,
) -> dict[str, Any]:
    """Build an audit-ready validation set from existing indexed records only."""
    # Deep copies are used to make mutation of scores/IDs/data impossible.
    source_records = deepcopy(indexed_records or [])
    maximum_targets = max(1, min(int(maximum_targets or 100), 100))
    capabilities = list(capability_categories or DEFAULT_CAPABILITIES)
    selected_problem_signals = list(dict.fromkeys(problem_signals or DEFAULT_PROBLEM_SIGNALS))
    profile = {
        "validation_title": (validation_title or DEFAULT_VALIDATION_TITLE).strip(),
        "seller_service_profile": (seller_service_profile or DEFAULT_SELLER_SERVICE_PROFILE).strip(),
        "capability_categories": capabilities,
        "problem_signals": selected_problem_signals,
        "region_filter": [region_filter] if isinstance(region_filter, str) and region_filter.strip() else list(region_filter or []),
        "include_monitor_only": bool(include_monitor_only),
        "include_preview_only": bool(include_preview_only),
        "include_low_priority_archive": bool(include_low_priority_archive),
        "minimum_evidence_quality": minimum_evidence_quality or "Any",
        "maximum_targets": maximum_targets,
        "prefer_unique_companies": bool(prefer_unique_companies),
        "require_full_report": bool(require_full_report),
        "require_enrichment": bool(require_enrichment),
    }

    if not source_records:
        metrics = calculate_metrics([], [], [])
        return {
            "status": "empty",
            "message": "Run Generate first to create indexed PharmaTune evidence before building the validation study.",
            "rows": [],
            "metrics": metrics,
            "limitations": ["No indexed records were available."],
            "validation_profile": profile,
            "warning": "Only 0 eligible indexed records are available. Generate/Refresh or expand sources before running a full 100-target validation study.",
        }

    visible_records = [r for r in source_records if not _is_truly_hidden_or_rejected(r)]
    lookup = _record_lookup(visible_records)
    seller_result = seller_target_matcher.match_seller_to_targets(
        seller_name=profile["seller_service_profile"],
        seller_description=profile["seller_service_profile"],
        capability_categories=capabilities,
        indexed_records=visible_records,
        problem_signals=selected_problem_signals,
        dosage_focus=None,
        region_preference=None,
        min_evidence_quality="Any",
        include_monitor_only=True,
        max_targets=max(len(visible_records), 100),
        include_weak=True,
    )

    matched: list[dict[str, Any]] = []
    for match in seller_result.get("matches", []) or []:
        original = _match_original(match, lookup)
        if not original:
            # Never create an unmatched target: provenance to an indexed record is mandatory.
            continue
        combined = {**deepcopy(original), **deepcopy(match)}
        combined["stable_lead_id"] = original.get("stable_lead_id") or match.get("stable_lead_id") or ""
        matched.append(combined)

    eligible: list[dict[str, Any]] = []
    for record in matched:
        lead_status = _normalise_lead_status(record.get("lead_status"))
        if lead_status == "monitor only" and not include_monitor_only:
            continue
        if lead_status == "low priority / archive" and not include_low_priority_archive:
            continue
        if not _matches_region(record, region_filter):
            continue
        if not _passes_quality(record, minimum_evidence_quality):
            continue
        has_report = _as_bool(record.get("has_full_report"))
        if require_full_report and not has_report:
            continue
        if not include_preview_only and not has_report:
            continue
        if require_enrichment and not _is_enriched(record):
            continue
        eligible.append(record)

    selected = _select_ranked(eligible, maximum_targets, prefer_unique_companies)
    rows = [_validation_row(record, rank) for rank, record in enumerate(selected, start=1)]
    metrics = calculate_metrics(source_records, eligible, rows)
    limitations = _build_limitations(
        indexed_count=len(source_records),
        eligible_count=len(eligible),
        selected=rows,
        requested_limit=maximum_targets,
        prefer_unique_companies=prefer_unique_companies,
    )
    warning = ""
    if len(eligible) < 100:
        warning = (
            f"Only {len(eligible)} eligible indexed records are available. "
            "Generate/Refresh or expand sources before running a full 100-target validation study."
        )

    if not rows:
        status = "no_matches"
        message = "No eligible indexed records matched the selected validation profile and filters."
    else:
        status = "ok"
        message = f"Built an internal validation set of {len(rows)} indexed opportunity record(s)."

    return {
        "status": status,
        "message": message,
        "warning": warning,
        "rows": rows,
        "metrics": metrics,
        "limitations": limitations,
        "validation_profile": profile,
        "seller_capabilities": seller_result.get("seller_capabilities", capabilities),
        "method_note": (
            "Selected from existing indexed/enriched PharmaTune public evidence using deterministic seller-to-target matching and configured filters only. "
            "No APIs or LLMs were called, and Opportunity Scores and stable lead IDs were not changed."
        ),
    }


def export_validation_csv(result: dict[str, Any]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for source_row in result.get("rows", []) or []:
        row = {field: source_row.get(field, "") for field in CSV_FIELDS}
        row["has_full_report"] = "yes" if source_row.get("has_full_report") else "no"
        for key in (
            "lead_status",
            "corroboration_status",
            "official_followup_status",
            "label_context_status",
            "clinical_trial_context_status",
            "literature_context_status",
        ):
            row[key] = db.normalize_status_label(row.get(key)) or ""
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig")


def _format_breakdown(values: dict[str, int]) -> str:
    if not values:
        return "None available"
    return "; ".join(f"{key}: {count}" for key, count in values.items())


def build_markdown_summary(result: dict[str, Any]) -> str:
    profile = result.get("validation_profile", {}) or {}
    metrics = result.get("metrics", {}) or {}
    limitations = result.get("limitations", []) or []
    title = profile.get("validation_title") or DEFAULT_VALIDATION_TITLE
    regions = profile.get("region_filter") or []
    filters = [
        f"Region: {'; '.join(regions) if regions else 'All regions'}",
        f"Include monitor-only leads: {'yes' if profile.get('include_monitor_only') else 'no'}",
        f"Include preview-only records: {'yes' if profile.get('include_preview_only') else 'no'}",
        f"Include low priority / archive leads: {'yes' if profile.get('include_low_priority_archive') else 'no'}",
        f"Minimum evidence quality: {profile.get('minimum_evidence_quality') or 'Any'}",
        f"Maximum targets: {profile.get('maximum_targets') or 100}",
        f"Prefer unique companies: {'yes' if profile.get('prefer_unique_companies') else 'no'}",
        f"Require full report: {'yes' if profile.get('require_full_report') else 'no'}",
        f"Require enrichment: {'yes' if profile.get('require_enrichment') else 'no'}",
    ]
    warning = result.get("warning") or ""

    lines = [
        f"# {title}",
        "",
        "## 1. Validation objective",
        "Determine whether PharmaTune can generate up to 100 evidence-backed pharma target opportunities that a human auditor can verify for source accuracy, problem classification, and reasonable seller-fit reasoning.",
        "This is an internal validation study, not a marketing case study, sales pipeline, or public showcase.",
        "",
        f"**Seller/service profile:** {profile.get('seller_service_profile') or DEFAULT_SELLER_SERVICE_PROFILE}",
        "",
        "**Capabilities selected:** " + "; ".join(profile.get("capability_categories") or DEFAULT_CAPABILITIES),
        "",
        "**Problem signals selected:** " + "; ".join(profile.get("problem_signals") or DEFAULT_PROBLEM_SIGNALS),
        "",
        "**Filters used:** " + " | ".join(filters),
        "",
        "## 2. Dataset used",
        f"The workflow reviewed {metrics.get('total_indexed_records_reviewed', 0)} currently indexed PharmaTune opportunity records.",
        f"{metrics.get('eligible_records_available', 0)} records were eligible after the selected profile and filters, and {metrics.get('total_selected', 0)} were selected for manual validation.",
        "The study uses indexed public evidence, stored reports, and existing enrichment metadata only. No APIs or LLMs were called during validation-set construction.",
    ]
    if warning:
        lines.extend(["", f"> **Coverage warning:** {warning}"])
    lines.extend([
        "",
        "## 3. Selection method",
        "Existing indexed records were matched against the selected seller capabilities and problem signals using PharmaTune's deterministic seller-to-target rules.",
        "Official-source evidence, enriched records, full reports, evidence quality, Seller Fit Strength, source coverage, and stored Opportunity Score were used for ordering. Unique companies were preferred when configured.",
        "Only truly hidden/rejected workflow records were excluded automatically. Low priority / archive lead classifications remained eligible when the corresponding filter was enabled and retained their original label.",
        "No records were invented, no Opportunity Scores were recalculated, and no stable lead IDs were changed.",
        "",
        "## 4. What the validation study is testing",
        "- Whether an official/public source can be located for each selected record.",
        "- Whether company, product, source ID, status/date, and problem-signal fields match the source.",
        "- Whether PharmaTune's problem classification is accurate or reasonable.",
        "- Whether deterministic seller-fit reasoning is technically reasonable without implying confirmed customer need.",
        "- Whether the system avoids false product-specific root-cause claims and false commercial-urgency claims.",
        "",
        "## 5. Summary metrics",
        f"- Total indexed records reviewed: {metrics.get('total_indexed_records_reviewed', 0)}",
        f"- Eligible records available: {metrics.get('eligible_records_available', 0)}",
        f"- Total selected: {metrics.get('total_selected', 0)}",
        f"- Unique companies selected: {metrics.get('unique_companies_selected', 0)}",
        f"- Full reports: {metrics.get('full_reports_count', 0)}",
        f"- Preview-only records: {metrics.get('preview_only_count', 0)}",
        f"- Enriched records: {metrics.get('enriched_count', 0)}",
        f"- Tier 1 / high: {metrics.get('tier1_high_count', 0)}",
        f"- Tier 2: {metrics.get('tier2_count', 0)}",
        f"- Evidence enrichment/quality not checked: {metrics.get('not_checked_count', 0)}",
        f"- Official direct-source records available: {metrics.get('official_direct_source_records_available', 0)}",
        f"- Monitor only: {metrics.get('monitor_only_count', 0)}",
        f"- Needs validation: {metrics.get('needs_validation_count', 0)}",
        f"- Low priority / archive: {metrics.get('low_priority_archive_count', 0)}",
        f"- Strong fit: {metrics.get('strong_fit_count', 0)}",
        f"- Moderate fit: {metrics.get('moderate_fit_count', 0)}",
        f"- Weak/background fit: {metrics.get('weak_background_fit_count', 0)}",
        f"- Average Opportunity Score: {metrics.get('average_opportunity_score') if metrics.get('average_opportunity_score') is not None else 'not available'}",
        f"- Records requiring manual audit: {metrics.get('number_requiring_manual_audit', 0)}",
        f"- Stored official-source URLs available: {metrics.get('number_with_official_source_urls_available', 0)}",
        f"- Source types: {_format_breakdown(metrics.get('source_type_breakdown', {}))}",
        f"- Problem categories: {_format_breakdown(metrics.get('problem_category_breakdown', {}))}",
        "",
        "## 6. Evidence quality distribution",
        _format_breakdown(metrics.get("evidence_strength_distribution", {})),
        "Evidence quality/enrichment marked not checked does not mean that no official source exists. Official direct-source presence is reported separately above.",
        "",
        "## 7. Readiness distribution",
        _format_breakdown(metrics.get("readiness_distribution", {})),
        "",
        "Seller Fit Strength distribution: " + _format_breakdown(metrics.get("seller_fit_distribution", {})),
        "Seller Fit Strength reflects technical/capability fit only, not commercial readiness.",
        "",
        "## 8. Manual audit instructions",
        "Open each official source URL where available, or locate the authoritative source manually when the URL is blank. Complete every manual audit column in the CSV.",
        "Record whether the source exists; whether company, product, source ID, problem signal, and status/date classification match; whether root cause is directly confirmed; and whether PharmaTune's seller-fit reasoning is reasonable.",
        "Allowed manual verdicts: " + ", ".join(MANUAL_VERDICT_OPTIONS) + ".",
        "Do not mark root cause as confirmed unless the authoritative source directly states it. Do not infer confirmed customer need, urgency, or partnership intent.",
        "",
        "## 9. Pass/fail criteria",
        "The validation study should be considered successful only if, after manual audit:",
        "- At least 80% of audited records have an official source found.",
        "- At least 80% have company/product/source ID matching.",
        "- At least 70% have a reasonable problem-signal classification.",
        "- At least 60% have a reasonable seller-fit classification.",
        "- Zero records falsely claim a confirmed root cause without official evidence.",
        "- Zero records claim confirmed customer need or commercial urgency.",
        "- Fewer than 20% require removal from external use.",
        "",
        "**Current result: Pending manual audit - no validation verdict has been calculated.**",
        "If thresholds are not met, the result is a product-improvement finding and must not be hidden.",
        "",
        "## 10. Limitations",
    ])
    for limitation in limitations:
        lines.append(f"- {limitation}")
    lines.extend([
        "",
        "## 11. Next decision after audit",
        "Calculate the manual pass/partial/fail/removal rates, identify systematic source or classification errors, and decide whether to improve source coverage, matching rules, enrichment, or analyst controls before any external 100-company case study.",
        "",
        "The selected records are possible evidence-backed targets requiring validation. They are not confirmed customer needs, do not prove commercial urgency, and do not establish that a seller can fix an issue. No product-specific root cause is confirmed unless directly stated by an authoritative source.",
    ])
    return "\n".join(lines).strip() + "\n"


def export_validation_markdown(result: dict[str, Any]) -> bytes:
    return build_markdown_summary(result).encode("utf-8")
