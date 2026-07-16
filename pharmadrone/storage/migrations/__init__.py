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



def _scheduler_schema(conn) -> None:
    ident = _identity(conn)
    ts = _timestamp_default(conn)
    bool_check = "CHECK (enabled IN (0,1))"
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS source_refresh_state (
        source_name TEXT PRIMARY KEY,
        source_type TEXT NOT NULL,
        cadence TEXT NOT NULL,
        last_attempt_at TEXT,
        last_success_at TEXT,
        next_due_at TEXT,
        last_cursor TEXT,
        last_watermark TEXT,
        last_status TEXT DEFAULT 'Never run',
        consecutive_failures INTEGER DEFAULT 0,
        last_error_summary TEXT,
        enabled INTEGER DEFAULT 1 {bool_check},
        updated_at TEXT DEFAULT {ts}
    );
    CREATE TABLE IF NOT EXISTS refresh_runs (
        run_id TEXT PRIMARY KEY,
        trigger_type TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        status TEXT NOT NULL,
        sources_due INTEGER DEFAULT 0,
        sources_completed INTEGER DEFAULT 0,
        sources_failed INTEGER DEFAULT 0,
        records_retrieved INTEGER DEFAULT 0,
        records_created INTEGER DEFAULT 0,
        records_updated INTEGER DEFAULT 0,
        records_unchanged INTEGER DEFAULT 0,
        records_rejected INTEGER DEFAULT 0,
        opportunities_created INTEGER DEFAULT 0,
        duplicate_records_prevented INTEGER DEFAULT 0,
        estimated_spend REAL DEFAULT 0,
        error_summary TEXT,
        metadata_json TEXT
    );
    CREATE TABLE IF NOT EXISTS source_refresh_runs (
        run_id TEXT NOT NULL,
        source_name TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        status TEXT NOT NULL,
        cursor_before TEXT,
        cursor_after TEXT,
        watermark_before TEXT,
        watermark_after TEXT,
        records_retrieved INTEGER DEFAULT 0,
        records_created INTEGER DEFAULT 0,
        records_updated INTEGER DEFAULT 0,
        records_unchanged INTEGER DEFAULT 0,
        records_rejected INTEGER DEFAULT 0,
        opportunities_created INTEGER DEFAULT 0,
        duplicate_records_prevented INTEGER DEFAULT 0,
        retry_count INTEGER DEFAULT 0,
        elapsed_seconds REAL DEFAULT 0,
        estimated_spend REAL DEFAULT 0,
        error_class TEXT,
        error_summary TEXT,
        metadata_json TEXT,
        PRIMARY KEY (run_id, source_name),
        FOREIGN KEY (run_id) REFERENCES refresh_runs(run_id),
        FOREIGN KEY (source_name) REFERENCES source_refresh_state(source_name)
    );
    CREATE TABLE IF NOT EXISTS source_records (
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_name TEXT NOT NULL,
        official_source_url TEXT,
        source_updated_at TEXT,
        content_checksum TEXT NOT NULL,
        record_json TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        last_changed_at TEXT NOT NULL,
        last_refresh_run_id TEXT,
        active INTEGER DEFAULT 1 CHECK (active IN (0,1)),
        PRIMARY KEY (source_type, source_id),
        FOREIGN KEY (last_refresh_run_id) REFERENCES refresh_runs(run_id)
    );
    CREATE TABLE IF NOT EXISTS source_record_changes (
        id {ident},
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        previous_checksum TEXT,
        new_checksum TEXT NOT NULL,
        fields_changed_json TEXT,
        source_update_timestamp TEXT,
        ingested_at TEXT NOT NULL,
        refresh_run_id TEXT NOT NULL,
        previous_record_json TEXT,
        new_record_json TEXT NOT NULL,
        FOREIGN KEY (refresh_run_id) REFERENCES refresh_runs(run_id),
        FOREIGN KEY (source_type, source_id) REFERENCES source_records(source_type, source_id)
    );
    CREATE TABLE IF NOT EXISTS opportunity_refresh_flags (
        stable_lead_id TEXT NOT NULL,
        refresh_run_id TEXT NOT NULL,
        review_status TEXT NOT NULL,
        reason TEXT,
        reviewed_at TEXT NOT NULL,
        metadata_json TEXT,
        PRIMARY KEY (stable_lead_id, refresh_run_id),
        FOREIGN KEY (stable_lead_id) REFERENCES opportunity_index(stable_lead_id),
        FOREIGN KEY (refresh_run_id) REFERENCES refresh_runs(run_id)
    );
    CREATE TABLE IF NOT EXISTS source_url_checks (
        id {ident},
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        official_source_url TEXT NOT NULL,
        checked_at TEXT NOT NULL,
        status TEXT NOT NULL,
        http_status INTEGER,
        error_summary TEXT,
        refresh_run_id TEXT NOT NULL,
        FOREIGN KEY (source_type, source_id) REFERENCES source_records(source_type, source_id),
        FOREIGN KEY (refresh_run_id) REFERENCES refresh_runs(run_id)
    );
    CREATE TABLE IF NOT EXISTS scheduler_notifications (
        id {ident},
        run_id TEXT,
        source_name TEXT,
        severity TEXT NOT NULL,
        event_type TEXT NOT NULL,
        safe_summary TEXT NOT NULL,
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        FOREIGN KEY (run_id) REFERENCES refresh_runs(run_id),
        FOREIGN KEY (source_name) REFERENCES source_refresh_state(source_name)
    );
    CREATE INDEX IF NOT EXISTS idx_refresh_state_due ON source_refresh_state(enabled, next_due_at);
    CREATE INDEX IF NOT EXISTS idx_refresh_runs_started ON refresh_runs(started_at DESC);
    CREATE INDEX IF NOT EXISTS idx_source_refresh_runs_source ON source_refresh_runs(source_name, started_at DESC);
    CREATE INDEX IF NOT EXISTS idx_source_records_name ON source_records(source_name, source_updated_at);
    CREATE INDEX IF NOT EXISTS idx_source_records_seen ON source_records(last_seen_at, active);
    CREATE INDEX IF NOT EXISTS idx_source_changes_identity ON source_record_changes(source_type, source_id, ingested_at DESC);
    CREATE INDEX IF NOT EXISTS idx_source_url_checks_identity ON source_url_checks(source_type, source_id, checked_at DESC);
    CREATE INDEX IF NOT EXISTS idx_scheduler_notifications_open ON scheduler_notifications(resolved_at, created_at DESC);
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


