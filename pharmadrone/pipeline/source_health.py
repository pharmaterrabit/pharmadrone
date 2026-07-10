"""Source/API health tracking for Phase 3A.

Normal user reports should show evidence gaps, not raw API errors. This module
keeps API/source diagnostics structured for developer/debug views and exports.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def classify_status(*, ok: bool | None = None, count: int | None = None, error: str | None = None, skipped: bool = False) -> str:
    if skipped:
        return "skipped"
    err = (error or "").lower()
    if err:
        if "429" in err or "rate limit" in err:
            return "rate-limited"
        if "rejected" in err or "http 432" in err or "http 422" in err or "http 400" in err:
            return "rejected"
        return "failed"
    if ok is False:
        return "failed"
    if count == 0:
        return "no results"
    return "available"


def source_type_for(source_name: str) -> str:
    text = (source_name or "").lower()
    if "fda" in text or "regulator" in text or "enforcement" in text:
        return "regulatory"
    if "trial" in text:
        return "clinical trial registry"
    if "pmc" in text or "crossref" in text or "openalex" in text:
        return "literature"
    if "tavily" in text or "web" in text:
        return "web enrichment"
    return "source"


def event_from_connector_result(res: Any, *, run_id: str | None = None, stable_lead_id: str | None = None,
                                accepted_count: int | None = None, rejected_count: int | None = None,
                                sanitized_query: str | None = None) -> dict[str, Any]:
    count = int(getattr(res, "count", 0) or 0)
    error = getattr(res, "error", None)
    ok = bool(getattr(res, "ok", False))
    return {
        "run_id": run_id or "",
        "stable_lead_id": stable_lead_id or "",
        "source_name": getattr(res, "source", "unknown source"),
        "source_type": source_type_for(getattr(res, "source", "")),
        "query": getattr(res, "query", "") or "",
        "sanitized_query": sanitized_query or "",
        "status": classify_status(ok=ok, count=count, error=error),
        "failure_reason": error or "",
        "retrieved_count": count,
        "accepted_count": count if accepted_count is None and ok else int(accepted_count or 0),
        "rejected_count": int(rejected_count or 0),
        "created_at": utc_now_iso(),
    }


def events_from_coverage(coverage: dict[str, dict[str, Any]], *, run_id: str | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for source, cov in (coverage or {}).items():
        errors = cov.get("errors") or []
        warnings = cov.get("warnings") or []
        queries = int(cov.get("queries") or 0)
        evidence = int(cov.get("evidence") or cov.get("evidence_items") or 0)
        failed = int(cov.get("failed") or 0)
        ok = int(cov.get("ok") or cov.get("succeeded") or 0)
        status = cov.get("status") or classify_status(ok=(failed == 0), count=evidence, error=(errors[0] if errors else None), skipped=(queries == 0))
        failure_reason = errors[0] if errors else (warnings[0] if warnings else "")
        events.append({
            "run_id": run_id or "",
            "stable_lead_id": "",
            "source_name": source,
            "source_type": source_type_for(source),
            "query": "",
            "sanitized_query": "",
            "status": status,
            "failure_reason": failure_reason,
            "retrieved_count": evidence,
            "accepted_count": evidence,
            "rejected_count": int(cov.get("rejected", 0) or 0),
            "created_at": utc_now_iso(),
            "query_count": queries,
            "queries": queries,
            "succeeded": ok,
            "failed": failed,
        })
    return events


def summarize_events(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_source: dict[str, dict[str, Any]] = {}
    for row in rows:
        src = row.get("source_name") or "unknown source"
        s = by_source.setdefault(src, {
            "source_name": src,
            "source_type": row.get("source_type") or source_type_for(src),
            "queries": 0,
            "retrieved_results": 0,
            "accepted_evidence": 0,
            "rejected_evidence": 0,
            "last_successful_call": "",
            "last_failed_call": "",
            "latest_status": "not checked",
            "failure_reason": "",
        })
        s["queries"] += int(row.get("query_count") or row.get("queries") or 1)
        s["retrieved_results"] += int(row.get("retrieved_count") or 0)
        s["accepted_evidence"] += int(row.get("accepted_count") or 0)
        s["rejected_evidence"] += int(row.get("rejected_count") or 0)
        created = row.get("created_at") or ""
        status = row.get("status") or "not checked"
        s["latest_status"] = status
        if status == "available":
            s["last_successful_call"] = max(s.get("last_successful_call") or "", created)
        if status in {"failed", "rate-limited", "rejected"}:
            s["last_failed_call"] = max(s.get("last_failed_call") or "", created)
            if row.get("failure_reason"):
                s["failure_reason"] = row.get("failure_reason")
    return sorted(by_source.values(), key=lambda x: x["source_name"])
