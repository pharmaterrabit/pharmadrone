"""Checkpoint 7A: real-provider, validation-gated customer case studies.

The provider capability profile is supported by the provider's own public
pages. Target candidates still come only from stored PharmaTune evidence.
Nothing in this module turns a public signal into proof of customer need.
"""
from __future__ import annotations

import csv
import html
import io
import json
from datetime import date, datetime, timezone
from hashlib import sha256
from typing import Any
from uuid import uuid4

from . import pilot_case_study


HOVIONE_PROFILE: dict[str, Any] = {
    "profile_id": "provider-hovione",
    "provider_name": "Hovione",
    "provider_type": "Integrated pharmaceutical CDMO and particle-engineering technology provider",
    "website_url": "https://www.hovione.com/",
    "profile_summary": (
        "Hovione publicly describes integrated drug-substance and drug-product development, "
        "particle-engineering and solubility-enhancement technologies, and analytical support "
        "covering dissolution, stability, related substances and impurity characterization."
    ),
    "capabilities": [
        "formulation CDMO",
        "particle engineering",
        "solubility enhancement",
        "dissolution testing",
        "analytical/QC testing",
        "stability troubleshooting",
        "impurity investigation",
    ],
    "evidence_sources": [
        {
            "title": "Hovione — Particle Engineering Development",
            "url": "https://www.hovione.com/products-and-services/contract-manufacturing-services/particle-engineering/development",
            "supports": "Particle-engineering development, formulation development and early clinical supply production.",
        },
        {
            "title": "Hovione — Particle Engineering Technologies",
            "url": "https://www.hovione.com/products-and-services/contract-manufacturing-services/particle-engineering/technologies",
            "supports": "Technologies addressing solubility, bioavailability and drug-delivery challenges from proof of concept to commercial manufacture.",
        },
        {
            "title": "Hovione — Analytical Support",
            "url": "https://www.hovione.com/products-and-services/supporting-capabilities/analytical-support",
            "supports": "Analytical development, physical stability evaluation, related-substances testing and dissolution release testing.",
        },
        {
            "title": "Hovione — Analytical Capabilities",
            "url": "https://www.hovione.com/products-and-services/supporting-capabilities/analytical-support/capabilities",
            "supports": "Impurity identification, analytical method work, dissolution methods and ICH stability studies.",
        },
    ],
    "last_verified_at": "2026-07-14",
}

CASE_STUDY_TITLE = "Hovione opportunity case study — evidence-backed product problem signals"
CASE_STUDY_OBJECTIVE = (
    "Identify public pharmaceutical product and problem signals that may warrant a validation-led "
    "discussion about Hovione's published formulation, particle-engineering, solubility, dissolution, "
    "analytical, stability or impurity capabilities."
)
PROBLEM_SIGNALS = list(pilot_case_study.DEFAULT_PROBLEM_SIGNALS)

REVIEW_EXPORT_FIELDS = [
    "pilot_rank", "target_company", "product", "problem_category", "source_type", "source_id",
    "seller_fit_strength", "seller_capability_match", "validation_status", "audit_version",
    "external_use_approved", "what_evidence_proves", "what_evidence_does_not_prove",
    "safe_bd_angle", "validation_questions", "stable_lead_id", "audit_key",
]


class CustomerExportBlocked(RuntimeError):
    """Raised when no target has passed the external-use human gate."""


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "approved"}
    return bool(value)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _record_key(record: dict[str, Any]) -> str:
    return str(
        record.get("audit_key")
        or record.get("stable_lead_id")
        or "|".join(
            (
                _norm(record.get("source_type")),
                _norm(record.get("source_id")),
                _norm(record.get("target_company") or record.get("company")),
                _norm(record.get("product")),
            )
        )
    )


