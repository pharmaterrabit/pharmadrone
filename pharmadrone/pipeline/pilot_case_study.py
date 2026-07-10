"""Checkpoint 3: deterministic 20-company pilot case study builder.

This module is deliberately read-only. It uses existing indexed/enriched
PharmaTune opportunity records and the existing deterministic seller-to-target
matcher. It does not call APIs, require an LLM, mutate Opportunity Scores, or
change stable lead IDs.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from statistics import mean
from typing import Any

from .. import db
from . import seller_target_matcher

DEFAULT_CASE_STUDY_TITLE = "20-company pilot: BD target discovery for formulation and solubility-enhancement service providers"
DEFAULT_CASE_STUDY_OBJECTIVE = (
    "Test whether PharmaTune can identify public pharma product/problem signals that may be relevant to "
    "formulation CDMOs, particle-engineering companies, solubility-enhancement technology providers, "
    "dissolution-testing labs, and analytical/QC service providers."
)
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
]

CSV_FIELDS = [
    "case_study_title",
    "case_study_objective",
    "seller_service_profile",
    "capabilities_selected",
    "problem_signals_selected",
    "region_filter",
    "include_monitor_only",
    "include_preview_only",
    "minimum_evidence_quality",
    "maximum_targets",
    "selection_basis",
    "case_study_caution",
    "pilot_rank",
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
    "pilot_commentary",
]

_BUCKET_LABELS = {
    "dissolution_solubility_bioavailability": "Dissolution / solubility / bioavailability",
    "stability_formulation": "Stability / formulation robustness",
    "impurity_analytical_qc": "Impurity / analytical / QC",
    "other": "Other quality, clinical, delivery, sterility, packaging, or manufacturing",
}

_FIT_RANK = {
    seller_target_matcher.FIT_STRONG: 0,
    seller_target_matcher.FIT_MODERATE: 1,
    seller_target_matcher.FIT_WEAK: 2,
}


def _norm(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+/.-]+", " ", str(value).lower())).strip()


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


def _quality_rank(record: dict[str, Any]) -> int:
    label = _norm(record.get("best_evidence_tier") or record.get("evidence_quality") or "not checked")
    if "tier 1" in label:
        return 0
    if "tier 2" in label:
        return 1
    if "not checked" in label or not label:
        return 2
    if "tier 3" in label:
        return 3
    if "tier 4" in label:
        return 4
    return 5


def _is_archived(record: dict[str, Any]) -> bool:
    text = _norm(f"{record.get('lead_status', '')} {record.get('queue_status', '')} {record.get('novelty_status', '')}")
    return "archive" in text or "rejected" in text or "hidden" in text


def _rank_key(record: dict[str, Any]) -> tuple[Any, ...]:
    """Required deterministic pilot ranking without changing stored scores."""
    return (
        _quality_rank(record),
        0 if record.get("has_full_report") else 1,
        -_safe_int(record.get("source_coverage_count")),
        -_safe_int(record.get("opportunity_score")),
        1 if _is_archived(record) else 0,
        _FIT_RANK.get(record.get("fit_strength"), 9),
        _norm(record.get("target_company") or record.get("company")),
        _norm(record.get("product")),
        _norm(record.get("source_id")),
        _norm(record.get("stable_lead_id")),
    )


def _bucket_for(record: dict[str, Any]) -> str:
    # Bucket composition is driven by the indexed problem signal/category, not by
    # the broad pilot seller portfolio. Including seller capability text here
    # would incorrectly place every multi-capability match in the dissolution
    # bucket simply because the profile contains solubility-related services.
    text = _norm(
        " ".join(
            str(record.get(key) or "")
            for key in ("problem_category", "problem_signal", "product", "molecule")
        )
    )
    if any(term in text for term in (
        "dissolution", "solubility", "bioavailability", "food effect", "dose burden",
    )):
        return "dissolution_solubility_bioavailability"
    if any(term in text for term in (
        "stability", "formulation", "robustness", "precipitation", "degradation", "shelf life",
    )):
        return "stability_formulation"
    if any(term in text for term in (
        "impurit", "analytical", "quality control", " qc ", "assay", "potency", "nitrosamine",
        "batch variability", "failed specification", "content uniformity",
    )):
        return "impurity_analytical_qc"
    return "other"


def _company_key(record: dict[str, Any]) -> str:
    return _norm(record.get("target_company") or record.get("company") or "unknown company")


def _record_key(record: dict[str, Any]) -> str:
    return str(
        record.get("stable_lead_id")
        or "|".join(
            [
                _company_key(record),
                _norm(record.get("product")),
                _norm(record.get("problem_category")),
                _norm(record.get("source_id")),
            ]
        )
    )


def _select_pilot(matches: list[dict[str, Any]], limit: int = 20) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Select preferred 5/5/5/5 composition, preferring unique companies."""
    limit = max(1, min(int(limit or 20), 20))
    ordered = sorted(matches, key=_rank_key)
    bucket_order = [
        "dissolution_solubility_bioavailability",
        "stability_formulation",
        "impurity_analytical_qc",
        "other",
    ]
    groups = {bucket: [m for m in ordered if _bucket_for(m) == bucket] for bucket in bucket_order}
    base_quota, remainder = divmod(limit, len(bucket_order))
    bucket_targets = {
        bucket: base_quota + (1 if i < remainder else 0)
        for i, bucket in enumerate(bucket_order)
    }
    selected: list[dict[str, Any]] = []
    selected_keys: set[str] = set()
    used_companies: set[str] = set()
    bucket_counts = {bucket: 0 for bucket in bucket_order}

    def add(record: dict[str, Any], bucket: str) -> bool:
        key = _record_key(record)
        if key in selected_keys or len(selected) >= limit:
            return False
        item = dict(record)
        item["pilot_bucket"] = bucket
        selected.append(item)
        selected_keys.add(key)
        used_companies.add(_company_key(record))
        bucket_counts[bucket] += 1
        return True

    # First pass: preferred bucket composition with unique companies globally.
    for bucket in bucket_order:
        for record in groups[bucket]:
            if bucket_counts[bucket] >= bucket_targets[bucket] or len(selected) >= limit:
                break
            if _company_key(record) in used_companies:
                continue
            add(record, bucket)

    # Second pass: fill bucket shortages, allowing repeat companies when necessary.
    for bucket in bucket_order:
        for record in groups[bucket]:
            if bucket_counts[bucket] >= bucket_targets[bucket] or len(selected) >= limit:
                break
            add(record, bucket)

    # Third pass: fill remaining slots with next-best unique companies across all buckets.
    for record in ordered:
        if len(selected) >= limit:
            break
        if _company_key(record) in used_companies:
            continue
        add(record, _bucket_for(record))

    # Final pass: fill any remaining slots with next-best unused opportunity records.
    for record in ordered:
        if len(selected) >= limit:
            break
        add(record, _bucket_for(record))

    return selected, bucket_counts


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


