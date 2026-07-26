import os

import pytest

from pharmadrone.storage.migrations import _foundation_pr_b_identity_schema


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured")
def test_real_postgresql_migration_17_is_fresh_and_rerunnable():
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
    assert first["schema_version"] >= 17
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=17"
    ).fetchone()["n"] == 1
    for table in (
        "product_profiles",
        "api_profiles",
        "product_api_relationships",
        "pharmaceutical_entity_aliases",
        "pharmaceutical_entity_identifiers",
        "pharmaceutical_evidence_links",
    ):
        assert conn.has_table(table)

    with conn.transaction():
        _foundation_pr_b_identity_schema(conn)
    second = conn.ensure_migrations()
    assert second["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=17"
    ).fetchone()["n"] == 1

    with conn.transaction():
        conn.execute(
            "INSERT INTO product_profiles "
            "(product_id,canonical_name,normalized_name,product_type,identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "postgres-product",
                "PostgreSQL Product",
                "postgresql product",
                "medicinal-product",
                "source-derived",
                "test evidence",
                "2026-07-26T00:00:00+00:00",
                "2026-07-26T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO api_profiles "
            "(api_id,canonical_name,normalized_name,substance_type,identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "postgres-api",
                "PostgreSQL API",
                "postgresql api",
                "active-substance",
                "source-derived",
                "test evidence",
                "2026-07-26T00:00:00+00:00",
                "2026-07-26T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO product_api_relationships "
            "(product_api_relationship_id,product_id,api_id,relationship_type,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "postgres-product-api",
                "postgres-product",
                "postgres-api",
                "active-ingredient",
                "test",
                "postgres-1",
                "https://example.test/postgres",
                "test evidence",
                "Direct test evidence",
                "reported",
                "2026-07-26T00:00:00+00:00",
            ),
        )
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM product_api_relationships "
        "WHERE product_api_relationship_id='postgres-product-api'"
    ).fetchone()["n"] == 1
    conn.close()
