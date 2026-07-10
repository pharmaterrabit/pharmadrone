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
"""


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


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
    return [dict(r) for r in rows]


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
    where = "" if include_hidden else "WHERE COALESCE(novelty_status,'') NOT IN ('archived','rejected / hidden') AND COALESCE(queue_status,'') NOT IN ('archived','rejected')"
    rows = conn.execute(
        f"SELECT * FROM opportunity_index {where} ORDER BY has_full_report DESC, COALESCE(queue_rank, 999999), last_checked_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


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