def _audit_lookup(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for record in records:
        for key in (
            record.get("audit_key"),
            record.get("stable_lead_id"),
            "|".join((_norm(record.get("source_type")), _norm(record.get("source_id")))),
        ):
            if key:
                lookup[str(key)] = record
    return lookup


def _source_record(row: dict[str, Any], lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    for key in (
        row.get("audit_key"),
        row.get("stable_lead_id"),
        "|".join((_norm(row.get("source_type")), _norm(row.get("source_id")))),
    ):
        if key and str(key) in lookup:
            return lookup[str(key)]
    return {}


def _validation_status(record: dict[str, Any]) -> tuple[str, bool]:
    approved = (
        _bool(record.get("external_case_study_eligible"))
        and _bool(record.get("external_use_approved"))
        and _bool(record.get("external_gate_passed"))
    )
    if approved:
        return "Approved for customer case study", True
    if _bool(record.get("external_use_approved")) and not _bool(record.get("external_case_study_eligible")):
        return "External approval recorded — deterministic eligibility failed", False
    status = _norm(record.get("audit_status") or "pending")
    if status in {"approved", "approved_with_caution", "rejected"}:
        return "Reviewed — external approval required", False
    if status in {"in_review", "correction_required"} or int(record.get("audit_version") or 0) > 0:
        return "Human review in progress", False
    return "Awaiting human validation", False


def build_real_case_study(validation_records: list[dict[str, Any]], limit: int = 12) -> dict[str, Any]:
    """Build a real Hovione case study from the human-validation dataset."""
    records = [dict(row) for row in (validation_records or [])]
    pilot = pilot_case_study.build_pilot_case_study(
        records,
        limit=max(5, min(int(limit or 12), 20)),
        case_study_title=CASE_STUDY_TITLE,
        case_study_objective=CASE_STUDY_OBJECTIVE,
        seller_service_profile=(
            "Hovione — integrated pharmaceutical CDMO with published formulation development, "
            "particle engineering, solubility enhancement, dissolution, analytical/QC, stability "
            "and impurity-characterization capabilities"
        ),
        capability_categories=list(HOVIONE_PROFILE["capabilities"]),
        problem_signals=PROBLEM_SIGNALS,
        include_monitor_only=False,
        include_preview_only=True,
        minimum_evidence_quality="Any",
    )
    lookup = _audit_lookup(records)
    candidates: list[dict[str, Any]] = []
    for source in pilot.get("rows", []) or []:
        row = dict(source)
        audit = _source_record(row, lookup)
        status, approved = _validation_status(audit)
        row.update(
            audit_key=audit.get("audit_key") or "",
            audit_status=audit.get("audit_status") or "pending",
            audit_version=int(audit.get("audit_version") or 0),
            reviewer_name=audit.get("reviewer_name") or "",
            validation_status=status,
            external_use_approved=approved,
            external_gate_passed=_bool(audit.get("external_gate_passed")),
        )
        candidates.append(row)

    approved = [dict(row) for row in candidates if row.get("external_use_approved")]
    reviewed = sum(1 for row in candidates if int(row.get("audit_version") or 0) > 0)
    workflow_status = "customer_ready" if approved else "human_validation_required"
    message = (
        f"{len(approved)} customer-safe target(s) passed the human external-use gate."
        if approved
        else "The real Hovione candidate set is built. Customer export remains locked until a human reviewer approves at least one target for external use."
    )
    return {
        "status": workflow_status,
        "message": message,
        "provider_profile": dict(HOVIONE_PROFILE),
        "case_study_profile": pilot.get("case_study_profile", {}),
        "candidate_rows": candidates,
        "approved_rows": approved,
        "metrics": {
            "validation_records_reviewed": len(records),
            "candidate_count": len(candidates),
            "reviewed_count": reviewed,
            "approved_count": len(approved),
            "approval_pending_count": len(candidates) - len(approved),
        },
        "method_note": pilot.get("method_note", ""),
        "limitations": [
            str(item).replace("current local SQLite opportunity index", "current durable validation dataset")
            for item in (pilot.get("limitations", []) or [])
        ],
    }


def export_review_csv(result: dict[str, Any]) -> bytes:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=REVIEW_EXPORT_FIELDS)
    writer.writeheader()
    for source in result.get("candidate_rows", []) or []:
        row = {key: source.get(key, "") for key in REVIEW_EXPORT_FIELDS}
        row["external_use_approved"] = "yes" if source.get("external_use_approved") else "no"
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig")


def _approved_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in result.get("approved_rows", []) or []]
    if not rows:
        raise CustomerExportBlocked(
            "Customer export is locked because no target has passed the human external-use approval gate."
        )
    return rows


