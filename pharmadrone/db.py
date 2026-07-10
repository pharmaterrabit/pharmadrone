"""Tiny SQLite persistence layer."""
from __future__ import annotations
import sqlite3
import json
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    company TEXT, parent_company TEXT, product TEXT, generic_name TEXT,
    brand_name TEXT, dev_code TEXT, indication TEXT, therapeutic_area TEXT,
    region TEXT, stage TEXT, problem_signal TEXT,
    score INTEGER, grade TEXT, report_type TEXT,
    confidence TEXT, evidence_count INTEGER,
    signal_status TEXT, provisional INTEGER, discovery_method TEXT,
    data_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT, source_type TEXT, source_name TEXT,
    record_id TEXT, title TEXT, url TEXT, language TEXT,
    english_summary TEXT, date_accessed TEXT,
    supports TEXT, does_not_prove TEXT
);
CREATE TABLE IF NOT EXISTS rejected (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT, product TEXT, reason TEXT, evidence_count INTEGER,
    data_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS opportunity_index (
    stable_lead_id TEXT PRIMARY KEY,
    company TEXT, product TEXT, molecule TEXT, problem_category TEXT,
    source_type TEXT, source_id TEXT, region TEXT, evidence_links_json TEXT,
    first_seen_at TEXT, last_seen_at TEXT, last_updated_at TEXT, last_checked_at TEXT,
    score INTEGER, grade TEXT, lead_status TEXT, novelty_status TEXT,
    queue_status TEXT, queue_rank INTEGER, has_full_report INTEGER DEFAULT 0,
    report_path TEXT, report_opportunity_id TEXT, evidence_hash TEXT,
    data_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS opportunity_run_summary (
    run_id TEXT PRIMARY KEY,
    started_at TEXT, mode TEXT, indexed_total INTEGER, new_count INTEGER,
    updated_count INTEGER, seen_count INTEGER, reports_generated INTEGER,
    waiting_count INTEGER, monitor_only_count INTEGER, llm_mode TEXT,
    web_enrichment_status TEXT, data_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_opportunity_index_queue ON opportunity_index(queue_status, has_full_report, queue_rank);
CREATE INDEX IF NOT EXISTS idx_opportunity_index_problem ON opportunity_index(problem_category);
CREATE INDEX IF NOT EXISTS idx_opportunity_index_seen ON opportunity_index(last_checked_at, novelty_status);

CREATE TABLE IF NOT EXISTS source_health_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT, stable_lead_id TEXT, source_name TEXT, source_type TEXT,
    query TEXT, sanitized_query TEXT, status TEXT, failure_reason TEXT,
    query_count INTEGER DEFAULT 1,
    retrieved_count INTEGER DEFAULT 0, accepted_count INTEGER DEFAULT 0,
    rejected_count INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS opportunity_enrichment (
    stable_lead_id TEXT PRIMARY KEY,
    last_enrichment_check TEXT, enrichment_status TEXT, corroboration_status TEXT,
    evidence_quality TEXT, source_coverage_count INTEGER DEFAULT 0,
    tier1_count INTEGER DEFAULT 0, tier2_count INTEGER DEFAULT 0,
    tier3_count INTEGER DEFAULT 0, tier4_count INTEGER DEFAULT 0,
    regulator_confirmed INTEGER DEFAULT 0, company_confirmed INTEGER DEFAULT 0,
    literature_supported INTEGER DEFAULT 0, external_corroboration_found INTEGER DEFAULT 0,
    official_followup_status TEXT DEFAULT 'not checked',
    official_followup_count INTEGER DEFAULT 0,
    label_context_status TEXT DEFAULT 'not checked',
    clinical_trial_context_status TEXT DEFAULT 'not checked',
    literature_context_status TEXT DEFAULT 'not checked',
    best_evidence_tier TEXT DEFAULT 'not checked',
    official_source_count INTEGER DEFAULT 0,
    literature_source_count INTEGER DEFAULT 0,
    data_json TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_source_health_source ON source_health_events(source_name, created_at);
CREATE INDEX IF NOT EXISTS idx_source_health_lead ON source_health_events(stable_lead_id, created_at);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Additive migrations for local MVP databases created by older ZIPs.
    _ensure_column(conn, "source_health_events", "query_count", "INTEGER DEFAULT 1")
    # Phase 3B additive enrichment columns for local MVP databases created by older ZIPs.
    for column, spec in {
        "official_followup_status": "TEXT DEFAULT 'not checked'",
        "official_followup_count": "INTEGER DEFAULT 0",
        "label_context_status": "TEXT DEFAULT 'not checked'",
        "clinical_trial_context_status": "TEXT DEFAULT 'not checked'",
        "literature_context_status": "TEXT DEFAULT 'not checked'",
        "best_evidence_tier": "TEXT DEFAULT 'not checked'",
        "official_source_count": "INTEGER DEFAULT 0",
        "literature_source_count": "INTEGER DEFAULT 0",
    }.items():
        _ensure_column(conn, "opportunity_enrichment", column, spec)
    return conn



_ASCII_SKIP_LABELS = {
    "skipped - not trial lead",
    "skipped - no product/molecule",
    "skipped - not FDA/regulatory lead",
}


def _clean_status_label(value):
    """Return ASCII-safe status labels for UI/export compatibility."""
    if value is None:
        return value
    text = str(value)
    # Normalise common dash mojibake from CSV/spreadsheet rendering paths.
    text = text.replace("\u2014", "-").replace("\u2013", "-").replace("\u201a\u00c4\u00ee", "-")
    text = " ".join(text.split())
    if text in _ASCII_SKIP_LABELS:
        return text
    return text


def _clean_status_fields(row: dict) -> dict:
    for key in list(row.keys()):
        if key.endswith("_status") or key in {"enrichment_status", "corroboration_status"}:
            row[key] = _clean_status_label(row.get(key))
    return row

def _ensure_column(conn, table: str, column: str, spec: str) -> None:
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")
            conn.commit()
    except Exception:
        # Do not block app startup on a non-critical additive migration.
        pass


def reset(db_path: Path) -> None:
    conn = connect(db_path)
    conn.executescript(
        "DELETE FROM opportunities; DELETE FROM evidence; DELETE FROM rejected;"
    )
    conn.commit()
    conn.close()


def save_opportunity(conn, opp: dict, evidence: list[dict]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO opportunities
        (id, company, parent_company, product, generic_name, brand_name, dev_code,
         indication, therapeutic_area, region, stage, problem_signal,
         score, grade, report_type, confidence, evidence_count,
         signal_status, provisional, discovery_method, data_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            opp["id"], opp.get("company"), opp.get("parent_company"),
            opp.get("product"), opp.get("generic_name"), opp.get("brand_name"),
            opp.get("dev_code"), opp.get("indication"), opp.get("therapeutic_area"),
            opp.get("region"), opp.get("stage"), opp.get("problem_signal"),
            opp.get("score"), opp.get("grade"), opp.get("report_type"),
            opp.get("confidence"), len(evidence),
            opp.get("signal_status"), int(bool(opp.get("provisional"))),
            opp.get("discovery_method"), json.dumps(opp, ensure_ascii=False),
        ),
    )
    for e in evidence:
        conn.execute(
            """INSERT INTO evidence
            (opportunity_id, source_type, source_name, record_id, title, url,
             language, english_summary, date_accessed, supports, does_not_prove)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                opp["id"], e.get("source_type"), e.get("source_name"),
                e.get("record_id"), e.get("title"), e.get("url"),
                e.get("language", "en"), e.get("english_summary"),
                e.get("date_accessed"), e.get("supports"), e.get("does_not_prove"),
            ),
        )
    conn.commit()


def save_rejected(conn, company, product, reason, ev_count, data) -> None:
    conn.execute(
        "INSERT INTO rejected (company, product, reason, evidence_count, data_json) VALUES (?,?,?,?,?)",
        (company, product, reason, ev_count, json.dumps(data, ensure_ascii=False)),
    )
    conn.commit()


def fetch_all(conn, table: str) -> list[dict]:
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    return [_clean_status_fields(dict(r)) for r in rows]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _int_or_none(value):
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def upsert_index_record(conn, record: dict) -> str:
    """Insert/update one persistent opportunity-index record.

    Returns the novelty status assigned in this upsert: new, seen, updated,
    or rejected / hidden. Existing generated-report metadata is preserved unless
    the incoming record has a full report.
    """
    now = _now_iso()
    sid = record["stable_lead_id"]
    existing = conn.execute(
        "SELECT * FROM opportunity_index WHERE stable_lead_id=?", (sid,)
    ).fetchone()

    incoming_hash = record.get("evidence_hash") or ""
    incoming_score = _int_or_none(record.get("score"))
    incoming_grade = record.get("grade") or ""
    incoming_status = record.get("lead_status") or "needs validation"
    incoming_has_report = int(bool(record.get("has_full_report")))

    if existing is None:
        novelty = record.get("novelty_status") or "new"
        if record.get("queue_status") == "rejected":
            novelty = "rejected / hidden"
        conn.execute(
            """INSERT INTO opportunity_index
            (stable_lead_id, company, product, molecule, problem_category,
             source_type, source_id, region, evidence_links_json, first_seen_at,
             last_seen_at, last_updated_at, last_checked_at, score, grade,
             lead_status, novelty_status, queue_status, queue_rank, has_full_report,
             report_path, report_opportunity_id, evidence_hash, data_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sid, record.get("company"), record.get("product"), record.get("molecule"),
                record.get("problem_category"), record.get("source_type"), record.get("source_id"),
                record.get("region"), record.get("evidence_links_json"), now, now, now, now,
                incoming_score, incoming_grade, incoming_status, novelty,
                record.get("queue_status") or "waiting", _int_or_none(record.get("queue_rank")),
                incoming_has_report, record.get("report_path") or "",
                record.get("report_opportunity_id") or sid, incoming_hash,
                record.get("data_json") or "{}",
            ),
        )
        conn.commit()
        return novelty

    ex = dict(existing)
    protected_novelty = ex.get("novelty_status") in {"saved", "archived", "rejected / hidden"}
    effective_score = incoming_score if incoming_score is not None else _int_or_none(ex.get("score"))
    effective_grade = incoming_grade if incoming_grade else (ex.get("grade") or "")
    effective_status = incoming_status if incoming_status else (ex.get("lead_status") or "needs validation")
    changed = (
        (ex.get("evidence_hash") or "") != incoming_hash
        or (incoming_score is not None and _int_or_none(ex.get("score")) != incoming_score)
        or (bool(incoming_grade) and (ex.get("grade") or "") != incoming_grade)
        or (bool(incoming_status) and (ex.get("lead_status") or "") != incoming_status)
    )
    if protected_novelty:
        novelty = ex.get("novelty_status")
    elif ex.get("novelty_status") == "new" and incoming_has_report:
        # Same-run promotion from indexed preview -> generated report should not
        # make a genuinely new lead look merely "updated".
        novelty = "new"
    else:
        novelty = "updated" if changed else "seen"
    if record.get("queue_status") == "rejected" and not incoming_has_report:
        novelty = "rejected / hidden"

    existing_has_report = int(ex.get("has_full_report") or 0)
    has_report = 1 if (existing_has_report or incoming_has_report) else 0
    report_path = record.get("report_path") or ex.get("report_path") or ""
    report_opp_id = record.get("report_opportunity_id") or ex.get("report_opportunity_id") or sid

    if has_report:
        queue_status = "report_generated"
    elif record.get("queue_status"):
        queue_status = record.get("queue_status")
    else:
        queue_status = ex.get("queue_status") or "waiting"

    last_updated = now if changed or incoming_has_report else (ex.get("last_updated_at") or now)
    first_seen = ex.get("first_seen_at") or now

    conn.execute(
        """UPDATE opportunity_index SET
           company=?, product=?, molecule=?, problem_category=?, source_type=?, source_id=?, region=?,
           evidence_links_json=?, last_seen_at=?, last_updated_at=?, last_checked_at=?,
           score=?, grade=?, lead_status=?, novelty_status=?, queue_status=?, queue_rank=?,
           has_full_report=?, report_path=?, report_opportunity_id=?, evidence_hash=?, data_json=?
           WHERE stable_lead_id=?""",
        (
            record.get("company") or ex.get("company"),
            record.get("product") or ex.get("product"),
            record.get("molecule") or ex.get("molecule"),
            record.get("problem_category") or ex.get("problem_category"),
            record.get("source_type") or ex.get("source_type"),
            record.get("source_id") or ex.get("source_id"),
            record.get("region") or ex.get("region"),
            record.get("evidence_links_json") or ex.get("evidence_links_json"),
            now, last_updated, now, effective_score, effective_grade, effective_status, novelty,
            queue_status, _int_or_none(record.get("queue_rank")) or ex.get("queue_rank"),
            has_report, report_path, report_opp_id, incoming_hash or ex.get("evidence_hash"),
            record.get("data_json") or ex.get("data_json"), sid,
        ),
    )
    # Preserve first_seen_at explicitly for older SQLite/table variants.
    conn.execute("UPDATE opportunity_index SET first_seen_at=? WHERE stable_lead_id=?", (first_seen, sid))
    conn.commit()
    return novelty


def fetch_index_records(conn, include_hidden: bool = False) -> list[dict]:
    where = "" if include_hidden else "WHERE COALESCE(oi.novelty_status,'') NOT IN ('archived','rejected / hidden') AND COALESCE(oi.queue_status,'') NOT IN ('archived','rejected')"
    rows = conn.execute(
        f"""SELECT oi.*,
                  CASE WHEN oe.enrichment_status='source unavailable' THEN 'external enrichment unavailable' ELSE COALESCE(oe.enrichment_status, 'enrichment not checked') END AS enrichment_status,
                  COALESCE(oe.corroboration_status, 'direct source only') AS corroboration_status,
                  COALESCE(oe.evidence_quality, 'not checked') AS evidence_quality,
                  COALESCE(oe.source_coverage_count, 0) AS source_coverage_count,
                  COALESCE(oe.last_enrichment_check, '') AS last_enrichment_check,
                  COALESCE(oe.tier1_count, 0) AS tier1_count,
                  COALESCE(oe.tier2_count, 0) AS tier2_count,
                  COALESCE(oe.tier3_count, 0) AS tier3_count,
                  COALESCE(oe.tier4_count, 0) AS tier4_count,
                  COALESCE(oe.regulator_confirmed, 0) AS regulator_confirmed,
                  COALESCE(oe.company_confirmed, 0) AS company_confirmed,
                  COALESCE(oe.literature_supported, 0) AS literature_supported,
                  COALESCE(oe.external_corroboration_found, 0) AS external_corroboration_found,
                  COALESCE(oe.official_followup_status, 'not checked') AS official_followup_status,
                  COALESCE(oe.official_followup_count, 0) AS official_followup_count,
                  COALESCE(oe.label_context_status, 'not checked') AS label_context_status,
                  COALESCE(oe.clinical_trial_context_status, 'not checked') AS clinical_trial_context_status,
                  COALESCE(oe.literature_context_status, 'not checked') AS literature_context_status,
                  CASE WHEN COALESCE(oe.best_evidence_tier, 'not checked') IN ('', 'not checked')
                       THEN COALESCE(oe.evidence_quality, 'not checked')
                       ELSE oe.best_evidence_tier END AS best_evidence_tier,
                  COALESCE(oe.official_source_count, 0) AS official_source_count,
                  COALESCE(oe.literature_source_count, 0) AS literature_source_count
           FROM opportunity_index oi
           LEFT JOIN opportunity_enrichment oe ON oe.stable_lead_id = oi.stable_lead_id
           {where}
           ORDER BY oi.has_full_report DESC, COALESCE(oi.queue_rank, 999999), oi.last_checked_at DESC"""
    ).fetchall()
    return [_clean_status_fields(dict(r)) for r in rows]


def fetch_waiting_index_records(conn, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """SELECT * FROM opportunity_index
           WHERE queue_status='waiting' AND COALESCE(has_full_report,0)=0
             AND COALESCE(novelty_status,'') NOT IN ('archived','rejected / hidden')
           ORDER BY COALESCE(queue_rank, 999999), COALESCE(score, 0) DESC, last_checked_at DESC
           LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_index_stats(conn) -> dict:
    rows = fetch_index_records(conn, include_hidden=True)
    total = len(rows)
    reports = sum(1 for r in rows if int(r.get("has_full_report") or 0))
    waiting = sum(1 for r in rows if r.get("queue_status") == "waiting" and not int(r.get("has_full_report") or 0))
    updated = sum(1 for r in rows if r.get("novelty_status") == "updated")
    new = sum(1 for r in rows if r.get("novelty_status") == "new")
    seen = sum(1 for r in rows if r.get("novelty_status") == "seen")
    monitor = sum(1 for r in rows if r.get("lead_status") == "monitor only")
    archived = sum(1 for r in rows if r.get("novelty_status") in {"archived", "rejected / hidden"} or r.get("queue_status") in {"archived", "rejected"})
    return {
        "indexed_total": total,
        "full_reports": reports,
        "waiting_queue": waiting,
        "new_count": new,
        "updated_count": updated,
        "seen_count": seen,
        "monitor_only_count": monitor,
        "archived_hidden_count": archived,
    }


def save_run_summary(conn, summary: dict) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO opportunity_run_summary
        (run_id, started_at, mode, indexed_total, new_count, updated_count, seen_count,
         reports_generated, waiting_count, monitor_only_count, llm_mode, web_enrichment_status, data_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            summary.get("run_id"), summary.get("started_at"), summary.get("mode"),
            summary.get("indexed_total"), summary.get("new_count"), summary.get("updated_count"),
            summary.get("seen_count"), summary.get("reports_generated"), summary.get("waiting_count"),
            summary.get("monitor_only_count"), summary.get("llm_mode"),
            summary.get("web_enrichment_status"), json.dumps(summary, ensure_ascii=False, default=str),
        ),
    )
    conn.commit()


def fetch_run_summaries(conn, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM opportunity_run_summary ORDER BY created_at DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Phase 3A source health / enrichment helpers --------------------------

def save_source_health_event(conn, event: dict) -> None:
    now = _now_iso()
    conn.execute(
        """INSERT INTO source_health_events
        (run_id, stable_lead_id, source_name, source_type, query, sanitized_query,
         status, failure_reason, query_count, retrieved_count, accepted_count, rejected_count, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event.get("run_id") or "",
            event.get("stable_lead_id") or "",
            event.get("source_name") or "unknown source",
            event.get("source_type") or "source",
            event.get("query") or "",
            event.get("sanitized_query") or "",
            event.get("status") or "not checked",
            event.get("failure_reason") or "",
            _int_or_none(event.get("query_count")) or _int_or_none(event.get("queries")) or 1,
            _int_or_none(event.get("retrieved_count")) or 0,
            _int_or_none(event.get("accepted_count")) or 0,
            _int_or_none(event.get("rejected_count")) or 0,
            event.get("created_at") or now,
        ),
    )
    conn.commit()


def save_source_health_events(conn, events: list[dict]) -> None:
    for event in events or []:
        save_source_health_event(conn, event)


def fetch_source_health_events(conn, limit: int = 250) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM source_health_events ORDER BY created_at DESC, id DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_source_health_summary(conn, limit: int = 500) -> list[dict]:
    from .pipeline import source_health as _sh
    return _sh.summarize_events(fetch_source_health_events(conn, limit=limit))


def upsert_enrichment(conn, payload: dict) -> None:
    now = _now_iso()
    sid = payload.get("stable_lead_id")
    if not sid:
        return
    existing = conn.execute(
        "SELECT stable_lead_id, created_at FROM opportunity_enrichment WHERE stable_lead_id=?",
        (sid,),
    ).fetchone()
    created_at = dict(existing).get("created_at") if existing else now
    # Store ASCII-safe status labels to avoid CSV mojibake in spreadsheet apps.
    clean_payload_statuses = dict(payload)
    for _status_key in (
        "enrichment_status", "corroboration_status", "official_followup_status",
        "label_context_status", "clinical_trial_context_status", "literature_context_status",
    ):
        if _status_key in clean_payload_statuses:
            clean_payload_statuses[_status_key] = _clean_status_label(clean_payload_statuses.get(_status_key))
    payload = clean_payload_statuses
    conn.execute(
        """INSERT OR REPLACE INTO opportunity_enrichment
        (stable_lead_id, last_enrichment_check, enrichment_status, corroboration_status,
         evidence_quality, source_coverage_count, tier1_count, tier2_count, tier3_count,
         tier4_count, regulator_confirmed, company_confirmed, literature_supported,
         external_corroboration_found, official_followup_status, official_followup_count,
         label_context_status, clinical_trial_context_status, literature_context_status,
         best_evidence_tier, official_source_count, literature_source_count,
         data_json, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            sid, now, payload.get("enrichment_status") or "checked",
            payload.get("corroboration_status") or "direct source only",
            payload.get("evidence_quality") or "not checked",
            _int_or_none(payload.get("source_coverage_count")) or 0,
            _int_or_none(payload.get("tier1_count")) or 0,
            _int_or_none(payload.get("tier2_count")) or 0,
            _int_or_none(payload.get("tier3_count")) or 0,
            _int_or_none(payload.get("tier4_count")) or 0,
            int(bool(payload.get("regulator_confirmed"))),
            int(bool(payload.get("company_confirmed"))),
            int(bool(payload.get("literature_supported"))),
            int(bool(payload.get("external_corroboration_found"))),
            payload.get("official_followup_status") or "not checked",
            _int_or_none(payload.get("official_followup_count")) or 0,
            payload.get("label_context_status") or "not checked",
            payload.get("clinical_trial_context_status") or "not checked",
            payload.get("literature_context_status") or "not checked",
            (payload.get("evidence_quality") if (payload.get("best_evidence_tier") in (None, "", "not checked")) else payload.get("best_evidence_tier")) or "not checked",
            _int_or_none(payload.get("official_source_count")) or 0,
            _int_or_none(payload.get("literature_source_count")) or 0,
            payload.get("data_json") or "{}", created_at, now,
        ),
    )
    conn.commit()


def fetch_enrichment_map(conn) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM opportunity_enrichment").fetchall()
    return {dict(r)["stable_lead_id"]: dict(r) for r in rows}


def fetch_enrichment_candidates(conn, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        """SELECT oi.*,
                  COALESCE(oe.last_enrichment_check, '') AS last_enrichment_check,
                  CASE WHEN oe.enrichment_status='source unavailable' THEN 'external enrichment unavailable' ELSE COALESCE(oe.enrichment_status, 'enrichment not checked') END AS enrichment_status
           FROM opportunity_index oi
           LEFT JOIN opportunity_enrichment oe ON oe.stable_lead_id = oi.stable_lead_id
           WHERE COALESCE(oi.novelty_status,'') NOT IN ('archived','rejected / hidden')
             AND COALESCE(oi.queue_status,'') NOT IN ('archived','rejected')
           ORDER BY
             CASE WHEN COALESCE(oe.last_enrichment_check,'')='' THEN 0 ELSE 1 END,
             oi.has_full_report DESC,
             CASE WHEN oi.novelty_status IN ('new','updated') THEN 0 ELSE 1 END,
             COALESCE(oi.score, 0) DESC,
             COALESCE(oi.queue_rank, 999999)
           LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]
