"""Checkpoint 6B human validation and audit registry.

This module is deliberately separate from the frozen Checkpoint 6A deterministic
engine. It stores human decisions and corrections in append-only audit tables,
never overwrites source/index records, and never calls APIs or an LLM.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable

from . import precision_validation

AUDIT_STATUSES = (
    "pending",
    "in_review",
    "approved",
    "approved_with_caution",
    "rejected",
    "correction_required",
)

AUDIT_ACTIONS = (
    "Save review progress",
    "Approve for internal use",
    "Approve for external case study",
    "Approve for outreach",
    "Approve with caution",
    "Reject",
    "Flag company attribution",
    "Flag product attribution",
    "Correct signal category",
    "Correct company role",
    "Add validation note",
    "Mark issue as historical/resolved",
    "Mark current relevance as unknown",
)

CURRENT_RELEVANCE_OPTIONS = (
    "not checked",
    "current/relevant",
    "historical/resolved",
    "unknown",
)

CORRECTION_TYPES = (
    "",
    "company attribution",
    "product attribution",
    "signal category",
    "company role",
    "source ID",
    "current relevance",
    "other",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_benchmark_batches (
    batch_id TEXT PRIMARY KEY,
    filename TEXT,
    sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    is_golden INTEGER DEFAULT 1,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS audit_queue_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    audit_key TEXT NOT NULL,
    stable_lead_id TEXT,
    source_type TEXT,
    source_id TEXT,
    original_row_json TEXT NOT NULL,
    original_row_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, audit_key)
);
CREATE INDEX IF NOT EXISTS idx_audit_queue_key ON audit_queue_records(audit_key);
CREATE INDEX IF NOT EXISTS idx_audit_queue_batch ON audit_queue_records(batch_id, id);

CREATE TABLE IF NOT EXISTS human_audit_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_key TEXT NOT NULL,
    stable_lead_id TEXT,
    source_type TEXT,
    source_id TEXT,
    audit_version INTEGER NOT NULL,
    parent_audit_id INTEGER,
    audit_status TEXT NOT NULL,
    audit_decision TEXT,
    reviewer_name TEXT,
    reviewed_at TEXT,
    audit_notes TEXT,
    evidence_checked INTEGER DEFAULT 0,
    company_identity_checked INTEGER DEFAULT 0,
    product_identity_checked INTEGER DEFAULT 0,
    problem_signal_checked INTEGER DEFAULT 0,
    evidence_supports_problem INTEGER DEFAULT 0,
    technical_fit_checked INTEGER DEFAULT 0,
    current_relevance_checked INTEGER DEFAULT 0,
    target_company_site_checked INTEGER DEFAULT 0,
    outreach_wording_reviewed INTEGER DEFAULT 0,
    unresolved_warnings_acknowledged INTEGER DEFAULT 0,
    company_warning_resolved INTEGER DEFAULT 0,
    internal_use_approved INTEGER DEFAULT 0,
    external_use_approved INTEGER DEFAULT 0,
    outreach_approved INTEGER DEFAULT 0,
    current_relevance_status TEXT DEFAULT 'not checked',
    historical_resolved INTEGER DEFAULT 0,
    correction_type TEXT,
    corrected_value TEXT,
    correction_reason TEXT,
    supporting_source_url TEXT,
    external_gate_passed INTEGER DEFAULT 0,
    outreach_gate_passed INTEGER DEFAULT 0,
    source_snapshot_hash TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(audit_key, audit_version)
);
CREATE INDEX IF NOT EXISTS idx_human_audit_key ON human_audit_versions(audit_key, audit_version DESC);
CREATE INDEX IF NOT EXISTS idx_human_audit_status ON human_audit_versions(audit_status, created_at DESC);

CREATE TABLE IF NOT EXISTS human_audit_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_version_id INTEGER NOT NULL,
    audit_key TEXT NOT NULL,
    stable_lead_id TEXT,
    source_type TEXT,
    source_id TEXT,
    field_name TEXT,
    original_value TEXT,
    corrected_value TEXT,
    reviewer_name TEXT,
    corrected_at TEXT NOT NULL,
    reason TEXT,
    supporting_source_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_corrections_key ON human_audit_corrections(audit_key, corrected_at DESC);
"""

