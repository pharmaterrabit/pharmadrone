"""Ordered, transactional schema migrations for SQLite and PostgreSQL."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
from typing import Callable, Any


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Any], None]


def _timestamp_default(conn) -> str:
    return "(CURRENT_TIMESTAMP::text)" if conn.backend == "postgresql" else "CURRENT_TIMESTAMP"


def _identity(conn) -> str:
    return "BIGSERIAL PRIMARY KEY" if conn.backend == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT"


def _core_schema(conn) -> None:
    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS opportunities (
        id TEXT PRIMARY KEY,
        company TEXT, parent_company TEXT, product TEXT, generic_name TEXT,
        brand_name TEXT, dev_code TEXT, indication TEXT, therapeutic_area TEXT,
        region TEXT, stage TEXT, problem_signal TEXT,
        score INTEGER, grade TEXT, report_type TEXT,
        confidence TEXT, evidence_count INTEGER,
        signal_status TEXT, provisional INTEGER, discovery_method TEXT,
        data_json TEXT, created_at TEXT DEFAULT {ts}
    );
    CREATE TABLE IF NOT EXISTS evidence (
        id {ident},
        opportunity_id TEXT, source_type TEXT, source_name TEXT,
        record_id TEXT, title TEXT, url TEXT, language TEXT,
        english_summary TEXT, date_accessed TEXT,
        supports TEXT, does_not_prove TEXT
    );
    CREATE TABLE IF NOT EXISTS rejected (
        id {ident},
        company TEXT, product TEXT, reason TEXT, evidence_count INTEGER,
        data_json TEXT, created_at TEXT DEFAULT {ts}
    );
    CREATE TABLE IF NOT EXISTS opportunity_index (
        stable_lead_id TEXT PRIMARY KEY,
        company TEXT, product TEXT, molecule TEXT, problem_category TEXT,
        source_type TEXT, source_id TEXT, region TEXT, evidence_links_json TEXT,
        first_seen_at TEXT, last_seen_at TEXT, last_updated_at TEXT, last_checked_at TEXT,
        score INTEGER, grade TEXT, lead_status TEXT, novelty_status TEXT,
        queue_status TEXT, queue_rank INTEGER, has_full_report INTEGER DEFAULT 0,
        report_path TEXT, report_opportunity_id TEXT, evidence_hash TEXT,
        data_json TEXT, created_at TEXT DEFAULT {ts}
    );
    CREATE TABLE IF NOT EXISTS opportunity_run_summary (
        run_id TEXT PRIMARY KEY,
        started_at TEXT, mode TEXT, indexed_total INTEGER, new_count INTEGER,
        updated_count INTEGER, seen_count INTEGER, reports_generated INTEGER,
        waiting_count INTEGER, monitor_only_count INTEGER, llm_mode TEXT,
        web_enrichment_status TEXT, data_json TEXT, created_at TEXT DEFAULT {ts}
    );
    CREATE TABLE IF NOT EXISTS source_health_events (
        id {ident},
        run_id TEXT, stable_lead_id TEXT, source_name TEXT, source_type TEXT,
        query TEXT, sanitized_query TEXT, status TEXT, failure_reason TEXT,
        query_count INTEGER DEFAULT 1,
        retrieved_count INTEGER DEFAULT 0, accepted_count INTEGER DEFAULT 0,
        rejected_count INTEGER DEFAULT 0, created_at TEXT DEFAULT {ts}
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
        data_json TEXT, created_at TEXT DEFAULT {ts}, updated_at TEXT DEFAULT {ts}
    );
    CREATE INDEX IF NOT EXISTS idx_opportunity_index_queue ON opportunity_index(queue_status, has_full_report, queue_rank);
    CREATE INDEX IF NOT EXISTS idx_opportunity_index_problem ON opportunity_index(problem_category);
    CREATE INDEX IF NOT EXISTS idx_opportunity_index_seen ON opportunity_index(last_checked_at, novelty_status);
    CREATE INDEX IF NOT EXISTS idx_source_health_source ON source_health_events(source_name, created_at);
    CREATE INDEX IF NOT EXISTS idx_source_health_lead ON source_health_events(stable_lead_id, created_at);
    """)


def _audit_schema(conn) -> None:
    ident = _identity(conn)
    outreach_check = ", CHECK (outreach_approved = 0 OR external_use_approved = 1)" if conn.backend == "postgresql" else ", CHECK (outreach_approved = 0 OR external_use_approved = 1)"
    conn.executescript(f"""
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
        id {ident},
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
    CREATE TABLE IF NOT EXISTS human_audit_versions (
        id {ident},
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
        {outreach_check}
    );
    CREATE TABLE IF NOT EXISTS human_audit_corrections (
        id {ident},
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
    CREATE INDEX IF NOT EXISTS idx_audit_queue_key ON audit_queue_records(audit_key);
    CREATE INDEX IF NOT EXISTS idx_audit_queue_batch ON audit_queue_records(batch_id, id);
    CREATE INDEX IF NOT EXISTS idx_human_audit_key ON human_audit_versions(audit_key, audit_version DESC);
    CREATE INDEX IF NOT EXISTS idx_human_audit_status ON human_audit_versions(audit_status, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_human_audit_source ON human_audit_versions(source_type, source_id);
    CREATE INDEX IF NOT EXISTS idx_human_audit_external ON human_audit_versions(external_use_approved, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_human_audit_outreach ON human_audit_versions(outreach_approved, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_human_audit_reviewer ON human_audit_versions(reviewer_name, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_audit_corrections_key ON human_audit_corrections(audit_key, corrected_at DESC);
    """)