def build_customer_markdown(result: dict[str, Any]) -> str:
    rows = _approved_rows(result)
    provider = result.get("provider_profile", {}) or HOVIONE_PROFILE
    lines = [
        f"# {CASE_STUDY_TITLE}", "",
        f"**Prepared:** {date.today().isoformat()}", "",
        "## Provider capability profile", "",
        f"**{provider.get('provider_name', 'Hovione')}** — {provider.get('provider_type', '')}", "",
        str(provider.get("profile_summary") or ""), "",
        "**Published capabilities used for matching:** " + "; ".join(provider.get("capabilities", [])), "",
        "## Provider capability sources", "",
    ]
    for source in provider.get("evidence_sources", []):
        lines.append(f"- [{source.get('title')}]({source.get('url')}) — {source.get('supports')}")
    lines.extend([
        "", "## Customer-safe shortlist", "",
        "Every target below passed PharmaTune's separate human external-use gate. Approval confirms suitability for this case-study output only; it does not prove buying intent, budget, urgency or technical root cause.", "",
    ])
    for index, row in enumerate(rows, start=1):
        lines.extend([
            f"### {index}. {row.get('target_company') or 'Company not recorded'} — {row.get('product') or 'Product not recorded'}",
            f"- Public problem signal: {row.get('problem_category') or 'Not recorded'}",
            f"- Potential Hovione capability fit: {row.get('seller_capability_match') or 'Requires technical confirmation'}",
            f"- Fit strength: {row.get('seller_fit_strength') or 'Requires validation'}",
            f"- Public source: {row.get('source_type') or 'Source'} — {row.get('source_id') or 'Identifier not recorded'}",
            f"- What the evidence supports: {row.get('what_evidence_proves') or 'A public signal was indexed.'}",
            f"- Evidence boundary: {row.get('what_evidence_does_not_prove') or 'No current commercial need or root cause is established.'}",
            f"- Validation-led discussion angle: {row.get('safe_bd_angle') or 'Confirm current relevance and technical fit before any outreach.'}",
            "",
        ])
    lines.extend([
        "## Method and governance", "",
        "PharmaTune matched Hovione's verified public capability profile against stored public opportunity evidence using deterministic rules. Human validation and external-use approval were applied separately. Opportunity Scores and source records were not modified.", "",
        "This case study is market-intelligence support, not proof of customer demand, commercial urgency, budget, partnership intent or a confirmed technical solution.",
    ])
    return "\n".join(lines).strip() + "\n"


def export_customer_markdown(result: dict[str, Any]) -> bytes:
    return build_customer_markdown(result).encode("utf-8")


def export_customer_html(result: dict[str, Any]) -> bytes:
    rows = _approved_rows(result)
    provider = result.get("provider_profile", {}) or HOVIONE_PROFILE
    sources = "".join(
        "<li><a href='{url}'>{title}</a> — {supports}</li>".format(
            url=html.escape(str(source.get("url") or ""), quote=True),
            title=html.escape(str(source.get("title") or "Official provider source")),
            supports=html.escape(str(source.get("supports") or "")),
        )
        for source in provider.get("evidence_sources", [])
    )
    target_blocks = []
    for index, row in enumerate(rows, start=1):
        items = [
            ("Public problem signal", row.get("problem_category") or "Not recorded"),
            ("Potential Hovione capability fit", row.get("seller_capability_match") or "Requires technical confirmation"),
            ("Fit strength", row.get("seller_fit_strength") or "Requires validation"),
            ("Public source", f"{row.get('source_type') or 'Source'} — {row.get('source_id') or 'Identifier not recorded'}"),
            ("What the evidence supports", row.get("what_evidence_proves") or "A public signal was indexed."),
            ("Evidence boundary", row.get("what_evidence_does_not_prove") or "No current commercial need or root cause is established."),
            ("Validation-led discussion angle", row.get("safe_bd_angle") or "Confirm current relevance and technical fit before outreach."),
        ]
        target_blocks.append(
            f"<section><h3>{index}. {html.escape(str(row.get('target_company') or 'Company not recorded'))} — "
            f"{html.escape(str(row.get('product') or 'Product not recorded'))}</h3><ul>"
            + "".join(f"<li><b>{html.escape(label)}:</b> {html.escape(str(value))}</li>" for label, value in items)
            + "</ul></section>"
        )
    document = """<!doctype html><html><head><meta charset='utf-8'><title>PharmaTune customer case study</title>
<style>body{font-family:Inter,Arial,sans-serif;max-width:980px;margin:48px auto;padding:0 28px;color:#12203a;line-height:1.55}h1{color:#173a78}h2{margin-top:34px;border-bottom:1px solid #dce4f2;padding-bottom:8px}section{border:1px solid #dce4f2;border-radius:12px;padding:8px 22px;margin:18px 0}li{margin:7px 0}.notice{background:#eef4ff;border-left:4px solid #4d8dff;padding:12px 16px}.footer{margin-top:42px;color:#63708a;font-size:12px}</style></head><body>"""
    document += f"<h1>{html.escape(CASE_STUDY_TITLE)}</h1><p><b>Prepared:</b> {date.today().isoformat()}</p>"
    document += f"<h2>Provider capability profile</h2><p><b>{html.escape(str(provider.get('provider_name') or 'Hovione'))}</b> — {html.escape(str(provider.get('provider_type') or ''))}</p>"
    document += f"<p>{html.escape(str(provider.get('profile_summary') or ''))}</p><p><b>Published capabilities used for matching:</b> {html.escape('; '.join(provider.get('capabilities', [])))}</p>"
    document += f"<h2>Provider capability sources</h2><ul>{sources}</ul>"
    document += "<h2>Customer-safe shortlist</h2><p class='notice'>Every target below passed PharmaTune's deterministic eligibility check and separate human external-use gate. This does not prove buying intent, budget, urgency or technical root cause.</p>"
    document += "".join(target_blocks)
    document += "<h2>Method and governance</h2><p>PharmaTune matched Hovione's verified public capability profile against stored public opportunity evidence using deterministic rules. Human validation and external-use approval were applied separately. Opportunity Scores and source records were not modified.</p>"
    document += "<p>This case study is market-intelligence support, not proof of customer demand, commercial urgency, budget, partnership intent or a confirmed technical solution.</p>"
    document += "<div class='footer'>PharmaTune · Evidence-backed intelligence · Human validation required</div></body></html>"
    return document.encode("utf-8")