_BOOL_FIELDS = (
    "evidence_checked",
    "company_identity_checked",
    "product_identity_checked",
    "problem_signal_checked",
    "evidence_supports_problem",
    "technical_fit_checked",
    "current_relevance_checked",
    "target_company_site_checked",
    "outreach_wording_reviewed",
    "unresolved_warnings_acknowledged",
    "company_warning_resolved",
    "internal_use_approved",
    "external_use_approved",
    "outreach_approved",
    "historical_resolved",
)

INTERNAL_EXPORT_FIELDS = [
    "audit_key", "stable_lead_id", "source_type", "source_id", "target_company", "product",
    "signal_tier", "specific_problem_subcategory", "external_case_study_eligible",
    "company_match_warning", "target_is_distributor_or_repackager_only", "official_source_url",
    "audit_version", "audit_status", "audit_decision", "reviewer_name", "reviewed_at",
    "audit_notes", "evidence_checked", "company_identity_checked", "product_identity_checked",
    "problem_signal_checked", "evidence_supports_problem", "technical_fit_checked", "current_relevance_checked",
    "target_company_site_checked", "outreach_wording_reviewed",
    "unresolved_warnings_acknowledged", "company_warning_resolved", "internal_use_approved",
    "external_use_approved", "outreach_approved", "current_relevance_status",
    "historical_resolved", "correction_type", "corrected_value", "correction_reason",
    "supporting_source_url", "external_gate_passed", "outreach_gate_passed",
]

EXTERNAL_EXPORT_FIELDS = [
    "source_type", "source_id", "target_company", "product", "molecule", "region",
    "signal_tier", "signal_type", "broad_problem_category", "specific_problem_subcategory",
    "seller_fit_strength", "why_fit", "what_evidence_proves", "what_evidence_does_not_prove",
    "safe_bd_angle", "official_source_url", "audit_status", "audit_decision", "reviewer_name",
    "reviewed_at", "external_use_approved",
]

HISTORY_EXPORT_FIELDS = [
    "audit_key", "stable_lead_id", "source_type", "source_id", "audit_version", "audit_status",
    "audit_decision", "reviewer_name", "reviewed_at", "audit_notes", "evidence_checked",
    "company_identity_checked", "product_identity_checked", "problem_signal_checked",
    "technical_fit_checked", "current_relevance_checked", "target_company_site_checked",
    "outreach_wording_reviewed", "unresolved_warnings_acknowledged", "company_warning_resolved",
    "internal_use_approved", "external_use_approved", "outreach_approved",
    "current_relevance_status", "historical_resolved", "correction_type", "corrected_value",
    "correction_reason", "supporting_source_url", "external_gate_passed", "outreach_gate_passed",
    "created_at",
]

