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