def _is_enriched(record: dict[str, Any]) -> bool:
    label = _norm(record.get("enrichment_status") or "not checked")
    return label not in {"", "not checked", "enrichment not checked", "unchecked"}


def _pilot_commentary(record: dict[str, Any]) -> str:
    status = _normalise_lead_status(record.get("lead_status"))
    if status == "monitor only":
        return (
            "Historical or limited evidence-backed signal retained as monitor only. "
            "Technical fit may be relevant, but current commercial urgency and product-specific root cause require validation."
        )
    if not record.get("has_full_report"):
        return (
            "Indexed preview only. A possible seller fit was identified deterministically, but a full report and human validation are still required."
        )
    return (
        "Full stored report available. Public evidence indicates a possible fit that requires validation; "
        "no product-specific root cause or confirmed partnership opportunity is implied."
    )


def _pilot_row(record: dict[str, Any], rank: int, profile: dict[str, Any]) -> dict[str, Any]:
    questions = record.get("validation_questions") or []
    if isinstance(questions, str):
        questions_text = questions
    else:
        questions_text = " | ".join(str(q) for q in questions if q)
    row = {
        "case_study_title": profile.get("case_study_title") or DEFAULT_CASE_STUDY_TITLE,
        "case_study_objective": profile.get("case_study_objective") or DEFAULT_CASE_STUDY_OBJECTIVE,
        "seller_service_profile": profile.get("seller_service_profile") or DEFAULT_SELLER_SERVICE_PROFILE,
        "capabilities_selected": "; ".join(profile.get("capability_categories") or []),
        "problem_signals_selected": "; ".join(profile.get("problem_signals") or []),
        "region_filter": "; ".join(profile.get("region_filter") or []) or "All regions",
        "include_monitor_only": "yes" if profile.get("include_monitor_only") else "no",
        "include_preview_only": "yes" if profile.get("include_preview_only") else "no",
        "minimum_evidence_quality": profile.get("minimum_evidence_quality") or "Any",
        "maximum_targets": int(profile.get("maximum_targets") or 20),
        "selection_basis": "Selected from currently indexed public PharmaTune evidence only.",
        "case_study_caution": "Possible BD targets requiring validation; not confirmed customer needs.",
        "pilot_rank": rank,
        "target_company": record.get("target_company") or record.get("company") or "",
        "product": record.get("product") or "",
        "molecule": record.get("molecule") or "",
        "problem_category": record.get("problem_category") or "",
        "source_type": record.get("source_type") or "",
        "source_id": record.get("source_id") or "",
        "region": record.get("region") or "",
        "opportunity_score": record.get("opportunity_score") if record.get("opportunity_score") is not None else "",
        "grade": record.get("grade") or "",
        "lead_status": _normalise_lead_status(record.get("lead_status")),
        "queue_status": record.get("queue_status") or "",
        "evidence_quality": record.get("evidence_quality") or "not checked",
        "best_evidence_tier": record.get("best_evidence_tier") or record.get("evidence_quality") or "not checked",
        "corroboration_status": db.normalize_status_label(record.get("corroboration_status") or "direct source only") or "direct source only",
        "official_followup_status": db.normalize_status_label(record.get("official_followup_status") or "not checked") or "not checked",
        "label_context_status": db.normalize_status_label(record.get("label_context_status") or "not checked") or "not checked",
        "clinical_trial_context_status": db.normalize_status_label(record.get("clinical_trial_context_status") or "not checked") or "not checked",
        "literature_context_status": db.normalize_status_label(record.get("literature_context_status") or "not checked") or "not checked",
        "source_coverage_count": _safe_int(record.get("source_coverage_count")),
        "seller_fit_strength": record.get("fit_strength") or seller_target_matcher.FIT_WEAK,
        "seller_capability_match": record.get("seller_capability") or "",
        "why_fit": record.get("why_fit") or "Possible fit; requires validation.",
        "what_evidence_proves": record.get("what_evidence_proves") or "Public evidence indicates an indexed opportunity signal.",
        "what_evidence_does_not_prove": record.get("what_evidence_does_not_prove") or (
            "The evidence does not prove current need, commercial urgency, or a product-specific root cause."
        ),
        "safe_bd_angle": record.get("safe_bd_angle") or record.get("recommended_bd_angle") or "Validation-led discussion only.",
        "validation_questions": questions_text,
        "has_full_report": bool(record.get("has_full_report")),
        "report_path": record.get("report_path") or "",
        "pilot_commentary": _pilot_commentary(record),
        # Internal QA/metrics fields; intentionally omitted from the pilot CSV schema.
        "stable_lead_id": record.get("stable_lead_id") or "",
        "enrichment_status": record.get("enrichment_status") or "enrichment not checked",
        "pilot_bucket": record.get("pilot_bucket") or _bucket_for(record),
    }
    return row


