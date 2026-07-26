import os

import pytest

from pharmadrone.storage.migrations import _foundation_pr_c_organisation_provider_schema


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured")
def test_real_postgresql_migration_18_is_fresh_rerunnable_and_enforces_integrity():
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
    assert first["schema_version"] >= 18
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=18"
    ).fetchone()["n"] == 1
    for table in (
        "organisation_profiles",
        "organisation_aliases",
        "organisation_identifiers",
        "capability_profiles",
        "organisation_capability_relationships",
        "organisation_solution_relationships",
        "organisation_product_relationships",
        "organisation_api_relationships",
        "organisation_evidence_links",
    ):
        assert conn.has_table(table)

    with conn.transaction():
        _foundation_pr_c_organisation_provider_schema(conn)
    second = conn.ensure_migrations()
    assert second["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=18"
    ).fetchone()["n"] == 1

    with conn.transaction():
        conn.execute(
            "INSERT INTO organisation_profiles "
            "(organisation_profile_id,canonical_name,normalized_name,organisation_type,"
            "identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "postgres-org",
                "PostgreSQL Provider",
                "postgresql provider",
                "service-provider",
                "source-derived",
                "test evidence",
                "2026-07-26T00:00:00+00:00",
                "2026-07-26T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO capability_profiles "
            "(capability_profile_id,canonical_name,normalized_name,capability_type,"
            "identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "postgres-capability",
                "PostgreSQL Capability",
                "postgresql capability",
                "service",
                "source-derived",
                "test evidence",
                "2026-07-26T00:00:00+00:00",
                "2026-07-26T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO organisation_capability_relationships "
            "(organisation_capability_relationship_id,organisation_profile_id,"
            "capability_profile_id,relationship_type,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "postgres-org-capability",
                "postgres-org",
                "postgres-capability",
                "provides",
                "test",
                "postgres-1",
                "https://example.test/postgres",
                "test evidence",
                "Direct PostgreSQL test evidence",
                "requires-review",
                "2026-07-26T00:00:00+00:00",
            ),
        )
    assert conn.execute(
        "SELECT inference_status FROM organisation_capability_relationships "
        "WHERE organisation_capability_relationship_id='postgres-org-capability'"
    ).fetchone()["inference_status"] == "not-inferred"

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO organisation_api_relationships "
            "(organisation_api_relationship_id,organisation_profile_id,api_id,"
            "relationship_type,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "postgres-invalid-api",
                "postgres-org",
                "missing-api",
                "supplier",
                "test",
                "postgres-invalid",
                "https://example.test/postgres",
                "test evidence",
                "Invalid missing API",
                "requires-review",
                "2026-07-26T00:00:00+00:00",
            ),
        )
    conn.close()
