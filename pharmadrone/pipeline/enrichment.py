"""Capped enrichment queue for Phase 3A.

This module enriches existing opportunity_index records only. It does not rerun
full discovery and does not require an LLM. Web enrichment is optional and all
raw API failures are recorded to source_health_events for developer/debug use.
"""
from __future__ import annotations

import json
from typing import Any

from .. import db, settings
from ..connectors import tavily_search
from . import evidence_quality, query_safety, source_health


def _load_lead(row: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(row.get("data_json") or "{}")
    except Exception:
        data = {}
    return {**row, **data}


def _lead_evidence(lead: dict[str, Any]) -> list[dict[str, Any]]:
    ev = lead.get("evidence") or []
    return ev if isinstance(ev, list) else []


def _safe_status_from_summary(summary: dict[str, Any], web_attempted: bool, web_available: bool) -> str:
    if not web_available and web_attempted:
        return "external enrichment unavailable"
    if summary.get("source_coverage_count", 0) > 0:
        return "checked"
    return "no corroboration found"


def _merge_unique_evidence(existing: list[dict[str, Any]], new_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {str(e.get("url") or e.get("record_id") or e.get("title") or "") for e in existing}
    merged = list(existing)
    for item in new_items:
        key = str(item.get("url") or item.get("record_id") or item.get("title") or "")
        if key and key not in seen:
            merged.append(item)
            seen.add(key)
    return merged


def enrich_one_index_record(conn, row: dict[str, Any], *, run_id: str = "", use_web: bool = True, cost=None, log=None) -> dict[str, Any]:
    say = log or (lambda _m: None)
    lead = _load_lead(row)
    stable_id = row.get("stable_lead_id") or lead.get("stable_lead_id") or ""
    evidence = _lead_evidence(lead)
    web_attempted = False
    web_available = True

    new_web_evidence: list[dict[str, Any]] = []
    if use_web and settings.env("TAVILY_API_KEY"):
        queries = query_safety.lead_web_enrichment_queries(row, max_queries=2)
        for q in queries:
            web_attempted = True
            safe_q = query_safety.sanitize_tavily_query(q)
            res = tavily_search.search(safe_q, max_results=3, cost=cost)
            event = source_health.event_from_connector_result(
                res, run_id=run_id, stable_lead_id=stable_id, sanitized_query=safe_q
            )
            db.save_source_health_event(conn, event)
            if not res.ok:
                web_available = False
                continue
            for rec in res.records:
                rec["enrichment"] = True
                rec["supports"] = rec.get("supports") or "web enrichment candidate; requires validation"
                rec["does_not_prove"] = rec.get("does_not_prove") or "does not prove root cause or company need"
            new_web_evidence.extend(res.records)
    elif use_web:
        web_attempted = True
        web_available = False
        db.save_source_health_event(conn, {
            "run_id": run_id,
            "stable_lead_id": stable_id,
            "source_name": "Web (Tavily)",
            "source_type": "web enrichment",
            "query": "",
            "sanitized_query": "",
            "status": "skipped",
            "failure_reason": "TAVILY_API_KEY missing — enrichment used indexed evidence only",
            "retrieved_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
        })

    combined = _merge_unique_evidence(evidence, new_web_evidence)
    summary = evidence_quality.summarise_evidence(combined)
    enrichment_status = _safe_status_from_summary(summary, web_attempted, web_available)
    if web_attempted and new_web_evidence and summary.get("source_coverage_count", 0) > len(evidence):
        enrichment_status = "partial" if not web_available else "checked"
    if not new_web_evidence and web_attempted and not web_available and not evidence:
        summary["corroboration_status"] = "no corroboration found"
    elif not new_web_evidence and web_attempted and not web_available:
        # Keep direct regulatory evidence visible without hiding the source issue.
        summary["corroboration_status"] = summary.get("corroboration_status") or "direct source only"

    payload = {
        "stable_lead_id": stable_id,
        "enrichment_status": enrichment_status,
        "corroboration_status": summary.get("corroboration_status") or "no corroboration found",
        "evidence_quality": summary.get("evidence_quality") or "Tier 4 / weak",
        "source_coverage_count": summary.get("source_coverage_count", 0),
        "tier1_count": summary.get("tier1_count", 0),
        "tier2_count": summary.get("tier2_count", 0),
        "tier3_count": summary.get("tier3_count", 0),
        "tier4_count": summary.get("tier4_count", 0),
        "regulator_confirmed": int(bool(summary.get("regulator_confirmed"))),
        "company_confirmed": int(bool(summary.get("company_confirmed"))),
        "literature_supported": int(bool(summary.get("literature_supported"))),
        "external_corroboration_found": int(bool(summary.get("external_corroboration_found"))),
        "data_json": json.dumps({
            "web_attempted": web_attempted,
            "web_available": web_available,
            "new_web_evidence_count": len(new_web_evidence),
            "note": "Evidence quality is separate from Opportunity Score. Root cause is not upgraded unless directly supported.",
        }, ensure_ascii=False),
    }
    db.upsert_enrichment(conn, payload)
    say(f"Enriched {row.get('company') or 'lead'} — {payload['corroboration_status']} · {payload['evidence_quality']}")
    return payload


def enrich_indexed_leads(conn, *, limit: int = 5, use_web: bool = True, cost=None, log=None, run_id: str = "") -> dict[str, Any]:
    rows = db.fetch_enrichment_candidates(conn, limit=limit)
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(enrich_one_index_record(conn, row, run_id=run_id, use_web=use_web, cost=cost, log=log))
    return {
        "checked": len(results),
        "results": results,
        "message": f"Enrichment checked {len(results)} indexed lead(s).",
    }
