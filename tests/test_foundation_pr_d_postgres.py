import os

import pytest

from pharmadrone.storage.migrations import _foundation_pr_d_opportunity_commercial_schema


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured")
def test_real_postgresql_migration_19_is_fresh_rerunnable_and_enforces_integrity():
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
    assert first["schema_version"] >= 19
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=19"
    ).fetchone()["n"] == 1
    for table in (
        "opportunity_profiles",
        "opportunity_identifiers",
        "opportunity_participants",
        "opportunity_problem_relationships",
        "opportunity_solution_relationships",
        "opportunity_product_relationships",
        "opportunity_api_relationships",
        "commercial_event_identity_links",
        "commercial_event_participants",
        "opportunity_commercial_event_relationships",
        "opportunity_evidence_links",
    ):
        assert conn.has_table(table)

    with conn.transaction():
        _foundation_pr_d_opportunity_commercial_schema(conn)
    second = conn.ensure_migrations()
    assert second["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=19"
    ).fetchone()["n"] == 1

    now = "2026-07-26T00:00:00+00:00"
    with conn.transaction():
        conn.execute(
            "INSERT INTO organisation_profiles "
            "(organisation_profile_id,canonical_name,normalized_name,organisation_type,"
            "identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "postgres-d-org",
                "PostgreSQL PR-D Organisation",
                "postgresql pr-d organisation",
                "investor",
                "source-derived",
                "test",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO commercial_events "
            "(commercial_event_id,event_type,evidence_class,source_type,source_name,source_id,"
            "evidence_url,evidence_status,validation_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "postgres-d-event",
                "Investment",
                "transaction",
                "test",
                "PostgreSQL test",
                "postgres-d-event",
                "https://example.test/postgres",
                "test",
                "requires review",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO opportunity_profiles "
            "(opportunity_profile_id,canonical_key,title,opportunity_type,lifecycle_status,"
            "source_type,source_record_id,evidence_url,evidence_status,evidence_basis,"
            "verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "postgres-d-opportunity",
                "postgres-d-opportunity",
                "PostgreSQL investment opportunity",
                "investment",
                "under-review",
                "test",
                "postgres-d-opportunity",
                "https://example.test/postgres",
                "test",
                "Direct PostgreSQL test evidence",
                "requires-review",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO commercial_event_identity_links "
            "(commercial_event_identity_id,canonical_event_key,commercial_event_id,event_type,"
            "lifecycle_status,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "postgres-d-event-identity",
                "postgres-d-event-identity",
                "postgres-d-event",
                "investment",
                "under-review",
                "test",
                "postgres-d-event",
                "https://example.test/postgres",
                "test",
                "Adapter to the existing observed commercial event",
                "requires-review",
                now,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO commercial_event_participants "
            "(commercial_event_participant_id,commercial_event_identity_id,"
            "organisation_profile_id,participant_role,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,"
            "observed_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "postgres-d-participant",
                "postgres-d-event-identity",
                "postgres-d-org",
                "investor",
                "test",
                "postgres-d-participant",
                "https://example.test/postgres",
                "test",
                "The source explicitly identifies the investor",
                "requires-review",
                now,
                now,
            ),
        )
    assert conn.execute(
        "SELECT inference_status FROM commercial_event_participants "
        "WHERE commercial_event_participant_id='postgres-d-participant'"
    ).fetchone()["inference_status"] == "not-inferred"

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO opportunity_participants "
            "(opportunity_participant_id,opportunity_profile_id,organisation_profile_id,"
            "participant_role,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "postgres-d-invalid",
                "postgres-d-opportunity",
                "missing-organisation",
                "investor",
                "test",
                "postgres-d-invalid",
                "https://example.test/postgres",
                "test",
                "Missing organisation",
                "requires-review",
                now,
                now,
            ),
        )
    conn.close()