def _infrastructure_schema(conn) -> None:
    ident = _identity(conn)
    ts = _timestamp_default(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS persistence_import_runs (
        import_id TEXT PRIMARY KEY,
        source_label TEXT NOT NULL,
        source_sha256 TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        status TEXT NOT NULL,
        summary_json TEXT
    );
    CREATE TABLE IF NOT EXISTS persistence_import_records (
        id {ident},
        import_id TEXT NOT NULL,
        source_label TEXT NOT NULL,
        source_table TEXT NOT NULL,
        source_primary_key TEXT NOT NULL,
        row_sha256 TEXT NOT NULL,
        destination_primary_key TEXT,
        status TEXT NOT NULL,
        note TEXT,
        imported_at TEXT DEFAULT {ts},
        UNIQUE(source_label, source_table, source_primary_key)
    );
    CREATE INDEX IF NOT EXISTS idx_import_records_source ON persistence_import_records(source_label, source_table);
    """)


def _additive_legacy_columns(conn) -> None:
    additions = {
        "source_health_events": {"query_count": "INTEGER DEFAULT 1"},
        "opportunity_enrichment": {
            "official_followup_status": "TEXT DEFAULT 'not checked'",
            "official_followup_count": "INTEGER DEFAULT 0",
            "label_context_status": "TEXT DEFAULT 'not checked'",
            "clinical_trial_context_status": "TEXT DEFAULT 'not checked'",
            "literature_context_status": "TEXT DEFAULT 'not checked'",
            "best_evidence_tier": "TEXT DEFAULT 'not checked'",
            "official_source_count": "INTEGER DEFAULT 0",
            "literature_source_count": "INTEGER DEFAULT 0",
        },
    }
    for table, columns in additions.items():
        if not conn.has_table(table):
            continue
        existing = conn.columns(table)
        for column, spec in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")


MIGRATIONS = (
    Migration(1, "checkpoint_6a_core_schema", _core_schema),
    Migration(2, "checkpoint_6b_audit_schema", _audit_schema),
    Migration(3, "checkpoint_6c_import_and_indexes", _infrastructure_schema),
    Migration(4, "legacy_additive_columns", _additive_legacy_columns),
)


def _checksum(migration: Migration) -> str:
    payload = f"{migration.version}:{migration.name}:" + inspect.getsource(migration.apply)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_history(conn) -> None:
    ts = _timestamp_default(conn)
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT {ts}
        )
    """)
    conn.commit()


def run_migrations(conn) -> dict[str, Any]:
    """Apply ordered migrations transactionally and safely on repeat runs."""
    locked = False
    try:
        if conn.backend == "postgresql":
            # Session-level advisory lock prevents concurrent app workers from
            # racing the migration history table during deployment startup.
            conn.execute("SELECT pg_advisory_lock(6032026)")
            conn.commit()
            locked = True
        _ensure_history(conn)
        rows = conn.execute("SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version").fetchall()
        applied = {int(row["version"]): dict(row) for row in rows}
        newly_applied: list[int] = []
        for migration in MIGRATIONS:
            expected = _checksum(migration)
            existing = applied.get(migration.version)
            if existing:
                if existing.get("checksum") != expected or existing.get("name") != migration.name:
                    raise RuntimeError(
                        f"Migration {migration.version} metadata mismatch. Refusing to alter an unknown schema state."
                    )
                continue
            with conn.transaction():
                migration.apply(conn)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, checksum, applied_at) VALUES (?,?,?,?)",
                    (
                        migration.version,
                        migration.name,
                        expected,
                        datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    ),
                )
            newly_applied.append(migration.version)
        final = conn.execute("SELECT version, name, checksum, applied_at FROM schema_migrations ORDER BY version").fetchall()
        return {
            "schema_version": max([int(r["version"]) for r in final], default=0),
            "migration_count": len(final),
            "newly_applied": newly_applied,
            "status": "up to date",
        }
    finally:
        if locked:
            try:
                conn.execute("SELECT pg_advisory_unlock(6032026)")
                conn.commit()
            except Exception:
                pass


def migration_manifest() -> list[dict[str, Any]]:
    return [
        {"version": m.version, "name": m.name, "checksum": _checksum(m)}
        for m in MIGRATIONS
    ]