def _administration_schema(conn) -> None:
    """Durable, tenant-aware administration records for Checkpoint 6D-B."""
    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS organisations (
        organisation_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE,
        plan_name TEXT DEFAULT 'Unassigned',
        status TEXT NOT NULL DEFAULT 'active',
        retention_days INTEGER DEFAULT 2555,
        created_at TEXT NOT NULL DEFAULT {ts},
        updated_at TEXT NOT NULL DEFAULT {ts}
    );
    CREATE TABLE IF NOT EXISTS workspaces (
        workspace_id TEXT PRIMARY KEY,
        organisation_id TEXT NOT NULL,
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL DEFAULT {ts},
        UNIQUE(organisation_id, name),
        FOREIGN KEY (organisation_id) REFERENCES organisations(organisation_id)
    );
    CREATE TABLE IF NOT EXISTS admin_users (
        user_id TEXT PRIMARY KEY,
        organisation_id TEXT,
        workspace_id TEXT,
        display_name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        role_name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'invited',
        mfa_enabled INTEGER DEFAULT 0 CHECK (mfa_enabled IN (0,1)),
        export_allowed INTEGER DEFAULT 0 CHECK (export_allowed IN (0,1)),
        outreach_allowed INTEGER DEFAULT 0 CHECK (outreach_allowed IN (0,1)),
        invited_at TEXT,
        last_login_at TEXT,
        created_at TEXT NOT NULL DEFAULT {ts},
        FOREIGN KEY (organisation_id) REFERENCES organisations(organisation_id),
        FOREIGN KEY (workspace_id) REFERENCES workspaces(workspace_id)
    );
    CREATE TABLE IF NOT EXISTS workspace_settings (
        organisation_id TEXT PRIMARY KEY,
        export_policy TEXT NOT NULL DEFAULT 'workspace_admin_approval',
        notification_mode TEXT NOT NULL DEFAULT 'daily_digest',
        retention_days INTEGER NOT NULL DEFAULT 2555,
        mfa_required INTEGER DEFAULT 1 CHECK (mfa_required IN (0,1)),
        updated_at TEXT NOT NULL DEFAULT {ts},
        FOREIGN KEY (organisation_id) REFERENCES organisations(organisation_id)
    );
    CREATE TABLE IF NOT EXISTS admin_audit_events (
        id {ident},
        organisation_id TEXT,
        workspace_id TEXT,
        actor_name TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'INFO',
        safe_summary TEXT NOT NULL,
        metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT {ts}
    );
    CREATE TABLE IF NOT EXISTS backup_records (
        id {ident},
        scope_name TEXT NOT NULL,
        status TEXT NOT NULL,
        checksum_verified INTEGER DEFAULT 0 CHECK (checksum_verified IN (0,1)),
        size_bytes INTEGER,
        safe_summary TEXT,
        created_by TEXT,
        created_at TEXT NOT NULL DEFAULT {ts},
        restore_tested_at TEXT
    );
    CREATE TABLE IF NOT EXISTS feature_flags (
        scope_key TEXT PRIMARY KEY,
        flag_key TEXT NOT NULL,
        description TEXT NOT NULL,
        scope_type TEXT NOT NULL DEFAULT 'global',
        organisation_id TEXT,
        enabled INTEGER DEFAULT 0 CHECK (enabled IN (0,1)),
        status TEXT NOT NULL DEFAULT 'active',
        updated_at TEXT NOT NULL DEFAULT {ts}
    );
    CREATE TABLE IF NOT EXISTS api_usage_daily (
        usage_key TEXT PRIMARY KEY,
        usage_date TEXT NOT NULL,
        provider TEXT NOT NULL,
        api_family TEXT NOT NULL,
        call_count INTEGER DEFAULT 0,
        success_count INTEGER DEFAULT 0,
        failure_count INTEGER DEFAULT 0,
        estimated_cost_usd REAL DEFAULT 0,
        recorded_at TEXT NOT NULL DEFAULT {ts}
    );
    CREATE INDEX IF NOT EXISTS idx_admin_users_org ON admin_users(organisation_id, status);
    CREATE INDEX IF NOT EXISTS idx_workspaces_org ON workspaces(organisation_id, status);
    CREATE INDEX IF NOT EXISTS idx_admin_audit_org ON admin_audit_events(organisation_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_admin_audit_type ON admin_audit_events(event_type, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_api_usage_date ON api_usage_daily(usage_date, provider);
    """)


def _seller_case_study_schema(conn) -> None:
    """Durable real-provider case studies and validation-gated shortlists."""
    ts = _timestamp_default(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS seller_profiles (
        profile_id TEXT PRIMARY KEY,
        provider_name TEXT NOT NULL,
        provider_type TEXT NOT NULL,
        website_url TEXT NOT NULL,
        profile_summary TEXT NOT NULL,
        capabilities_json TEXT NOT NULL,
        evidence_sources_json TEXT NOT NULL,
        last_verified_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        updated_at TEXT NOT NULL DEFAULT {ts}
    );
    CREATE TABLE IF NOT EXISTS seller_case_studies (
        case_study_id TEXT PRIMARY KEY,
        organisation_id TEXT NOT NULL DEFAULT 'platform',
        profile_id TEXT NOT NULL,
        title TEXT NOT NULL,
        objective TEXT NOT NULL,
        workflow_status TEXT NOT NULL,
        candidate_count INTEGER NOT NULL DEFAULT 0,
        approved_count INTEGER NOT NULL DEFAULT 0,
        created_by TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT {ts},
        FOREIGN KEY (profile_id) REFERENCES seller_profiles(profile_id)
    );
    CREATE TABLE IF NOT EXISTS seller_case_study_targets (
        case_study_id TEXT NOT NULL,
        target_key TEXT NOT NULL,
        audit_key TEXT,
        stable_lead_id TEXT,
        target_company TEXT,
        product TEXT,
        problem_category TEXT,
        source_type TEXT,
        source_id TEXT,
        seller_fit_strength TEXT,
        validation_status TEXT NOT NULL,
        external_use_approved INTEGER DEFAULT 0 CHECK (external_use_approved IN (0,1)),
        target_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT {ts},
        PRIMARY KEY (case_study_id, target_key),
        FOREIGN KEY (case_study_id) REFERENCES seller_case_studies(case_study_id)
    );
    CREATE INDEX IF NOT EXISTS idx_seller_case_studies_org ON seller_case_studies(organisation_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_seller_case_studies_status ON seller_case_studies(workflow_status, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_seller_case_targets_audit ON seller_case_study_targets(audit_key, validation_status);
    """)


def _pharmaceutical_memory_schema(conn) -> None:
    """Evidence-derived entities, relationships and append-only observations."""
    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS memory_entities (
        entity_id TEXT PRIMARY KEY,
        entity_type TEXT NOT NULL,
        canonical_key TEXT NOT NULL,
        display_name TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_seen_at TEXT NOT NULL DEFAULT {ts},
        UNIQUE(entity_type, canonical_key)
    );
    CREATE TABLE IF NOT EXISTS memory_relationships (
        relationship_id TEXT PRIMARY KEY,
        subject_entity_id TEXT NOT NULL,
        relationship_type TEXT NOT NULL,
        object_entity_id TEXT NOT NULL,
        stable_lead_id TEXT NOT NULL,
        source_type TEXT,
        source_id TEXT,
        evidence_url TEXT,
        evidence_status TEXT NOT NULL DEFAULT 'requires validation',
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_seen_at TEXT NOT NULL DEFAULT {ts},
        UNIQUE(subject_entity_id, relationship_type, object_entity_id, stable_lead_id),
        FOREIGN KEY (subject_entity_id) REFERENCES memory_entities(entity_id),
        FOREIGN KEY (object_entity_id) REFERENCES memory_entities(entity_id)
    );
    CREATE TABLE IF NOT EXISTS memory_observations (
        id {ident},
        stable_lead_id TEXT NOT NULL,
        observation_hash TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        UNIQUE(stable_lead_id, observation_hash)
    );
    CREATE INDEX IF NOT EXISTS idx_memory_entities_type ON memory_entities(entity_type, display_name);
    CREATE INDEX IF NOT EXISTS idx_memory_relationship_subject ON memory_relationships(subject_entity_id, relationship_type);
    CREATE INDEX IF NOT EXISTS idx_memory_relationship_object ON memory_relationships(object_entity_id, relationship_type);
    CREATE INDEX IF NOT EXISTS idx_memory_relationship_lead ON memory_relationships(stable_lead_id);
    CREATE INDEX IF NOT EXISTS idx_memory_observation_lead ON memory_observations(stable_lead_id, observed_at DESC);
    """)


def _account_intelligence_schema(conn) -> None:
    """Evidence-governed organisation, contact-route and monitoring records."""
    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS account_organisations (
        organisation_id TEXT PRIMARY KEY,
        canonical_key TEXT NOT NULL UNIQUE,
        canonical_name TEXT NOT NULL,
        organisation_type TEXT NOT NULL DEFAULT 'commercial organisation',
        country TEXT,
        official_website_url TEXT,
        identity_status TEXT NOT NULL DEFAULT 'source-derived',
        source_count INTEGER NOT NULL DEFAULT 0,
        relationship_count INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT,
        next_review_at TEXT,
        attributes_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE TABLE IF NOT EXISTS account_aliases (
        alias_id TEXT PRIMARY KEY,
        organisation_id TEXT NOT NULL,
        alias_name TEXT NOT NULL,
        alias_key TEXT NOT NULL,
        source_type TEXT,
        source_id TEXT,
        evidence_url TEXT,
        verification_status TEXT NOT NULL DEFAULT 'source-derived',
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_seen_at TEXT NOT NULL DEFAULT {ts},
        FOREIGN KEY (organisation_id) REFERENCES account_organisations(organisation_id)
    );
    CREATE TABLE IF NOT EXISTS account_relationships (
        relationship_id TEXT PRIMARY KEY,
        organisation_id TEXT NOT NULL,
        relationship_type TEXT NOT NULL,
        object_type TEXT NOT NULL,
        object_name TEXT NOT NULL,
        object_key TEXT NOT NULL,
        stable_lead_id TEXT,
        source_type TEXT,
        source_id TEXT,
        evidence_url TEXT,
        evidence_status TEXT NOT NULL DEFAULT 'requires validation',
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_seen_at TEXT NOT NULL DEFAULT {ts},
        FOREIGN KEY (organisation_id) REFERENCES account_organisations(organisation_id)
    );
    CREATE TABLE IF NOT EXISTS account_contact_routes (
        route_id TEXT PRIMARY KEY,
        organisation_id TEXT NOT NULL,
        contact_function TEXT NOT NULL,
        product_scope TEXT,
        signal_scope TEXT,
        rationale TEXT NOT NULL,
        stable_lead_id TEXT,
        source_type TEXT,
        source_id TEXT,
        evidence_url TEXT,
        route_status TEXT NOT NULL DEFAULT 'function inferred from evidence',
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        FOREIGN KEY (organisation_id) REFERENCES account_organisations(organisation_id)
    );
    CREATE TABLE IF NOT EXISTS account_contacts (
        contact_id TEXT PRIMARY KEY,
        organisation_id TEXT NOT NULL,
        person_name TEXT NOT NULL,
        job_title TEXT,
        contact_function TEXT,
        email TEXT,
        phone TEXT,
        product_scope TEXT,
        source_type TEXT,
        source_id TEXT,
        evidence_url TEXT NOT NULL,
        verification_status TEXT NOT NULL,
        confidence_note TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        FOREIGN KEY (organisation_id) REFERENCES account_organisations(organisation_id)
    );
    CREATE TABLE IF NOT EXISTS account_contact_observations (
        id {ident},
        contact_id TEXT NOT NULL,
        observation_hash TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        UNIQUE(contact_id, observation_hash),
        FOREIGN KEY (contact_id) REFERENCES account_contacts(contact_id)
    );
    CREATE TABLE IF NOT EXISTS account_organisation_observations (
        id {ident},
        organisation_id TEXT NOT NULL,
        observation_hash TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        UNIQUE(organisation_id, observation_hash),
        FOREIGN KEY (organisation_id) REFERENCES account_organisations(organisation_id)
    );
    CREATE TABLE IF NOT EXISTS account_monitor_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        status TEXT NOT NULL,
        organisations_seen INTEGER NOT NULL DEFAULT 0,
        organisations_changed INTEGER NOT NULL DEFAULT 0,
        contacts_seen INTEGER NOT NULL DEFAULT 0,
        contacts_changed INTEGER NOT NULL DEFAULT 0,
        contacts_due_review INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE INDEX IF NOT EXISTS idx_account_org_name ON account_organisations(canonical_name);
    CREATE INDEX IF NOT EXISTS idx_account_alias_key ON account_aliases(alias_key);
    CREATE INDEX IF NOT EXISTS idx_account_relationship_org ON account_relationships(organisation_id, relationship_type);
    CREATE INDEX IF NOT EXISTS idx_account_route_org ON account_contact_routes(organisation_id, contact_function);
    CREATE INDEX IF NOT EXISTS idx_account_contact_org ON account_contacts(organisation_id, active, next_review_at);
    CREATE INDEX IF NOT EXISTS idx_account_monitor_completed ON account_monitor_runs(completed_at DESC);
    """)


