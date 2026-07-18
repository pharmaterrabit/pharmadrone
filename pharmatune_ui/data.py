"""Read-optimised data access for 6D-A; frozen write paths remain untouched."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import streamlit as st

from pharmadrone import db
from pharmadrone import production_readiness
from pharmadrone.pipeline import account_intelligence, commercial_intelligence, customer_product, human_audit, opportunity_index, patent_lifecycle, pharmaceutical_memory as memory, regulatory_intelligence, research_innovation, seller_case_study
from pharmadrone.scheduler import repository as scheduler_repository


def connection():
    return db.connect()


@st.cache_data(ttl=15, show_spinner=False)
def customer_workspace_snapshot(principal: dict[str, Any]) -> dict[str, Any]:
    return customer_product.workspace_snapshot(principal)


@st.cache_data(ttl=15, show_spinner=False)
def customer_saved_items(principal: dict[str, Any], saved_list_id: str) -> list[dict[str, Any]]:
    return customer_product.list_items(principal, saved_list_id)


@st.cache_data(ttl=15, show_spinner=False)
def customer_alert_inbox(principal: dict[str, Any]) -> list[dict[str, Any]]:
    return customer_product.alert_inbox(principal)


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


def _qualification_tier_sql() -> str:
    has_company = "TRIM(COALESCE(company,''))<>''"
    has_product = "TRIM(COALESCE(product,''))<>''"
    has_link = "LOWER(COALESCE(evidence_links_json,'')) LIKE '%http%'"
    return (
        f"CASE WHEN {has_company} AND {has_product} AND {has_link} THEN 'P1 · Ready to qualify' "
        f"WHEN {has_link} AND ({has_company} OR {has_product}) THEN 'P2 · Account research' "
        "ELSE 'P3 · Evidence repair' END"
    )


def _contact_role_sql() -> str:
    signal = "LOWER(COALESCE(source_type,'') || ' ' || COALESCE(problem_category,''))"
    return (
        f"CASE WHEN {signal} LIKE '%clinicaltrials%' OR {signal} LIKE '%clinical trial%' "
        "OR " + signal + " LIKE '%terminated trial%' THEN 'Clinical Development / Business Development' "
        f"WHEN {signal} LIKE '%shortage%' OR {signal} LIKE '%supply%' OR {signal} LIKE '%availability%' "
        "THEN 'Supply Chain / Procurement' "
        f"WHEN {signal} LIKE '%recall%' OR {signal} LIKE '%impurity%' OR {signal} LIKE '%quality%' "
        f"OR {signal} LIKE '%contamination%' OR {signal} LIKE '%precipitation%' OR {signal} LIKE '%stability%' "
        "THEN 'Quality / CMC' "
        f"WHEN {signal} LIKE '%safety%' OR {signal} LIKE '%referral%' OR {signal} LIKE '%pharmacovigilance%' "
        f"OR {signal} LIKE '%post-authorisation%' OR {signal} LIKE '%withdrawal%' "
        "THEN 'Pharmacovigilance / Regulatory Affairs' "
        "ELSE 'External Innovation / Business Development' END"
    )


@st.cache_data(ttl=15, show_spinner=False)
def opportunity_page(*, page: int = 1, page_size: int = 25, search: str = "", source: str = "All", region: str = "All", priority: str = "All", contact_role: str = "All", include_hidden: bool = False) -> dict[str, Any]:
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
        tier_sql = _qualification_tier_sql()
        role_sql = _contact_role_sql()
        if priority != "All":
            clauses.append(f"({tier_sql})=?"); params.append(priority)
        if contact_role != "All":
            clauses.append(f"({role_sql})=?"); params.append(contact_role)
        where = " AND ".join(clauses)
        total = int(conn.execute(f"SELECT COUNT(*) AS n FROM opportunity_index WHERE {where}", tuple(params)).fetchone()["n"])
        offset = max(0, page - 1) * page_size
        rows = conn.execute(
            f"""SELECT stable_lead_id,company,product,molecule,problem_category,source_type,source_id,region,
            score,grade,lead_status,novelty_status,queue_status,has_full_report,first_seen_at,last_checked_at,last_updated_at,
            evidence_links_json,data_json,({tier_sql}) AS qualification_priority,
            ({role_sql}) AS recommended_contact_role
            FROM opportunity_index WHERE {where}
            ORDER BY CASE ({tier_sql}) WHEN 'P1 · Ready to qualify' THEN 1 WHEN 'P2 · Account research' THEN 2 ELSE 3 END,
            COALESCE(score,0) DESC, COALESCE(last_updated_at,last_checked_at) DESC LIMIT ? OFFSET ?""",
            tuple(params + [page_size, offset]),
        ).fetchall()
        items = []
        for raw in rows:
            item = dict(raw)
            try:
                links = json.loads(item.get("evidence_links_json") or "[]")
            except Exception:
                links = []
            item["official_source_url"] = next(
                (str(link) for link in links if str(link).startswith(("https://", "http://"))), ""
            )
            qualification = opportunity_index.commercial_qualification({**item, "evidence_links": links})
            item.update(qualification)
            items.append(item)
        return {"rows": items, "total": total, "page": page, "page_size": page_size}
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
        facets["priority"] = ["P1 · Ready to qualify", "P2 · Account research", "P3 · Evidence repair"]
        facets["contact_role"] = list(opportunity_index.CONTACT_ROLES)
        return facets
    finally:
        conn.close()


def _regulatory_family_sql() -> str:
    text = "LOWER(COALESCE(source_type,'') || ' ' || COALESCE(problem_category,''))"
    return (
        f"CASE WHEN {text} LIKE '%recall%' OR {text} LIKE '%quality defect%' OR {text} LIKE '%impurity%' OR {text} LIKE '%contamination%' THEN 'Recall / quality defect' "
        f"WHEN {text} LIKE '%shortage%' OR {text} LIKE '%availability%' THEN 'Medicine shortage' "
        f"WHEN {text} LIKE '%communication%' OR {text} LIKE '%dhpc%' THEN 'Safety communication' "
        f"WHEN {text} LIKE '%referral%' OR {text} LIKE '%safety outcome%' OR {text} LIKE '%safety assessment%' OR {text} LIKE '%psusa%' THEN 'Safety review / referral' "
        f"WHEN {text} LIKE '%withdraw%' OR {text} LIKE '%post-authorisation%' THEN 'Post-authorisation withdrawal' "
        "ELSE 'Other regulatory event' END"
    )


def _regulator_sql() -> str:
    return (
        "CASE WHEN UPPER(COALESCE(source_type,'')) LIKE 'FDA %' THEN 'FDA' "
        "WHEN UPPER(COALESCE(source_type,'')) LIKE 'EMA %' THEN 'EMA' "
        "WHEN UPPER(COALESCE(source_type,'')) LIKE 'MHRA %' THEN 'MHRA' ELSE 'Other' END"
    )


def _first_official_url(value: Any) -> str:
    return next(iter(regulatory_intelligence.evidence_urls({"evidence_links_json": value})), "")


@st.cache_data(ttl=30, show_spinner=False)
def regulatory_page(*, page: int = 1, page_size: int = 25, search: str = "",
                    regulator: str = "All", event_family: str = "All", source: str = "All",
                    region: str = "All", account_status: str = "All",
                    evidence_status: str = "All", review_status: str = "All",
                    include_hidden: bool = False) -> dict[str, Any]:
    conn = connection()
    try:
        clauses = [_active_where(include_hidden), "(source_type LIKE 'FDA %' OR source_type LIKE 'EMA %' OR source_type LIKE 'MHRA %')"]
        params: list[Any] = []
        if search.strip():
            q = f"%{search.strip().lower()}%"
            clauses.append("(LOWER(COALESCE(company,'')) LIKE ? OR LOWER(COALESCE(product,'')) LIKE ? OR LOWER(COALESCE(problem_category,'')) LIKE ? OR LOWER(COALESCE(source_id,'')) LIKE ?)")
            params.extend([q, q, q, q])
        family_sql, regulator_sql = _regulatory_family_sql(), _regulator_sql()
        if regulator != "All": clauses.append(f"({regulator_sql})=?"); params.append(regulator)
        if event_family != "All": clauses.append(f"({family_sql})=?"); params.append(event_family)
        if source != "All": clauses.append("source_type=?"); params.append(source)
        if region != "All": clauses.append("region=?"); params.append(region)
        if account_status == "Resolved organisation": clauses.append("TRIM(COALESCE(company,''))<>''")
        elif account_status == "Organisation missing": clauses.append("TRIM(COALESCE(company,''))='' ")
        if evidence_status == "Official link present": clauses.append("LOWER(COALESCE(evidence_links_json,'')) LIKE '%http%'")
        elif evidence_status == "Evidence repair required": clauses.append("LOWER(COALESCE(evidence_links_json,'')) NOT LIKE '%http%'")
        now = datetime.now(timezone.utc).replace(microsecond=0)
        current_cutoff = (now - timedelta(days=7)).isoformat()
        stale_cutoff = (now - timedelta(days=30)).isoformat()
        if review_status == "Current": clauses.append("COALESCE(last_checked_at,'')>=?"); params.append(current_cutoff)
        elif review_status == "Review due":
            clauses.append("COALESCE(last_checked_at,'')<? AND COALESCE(last_checked_at,'')>=?")
            params.extend([current_cutoff, stale_cutoff])
        elif review_status == "Stale": clauses.append("COALESCE(last_checked_at,'')<>'' AND last_checked_at<?"); params.append(stale_cutoff)
        elif review_status == "Review date missing": clauses.append("COALESCE(last_checked_at,'')='' ")
        where = " AND ".join(clauses)
        total = int(conn.execute(f"SELECT COUNT(*) AS n FROM opportunity_index WHERE {where}", tuple(params)).fetchone()["n"])
        offset = max(0, page - 1) * page_size
        rows = conn.execute(
            f"""SELECT stable_lead_id,company,product,molecule,problem_category,source_type,source_id,region,
            score,grade,lead_status,novelty_status,queue_status,evidence_links_json,last_checked_at,last_updated_at,
            ({regulator_sql}) AS regulator,({family_sql}) AS event_family
            FROM opportunity_index WHERE {where}
            ORDER BY COALESCE(last_updated_at,last_checked_at) DESC,COALESCE(score,0) DESC LIMIT ? OFFSET ?""",
            tuple(params + [page_size, offset]),
        ).fetchall()
        items = []
        for raw in rows:
            item = dict(raw)
            item["official_source_url"] = _first_official_url(item.get("evidence_links_json"))
            item["freshness"] = regulatory_intelligence.freshness(item.get("last_checked_at"))
            item.update(regulatory_intelligence.action_route(item))
            items.append(item)
        grouped = [dict(row) for row in conn.execute(
            f"SELECT ({regulator_sql}) AS regulator,({family_sql}) AS event_family,COUNT(*) AS total "
            f"FROM opportunity_index WHERE {_active_where(False)} AND (source_type LIKE 'FDA %' OR source_type LIKE 'EMA %' OR source_type LIKE 'MHRA %') "
            "GROUP BY 1,2 ORDER BY 1,2"
        ).fetchall()]
        return {"rows": items, "total": total, "page": page, "page_size": page_size, "coverage": grouped}
    finally:
        conn.close()


@st.cache_data(ttl=300, show_spinner=False)
def regulatory_facets() -> dict[str, list[str]]:
    conn = connection()
    try:
        base = "(source_type LIKE 'FDA %' OR source_type LIKE 'EMA %' OR source_type LIKE 'MHRA %')"
        return {
            "regulator": list(regulatory_intelligence.REGULATORS),
            "event_family": list(regulatory_intelligence.EVENT_FAMILIES),
            "source": [str(row[0]) for row in conn.execute(f"SELECT DISTINCT source_type FROM opportunity_index WHERE {base} ORDER BY source_type").fetchall()],
            "region": [str(row[0]) for row in conn.execute(f"SELECT DISTINCT region FROM opportunity_index WHERE {base} AND COALESCE(region,'')<>'' ORDER BY region").fetchall()],
        }
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
        item.update(opportunity_index.commercial_qualification(item))
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


@st.cache_data(ttl=60, show_spinner=False)
def account_directory(search: str = "") -> dict[str, Any]:
    """Read the weekly-built organisation projection without slowing page navigation."""
    conn = connection()
    try:
        return {
            "metrics": account_intelligence.metrics(conn),
            "organisations": account_intelligence.organisations(conn, search=search, limit=250),
        }
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def account_profile(organisation_id: str) -> dict[str, Any] | None:
    conn = connection()
    try:
        return account_intelligence.profile(conn, organisation_id)
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def patent_lifecycle_directory(search: str = "", status: str = "All", holder: str = "All") -> dict[str, Any]:
    """Read the pre-built lifecycle projection; normal page loads never call FDA."""
    conn = connection()
    try:
        return {
            "metrics": patent_lifecycle.metrics(conn),
            "facets": patent_lifecycle.facets(conn),
            "products": patent_lifecycle.products(conn, search=search, status=status, holder=holder),
        }
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def patent_lifecycle_profile(lifecycle_id: str) -> dict[str, Any] | None:
    conn = connection()
    try:
        return patent_lifecycle.profile(conn, lifecycle_id)
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def global_patent_directory(search: str = "", jurisdiction: str = "All", source: str = "All") -> dict[str, Any]:
    """Read the weekly-built global projection; never call patent services here."""
    conn = connection()
    try:
        return {
            "metrics": patent_lifecycle.global_metrics(conn),
            "facets": patent_lifecycle.global_facets(conn),
            "documents": patent_lifecycle.global_documents(conn, search=search, jurisdiction=jurisdiction, source=source),
        }
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def global_patent_profile(patent_document_id: str) -> dict[str, Any] | None:
    conn = connection()
    try:
        return patent_lifecycle.global_document_profile(conn, patent_document_id)
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def research_innovation_directory(search: str = "", country: str = "All") -> dict[str, Any]:
    """Read the weekly-built research graph without network work during navigation."""
    conn = connection()
    try:
        return {
            "metrics": research_innovation.metrics(conn), "facets": research_innovation.facets(conn),
            "organisations": research_innovation.organisations(conn, search=search, country=country),
            "publications": research_innovation.publications(conn, search=search),
            "partnerships": research_innovation.partnerships(conn, search=search),
            "technologies": research_innovation.technologies(conn, search=search),
        }
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def research_organisation_profile(organisation_id: str) -> dict[str, Any] | None:
    conn = connection()
    try:
        return research_innovation.profile(conn, organisation_id)
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def commercial_intelligence_directory(search: str = "", event_type: str = "All", evidence: str = "All") -> dict[str, Any]:
    conn = connection()
    try:
        return {
            "metrics": commercial_intelligence.metrics(conn), "facets": commercial_intelligence.facets(conn),
            "events": commercial_intelligence.events(conn, search=search, event_filter=event_type, evidence_filter=evidence),
            "funding": commercial_intelligence.funding(conn, search=search),
        }
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def commercial_event_profile(event_id: str) -> dict[str, Any] | None:
    conn = connection()
    try:
        return commercial_intelligence.profile(conn, event_id)
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
    """Read the governed Phase 7 memory projection."""
    conn = connection()
    try:
        metrics = memory.memory_metrics(conn)
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
def regulator_quality() -> dict[str, Any]:
    """Keep the heavier JSON quality audit off normal health/navigation reads."""
    from pharmadrone.pipeline import opportunity_index
    conn = connection()
    try:
        return opportunity_index.regulator_data_quality(conn)
    finally: conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def regulatory_workspace_quality() -> dict[str, Any]:
    conn = connection()
    try:
        return opportunity_index.regulator_data_quality(conn, include_trials=False)
    finally:
        conn.close()


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
        memory_metrics = memory.memory_metrics(conn)
        return production_readiness.evaluate(database, scheduler, audit, memory_metrics)
    finally:
        conn.close()