def save_snapshot(
    conn,
    result: dict[str, Any],
    *,
    organisation_id: str = "platform",
    created_by: str = "Analyst / Reviewer",
) -> str:
    """Persist an immutable case-study snapshot and its candidate targets."""
    provider = result.get("provider_profile", {}) or HOVIONE_PROFILE
    profile_id = str(provider.get("profile_id") or HOVIONE_PROFILE["profile_id"])
    case_study_id = "cs-" + uuid4().hex
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    profile_exists = conn.execute("SELECT profile_id FROM seller_profiles WHERE profile_id=?", (profile_id,)).fetchone()
    with conn.transaction():
        profile_values = (
            provider.get("provider_name") or "Hovione",
            provider.get("provider_type") or "Pharmaceutical service provider",
            provider.get("website_url") or "https://www.hovione.com/",
            provider.get("profile_summary") or "",
            json.dumps(provider.get("capabilities", []), ensure_ascii=False, sort_keys=True),
            json.dumps(provider.get("evidence_sources", []), ensure_ascii=False, sort_keys=True),
            provider.get("last_verified_at") or now[:10],
            now,
            profile_id,
        )
        if profile_exists:
            conn.execute(
                """UPDATE seller_profiles SET provider_name=?,provider_type=?,website_url=?,profile_summary=?,
                capabilities_json=?,evidence_sources_json=?,last_verified_at=?,status='active',updated_at=? WHERE profile_id=?""",
                profile_values,
            )
        else:
            conn.execute(
                """INSERT INTO seller_profiles
                (provider_name,provider_type,website_url,profile_summary,capabilities_json,evidence_sources_json,
                 last_verified_at,status,updated_at,profile_id) VALUES (?,?,?,?,?,?,?,'active',?,?)""",
                profile_values,
            )
        profile = result.get("case_study_profile", {}) or {}
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
        metrics = result.get("metrics", {}) or {}
        conn.execute(
            """INSERT INTO seller_case_studies
            (case_study_id,organisation_id,profile_id,title,objective,workflow_status,candidate_count,
             approved_count,created_by,result_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                case_study_id, organisation_id or "platform", profile_id,
                profile.get("case_study_title") or CASE_STUDY_TITLE,
                profile.get("case_study_objective") or CASE_STUDY_OBJECTIVE,
                result.get("status") or "human_validation_required",
                int(metrics.get("candidate_count") or 0), int(metrics.get("approved_count") or 0),
                created_by or "Analyst / Reviewer", payload, now,
            ),
        )
        for row in result.get("candidate_rows", []) or []:
            target_key = _record_key(row)
            conn.execute(
                """INSERT INTO seller_case_study_targets
                (case_study_id,target_key,audit_key,stable_lead_id,target_company,product,problem_category,
                 source_type,source_id,seller_fit_strength,validation_status,external_use_approved,target_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    case_study_id, target_key, row.get("audit_key") or "", row.get("stable_lead_id") or "",
                    row.get("target_company") or "", row.get("product") or "", row.get("problem_category") or "",
                    row.get("source_type") or "", row.get("source_id") or "", row.get("seller_fit_strength") or "",
                    row.get("validation_status") or "Awaiting human validation",
                    int(_bool(row.get("external_use_approved"))),
                    json.dumps(row, ensure_ascii=False, sort_keys=True, default=str), now,
                ),
            )
    result["case_study_id"] = case_study_id
    result["snapshot_sha256"] = sha256(payload.encode("utf-8")).hexdigest()
    return case_study_id


def history(conn, organisation_id: str = "platform", limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT case_study_id,title,workflow_status,candidate_count,approved_count,created_by,created_at
        FROM seller_case_studies WHERE organisation_id=? ORDER BY created_at DESC LIMIT ?""",
        (organisation_id or "platform", max(1, min(int(limit or 20), 100))),
    ).fetchall()
    return [dict(row) for row in rows]