def _patent_lifecycle_schema(conn) -> None:
    """Phase 9 evidence-governed FDA patent and exclusivity lifecycle model."""
    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS lifecycle_products (
        lifecycle_id TEXT PRIMARY KEY,
        application_number TEXT NOT NULL,
        product_number TEXT NOT NULL,
        trade_name TEXT NOT NULL,
        ingredient TEXT,
        application_holder TEXT,
        application_type TEXT,
        dosage_form_route TEXT,
        strength TEXT,
        approval_date TEXT,
        reference_listed_drug TEXT,
        reference_standard TEXT,
        therapeutic_equivalence_code TEXT,
        market_category TEXT,
        dataset_mode TEXT,
        official_source_url TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        lifecycle_status TEXT NOT NULL,
        next_expiry_date TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        UNIQUE(application_number, product_number)
    );
    CREATE TABLE IF NOT EXISTS lifecycle_patents (
        lifecycle_patent_id TEXT PRIMARY KEY,
        lifecycle_id TEXT NOT NULL,
        patent_number TEXT NOT NULL,
        expiry_date TEXT,
        drug_substance_flag TEXT,
        drug_product_flag TEXT,
        use_code TEXT,
        delist_requested TEXT,
        submission_date TEXT,
        application_holder_context TEXT,
        ownership_status TEXT NOT NULL,
        family_status TEXT NOT NULL,
        family_id TEXT,
        official_source_url TEXT NOT NULL,
        family_lookup_url TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        UNIQUE(lifecycle_id, patent_number, use_code),
        FOREIGN KEY (lifecycle_id) REFERENCES lifecycle_products(lifecycle_id)
    );
    CREATE TABLE IF NOT EXISTS lifecycle_exclusivities (
        lifecycle_exclusivity_id TEXT PRIMARY KEY,
        lifecycle_id TEXT NOT NULL,
        exclusivity_code TEXT NOT NULL,
        expiry_date TEXT,
        official_source_url TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        UNIQUE(lifecycle_id, exclusivity_code, expiry_date),
        FOREIGN KEY (lifecycle_id) REFERENCES lifecycle_products(lifecycle_id)
    );
    CREATE TABLE IF NOT EXISTS lifecycle_observations (
        id {ident},
        lifecycle_id TEXT NOT NULL,
        observation_hash TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        UNIQUE(lifecycle_id, observation_hash),
        FOREIGN KEY (lifecycle_id) REFERENCES lifecycle_products(lifecycle_id)
    );
    CREATE TABLE IF NOT EXISTS lifecycle_monitor_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        status TEXT NOT NULL,
        products_seen INTEGER NOT NULL DEFAULT 0,
        products_changed INTEGER NOT NULL DEFAULT 0,
        patents_seen INTEGER NOT NULL DEFAULT 0,
        exclusivities_seen INTEGER NOT NULL DEFAULT 0,
        family_resolution_required INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE INDEX IF NOT EXISTS idx_lifecycle_product_name ON lifecycle_products(trade_name, ingredient);
    CREATE INDEX IF NOT EXISTS idx_lifecycle_holder ON lifecycle_products(application_holder);
    CREATE INDEX IF NOT EXISTS idx_lifecycle_expiry ON lifecycle_products(next_expiry_date, lifecycle_status);
    CREATE INDEX IF NOT EXISTS idx_lifecycle_patent_number ON lifecycle_patents(patent_number);
    CREATE INDEX IF NOT EXISTS idx_lifecycle_patent_expiry ON lifecycle_patents(expiry_date);
    CREATE INDEX IF NOT EXISTS idx_lifecycle_exclusivity_expiry ON lifecycle_exclusivities(expiry_date);
    """)


MIGRATIONS = (
    Migration(1, "checkpoint_6a_core_schema", _core_schema),
    Migration(2, "checkpoint_6b_audit_schema", _audit_schema),
    Migration(3, "checkpoint_6c_import_and_indexes", _infrastructure_schema),
    Migration(4, "legacy_additive_columns", _additive_legacy_columns),
    Migration(5, "checkpoint_6c1_scheduler_schema", _scheduler_schema),
    Migration(6, "checkpoint_6db_administration_schema", _administration_schema),
    Migration(7, "checkpoint_7a_seller_case_study_schema", _seller_case_study_schema),
    Migration(8, "phase_7_pharmaceutical_memory_schema", _pharmaceutical_memory_schema),
    Migration(9, "checkpoint_8_3_account_intelligence_schema", _account_intelligence_schema),
    Migration(10, "phase_9_patent_lifecycle_schema", _patent_lifecycle_schema),
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
