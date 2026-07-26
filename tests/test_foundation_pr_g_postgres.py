from __future__ import annotations

import os

import pytest

from pharmadrone.canonicalisation import CanonicalisationService
from pharmadrone.storage.migrations import (
    _foundation_pr_g_canonicalisation_review_schema,
)


@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not configured",
)
def test_real_postgresql_migration_20_and_governed_acceptance():
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
    assert first["schema_version"] >= 20
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=20"
    ).fetchone()["n"] == 1
    for table in (
        "canonicalisation_runs",
        "canonicalisation_candidates",
        "canonicalisation_decisions",
        "canonical_record_links",
    ):
        assert conn.has_table(table)

    with conn.transaction():
        _foundation_pr_g_canonicalisation_review_schema(conn)
    assert conn.ensure_migrations()["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=20"
    ).fetchone()["n"] == 1

    now = "2026-07-27T00:00:00+00:00"
    with conn.transaction():
        conn.execute(
            """INSERT INTO product_profiles
            (product_id,canonical_name,normalized_name,product_type,
            identity_status,evidence_status,last_verified_at,next_review_at)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(product_id) DO NOTHING""",
            (
                "postgres-g-product",
                "PostgreSQL Canonical Product",
                "postgresql canonical product",
                "medicinal-product",
                "source-derived",
                "official product identity",
                now,
                now,
            ),
        )
        conn.execute(
            """INSERT INTO opportunities (id,product,data_json)
            VALUES (?,?,?) ON CONFLICT(id) DO NOTHING""",
            (
                "postgres-g-source",
                "PostgreSQL Canonical Product",
                "{}",
            ),
        )
    admin = {
        "role": "platform_admin",
        "display_name": "PostgreSQL Administrator",
    }
    reviewer = {
        "role": "analyst_reviewer",
        "display_name": "PostgreSQL Reviewer",
    }
    service = CanonicalisationService(conn)
    generated = service.generate_candidates(
        admin,
        source_table="opportunities",
        max_records=100,
        permitted_rules=("exact-normalized-name",),
    )
    assert generated["status"] == "completed"
    candidate = next(
        row
        for row in service.list_candidates(
            reviewer, page=1, page_size=50, status=""
        ).candidates
        if row["source_record_id"] == "postgres-g-source"
    )
    assert candidate["review_status"] == "pending-review"
    assert conn.execute(
        """SELECT COUNT(*) AS n FROM canonical_record_links
        WHERE source_record_id='postgres-g-source'"""
    ).fetchone()["n"] == 0
    accepted = service.accept_candidate(
        reviewer,
        candidate["canonicalisation_candidate_id"],
        "Confirmed in real PostgreSQL.",
    )
    assert accepted["canonical_record_link_id"]
    assert conn.execute(
        """SELECT COUNT(*) AS n FROM canonical_record_links
        WHERE source_record_id='postgres-g-source' AND active=1"""
    ).fetchone()["n"] == 1
    with pytest.raises(Exception):
        conn.execute(
            """INSERT INTO canonical_record_links
            (canonical_record_link_id,scope_key,source_table,source_record_id,
            canonical_entity_type,canonical_id,canonicalisation_candidate_id,
            accepted_decision_id,evidence_status,evidence_basis)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "postgres-g-invalid-link",
                "platform",
                "opportunities",
                "postgres-g-invalid",
                "product",
                "postgres-g-product",
                candidate["canonicalisation_candidate_id"],
                "missing-decision",
                "test",
                "No accepted decision",
            ),
        )
    conn.close()
