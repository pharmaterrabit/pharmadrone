"""Read-optimised data access for 6D-A; frozen write paths remain untouched."""
from __future__ import annotations

import json
from typing import Any

import streamlit as st

from pharmadrone import db
from pharmadrone import production_readiness
from pharmadrone.pipeline import human_audit, pharmaceutical_memory as memory, seller_case_study
from pharmadrone.scheduler import repository as scheduler_repository


def connection():
    return db.connect()


@st.cache_data(ttl=15, show_spinner=False)
def overview() -> dict[str, Any]:
    conn = connection()
    try:
        stats = db.fetch_index_stats(conn)
        status = db.database_status()
        scheduler = scheduler_repository.scheduler_summary(conn)
        queue = human_audit.merge_queue_with_audits(
            human_audit.benchmark_rows(conn), human_audit.latest_audit_map(conn)
        )
        return {"stats": stats, "database": status, "scheduler": scheduler, "audit": human_audit.audit_metrics(queue)}
    finally:
        conn.close()


def _active_where(include_hidden: bool) -> str:
    return "1=1" if include_hidden else "COALESCE(novelty_status,'') NOT IN ('archived','rejected / hidden') AND COALESCE(queue_status,'') NOT IN ('archived','rejected')"


@st.cache_data(ttl=15, show_spinner=False)
def opportunity_page(*, page: int = 1, page_size: int = 25, search: str = "", source: str = "All", region: str = "All", include_hidden: bool = False) -> dict[str, Any]:
    conn = connection()
    try:
        clauses = [_active_where(include_hidden)]
        params: list[Any] = []
        if search.strip():
            q = f"%{search.strip().lower()}%"
            clauses.append("(LOWER(COALESCE(company,'')) LIKE ? OR LOWER(COALESCE(product,'')) LIKE ? OR LOWER(COALESCE(problem_category,'')) LIKE ? OR LOWER(COALESCE(source_id,'')) LIKE ?)")
            params.extend([q, q, q, q])
        if source != "All":
            clauses.append("source_type=?"); params.append(source)
        if region != "All":
            clauses.append("region=?"); params.append(region)
        where = " AND ".join(clauses)
        total = int(conn.execute(f"SELECT COUNT(*) AS n FROM opportunity_index WHERE {where}", tuple(params)).fetchone()["n"])
        offset = max(0, page - 1) * page_size
        rows = conn.execute(
            f"""SELECT stable_lead_id,company,product,molecule,problem_category,source_type,source_id,region,
            score,grade,lead_status,novelty_status,queue_status,has_full_report,first_seen_at,last_checked_at,last_updated_at,data_json
            FROM opportunity_index WHERE {where}
            ORDER BY COALESCE(score,0) DESC, COALESCE(last_updated_at,last_checked_at) DESC LIMIT ? OFFSET ?""",
            tuple(params + [page_size, offset]),
        ).fetchall()
        return {"rows": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}
    finally:
        conn.close()


@st.cache_data(ttl=300, show_spinner=False)
def opportunity_facets() -> dict[str, list[str]]:
    """Load stable filter choices separately from paginated result rows."""
    conn = connection()
    try:
        facets = {}
        for key in ("source_type", "region"):
            facets[key] = [str(r[0]) for r in conn.execute(
                f"SELECT DISTINCT {key} FROM opportunity_index WHERE {_active_where(False)} "
                f"AND COALESCE({key},'')<>'' ORDER BY {key}"
            ).fetchall()]
        return facets
    finally:
        conn.close()