_HISTORICAL_CORRECTIONS = (
    {
        "source_type": "FDA recall",
        "source_id": "D-0202-2025",
        "audit_status": "approved_with_caution",
        "audit_decision": "Historical Checkpoint 6A correction retained: unresolved company attribution conflict.",
        "audit_notes": "Amerisource Health Services versus American Health Packaging attribution requires validation.",
        "correction_type": "company attribution",
        "original_value": "company attribution unresolved/not surfaced",
        "corrected_value": "material company-attribution warning retained; external use excluded",
        "correction_reason": "Manual audit correction from the frozen Checkpoint 6A baseline.",
    },
    {
        "source_type": "FDA recall",
        "source_id": "D-0386-2024",
        "audit_status": "approved_with_caution",
        "audit_decision": "Historical Checkpoint 6A correction retained: company/manufacturer and regulatory-status concerns.",
        "audit_notes": "Broncochem attribution and unapproved-product/regulatory-status concern; external use excluded.",
        "correction_type": "company role",
        "original_value": "company/regulatory warning missing or misclassified",
        "corrected_value": "company/manufacturer and regulatory-status warning retained",
        "correction_reason": "Manual audit correction from the frozen Checkpoint 6A baseline.",
    },
    {
        "source_type": "ClinicalTrials.gov trial",
        "source_id": "NCT00990444",
        "audit_status": "approved_with_caution",
        "audit_decision": "Historical Checkpoint 6A correction retained: attributable oral-insulin delivery evidence restored.",
        "audit_notes": "Dextran-matrix oral-delivery evidence restored for a sparse legacy indexed row; external approval remains a separate 6B decision.",
        "correction_type": "signal category",
        "original_value": "Tier B / missing attributable delivery trace",
        "corrected_value": "Tier A explicit_delivery_optimization with attributable registry evidence",
        "correction_reason": "Manual source-ID correction from the frozen Checkpoint 6A baseline.",
    },
)


class AuditValidationError(ValueError):
    """Raised when a requested approval does not satisfy human-audit gates."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _norm(value) in {"1", "true", "yes", "y", "checked"}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _source_type(value: Any) -> str:
    text = _norm(value)
    if "clinical" in text and "trial" in text:
        return "ClinicalTrials.gov trial"
    if "fda" in text and ("recall" in text or "enforcement" in text):
        return "FDA recall"
    if "shortage" in text:
        return "FDA drug shortage"
    return str(value or "Unknown source").strip() or "Unknown source"


def audit_key(record: dict[str, Any]) -> str:
    stype = _source_type(record.get("source_type"))
    sid = str(record.get("source_id") or record.get("record_id") or "").strip()
    stable = str(record.get("stable_lead_id") or "").strip()
    identity = sid or stable or _hash_payload({
        "company": record.get("target_company") or record.get("company"),
        "product": record.get("product"),
        "source_type": stype,
    })[:20]
    return f"{stype.lower()}|{identity.upper()}"


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
    seed_historical_corrections(conn)


def seed_historical_corrections(conn) -> None:
    """Seed the three frozen Checkpoint 6A manual corrections once."""
    for item in _HISTORICAL_CORRECTIONS:
        rec = {
            "source_type": item["source_type"],
            "source_id": item["source_id"],
            "stable_lead_id": "",
        }
        key = audit_key(rec)
        exists = conn.execute(
            "SELECT 1 FROM human_audit_versions WHERE audit_key=? LIMIT 1", (key,)
        ).fetchone()
        if exists:
            continue
        now = _now()
        cursor = conn.execute(
            """INSERT INTO human_audit_versions
            (audit_key, stable_lead_id, source_type, source_id, audit_version, parent_audit_id,
             audit_status, audit_decision, reviewer_name, reviewed_at, audit_notes,
             evidence_checked, company_identity_checked, product_identity_checked,
             problem_signal_checked, evidence_supports_problem, technical_fit_checked, current_relevance_checked,
             target_company_site_checked, outreach_wording_reviewed,
             unresolved_warnings_acknowledged, company_warning_resolved,
             internal_use_approved, external_use_approved, outreach_approved,
             current_relevance_status, historical_resolved, correction_type, corrected_value,
             correction_reason, supporting_source_url, external_gate_passed,
             outreach_gate_passed, source_snapshot_hash, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                key, "", item["source_type"], item["source_id"], 1, None, item["audit_status"],
                item["audit_decision"], "Checkpoint 6A manual audit", now, item["audit_notes"],
                1, 1, 1, 1, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, "unknown", 0,
                item["correction_type"], item["corrected_value"], item["correction_reason"],
                "", 0, 0, "historical-checkpoint-6a", now,
            ),
        )
        audit_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO human_audit_corrections
            (audit_version_id, audit_key, stable_lead_id, source_type, source_id, field_name,
             original_value, corrected_value, reviewer_name, corrected_at, reason, supporting_source_url)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                audit_id, key, "", item["source_type"], item["source_id"], item["correction_type"],
                item["original_value"], item["corrected_value"], "Checkpoint 6A manual audit",
                now, item["correction_reason"], "",
            ),
        )
    conn.commit()


