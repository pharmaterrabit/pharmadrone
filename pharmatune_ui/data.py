"""Read-optimised data access for 6D-A; frozen write paths remain untouched."""
from __future__ import annotations

import json
from typing import Any

from pharmadrone import db
from pharmadrone.pipeline import human_audit
from pharmadrone.scheduler import repository as scheduler_repository


def connection():
    return db.connect()


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
        facets = {}
        for key in ("source_type", "region"):
            facets[key] = [str(r[0]) for r in conn.execute(f"SELECT DISTINCT {key} FROM opportunity_index WHERE {_active_where(False)} AND COALESCE({key},'')<>'' ORDER BY {key}").fetchall()]
        return {"rows": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size, "facets": facets}
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


def audit_queue() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    conn = connection()
    try:
        rows = human_audit.merge_queue_with_audits(human_audit.benchmark_rows(conn), human_audit.latest_audit_map(conn))
        return sorted(rows, key=human_audit.default_queue_sort_key), human_audit.audit_metrics(rows)
    finally:
        conn.close()


def save_audit(record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    conn = connection()
    try: return human_audit.save_audit_version(conn, record, payload)
    finally: conn.close()


def audit_histories(key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conn = connection()
    try: return human_audit.audit_history(conn, key), human_audit.correction_history(conn, key)
    finally: conn.close()


def source_health() -> dict[str, Any]:
    conn = connection()
    try:
        return {"summary": scheduler_repository.scheduler_summary(conn), "sources": scheduler_repository.source_status_rows(conn), "database": db.database_status()}
    finally: conn.close()