def opportunity(stable_lead_id: str) -> dict[str, Any] | None:
    conn = connection()
    try:
        row = conn.execute("SELECT * FROM opportunity_index WHERE stable_lead_id=?", (stable_lead_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        try: item["details"] = json.loads(item.get("data_json") or "{}")
        except Exception: item["details"] = {}
        enrichment = conn.execute("SELECT * FROM opportunity_enrichment WHERE stable_lead_id=?", (stable_lead_id,)).fetchone()
        item["enrichment"] = dict(enrichment) if enrichment else {}
        return item
    finally:
        conn.close()


def all_opportunities() -> list[dict[str, Any]]:
    """Load the complete index only for an explicit on-demand workflow."""
    conn = connection()
    try: return db.fetch_index_records(conn)
    finally: conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def entity_summary(field: str, limit: int = 100) -> list[dict[str, Any]]:
    if field not in {"company", "product", "molecule", "problem_category"}:
        raise ValueError("Unsupported entity field")
    conn = connection()
    try:
        rows = conn.execute(f"""SELECT {field} AS name, COUNT(*) AS opportunities, MAX(COALESCE(score,0)) AS highest_score,
            MAX(COALESCE(last_updated_at,last_checked_at)) AS latest_signal
            FROM opportunity_index WHERE COALESCE({field},'')<>'' AND {_active_where(False)}
            GROUP BY {field} ORDER BY opportunities DESC, {field} LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@st.cache_data(ttl=15, show_spinner=False)
def audit_queue() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    conn = connection()
    try:
        rows = human_audit.merge_queue_with_audits(human_audit.benchmark_rows(conn), human_audit.latest_audit_map(conn))
        return sorted(rows, key=human_audit.default_queue_sort_key), human_audit.audit_metrics(rows)
    finally:
        conn.close()


def save_audit(record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    conn = connection()
    try:
        result = human_audit.save_audit_version(conn, record, payload)
        audit_queue.clear()
        overview.clear()
        return result
    finally: conn.close()


def audit_histories(key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = connection()
    try: return human_audit.audit_history(conn, key), human_audit.correction_history(conn, key)
    finally: conn.close()


def build_seller_case_study(limit: int, principal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build and persist a real-provider case study from the human audit queue."""
    principal = principal or {}
    conn = connection()
    try:
        records = human_audit.merge_queue_with_audits(
            human_audit.benchmark_rows(conn), human_audit.latest_audit_map(conn)
        )
        result = seller_case_study.build_real_case_study(records, limit=limit)
        seller_case_study.save_snapshot(
            conn,
            result,
            organisation_id=str(principal.get("organisation_id") or "platform"),
            created_by=str(principal.get("display_name") or "Analyst / Reviewer"),
        )
        return result
    finally:
        conn.close()


@st.cache_data(ttl=15, show_spinner=False)
def seller_case_study_history(principal: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    principal = principal or {}
    conn = connection()
    try:
        return seller_case_study.history(
            conn, organisation_id=str(principal.get("organisation_id") or "platform")
        )
    finally:
        conn.close()


@st.cache_data(ttl=30, show_spinner=False)
def pharmaceutical_memory(search: str = "") -> dict[str, Any]:
    """Synchronise and read the governed Phase 7 memory projection."""
    conn = connection()
    try:
        metrics = memory.sync_from_opportunity_index(conn)
        metrics = memory.sync_ema_medicines(conn)
        metrics = memory.sync_fda_orange_book(conn)
        companies = memory.company_memories(conn, search=search)
        return {"metrics": metrics, "companies": companies}
    finally:
        conn.close()


@st.cache_data(ttl=30, show_spinner=False)
def pharmaceutical_memory_relationships(entity_id: str) -> list[dict[str, Any]]:
    conn = connection()
    try:
        return memory.company_relationships(conn, entity_id)
    finally:
        conn.close()


@st.cache_data(ttl=15, show_spinner=False)
def source_health() -> dict[str, Any]:
    conn = connection()
    try:
        return {"summary": scheduler_repository.scheduler_summary(conn), "sources": scheduler_repository.source_status_rows(conn), "database": db.database_status()}
    finally: conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def ema_coverage() -> dict[str, Any]:
    conn = connection()
    try:
        rows = conn.execute(
            "SELECT source_id,record_json,source_updated_at,last_seen_at FROM source_records "
            "WHERE source_type='ema_medicine' AND active=1"
        ).fetchall()
        categories: dict[str, int] = {}
        statuses: dict[str, int] = {}
        latest = ""
        for stored in rows:
            item = dict(stored)
            try: record = json.loads(item.get("record_json") or "{}")
            except (TypeError, ValueError): continue
            entities = record.get("entities") or {}
            category = str(entities.get("medicine_category") or "Not stated")
            status = str(entities.get("medicine_status") or "Not stated")
            categories[category] = categories.get(category, 0) + 1
            statuses[status] = statuses.get(status, 0) + 1
            latest = max(latest, str(item.get("source_updated_at") or ""))
        return {"total": len(rows), "categories": categories, "statuses": statuses, "latest_update": latest}
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def mhra_coverage() -> dict[str, Any]:
    conn = connection()
    try:
        rows = conn.execute(
            "SELECT record_json,source_updated_at FROM source_records "
            "WHERE source_name='MHRA Medicines Recalls' AND active=1"
        ).fetchall()
        classes: dict[str, int] = {}
        latest = ""
        direct = 0
        for stored in rows:
            item = dict(stored)
            try: regulatory = json.loads(item.get("record_json") or "{}")
            except (TypeError, ValueError): continue
            entities = regulatory.get("entities") or {}
            alert_class = str(entities.get("alert_class") or "Medicines recall/notification")
            classes[alert_class] = classes.get(alert_class, 0) + 1
            direct += int(bool(entities.get("direct_problem_evidence")))
            latest = max(latest, str(item.get("source_updated_at") or ""))
        return {"total": len(rows), "direct": direct, "classes": classes, "latest_update": latest}
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def fda_orange_book_coverage() -> dict[str, Any]:
    conn = connection()
    try:
        rows = conn.execute(
            "SELECT record_json,source_updated_at FROM source_records "
            "WHERE source_type='fda_orange_book_product' AND active=1"
        ).fetchall()
        patents = exclusivities = rld = fallback = 0
        latest = ""
        for stored in rows:
            item = dict(stored)
            try: regulatory = json.loads(item.get("record_json") or "{}")
            except (TypeError, ValueError): continue
            entities = regulatory.get("entities") or {}
            patents += len(entities.get("patents") or [])
            exclusivities += len(entities.get("exclusivities") or [])
            rld += int(bool(entities.get("reference_listed_drug")))
            fallback += int(entities.get("dataset_mode") == "Drugs@FDA product fallback")
            latest = max(latest, str(item.get("source_updated_at") or ""))
        return {"total": len(rows), "patents": patents, "exclusivities": exclusivities, "fallback": fallback,
                "reference_listed": rld, "latest_update": latest}
    finally:
        conn.close()


@st.cache_data(ttl=15, show_spinner=False)
def readiness() -> dict[str, Any]:
    """Evaluate Checkpoint 7B against current production telemetry."""
    conn = connection()
    try:
        database = db.database_status()
        scheduler = scheduler_repository.scheduler_summary(conn)
        queue = human_audit.merge_queue_with_audits(
            human_audit.benchmark_rows(conn), human_audit.latest_audit_map(conn)
        )
        audit = human_audit.audit_metrics(queue)
        memory_metrics = memory.sync_from_opportunity_index(conn)
        return production_readiness.evaluate(database, scheduler, audit, memory_metrics)
    finally:
        conn.close()
