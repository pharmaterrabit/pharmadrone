from __future__ import annotations

import os
from uuid import uuid4

import pytest

from pharmadrone.pipeline import ai_bd_service
from pharmadrone.storage.migrations import _pharmadrone_ai_saas_schema
from pharmadrone_ai import repository


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not configured",
)
def test_real_postgresql_migration_21_auth_scope_and_rerun():
    from pharmadrone.storage.config import DatabaseConfig, normalize_postgres_url
    from pharmadrone.storage.database import open_connection

    conn = open_connection(
        DatabaseConfig(
            backend="postgresql",
            url=normalize_postgres_url(os.environ["TEST_DATABASE_URL"]),
            app_env="test",
        )
    )
    first = conn.ensure_migrations()
    assert first["schema_version"] >= 21
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=21"
    ).fetchone()["n"] == 1
    for table in (
        "saas_users",
        "saas_workspaces",
        "saas_workspace_memberships",
        "saas_conversations",
        "saas_messages",
        "saas_saved_leads",
        "saas_saved_reports",
        "saas_usage_events",
        "saas_subscriptions",
    ):
        assert conn.has_table(table)

    with conn.transaction():
        _pharmadrone_ai_saas_schema(conn)
    assert conn.ensure_migrations()["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=21"
    ).fetchone()["n"] == 1

    suffix = uuid4().hex
    principal = repository.register(
        conn,
        f"postgres-{suffix}@example.org",
        "correct horse battery",
        "PostgreSQL SaaS User",
        f"PostgreSQL Workspace {suffix}",
    )
    lead = {
        "lead_id": f"postgres-lead-{suffix}",
        "target_company": "PostgreSQL Pharma",
        "theme": "Poor solubility",
        "readiness_status": "Partial company evidence",
        "opportunity_hypothesis": "A possible opportunity requiring validation.",
        "pitch_angle": "A potential fit requiring analyst validation.",
        "evidence_summary": "Retained test evidence.",
        "evidence_counts": {"company_opportunities": 1},
        "source_links": [{"title": "Source", "url": "https://example.org/postgres"}],
        "limitations": ["Evidence is incomplete."],
        "recommended_next_action": "Validate the source.",
        "created_from": "retained-pharmadrone-intelligence",
    }
    saved = ai_bd_service.save_lead(
        principal["user_id"], principal["workspace_id"], lead, conn=conn,
    )
    assert saved["data"]["lead_id"] == lead["lead_id"]
    assert repository.list_saved_leads(
        conn, principal["user_id"], principal["workspace_id"],
    )[0]["lead_id"] == lead["lead_id"]
    repository.record_usage(
        conn, principal["user_id"], principal["workspace_id"], "save-lead",
    )
    repository.record_usage(
        conn, principal["user_id"], principal["workspace_id"], "save-report",
    )
    usage = repository.billing_status(
        conn, principal["user_id"], principal["workspace_id"],
    )["usage"]
    assert usage["saved_lead_count"] == 1
    assert usage["saved_report_count"] == 1
    conn.close()