def import_benchmark_csv(conn, payload: bytes, filename: str = "pharmatune_100_target_validation_study.csv") -> dict[str, Any]:
    """Import an immutable golden validation CSV snapshot into the audit queue."""
    ensure_schema(conn)
    digest = hashlib.sha256(payload).hexdigest()
    batch_id = f"golden-{digest[:20]}"
    existing = conn.execute(
        "SELECT row_count FROM audit_benchmark_batches WHERE batch_id=?", (batch_id,)
    ).fetchone()
    if existing:
        return {"batch_id": batch_id, "row_count": int(existing[0]), "already_imported": True, "sha256": digest}

    text = payload.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    now = _now()
    conn.execute(
        """INSERT INTO audit_benchmark_batches
        (batch_id, filename, sha256, imported_at, row_count, is_golden, notes)
        VALUES (?,?,?,?,?,1,?)""",
        (batch_id, filename, digest, now, len(rows), "Frozen Checkpoint 6A.5.2 validation benchmark; source rows remain immutable."),
    )
    inserted = 0
    for raw in rows:
        record = {k: v for k, v in raw.items()}
        key = audit_key(record)
        original_hash = _hash_payload(record)
        conn.execute(
            """INSERT OR IGNORE INTO audit_queue_records
            (batch_id, audit_key, stable_lead_id, source_type, source_id,
             original_row_json, original_row_hash, created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (
                batch_id, key, record.get("stable_lead_id") or "", _source_type(record.get("source_type")),
                record.get("source_id") or "", _json_dumps(record), original_hash, now,
            ),
        )
        inserted += 1
    conn.commit()
    return {"batch_id": batch_id, "row_count": inserted, "already_imported": False, "sha256": digest}


def latest_benchmark_batch(conn) -> dict[str, Any] | None:
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM audit_benchmark_batches ORDER BY imported_at DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def benchmark_rows(conn, batch_id: str | None = None) -> list[dict[str, Any]]:
    ensure_schema(conn)
    batch = batch_id or (latest_benchmark_batch(conn) or {}).get("batch_id")
    if not batch:
        return []
    rows = conn.execute(
        "SELECT * FROM audit_queue_records WHERE batch_id=? ORDER BY id", (batch,)
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            original = json.loads(item.pop("original_row_json"))
        except Exception:
            original = {}
        original["audit_key"] = item.get("audit_key")
        original["benchmark_batch_id"] = item.get("batch_id")
        original["original_row_hash"] = item.get("original_row_hash")
        out.append(original)
    return out


def prepare_index_records(records: Iterable[dict[str, Any]], seller_profile: str) -> list[dict[str, Any]]:
    """Return copied, deterministically annotated records for the audit queue."""
    out: list[dict[str, Any]] = []
    for source in records:
        record = precision_validation.annotate_record(
            deepcopy(source),
            seller_profile=seller_profile,
            official_source_url=precision_validation.extract_stored_official_url(source),
        )
        record["audit_key"] = audit_key(record)
        record["original_row_hash"] = _hash_payload(source)
        out.append(record)
    return out


def latest_audit_map(conn) -> dict[str, dict[str, Any]]:
    ensure_schema(conn)
    rows = conn.execute(
        """SELECT hav.* FROM human_audit_versions hav
        JOIN (
            SELECT audit_key, MAX(audit_version) AS max_version
            FROM human_audit_versions GROUP BY audit_key
        ) latest ON latest.audit_key=hav.audit_key AND latest.max_version=hav.audit_version"""
    ).fetchall()
    return {str(r["audit_key"]): dict(r) for r in rows}


def audit_history(conn, audit_key_value: str | None = None) -> list[dict[str, Any]]:
    ensure_schema(conn)
    if audit_key_value:
        rows = conn.execute(
            "SELECT * FROM human_audit_versions WHERE audit_key=? ORDER BY audit_version DESC",
            (audit_key_value,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM human_audit_versions ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def correction_history(conn, audit_key_value: str | None = None) -> list[dict[str, Any]]:
    ensure_schema(conn)
    if audit_key_value:
        rows = conn.execute(
            "SELECT * FROM human_audit_corrections WHERE audit_key=? ORDER BY corrected_at DESC, id DESC",
            (audit_key_value,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM human_audit_corrections ORDER BY corrected_at DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def merge_queue_with_audits(records: list[dict[str, Any]], latest: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    for raw in records:
        row = deepcopy(raw)
        key = row.get("audit_key") or audit_key(row)
        row["audit_key"] = key
        audit = latest.get(key, {})
        row["audit_status"] = audit.get("audit_status") or "pending"
        row["audit_decision"] = audit.get("audit_decision") or ""
        row["reviewer_name"] = audit.get("reviewer_name") or ""
        row["reviewed_at"] = audit.get("reviewed_at") or ""
        row["audit_version"] = audit.get("audit_version") or 0
        for field in (
            "audit_notes", "correction_type", "corrected_value", "correction_reason",
            "supporting_source_url", "created_at"
        ):
            row[field] = audit.get(field) or ""
        for field in _BOOL_FIELDS + ("external_gate_passed", "outreach_gate_passed"):
            row[field] = bool(audit.get(field) or 0)
        row["current_relevance_status"] = audit.get("current_relevance_status") or "not checked"
        row["company_warning_resolved"] = bool(audit.get("company_warning_resolved") or 0)
        merged.append(row)
    return merged


def default_queue_sort_key(record: dict[str, Any]) -> tuple:
    status = _norm(record.get("audit_status") or "pending")
    completed = status in {"approved", "approved_with_caution", "rejected"}
    external = _bool(record.get("external_case_study_eligible"))
    warning = _bool(record.get("company_match_warning")) and not _bool(record.get("company_warning_resolved"))
    tier = str(record.get("signal_tier") or "C").upper()
    tier_rank = {"A": 0, "C": 1, "B": 2, "D": 3}.get(tier, 2)
    priority = 0 if (external and not completed) else 1 if (warning and not completed) else 2
    return (
        completed,
        priority,
        tier_rank,
        -int(float(record.get("opportunity_score") or record.get("score") or 0)),
        _norm(record.get("target_company") or record.get("company")),
        _norm(record.get("source_id")),
    )


def filter_queue(records: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    def selected(value: Any, options: Any) -> bool:
        if not options:
            return True
        opts = options if isinstance(options, (list, tuple, set)) else [options]
        opts_n = {_norm(x) for x in opts if _norm(x) not in {"", "all", "any"}}
        return not opts_n or _norm(value) in opts_n

    text_filters = {
        "source_id": _norm(filters.get("source_id")),
        "company": _norm(filters.get("company")),
        "product": _norm(filters.get("product")),
    }
    out = []
    for row in records:
        if not selected(row.get("audit_status") or "pending", filters.get("audit_status")):
            continue
        ext_filter = filters.get("external_eligibility")
        if ext_filter == "Eligible only" and not _bool(row.get("external_case_study_eligible")):
            continue
        if ext_filter == "Not eligible only" and _bool(row.get("external_case_study_eligible")):
            continue
        if not selected(row.get("signal_tier"), filters.get("signal_tier")):
            continue
        if not selected(row.get("source_type"), filters.get("source_type")):
            continue
        warning_filter = filters.get("company_warning")
        if warning_filter == "Warnings only" and not _bool(row.get("company_match_warning")):
            continue
        if warning_filter == "No warnings only" and _bool(row.get("company_match_warning")):
            continue
        if not selected(row.get("seller_fit_strength"), filters.get("seller_fit")):
            continue
        if not selected(row.get("region"), filters.get("region")):
            continue
        report_filter = filters.get("report_availability")
        if report_filter == "Full reports only" and not _bool(row.get("has_full_report")):
            continue
        if report_filter == "Previews only" and _bool(row.get("has_full_report")):
            continue
        if text_filters["source_id"] and text_filters["source_id"] not in _norm(row.get("source_id")):
            continue
        if text_filters["company"] and text_filters["company"] not in _norm(row.get("target_company") or row.get("company")):
            continue
        if text_filters["product"] and text_filters["product"] not in _norm(row.get("product")):
            continue
        out.append(row)
    return sorted(out, key=default_queue_sort_key)


def _deterministic_warning(record: dict[str, Any]) -> bool:
    return any(
        _bool(record.get(field))
        for field in (
            "company_match_warning",
            "company_identity_mismatch",
            "target_is_distributor_or_repackager_only",
        )
    )


def evaluate_gates(record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate external/outreach approval gates without mutating data."""
    external_requested = _bool(payload.get("external_use_approved"))
    outreach_requested = _bool(payload.get("outreach_approved"))
    external_errors: list[str] = []
    outreach_errors: list[str] = []

    if external_requested:
        required = {
            "evidence_checked": "official source was checked",
            "product_identity_checked": "product identity was checked",
            "company_identity_checked": "company identity/role was checked",
            "problem_signal_checked": "signal classification was checked",
            "evidence_supports_problem": "source evidence supports the stated problem/signal",
            "unresolved_warnings_acknowledged": "material warnings were acknowledged",
        }
        for field, message in required.items():
            if not _bool(payload.get(field)):
                external_errors.append(message)
        if str(record.get("signal_tier") or "").upper() == "D":
            external_errors.append("Signal Tier D cannot be approved for external use")
        if _deterministic_warning(record) and not _bool(payload.get("company_warning_resolved")):
            external_errors.append("unresolved company/distributor warning requires explicit human resolution")

    external_passed = external_requested and not external_errors

    if outreach_requested:
        if not external_requested or not external_passed:
            outreach_errors.append("outreach approval requires valid external-use approval")
        outreach_required = {
            "current_relevance_checked": "current commercial relevance was checked",
            "target_company_site_checked": "correct target company/site was checked",
            "technical_fit_checked": "technical solution fit was checked",
            "outreach_wording_reviewed": "outreach wording was reviewed",
        }
        for field, message in outreach_required.items():
            if not _bool(payload.get(field)):
                outreach_errors.append(message)
        if _norm(payload.get("current_relevance_status")) in {"", "not checked", "unknown"}:
            outreach_errors.append("current relevance must be resolved, not unknown")

    outreach_passed = outreach_requested and not outreach_errors
    return {
        "external_gate_passed": external_passed,
        "outreach_gate_passed": outreach_passed,
        "external_errors": external_errors,
        "outreach_errors": outreach_errors,
    }


def apply_action_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(payload)
    action = out.get("action") or "Save review progress"
    if action == "Approve for internal use":
        out.update(audit_status="approved", internal_use_approved=True)
    elif action == "Approve for external case study":
        out.update(audit_status="approved", internal_use_approved=True, external_use_approved=True)
    elif action == "Approve for outreach":
        out.update(audit_status="approved", internal_use_approved=True, external_use_approved=True, outreach_approved=True)
    elif action == "Approve with caution":
        out.update(audit_status="approved_with_caution", internal_use_approved=True)
    elif action == "Reject":
        out.update(audit_status="rejected", internal_use_approved=False, external_use_approved=False, outreach_approved=False)
    elif action == "Flag company attribution":
        out.update(audit_status="correction_required", correction_type="company attribution")
    elif action == "Flag product attribution":
        out.update(audit_status="correction_required", correction_type="product attribution")
    elif action == "Correct signal category":
        out.update(audit_status="correction_required", correction_type="signal category")
    elif action == "Correct company role":
        out.update(audit_status="correction_required", correction_type="company role")
    elif action == "Mark issue as historical/resolved":
        out.update(audit_status="approved_with_caution", historical_resolved=True, current_relevance_checked=True, current_relevance_status="historical/resolved")
    elif action == "Mark current relevance as unknown":
        out.update(audit_status="in_review", current_relevance_checked=True, current_relevance_status="unknown")
    elif action == "Add validation note":
        out.setdefault("audit_status", "in_review")
    else:
        out.setdefault("audit_status", "in_review")
    return out


def save_audit_version(conn, record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Append one audit version and optional correction; never update prior rows."""
    ensure_schema(conn)
    data = apply_action_defaults(payload)
    status = data.get("audit_status") or "pending"
    if status not in AUDIT_STATUSES:
        raise AuditValidationError(f"Unsupported audit status: {status}")
    reviewer = str(data.get("reviewer_name") or "").strip()
    if not reviewer:
        raise AuditValidationError("Reviewer name is required.")

    gates = evaluate_gates(record, data)
    if _bool(data.get("external_use_approved")) and gates["external_errors"]:
        raise AuditValidationError("External approval blocked: " + "; ".join(gates["external_errors"]))
    if _bool(data.get("outreach_approved")) and gates["outreach_errors"]:
        raise AuditValidationError("Outreach approval blocked: " + "; ".join(gates["outreach_errors"]))

    key = record.get("audit_key") or audit_key(record)
    previous = conn.execute(
        "SELECT * FROM human_audit_versions WHERE audit_key=? ORDER BY audit_version DESC LIMIT 1",
        (key,),
    ).fetchone()
    version = int(previous["audit_version"] if previous else 0) + 1
    parent_id = int(previous["id"]) if previous else None
    reviewed_at = str(data.get("reviewed_at") or _now())
    snapshot_hash = str(record.get("original_row_hash") or _hash_payload(record))

    values = {field: int(_bool(data.get(field))) for field in _BOOL_FIELDS}
    cursor = conn.execute(
        """INSERT INTO human_audit_versions
        (audit_key, stable_lead_id, source_type, source_id, audit_version, parent_audit_id,
         audit_status, audit_decision, reviewer_name, reviewed_at, audit_notes,
         evidence_checked, company_identity_checked, product_identity_checked,
         problem_signal_checked, evidence_supports_problem, technical_fit_checked, current_relevance_checked,
         target_company_site_checked, outreach_wording_reviewed,
         unresolved_warnings_acknowledged, company_warning_resolved,
         internal_use_approved, external_use_approved, outreach_approved,
         current_relevance_status, historical_resolved, correction_type, corrected_value,
         correction_reason, supporting_source_url, external_gate_passed,
         outreach_gate_passed, source_snapshot_hash, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            key, record.get("stable_lead_id") or "", _source_type(record.get("source_type")),
            record.get("source_id") or "", version, parent_id, status,
            data.get("audit_decision") or data.get("action") or "", reviewer, reviewed_at,
            data.get("audit_notes") or "", values["evidence_checked"],
            values["company_identity_checked"], values["product_identity_checked"],
            values["problem_signal_checked"], values["evidence_supports_problem"], values["technical_fit_checked"],
            values["current_relevance_checked"], values["target_company_site_checked"],
            values["outreach_wording_reviewed"], values["unresolved_warnings_acknowledged"],
            values["company_warning_resolved"], values["internal_use_approved"],
            values["external_use_approved"], values["outreach_approved"],
            data.get("current_relevance_status") or "not checked", values["historical_resolved"],
            data.get("correction_type") or "", data.get("corrected_value") or "",
            data.get("correction_reason") or "", data.get("supporting_source_url") or "",
            int(gates["external_gate_passed"]), int(gates["outreach_gate_passed"]),
            snapshot_hash, _now(),
        ),
    )
    audit_id = cursor.lastrowid

    correction_type = str(data.get("correction_type") or "").strip()
    corrected_value = str(data.get("corrected_value") or "").strip()
    if correction_type or corrected_value:
        original_value = str(data.get("original_value") or record.get(correction_type.replace(" ", "_")) or "").strip()
        conn.execute(
            """INSERT INTO human_audit_corrections
            (audit_version_id, audit_key, stable_lead_id, source_type, source_id, field_name,
             original_value, corrected_value, reviewer_name, corrected_at, reason, supporting_source_url)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                audit_id, key, record.get("stable_lead_id") or "", _source_type(record.get("source_type")),
                record.get("source_id") or "", correction_type, original_value, corrected_value,
                reviewer, reviewed_at, data.get("correction_reason") or "",
                data.get("supporting_source_url") or "",
            ),
        )
    conn.commit()
    saved = conn.execute("SELECT * FROM human_audit_versions WHERE id=?", (audit_id,)).fetchone()
    return dict(saved)


def audit_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    counts = {status: 0 for status in AUDIT_STATUSES}
    internal = external = outreach = unresolved = relevance_unknown = completed = 0
    for row in records:
        status = row.get("audit_status") or "pending"
        counts[status] = counts.get(status, 0) + 1
        if status in {"approved", "approved_with_caution", "rejected"}:
            completed += 1
        internal += int(_bool(row.get("internal_use_approved")))
        external += int(_bool(row.get("external_use_approved")))
        outreach += int(_bool(row.get("outreach_approved")))
        if _deterministic_warning(row) and not _bool(row.get("company_warning_resolved")):
            unresolved += 1
        if _norm(row.get("current_relevance_status")) == "unknown":
            relevance_unknown += 1
    return {
        "pending_audits": counts.get("pending", 0),
        "audits_completed": completed,
        "approved_for_internal_use": internal,
        "approved_for_external_use": external,
        "approved_for_outreach": outreach,
        "approved_with_caution": counts.get("approved_with_caution", 0),
        "rejected": counts.get("rejected", 0),
        "correction_required": counts.get("correction_required", 0),
        "unresolved_company_warnings": unresolved,
        "current_relevance_unknown": relevance_unknown,
        "audit_completion_percentage": round((completed / total * 100), 1) if total else 0.0,
        "total_queue_records": total,
    }


def _csv_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({f: row.get(f, "") for f in fields})
    return buf.getvalue().encode("utf-8-sig")


def export_full_internal(records: list[dict[str, Any]]) -> bytes:
    return _csv_bytes(records, INTERNAL_EXPORT_FIELDS)


def export_external_approved(records: list[dict[str, Any]]) -> bytes:
    rows = [
        r for r in records
        if _bool(r.get("external_use_approved"))
        and _bool(r.get("external_gate_passed"))
        and str(r.get("signal_tier") or "").upper() != "D"
        and (r.get("audit_status") not in {"rejected", "correction_required"})
    ]
    return _csv_bytes(rows, EXTERNAL_EXPORT_FIELDS)


def export_outreach_approved(records: list[dict[str, Any]]) -> bytes:
    rows = [
        r for r in records
        if _bool(r.get("outreach_approved")) and _bool(r.get("outreach_gate_passed"))
    ]
    return _csv_bytes(rows, EXTERNAL_EXPORT_FIELDS + ["outreach_approved"])


def export_rejected_or_correction(records: list[dict[str, Any]]) -> bytes:
    rows = [r for r in records if r.get("audit_status") in {"rejected", "correction_required"}]
    return _csv_bytes(rows, INTERNAL_EXPORT_FIELDS)


def export_history(conn) -> bytes:
    rows = audit_history(conn)
    return _csv_bytes(rows, HISTORY_EXPORT_FIELDS)


def export_correction_history(conn) -> bytes:
    fields = [
        "audit_version_id", "audit_key", "stable_lead_id", "source_type", "source_id",
        "field_name", "original_value", "corrected_value", "reviewer_name", "corrected_at",
        "reason", "supporting_source_url",
    ]
    return _csv_bytes(correction_history(conn), fields)
