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


def _research_innovation_schema(conn) -> None:
    """Phase 10 evidence-governed research, publication and collaboration graph."""
    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS research_organisations (
        research_organisation_id TEXT PRIMARY KEY,
        canonical_name TEXT NOT NULL,
        organisation_type TEXT,
        country_code TEXT,
        ror_id TEXT,
        openalex_id TEXT,
        official_url TEXT,
        identity_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE TABLE IF NOT EXISTS research_publications (
        research_publication_id TEXT PRIMARY KEY,
        canonical_key TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        doi TEXT,
        pmid TEXT,
        pmcid TEXT,
        openalex_id TEXT,
        journal TEXT,
        publication_type TEXT,
        publication_date TEXT,
        publication_year TEXT,
        abstract_text TEXT,
        citation_count INTEGER NOT NULL DEFAULT 0,
        open_access INTEGER NOT NULL DEFAULT 0,
        sources_json TEXT NOT NULL DEFAULT '[]',
        evidence_urls_json TEXT NOT NULL DEFAULT '[]',
        evidence_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE TABLE IF NOT EXISTS research_authors (
        research_author_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        orcid TEXT,
        openalex_id TEXT,
        profile_url TEXT,
        identity_status TEXT NOT NULL,
        current_role_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE TABLE IF NOT EXISTS research_publication_authors (
        research_publication_id TEXT NOT NULL,
        research_author_id TEXT NOT NULL,
        affiliation_text TEXT,
        research_organisation_id TEXT,
        evidence_url TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        PRIMARY KEY (research_publication_id, research_author_id, affiliation_text),
        FOREIGN KEY (research_publication_id) REFERENCES research_publications(research_publication_id),
        FOREIGN KEY (research_author_id) REFERENCES research_authors(research_author_id),
        FOREIGN KEY (research_organisation_id) REFERENCES research_organisations(research_organisation_id)
    );
    CREATE TABLE IF NOT EXISTS research_organisation_publications (
        research_organisation_id TEXT NOT NULL,
        research_publication_id TEXT NOT NULL,
        affiliation_evidence TEXT,
        evidence_url TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        PRIMARY KEY (research_organisation_id, research_publication_id),
        FOREIGN KEY (research_organisation_id) REFERENCES research_organisations(research_organisation_id),
        FOREIGN KEY (research_publication_id) REFERENCES research_publications(research_publication_id)
    );
    CREATE TABLE IF NOT EXISTS research_partnerships (
        research_partnership_id TEXT PRIMARY KEY,
        party_a_name TEXT NOT NULL,
        party_b_name TEXT NOT NULL,
        party_a_organisation_id TEXT,
        party_b_organisation_id TEXT,
        partnership_type TEXT NOT NULL,
        programme_name TEXT,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        evidence_url TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        formal_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE TABLE IF NOT EXISTS research_technologies (
        research_technology_id TEXT PRIMARY KEY,
        research_organisation_id TEXT,
        title TEXT NOT NULL,
        summary TEXT,
        technology_category TEXT,
        licensing_status TEXT NOT NULL,
        transfer_contact TEXT,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        evidence_url TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        FOREIGN KEY (research_organisation_id) REFERENCES research_organisations(research_organisation_id)
    );
    CREATE TABLE IF NOT EXISTS research_organisation_observations (
        id {ident},
        research_organisation_id TEXT NOT NULL,
        observation_hash TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        UNIQUE(research_organisation_id, observation_hash),
        FOREIGN KEY (research_organisation_id) REFERENCES research_organisations(research_organisation_id)
    );
    CREATE TABLE IF NOT EXISTS research_monitor_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        status TEXT NOT NULL,
        organisations_seen INTEGER NOT NULL DEFAULT 0,
        organisations_changed INTEGER NOT NULL DEFAULT 0,
        publications_seen INTEGER NOT NULL DEFAULT 0,
        authors_seen INTEGER NOT NULL DEFAULT 0,
        partnerships_seen INTEGER NOT NULL DEFAULT 0,
        technologies_seen INTEGER NOT NULL DEFAULT 0,
        transfer_resolution_required INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE INDEX IF NOT EXISTS idx_research_org_name ON research_organisations(canonical_name);
    CREATE INDEX IF NOT EXISTS idx_research_org_ror ON research_organisations(ror_id);
    CREATE INDEX IF NOT EXISTS idx_research_pub_doi ON research_publications(doi);
    CREATE INDEX IF NOT EXISTS idx_research_pub_year ON research_publications(publication_year);
    CREATE INDEX IF NOT EXISTS idx_research_author_orcid ON research_authors(orcid);
    CREATE INDEX IF NOT EXISTS idx_research_parties ON research_partnerships(party_a_name, party_b_name);
    CREATE INDEX IF NOT EXISTS idx_research_technology_org ON research_technologies(research_organisation_id);
    """)


def _deals_funding_schema(conn) -> None:
    """Phase 11 governed licensing, transaction, financing and grant evidence."""
    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS commercial_events (
        commercial_event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        evidence_class TEXT NOT NULL,
        announcement_date TEXT,
        event_status TEXT,
        party_a_name TEXT,
        party_b_name TEXT,
        subject_name TEXT,
        value_amount REAL,
        currency TEXT,
        value_text TEXT,
        geography TEXT,
        source_type TEXT NOT NULL,
        source_name TEXT NOT NULL,
        source_id TEXT NOT NULL,
        evidence_url TEXT NOT NULL,
        primary_source_verified INTEGER NOT NULL DEFAULT 0,
        evidence_status TEXT NOT NULL,
        validation_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        UNIQUE(source_type, source_id, event_type)
    );
    CREATE TABLE IF NOT EXISTS funding_awards (
        funding_award_id TEXT PRIMARY KEY,
        funding_type TEXT NOT NULL,
        funder_name TEXT,
        recipient_name TEXT,
        award_id TEXT,
        programme_name TEXT,
        linked_publication_id TEXT,
        amount_value REAL,
        currency TEXT,
        value_text TEXT,
        award_date TEXT,
        source_type TEXT NOT NULL,
        source_name TEXT NOT NULL,
        source_id TEXT NOT NULL,
        evidence_url TEXT NOT NULL,
        primary_source_verified INTEGER NOT NULL DEFAULT 0,
        evidence_status TEXT NOT NULL,
        validation_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE TABLE IF NOT EXISTS commercial_event_observations (
        id {ident},
        commercial_event_id TEXT NOT NULL,
        observation_hash TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        UNIQUE(commercial_event_id, observation_hash),
        FOREIGN KEY (commercial_event_id) REFERENCES commercial_events(commercial_event_id)
    );
    CREATE TABLE IF NOT EXISTS commercial_monitor_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        status TEXT NOT NULL,
        events_seen INTEGER NOT NULL DEFAULT 0,
        events_changed INTEGER NOT NULL DEFAULT 0,
        licensing_seen INTEGER NOT NULL DEFAULT 0,
        mergers_acquisitions_seen INTEGER NOT NULL DEFAULT 0,
        partnerships_seen INTEGER NOT NULL DEFAULT 0,
        financing_seen INTEGER NOT NULL DEFAULT 0,
        grants_seen INTEGER NOT NULL DEFAULT 0,
        primary_verification_required INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE INDEX IF NOT EXISTS idx_commercial_event_type ON commercial_events(event_type, announcement_date);
    CREATE INDEX IF NOT EXISTS idx_commercial_party_a ON commercial_events(party_a_name);
    CREATE INDEX IF NOT EXISTS idx_commercial_party_b ON commercial_events(party_b_name);
    CREATE INDEX IF NOT EXISTS idx_commercial_verified ON commercial_events(primary_source_verified, validation_status);
    CREATE INDEX IF NOT EXISTS idx_funding_funder ON funding_awards(funder_name);
    CREATE INDEX IF NOT EXISTS idx_funding_recipient ON funding_awards(recipient_name);
    CREATE INDEX IF NOT EXISTS idx_funding_award ON funding_awards(award_id);
    """)


def _customer_product_schema(conn) -> None:
    """Phase 12 tenant-scoped customer workflows and governed delivery."""
    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS customer_saved_lists (
        saved_list_id TEXT PRIMARY KEY,
        scope_key TEXT NOT NULL,
        organisation_id TEXT,
        workspace_id TEXT,
        name TEXT NOT NULL,
        description TEXT,
        visibility TEXT NOT NULL DEFAULT 'workspace',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT {ts},
        updated_at TEXT NOT NULL DEFAULT {ts},
        archived INTEGER NOT NULL DEFAULT 0,
        UNIQUE(scope_key, name)
    );
    CREATE TABLE IF NOT EXISTS customer_saved_items (
        saved_item_id TEXT PRIMARY KEY,
        saved_list_id TEXT NOT NULL,
        scope_key TEXT NOT NULL,
        record_type TEXT NOT NULL,
        record_id TEXT NOT NULL,
        record_label TEXT NOT NULL,
        source_url TEXT,
        evidence_status TEXT NOT NULL DEFAULT 'internal intelligence',
        note TEXT,
        added_by TEXT NOT NULL,
        added_at TEXT NOT NULL DEFAULT {ts},
        snapshot_json TEXT NOT NULL DEFAULT '{{}}',
        UNIQUE(saved_list_id, record_type, record_id),
        FOREIGN KEY (saved_list_id) REFERENCES customer_saved_lists(saved_list_id)
    );
    CREATE TABLE IF NOT EXISTS customer_alert_rules (
        alert_rule_id TEXT PRIMARY KEY,
        scope_key TEXT NOT NULL,
        saved_list_id TEXT,
        name TEXT NOT NULL,
        record_type TEXT NOT NULL,
        search_term TEXT,
        source_filter TEXT,
        region_filter TEXT,
        severity TEXT NOT NULL DEFAULT 'medium',
        cadence TEXT NOT NULL DEFAULT 'daily',
        enabled INTEGER NOT NULL DEFAULT 1,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT {ts},
        last_evaluated_at TEXT,
        UNIQUE(scope_key, name),
        FOREIGN KEY (saved_list_id) REFERENCES customer_saved_lists(saved_list_id)
    );
    CREATE TABLE IF NOT EXISTS customer_alert_events (
        alert_event_id TEXT PRIMARY KEY,
        scope_key TEXT NOT NULL,
        alert_rule_id TEXT NOT NULL,
        record_type TEXT NOT NULL,
        record_id TEXT NOT NULL,
        title TEXT NOT NULL,
        summary TEXT NOT NULL,
        severity TEXT NOT NULL,
        source_url TEXT,
        evidence_status TEXT NOT NULL,
        event_fingerprint TEXT NOT NULL,
        detected_at TEXT NOT NULL DEFAULT {ts},
        read_at TEXT,
        dismissed_at TEXT,
        UNIQUE(alert_rule_id, event_fingerprint),
        FOREIGN KEY (alert_rule_id) REFERENCES customer_alert_rules(alert_rule_id)
    );
    CREATE TABLE IF NOT EXISTS customer_exports (
        export_id TEXT PRIMARY KEY,
        scope_key TEXT NOT NULL,
        saved_list_id TEXT NOT NULL,
        export_kind TEXT NOT NULL,
        export_format TEXT NOT NULL,
        status TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        requested_role TEXT NOT NULL,
        record_count INTEGER NOT NULL DEFAULT 0,
        excluded_count INTEGER NOT NULL DEFAULT 0,
        policy_snapshot TEXT NOT NULL,
        checksum TEXT,
        created_at TEXT NOT NULL DEFAULT {ts},
        metadata_json TEXT NOT NULL DEFAULT '{{}}',
        FOREIGN KEY (saved_list_id) REFERENCES customer_saved_lists(saved_list_id)
    );
    CREATE TABLE IF NOT EXISTS customer_activity_events (
        id {ident},
        scope_key TEXT NOT NULL,
        actor_name TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        event_type TEXT NOT NULL,
        safe_summary TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT {ts},
        metadata_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE INDEX IF NOT EXISTS idx_customer_lists_scope ON customer_saved_lists(scope_key, archived, updated_at);
    CREATE INDEX IF NOT EXISTS idx_customer_items_list ON customer_saved_items(saved_list_id, added_at);
    CREATE INDEX IF NOT EXISTS idx_customer_rules_scope ON customer_alert_rules(scope_key, enabled);
    CREATE INDEX IF NOT EXISTS idx_customer_alerts_scope ON customer_alert_events(scope_key, dismissed_at, detected_at);
    CREATE INDEX IF NOT EXISTS idx_customer_exports_scope ON customer_exports(scope_key, created_at);
    CREATE INDEX IF NOT EXISTS idx_customer_activity_scope ON customer_activity_events(scope_key, created_at);
    """)


def _global_patent_schema(conn) -> None:
    """Phase 9 global patent documents, parties, families and legal events."""
    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS patent_documents (
        patent_document_id TEXT PRIMARY KEY,
        publication_number TEXT NOT NULL,
        application_number TEXT,
        jurisdiction TEXT NOT NULL,
        document_kind TEXT,
        title TEXT,
        abstract_text TEXT,
        filing_date TEXT,
        publication_date TEXT,
        grant_date TEXT,
        family_id TEXT,
        family_status TEXT NOT NULL,
        legal_status_summary TEXT,
        legal_status_as_of TEXT,
        source_name TEXT NOT NULL,
        source_authority TEXT NOT NULL,
        official_source_url TEXT NOT NULL,
        google_patents_url TEXT NOT NULL,
        uk_register_url TEXT,
        evidence_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        UNIQUE(jurisdiction, publication_number)
    );
    CREATE TABLE IF NOT EXISTS patent_parties (
        patent_party_id TEXT PRIMARY KEY,
        patent_document_id TEXT NOT NULL,
        party_type TEXT NOT NULL,
        party_name TEXT NOT NULL,
        country_code TEXT,
        sequence_number TEXT,
        evidence_status TEXT NOT NULL,
        official_source_url TEXT NOT NULL,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        UNIQUE(patent_document_id, party_type, party_name),
        FOREIGN KEY (patent_document_id) REFERENCES patent_documents(patent_document_id)
    );
    CREATE TABLE IF NOT EXISTS patent_family_members (
        patent_family_member_id TEXT PRIMARY KEY,
        family_id TEXT NOT NULL,
        patent_document_id TEXT,
        publication_number TEXT NOT NULL,
        jurisdiction TEXT,
        relationship_type TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        official_source_url TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        UNIQUE(family_id, publication_number),
        FOREIGN KEY (patent_document_id) REFERENCES patent_documents(patent_document_id)
    );
    CREATE TABLE IF NOT EXISTS patent_legal_events (
        patent_legal_event_id TEXT PRIMARY KEY,
        patent_document_id TEXT NOT NULL,
        event_code TEXT,
        event_date TEXT,
        event_text TEXT NOT NULL,
        authority TEXT,
        evidence_status TEXT NOT NULL,
        official_source_url TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        UNIQUE(patent_document_id, event_code, event_date, event_text),
        FOREIGN KEY (patent_document_id) REFERENCES patent_documents(patent_document_id)
    );
    CREATE TABLE IF NOT EXISTS patent_product_links (
        patent_product_link_id TEXT PRIMARY KEY,
        patent_document_id TEXT NOT NULL,
        lifecycle_id TEXT NOT NULL,
        link_basis TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        official_source_url TEXT NOT NULL,
        verified INTEGER NOT NULL DEFAULT 0,
        observed_at TEXT NOT NULL,
        UNIQUE(patent_document_id, lifecycle_id, link_basis),
        FOREIGN KEY (patent_document_id) REFERENCES patent_documents(patent_document_id),
        FOREIGN KEY (lifecycle_id) REFERENCES lifecycle_products(lifecycle_id)
    );
    CREATE TABLE IF NOT EXISTS patent_global_monitor_runs (
        run_id TEXT PRIMARY KEY,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        status TEXT NOT NULL,
        documents_seen INTEGER NOT NULL DEFAULT 0,
        eu_documents_seen INTEGER NOT NULL DEFAULT 0,
        uk_documents_seen INTEGER NOT NULL DEFAULT 0,
        parties_seen INTEGER NOT NULL DEFAULT 0,
        families_seen INTEGER NOT NULL DEFAULT 0,
        legal_events_seen INTEGER NOT NULL DEFAULT 0,
        metadata_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE INDEX IF NOT EXISTS idx_patent_doc_jurisdiction ON patent_documents(jurisdiction, publication_date);
    CREATE INDEX IF NOT EXISTS idx_patent_doc_publication ON patent_documents(publication_number);
    CREATE INDEX IF NOT EXISTS idx_patent_doc_family ON patent_documents(family_id);
    CREATE INDEX IF NOT EXISTS idx_patent_party_name ON patent_parties(party_name, party_type);
    CREATE INDEX IF NOT EXISTS idx_patent_legal_event_date ON patent_legal_events(event_date);
    CREATE INDEX IF NOT EXISTS idx_patent_product_lifecycle ON patent_product_links(lifecycle_id);
    """)


def _canonical_patent_foundation_schema(conn) -> None:
    """Additive canonical patent identity, provenance and verification fields."""
    additions = {
        "patent_documents": {
            "normalized_publication_number": "TEXT",
            "normalized_application_number": "TEXT",
            "publication_kind": "TEXT",
            "legal_status_code": "TEXT",
            "legal_status_label": "TEXT",
            "legal_status_basis": "TEXT",
            "status_as_of_date": "TEXT",
            "expiry_date": "TEXT",
            "expiry_basis": "TEXT",
            "expiry_status": "TEXT",
            "expiry_as_of_date": "TEXT",
            "last_source_refresh_id": "TEXT",
        },
        "patent_parties": {
            "normalized_party_name": "TEXT",
            "party_identity_key": "TEXT",
            "party_identity_basis": "TEXT",
        },
        "patent_product_links": {
            "evidence_basis": "TEXT",
            "evidence_source_record_id": "TEXT",
            "verification_status": "TEXT",
            "verified_at": "TEXT",
            "verification_basis": "TEXT",
        },
    }
    for table, columns in additions.items():
        if not conn.has_table(table):
            continue
        existing = conn.columns(table)
        for column, spec in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")

    ts = _timestamp_default(conn)
    ident = _identity(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS patent_families (
        family_id TEXT PRIMARY KEY,
        canonical_family_id TEXT NOT NULL UNIQUE,
        family_status TEXT NOT NULL,
        source_authority TEXT NOT NULL,
        official_source_url TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}'
    );
    CREATE TABLE IF NOT EXISTS patent_document_sources (
        patent_document_source_id TEXT PRIMARY KEY,
        patent_document_id TEXT NOT NULL,
        source_system TEXT NOT NULL,
        source_record_id TEXT NOT NULL,
        source_authority TEXT NOT NULL,
        official_source_url TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        last_source_refresh_id TEXT,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        UNIQUE(patent_document_id, source_system, source_record_id),
        FOREIGN KEY (patent_document_id) REFERENCES patent_documents(patent_document_id)
    );
    CREATE INDEX IF NOT EXISTS idx_patent_family_canonical ON patent_families(canonical_family_id);
    CREATE INDEX IF NOT EXISTS idx_patent_source_document ON patent_document_sources(patent_document_id, last_verified_at);
    CREATE INDEX IF NOT EXISTS idx_patent_source_record ON patent_document_sources(source_system, source_record_id);
    CREATE INDEX IF NOT EXISTS idx_patent_doc_normalized_application ON patent_documents(normalized_application_number);
    CREATE INDEX IF NOT EXISTS idx_patent_doc_legal_status ON patent_documents(legal_status_code, legal_status_label);
    CREATE INDEX IF NOT EXISTS idx_patent_doc_expiry ON patent_documents(expiry_date, expiry_status);
    CREATE INDEX IF NOT EXISTS idx_patent_doc_refresh ON patent_documents(last_source_refresh_id);
    CREATE INDEX IF NOT EXISTS idx_patent_party_identity ON patent_parties(party_identity_key, party_type);
    CREATE INDEX IF NOT EXISTS idx_patent_link_verification ON patent_product_links(verification_status, verified_at);
    """)

    def normalise(value: Any) -> str:
        return " ".join(str(value or "").casefold().split())

    def canonical(value: Any) -> str:
        return "".join(ch for ch in str(value or "").upper() if ch.isalnum())

    if conn.has_table("patent_documents"):
        rows = conn.execute(
            "SELECT patent_document_id,publication_number,application_number,document_kind,legal_status_summary,"
            "legal_status_as_of,source_name,source_authority,official_source_url,evidence_status,first_seen_at,"
            "last_verified_at,next_review_at,last_source_refresh_id FROM patent_documents"
        ).fetchall()
        for row in rows:
            publication = canonical(row.get("publication_number"))
            application = canonical(row.get("application_number"))
            label = str(row.get("legal_status_summary") or "Legal status not established")
            basis = "Source-reported label; no legal conclusion inferred"
            source_refresh = row.get("last_source_refresh_id")
            conn.execute(
                "UPDATE patent_documents SET normalized_publication_number=?, normalized_application_number=?, "
                "publication_kind=?, legal_status_code=COALESCE(legal_status_code,''), legal_status_label=?, "
                "legal_status_basis=COALESCE(legal_status_basis,?), status_as_of_date=COALESCE(status_as_of_date,?), "
                "expiry_basis=COALESCE(expiry_basis,''), expiry_status=COALESCE(expiry_status,''), "
                "expiry_as_of_date=COALESCE(expiry_as_of_date,''), last_source_refresh_id=COALESCE(last_source_refresh_id,?) "
                "WHERE patent_document_id=?",
                (publication, application, row.get("document_kind") or "", label, basis, row.get("legal_status_as_of"),
                 source_refresh, row["patent_document_id"]),
            )
            source_id = str(row["patent_document_id"])
            conn.execute(
                "INSERT INTO patent_document_sources "
                "(patent_document_source_id,patent_document_id,source_system,source_record_id,source_authority,"
                "official_source_url,evidence_status,first_seen_at,last_verified_at,next_review_at,last_source_refresh_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(patent_document_id,source_system,source_record_id) DO NOTHING",
                (f"patentsource_{row['patent_document_id']}", row["patent_document_id"], row.get("source_name") or "",
                 source_id, row.get("source_authority") or "", row.get("official_source_url") or "",
                 row.get("evidence_status") or "", row.get("first_seen_at") or row.get("last_verified_at") or "",
                 row.get("last_verified_at") or "", row.get("next_review_at") or "", source_refresh),
            )

    if conn.has_table("patent_parties"):
        rows = conn.execute("SELECT patent_party_id,party_name FROM patent_parties").fetchall()
        for row in rows:
            normalized = normalise(row.get("party_name"))
            conn.execute(
                "UPDATE patent_parties SET normalized_party_name=?, party_identity_key=?, "
                "party_identity_basis=COALESCE(party_identity_basis,?) WHERE patent_party_id=?",
                (normalized, normalized, "Name normalization only; identity and ownership not verified", row["patent_party_id"]),
            )

    family_rows = conn.execute(
        "SELECT family_id, family_status, source_authority, official_source_url, evidence_status, first_seen_at, "
        "last_verified_at, next_review_at FROM patent_documents WHERE COALESCE(family_id,'')<>'' "
        "UNION SELECT family_id, '', '', official_source_url, evidence_status, observed_at, observed_at, observed_at "
        "FROM patent_family_members WHERE COALESCE(family_id,'')<>''"
    ).fetchall()
    for row in family_rows:
        family_id = str(row.get("family_id") or "")
        if not family_id:
            continue
        conn.execute(
            "INSERT INTO patent_families "
            "(family_id,canonical_family_id,family_status,source_authority,official_source_url,evidence_status,"
            "first_seen_at,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(family_id) DO UPDATE SET family_status=CASE WHEN excluded.family_status<>'' THEN excluded.family_status ELSE patent_families.family_status END, "
            "source_authority=CASE WHEN excluded.source_authority<>'' THEN excluded.source_authority ELSE patent_families.source_authority END, "
            "official_source_url=CASE WHEN excluded.official_source_url<>'' THEN excluded.official_source_url ELSE patent_families.official_source_url END, "
            "evidence_status=CASE WHEN excluded.evidence_status<>'' THEN excluded.evidence_status ELSE patent_families.evidence_status END, "
            "last_verified_at=excluded.last_verified_at,next_review_at=excluded.next_review_at",
            (family_id, family_id, row.get("family_status") or "Family evidence retained", row.get("source_authority") or "",
             row.get("official_source_url") or "", row.get("evidence_status") or "Family evidence retained",
             row.get("first_seen_at") or row.get("last_verified_at") or "", row.get("last_verified_at") or "",
             row.get("next_review_at") or row.get("last_verified_at") or ""),
        )

    if conn.has_table("patent_product_links"):
        conn.execute(
            "UPDATE patent_product_links SET evidence_basis=COALESCE(evidence_basis,link_basis), "
            "verification_status=COALESCE(verification_status,CASE WHEN verified=1 THEN 'verified' ELSE 'unverified' END), "
            "verified_at=COALESCE(verified_at,CASE WHEN verified=1 THEN observed_at ELSE NULL END), "
            "verification_basis=COALESCE(verification_basis,evidence_status)"
        )
    collisions: dict[tuple[str, str], list[str]] = {}
    for row in conn.execute(
        "SELECT patent_document_id,jurisdiction,normalized_publication_number FROM patent_documents "
        "WHERE COALESCE(normalized_publication_number,'')<>'' ORDER BY jurisdiction,normalized_publication_number,patent_document_id"
    ).fetchall():
        key = (str(row.get("jurisdiction") or ""), str(row.get("normalized_publication_number") or ""))
        collisions.setdefault(key, []).append(str(row["patent_document_id"]))
    conflicts = {key: ids for key, ids in collisions.items() if len(ids) > 1}
    if conflicts:
        details = "; ".join(
            f"{jurisdiction}/{publication}: document IDs {', '.join(ids)}"
            for (jurisdiction, publication), ids in conflicts.items()
        )
        raise RuntimeError(
            "Migration 15 refused to create the canonical patent identity index because "
            f"{len(conflicts)} canonical identity collision(s) exist: {details}"
        )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_patent_doc_normalized_identity "
        "ON patent_documents(jurisdiction, normalized_publication_number)"
    )


def _foundation_pr_a_schema(conn) -> None:
    """Additive, domain-neutral problem/solution evidence foundation."""
    ts = _timestamp_default(conn)
    conn.executescript(f"""
    CREATE TABLE IF NOT EXISTS intelligence_taxonomy_terms (
        term_id TEXT PRIMARY KEY,
        taxonomy_namespace TEXT NOT NULL CHECK (taxonomy_namespace IN ('problem_domain','solution_domain','solution_type')),
        term_kind TEXT NOT NULL CHECK (term_kind IN ('domain','category','sub_category','type')),
        parent_term_id TEXT,
        code TEXT NOT NULL,
        label TEXT NOT NULL,
        definition TEXT NOT NULL,
        scope_note TEXT NOT NULL DEFAULT '',
        version TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        UNIQUE(taxonomy_namespace, code),
        UNIQUE(taxonomy_namespace, label),
        FOREIGN KEY (parent_term_id) REFERENCES intelligence_taxonomy_terms(term_id)
    );
    CREATE TABLE IF NOT EXISTS pharmaceutical_problems (
        problem_id TEXT PRIMARY KEY,
        canonical_key TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        taxonomy_term_id TEXT NOT NULL,
        definition TEXT NOT NULL,
        identity_status TEXT NOT NULL CHECK (identity_status IN ('controlled','source-derived','requires-review')),
        evidence_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        FOREIGN KEY (taxonomy_term_id) REFERENCES intelligence_taxonomy_terms(term_id)
    );
    CREATE TABLE IF NOT EXISTS technology_solutions (
        technology_id TEXT PRIMARY KEY,
        canonical_key TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        taxonomy_term_id TEXT NOT NULL,
        solution_type_term_id TEXT NOT NULL,
        mechanism_summary TEXT NOT NULL DEFAULT '',
        scope_note TEXT NOT NULL DEFAULT '',
        maturity_status TEXT NOT NULL CHECK (maturity_status IN ('research','development','commercial','service-delivered','unknown')),
        identity_status TEXT NOT NULL CHECK (identity_status IN ('controlled','source-derived','requires-review')),
        evidence_status TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
        first_seen_at TEXT NOT NULL DEFAULT {ts},
        last_verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        FOREIGN KEY (taxonomy_term_id) REFERENCES intelligence_taxonomy_terms(term_id),
        FOREIGN KEY (solution_type_term_id) REFERENCES intelligence_taxonomy_terms(term_id)
    );
    CREATE TABLE IF NOT EXISTS technology_problem_relationships (
        relationship_id TEXT PRIMARY KEY,
        technology_id TEXT NOT NULL,
        problem_id TEXT NOT NULL,
        relationship_type TEXT NOT NULL CHECK (relationship_type IN ('addresses','may-address','supports','enables','diagnoses','mitigates','requires-review')),
        relationship_statement TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        evidence_url TEXT NOT NULL,
        evidence_title TEXT,
        evidence_excerpt TEXT,
        evidence_status TEXT NOT NULL,
        inference_status TEXT NOT NULL CHECK (inference_status IN ('reported','source-derived','inferred','human-verified','contradicted','requires-review')),
        confidence_score REAL NOT NULL CHECK (confidence_score >= 0.0 AND confidence_score <= 1.0),
        confidence_basis TEXT NOT NULL,
        verified_at TEXT NOT NULL,
        next_review_at TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
        attributes_json TEXT NOT NULL DEFAULT '{{}}',
        UNIQUE(technology_id, problem_id, relationship_type, source_type, source_id),
        FOREIGN KEY (technology_id) REFERENCES technology_solutions(technology_id),
        FOREIGN KEY (problem_id) REFERENCES pharmaceutical_problems(problem_id)
    );
    CREATE INDEX IF NOT EXISTS idx_foundation_taxonomy_parent ON intelligence_taxonomy_terms(taxonomy_namespace, parent_term_id);
    CREATE INDEX IF NOT EXISTS idx_foundation_problem_taxonomy ON pharmaceutical_problems(taxonomy_term_id, active);
    CREATE INDEX IF NOT EXISTS idx_foundation_solution_taxonomy ON technology_solutions(taxonomy_term_id, solution_type_term_id, active);
    CREATE INDEX IF NOT EXISTS idx_foundation_relationship_problem ON technology_problem_relationships(problem_id, active);
    CREATE INDEX IF NOT EXISTS idx_foundation_relationship_solution ON technology_problem_relationships(technology_id, active);
    CREATE INDEX IF NOT EXISTS idx_foundation_relationship_source ON technology_problem_relationships(source_type, source_id);
    """)

    # Representative, cross-domain seeds only. These are not an exhaustive
    # pharmaceutical taxonomy and do not create any product/provider records.
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    terms = [
        ("problem-domain-formulation", "problem_domain", "domain", None, "formulation-and-drug-delivery", "Formulation and drug delivery", "Problems involving formulation design, dosage form and drug delivery performance.", "", "1.0"),
        ("problem-domain-physchem", "problem_domain", "domain", None, "physicochemical-and-solid-state", "Physicochemical and solid-state", "Problems involving physicochemical properties, solid state and material behavior.", "", "1.0"),
        ("problem-domain-stability", "problem_domain", "domain", None, "stability-and-degradation", "Stability and degradation", "Problems involving degradation, instability and shelf-life performance.", "", "1.0"),
        ("problem-domain-manufacturing", "problem_domain", "domain", None, "manufacturing-process-development-and-scale-up", "Manufacturing, process development and scale-up", "Problems involving process design, reproducibility, manufacturability and scale-up.", "", "1.0"),
        ("problem-domain-analytical", "problem_domain", "domain", None, "analytical-and-quality-control", "Analytical and quality control", "Problems involving measurement, assay, specifications and quality-control capability.", "", "1.0"),
        ("problem-domain-packaging", "problem_domain", "domain", None, "packaging-and-container-closure", "Packaging and container closure", "Problems involving packaging systems, compatibility and container closure.", "", "1.0"),
        ("problem-domain-devices", "problem_domain", "domain", None, "devices-and-combination-products", "Devices and combination products", "Problems involving device integration, delivery devices and combination products.", "", "1.0"),
        ("problem-domain-regulatory", "problem_domain", "domain", None, "regulatory-and-lifecycle", "Regulatory and lifecycle", "Problems involving regulatory pathways, lifecycle management and post-approval change.", "", "1.0"),
        ("problem-domain-biologics", "problem_domain", "domain", None, "biologics-and-advanced-therapies", "Biologics and advanced therapies", "Problems involving biologics, cell and gene therapies and advanced modalities.", "", "1.0"),
        ("problem-domain-digital", "problem_domain", "domain", None, "digital-data-and-ai-enabled-pharmaceutical-problems", "Digital, data and AI-enabled pharmaceutical problems", "Problems involving pharmaceutical data, interoperability, automation and AI-enabled workflows.", "", "1.0"),
        ("solution-domain-formulation", "solution_domain", "domain", None, "formulation-technologies", "Formulation technologies", "Technologies and capabilities for formulation design and optimization.", "", "1.0"),
        ("solution-domain-delivery", "solution_domain", "domain", None, "drug-delivery-technologies", "Drug-delivery technologies", "Technologies and capabilities for delivery and release of pharmaceutical substances.", "", "1.0"),
        ("solution-domain-excipients", "solution_domain", "domain", None, "excipient-technologies", "Excipient technologies", "Excipient systems and enabling material technologies.", "", "1.0"),
        ("solution-domain-manufacturing", "solution_domain", "domain", None, "manufacturing-and-process-technologies", "Manufacturing and process technologies", "Manufacturing, process-development and scale-up technologies.", "", "1.0"),
        ("solution-domain-analytical", "solution_domain", "domain", None, "analytical-and-quality-control-technologies", "Analytical and quality-control technologies", "Analytical, testing and quality-control technologies.", "", "1.0"),
        ("solution-domain-packaging", "solution_domain", "domain", None, "packaging-technologies", "Packaging technologies", "Packaging, container-closure and protection technologies.", "", "1.0"),
        ("solution-domain-devices", "solution_domain", "domain", None, "device-and-combination-product-technologies", "Device and combination-product technologies", "Device and combination-product technologies.", "", "1.0"),
        ("solution-domain-regulatory", "solution_domain", "domain", None, "regulatory-and-lifecycle-services", "Regulatory and lifecycle services", "Services supporting regulatory strategy and lifecycle management.", "", "1.0"),
        ("solution-domain-biologics", "solution_domain", "domain", None, "biologics-and-advanced-therapy-technologies", "Biologics and advanced-therapy technologies", "Technologies for biologics and advanced therapies.", "", "1.0"),
        ("solution-domain-digital", "solution_domain", "domain", None, "digital-data-and-ai-enabled-capabilities", "Digital, data and AI-enabled capabilities", "Digital, data and AI-enabled pharmaceutical capabilities.", "", "1.0"),
        ("solution-domain-services", "solution_domain", "domain", None, "specialist-pharmaceutical-services", "Specialist pharmaceutical services", "Specialist scientific, technical and operational pharmaceutical services.", "", "1.0"),
        ("solution-type-technology", "solution_type", "type", None, "technology", "Technology", "A technical method, system or material approach.", "", "1.0"),
        ("solution-type-tool", "solution_type", "type", None, "tool", "Tool", "A software, laboratory or operational tool.", "", "1.0"),
        ("solution-type-service", "solution_type", "type", None, "service", "Service", "A delivered specialist service.", "", "1.0"),
        ("solution-type-platform", "solution_type", "type", None, "platform", "Platform", "A reusable technical or operational platform.", "", "1.0"),
        ("solution-type-process", "solution_type", "type", None, "process", "Process", "A defined process or workflow capability.", "", "1.0"),
        ("solution-type-capability", "solution_type", "type", None, "capability", "Capability", "A described organisational or technical capability.", "", "1.0"),
        ("problem-poor-solubility", "problem_domain", "sub_category", "problem-domain-physchem", "poor-solubility", "Poor solubility", "A solubility limitation affecting pharmaceutical development or performance.", "Representative child seed; not an exhaustive domain taxonomy.", "1.0"),
        ("problem-slow-dissolution", "problem_domain", "sub_category", "problem-domain-physchem", "slow-dissolution", "Slow dissolution", "A dissolution-rate limitation affecting pharmaceutical development or performance.", "Representative child seed; not an exhaustive domain taxonomy.", "1.0"),
        ("problem-scale-up-variability", "problem_domain", "sub_category", "problem-domain-manufacturing", "scale-up-variability", "Scale-up variability", "A reproducibility or process-performance problem during scale-up.", "Representative child seed; not an exhaustive domain taxonomy.", "1.0"),
        ("problem-assay-limitation", "problem_domain", "sub_category", "problem-domain-analytical", "assay-method-limitation", "Assay-method limitation", "A limitation in analytical measurement or quality-control capability.", "Representative child seed; not an exhaustive domain taxonomy.", "1.0"),
    ]
    for term_id, namespace, kind, parent, code, label, definition, scope_note, version in terms:
        conn.execute(
            "INSERT INTO intelligence_taxonomy_terms "
            "(term_id,taxonomy_namespace,term_kind,parent_term_id,code,label,definition,scope_note,version,active,first_seen_at,last_verified_at,next_review_at,attributes_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,'{}') "
            "ON CONFLICT(taxonomy_namespace,code) DO NOTHING",
            (term_id, namespace, kind, parent, code, label, definition, scope_note, version, now, now, now),
        )


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
    Migration(11, "phase_10_research_innovation_schema", _research_innovation_schema),
    Migration(12, "phase_11_deals_funding_schema", _deals_funding_schema),
    Migration(13, "phase_12_customer_product_schema", _customer_product_schema),
    Migration(14, "phase_9_global_patent_intelligence_schema", _global_patent_schema),
    Migration(15, "phase_9_canonical_patent_foundation_schema", _canonical_patent_foundation_schema),
    Migration(16, "foundation_pr_a_domain_neutral_intelligence_schema", _foundation_pr_a_schema),
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