def _counter_dict(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(v or "not specified" for v in values).items(), key=lambda x: (-x[1], x[0])))


def calculate_metrics(indexed_records: list[dict[str, Any]], pilot_rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [_safe_float(r.get("opportunity_score")) for r in pilot_rows]
    scores = [s for s in scores if s is not None]
    evidence_labels = [str(r.get("best_evidence_tier") or r.get("evidence_quality") or "not checked") for r in pilot_rows]
    lead_labels = [_normalise_lead_status(r.get("lead_status")) for r in pilot_rows]
    fit_labels = [str(r.get("seller_fit_strength") or seller_target_matcher.FIT_WEAK) for r in pilot_rows]
    return {
        "total_indexed_records_reviewed": len(indexed_records),
        "target_opportunities_selected": len(pilot_rows),
        "full_reports_count": sum(1 for r in pilot_rows if r.get("has_full_report")),
        "preview_only_count": sum(1 for r in pilot_rows if not r.get("has_full_report")),
        "enriched_count": sum(1 for r in pilot_rows if _is_enriched(r)),
        "tier1_high_count": sum(1 for x in evidence_labels if "tier 1" in _norm(x)),
        "tier2_count": sum(1 for x in evidence_labels if "tier 2" in _norm(x)),
        "not_checked_count": sum(1 for x in evidence_labels if "not checked" in _norm(x) or not _norm(x)),
        "monitor_only_count": sum(1 for x in lead_labels if x == "monitor only"),
        "needs_validation_count": sum(1 for x in lead_labels if x == "needs validation"),
        "low_priority_archive_count": sum(1 for x in lead_labels if x == "low priority / archive"),
        "outreach_ready_count": sum(1 for x in lead_labels if x == "outreach-ready"),
        "strong_fit_count": sum(1 for x in fit_labels if x == seller_target_matcher.FIT_STRONG),
        "moderate_fit_count": sum(1 for x in fit_labels if x == seller_target_matcher.FIT_MODERATE),
        "weak_background_fit_count": sum(1 for x in fit_labels if x == seller_target_matcher.FIT_WEAK),
        "source_type_breakdown": _counter_dict([str(r.get("source_type") or "not specified") for r in pilot_rows]),
        "problem_category_breakdown": _counter_dict([str(r.get("problem_category") or "not specified") for r in pilot_rows]),
        "average_opportunity_score": round(mean(scores), 1) if scores else None,
        "evidence_strength_distribution": _counter_dict(evidence_labels),
        "readiness_distribution": _counter_dict(lead_labels),
        "seller_fit_distribution": _counter_dict(fit_labels),
    }


def _limitations(
    indexed_records: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    bucket_counts: dict[str, int],
    requested_limit: int,
) -> list[str]:
    limitations: list[str] = []
    requested_limit = max(1, min(int(requested_limit or 20), 20))
    if len(selected) < requested_limit:
        limitations.append(
            f"The current opportunity index produced only {len(selected)} eligible seller-fit record(s), so the pilot contains fewer than the requested {requested_limit} targets."
        )
    unique_companies = len({_company_key(r) for r in selected})
    if unique_companies < len(selected):
        limitations.append(
            f"The pilot contains {unique_companies} unique companies across {len(selected)} opportunity records because the current index did not provide enough unique companies for every slot."
        )
    base_quota, remainder = divmod(requested_limit, len(bucket_counts))
    for i, (bucket, count) in enumerate(bucket_counts.items()):
        expected = base_quota + (1 if i < remainder else 0)
        if count < expected:
            limitations.append(
                f"Only {count} target(s) were available for the preferred '{_BUCKET_LABELS[bucket]}' bucket versus a target of {expected}; remaining capacity was filled with the next best evidence-backed opportunities where available."
            )
    preview_count = sum(1 for r in selected if not r.get("has_full_report"))
    if preview_count:
        limitations.append(
            f"{preview_count} selected record(s) are indexed previews without a full stored report and require additional report generation and human validation."
        )
    not_checked = sum(
        1 for r in selected
        if "not checked" in _norm(r.get("best_evidence_tier") or r.get("evidence_quality") or "not checked")
    )
    if not_checked:
        limitations.append(f"{not_checked} selected record(s) have evidence enrichment that is not yet checked.")
    monitor_count = sum(1 for r in selected if _normalise_lead_status(r.get("lead_status")) == "monitor only")
    if monitor_count:
        limitations.append(
            f"{monitor_count} selected record(s) remain monitor only; these are historical/limited signals and do not establish current commercial urgency."
        )
    limitations.append(
        "Seller Fit Strength reflects deterministic technical/capability fit only, not commercial readiness or proof that a target company needs a technology or service."
    )
    limitations.append(
        "The pilot uses the current local SQLite opportunity index and existing enrichment only; it is not a complete global market or production SaaS dataset."
    )
    return limitations


def build_pilot_case_study(
    indexed_records: list[dict[str, Any]] | None,
    limit: int = 20,
    *,
    case_study_title: str = DEFAULT_CASE_STUDY_TITLE,
    case_study_objective: str = DEFAULT_CASE_STUDY_OBJECTIVE,
    seller_service_profile: str = DEFAULT_SELLER_SERVICE_PROFILE,
    capability_categories: list[str] | None = None,
    problem_signals: list[str] | str | None = None,
    region_filter: list[str] | str | None = None,
    include_monitor_only: bool = True,
    include_preview_only: bool = True,
    minimum_evidence_quality: str = "Any",
) -> dict[str, Any]:
    """Build a configurable, deterministic, read-only pilot from indexed evidence."""
    records = [dict(r) for r in (indexed_records or [])]
    max_targets = max(1, min(int(limit or 20), 20))
    capabilities = list(DEFAULT_CAPABILITIES) if capability_categories is None else list(capability_categories)
    selected_problem_signals = (
        list(DEFAULT_PROBLEM_SIGNALS)
        if problem_signals is None
        else seller_target_matcher._split_lines(problem_signals)
    )
    regions = seller_target_matcher._split_lines(region_filter)
    profile = {
        "case_study_title": (case_study_title or DEFAULT_CASE_STUDY_TITLE).strip(),
        "case_study_objective": (case_study_objective or DEFAULT_CASE_STUDY_OBJECTIVE).strip(),
        "seller_service_profile": (seller_service_profile or DEFAULT_SELLER_SERVICE_PROFILE).strip(),
        "capability_categories": capabilities,
        "problem_signals": selected_problem_signals,
        "region_filter": regions,
        "include_monitor_only": bool(include_monitor_only),
        "include_preview_only": bool(include_preview_only),
        "minimum_evidence_quality": minimum_evidence_quality or "Any",
        "maximum_targets": max_targets,
    }
    if not records:
        return {
            "status": "empty",
            "message": "Run Generate first to create indexed PharmaTune evidence before building the pilot case study.",
            "rows": [],
            "metrics": calculate_metrics([], []),
            "limitations": ["No indexed opportunity records were available."],
            "case_study_profile": profile,
        }

    eligible_records = records if include_preview_only else [r for r in records if bool(r.get("has_full_report"))]
    seller_result = seller_target_matcher.match_seller_to_targets(
        profile["seller_service_profile"],
        profile["case_study_objective"],
        capabilities,
        eligible_records,
        problem_signals=selected_problem_signals,
        dosage_focus=None,
        region_preference=regions or None,
        min_evidence_quality=profile["minimum_evidence_quality"],
        include_monitor_only=bool(include_monitor_only),
        max_targets=max(len(eligible_records), max_targets),
        include_weak=True,
    )
    matches = seller_result.get("matches", []) if seller_result.get("status") == "ok" else []
    selected, bucket_counts = _select_pilot(matches, limit=max_targets)
    rows = [_pilot_row(record, rank=i, profile=profile) for i, record in enumerate(selected, start=1)]
    metrics = calculate_metrics(records, rows)
    limitations = _limitations(records, selected, bucket_counts, max_targets)
    if not include_preview_only:
        limitations.append("Indexed preview-only records were excluded by the selected case-study filter.")
    if not include_monitor_only:
        limitations.append("Monitor-only records were excluded by the selected case-study filter.")
    if regions:
        limitations.append("The pilot was restricted to the selected region filter: " + "; ".join(regions) + ".")
    if profile["minimum_evidence_quality"] != "Any":
        limitations.append(
            "The pilot applied a minimum evidence-quality filter of "
            + profile["minimum_evidence_quality"]
            + "."
        )

    if not rows:
        message = (
            "No seller-fit matches were found using the selected case-study profile and filters. "
            "Broaden the profile or Generate/Refresh more indexed evidence."
        )
        status = "no_matches"
    else:
        message = f"Built a deterministic pilot set of {len(rows)} indexed opportunity record(s) for the selected case-study profile."
        status = "ok"

    return {
        "status": status,
        "message": message,
        "rows": rows,
        "metrics": metrics,
        "limitations": limitations,
        "bucket_counts": {_BUCKET_LABELS[k]: v for k, v in bucket_counts.items()},
        "seller_capabilities": capabilities,
        "problem_signals": selected_problem_signals,
        "case_study_profile": profile,
        "method_note": (
            "Targets were selected from currently indexed public PharmaTune evidence using the deterministic seller-to-target matcher only. "
            "They are possible BD targets requiring validation, not confirmed customer needs. "
            "The workflow does not call APIs or an LLM and does not modify Opportunity Scores or stable lead IDs."
        ),
    }


def export_pilot_csv(result: dict[str, Any]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for source_row in result.get("rows", []) or []:
        row = {field: source_row.get(field, "") for field in CSV_FIELDS}
        row["has_full_report"] = "yes" if source_row.get("has_full_report") else "no"
        for key in (
            "lead_status", "corroboration_status", "official_followup_status",
            "label_context_status", "clinical_trial_context_status", "literature_context_status",
        ):
            row[key] = db.normalize_status_label(row.get(key)) or ""
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig")


def _format_breakdown(values: dict[str, int]) -> str:
    if not values:
        return "None available"
    return "; ".join(f"{key}: {count}" for key, count in values.items())


def build_markdown_summary(result: dict[str, Any]) -> str:
    metrics = result.get("metrics", {}) or {}
    rows = result.get("rows", []) or []
    limitations = result.get("limitations", []) or []
    profile = result.get("case_study_profile", {}) or {}
    examples = rows[:5]
    title = profile.get("case_study_title") or DEFAULT_CASE_STUDY_TITLE
    objective = profile.get("case_study_objective") or DEFAULT_CASE_STUDY_OBJECTIVE
    seller_profile = profile.get("seller_service_profile") or DEFAULT_SELLER_SERVICE_PROFILE
    capabilities = profile.get("capability_categories") or DEFAULT_CAPABILITIES
    problem_signals = profile.get("problem_signals") or DEFAULT_PROBLEM_SIGNALS
    regions = profile.get("region_filter") or []
    filters = [
        f"Region: {'; '.join(regions) if regions else 'All regions'}",
        f"Include monitor-only leads: {'yes' if profile.get('include_monitor_only') else 'no'}",
        f"Include preview-only records: {'yes' if profile.get('include_preview_only') else 'no'}",
        f"Minimum evidence quality: {profile.get('minimum_evidence_quality') or 'Any'}",
        f"Maximum targets: {profile.get('maximum_targets') or 20}",
    ]

    lines = [
        f"# {title}",
        "",
        "## 1. Pilot objective",
        objective,
        "",
        "**Seller/service profile:** " + seller_profile,
        "",
        "**Capabilities selected:** " + "; ".join(capabilities),
        "",
        "**Problem signals selected:** " + "; ".join(problem_signals),
        "",
        "**Filters used:** " + " | ".join(filters),
        "",
        "## 2. Dataset used",
        f"The pilot reviewed {metrics.get('total_indexed_records_reviewed', 0)} currently indexed PharmaTune opportunity records and selected {metrics.get('target_opportunities_selected', 0)} evidence-backed target opportunities.",
        "Targets were selected from indexed public evidence, stored reports, and existing enrichment metadata only.",
        "These are possible BD targets requiring validation, not confirmed customer needs.",
        "",
        "## 3. Method",
        "The selected seller/service profile and capability categories were matched deterministically against existing indexed opportunity records. Problem-signal interests were used in deterministic matching, while region, evidence-quality, monitor-only, preview-only, and maximum-target filters were applied as configured. The preferred 20-target composition is five dissolution/solubility/bioavailability opportunities, five stability/formulation robustness opportunities, five impurity/analytical/QC opportunities, and five other quality, clinical, delivery, sterility, packaging, or manufacturing opportunities. For smaller target limits, the same composition is distributed proportionally.",
        "Tier 1 evidence, full reports, source coverage, and stored Opportunity Score were used for ordering; one record per company was preferred where possible.",
        "Seller Fit Strength reflects technical/capability fit only, not commercial readiness. Opportunity Scores and stable lead IDs were not changed.",
        "",
        "## 4. Summary metrics",
        f"- Full reports: {metrics.get('full_reports_count', 0)}",
        f"- Indexed previews: {metrics.get('preview_only_count', 0)}",
        f"- Enriched records: {metrics.get('enriched_count', 0)}",
        f"- Tier 1 / high: {metrics.get('tier1_high_count', 0)}",
        f"- Tier 2: {metrics.get('tier2_count', 0)}",
        f"- Evidence not checked: {metrics.get('not_checked_count', 0)}",
        f"- Monitor only: {metrics.get('monitor_only_count', 0)}",
        f"- Needs validation: {metrics.get('needs_validation_count', 0)}",
        f"- Low priority / archive: {metrics.get('low_priority_archive_count', 0)}",
        f"- Strong fit: {metrics.get('strong_fit_count', 0)}",
        f"- Moderate fit: {metrics.get('moderate_fit_count', 0)}",
        f"- Weak/background fit: {metrics.get('weak_background_fit_count', 0)}",
        f"- Average Opportunity Score: {metrics.get('average_opportunity_score') if metrics.get('average_opportunity_score') is not None else 'not available'}",
        f"- Source types: {_format_breakdown(metrics.get('source_type_breakdown', {}))}",
        f"- Evidence strength: {_format_breakdown(metrics.get('evidence_strength_distribution', {}))}",
        f"- Readiness: {_format_breakdown(metrics.get('readiness_distribution', {}))}",
        f"- Seller fit: {_format_breakdown(metrics.get('seller_fit_distribution', {}))}",
        "",
        "## 5. Top opportunity themes",
        _format_breakdown(metrics.get("problem_category_breakdown", {})),
        "",
        "## 6. Example target opportunities",
    ]
    if examples:
        for row in examples:
            lines.extend([
                f"### {row.get('pilot_rank')}. {row.get('target_company') or 'Unknown company'} - {row.get('product') or 'Unknown product'}",
                f"- Problem category: {row.get('problem_category') or 'not specified'}",
                f"- Seller capability match: {row.get('seller_capability_match') or 'not specified'}",
                f"- Seller Fit Strength: {row.get('seller_fit_strength') or 'not specified'}",
                f"- Lead status: {row.get('lead_status') or 'needs validation'}",
                f"- Evidence: {row.get('best_evidence_tier') or row.get('evidence_quality') or 'not checked'}",
                f"- Possible BD angle: {row.get('safe_bd_angle') or 'Requires validation.'}",
                f"- Evidence boundary: {row.get('what_evidence_does_not_prove') or 'No product-specific root cause confirmed unless directly stated.'}",
                "",
            ])
    else:
        lines.append("No eligible opportunities were selected.")

    lines.extend([
        "## 7. What PharmaTune did well",
        "- Identified real indexed public product/problem signals without creating new companies or events.",
        "- Applied the selected seller/service lens and filters deterministically.",
        "- Preserved evidence quality, lead readiness, full-report/preview distinctions, and monitor-only classifications.",
        "- Produced deterministic seller-fit explanations, safe BD angles, and validation questions without requiring an LLM.",
        "- Kept label and literature context separate from product-specific root-cause proof.",
        "",
        "## 8. What still requires human validation",
        "- Whether the public signal remains current and commercially relevant.",
        "- Whether the target company has an active technical need or budget.",
        "- The product-specific root cause, unless directly stated by an authoritative source.",
        "- Whether the seller capability is technically and operationally suitable for the target context.",
        "",
        "## 9. Limitations",
    ])
    for limitation in limitations:
        lines.append(f"- {limitation}")
    lines.extend([
        "",
        "## 10. Next step before 100-company study",
        "Review the selected pilot records manually, record false positives and missing context, confirm whether the target cards reduce analyst screening time, and refine the case-study lens and deterministic selection criteria before expanding to a 100-company study.",
        "",
        "This pilot identifies possible fits that may be relevant and require validation. Targets were selected from indexed public evidence and are not confirmed customer needs. No product-specific root cause is confirmed unless directly stated by an authoritative source. The pilot does not prove that a company currently needs a technology, that a seller can fix an issue, that commercial urgency exists, or that a partnership opportunity is confirmed.",
    ])
    return "\n".join(lines).strip() + "\n"


def export_pilot_markdown(result: dict[str, Any]) -> bytes:
    return build_markdown_summary(result).encode("utf-8")
