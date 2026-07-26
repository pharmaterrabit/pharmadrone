import pytest

from pharmadrone import db
from pharmadrone.storage import configured_database, open_connection
from pharmadrone.storage import migrations
from pharmadrone.storage.migrations import MIGRATIONS, _foundation_pr_b_identity_schema


PR_B_TABLES = {
    "product_profiles",
    "api_profiles",
    "product_api_relationships",
    "pharmaceutical_entity_aliases",
    "pharmaceutical_entity_identifiers",
    "pharmaceutical_evidence_links",
}
NOW = "2026-07-26T00:00:00+00:00"


def _insert_product_and_api(conn, *, suffix: str = ""):
    product_id = f"product{suffix}"
    api_id = f"api{suffix}"
    conn.execute(
        "INSERT INTO product_profiles "
        "(product_id,canonical_name,normalized_name,product_type,identity_status,evidence_status,last_verified_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            product_id,
            f"Example Product {suffix}".strip(),
            f"example product {suffix}".strip(),
            "medicinal-product",
            "source-derived",
            "official source",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO api_profiles "
        "(api_id,canonical_name,normalized_name,substance_type,identity_status,evidence_status,last_verified_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            api_id,
            f"Example API {suffix}".strip(),
            f"example api {suffix}".strip(),
            "active-substance",
            "source-derived",
            "official source",
            NOW,
            NOW,
        ),
    )
    return product_id, api_id


def test_foundation_pr_b_fresh_migration_is_additive_and_rerunnable(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-b-fresh.sqlite")
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert PR_B_TABLES.issubset(tables)
    assert [migration.version for migration in MIGRATIONS] == list(range(1, 18))
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=17"
    ).fetchone()["n"] == 1
    assert conn.execute(
        "SELECT MAX(version) AS version FROM schema_migrations"
    ).fetchone()["version"] == 17
    assert all(
        conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        for table in PR_B_TABLES
    )

    with conn.transaction():
        _foundation_pr_b_identity_schema(conn)
    assert conn.ensure_migrations()["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=17"
    ).fetchone()["n"] == 1


def test_foundation_pr_b_upgrades_schema_16_and_preserves_existing_data(tmp_path, monkeypatch):
    conn = open_connection(configured_database(tmp_path / "foundation-pr-b-upgrade.sqlite"))
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:-1])
    assert conn.ensure_migrations()["schema_version"] == 16

    with conn.transaction():
        conn.execute(
            "INSERT INTO evidence "
            "(opportunity_id,source_type,source_name,record_id,title,url) VALUES (?,?,?,?,?,?)",
            ("opp-existing", "official", "Existing evidence", "record-1", "Existing title", "https://example.test/evidence"),
        )
        evidence_id = conn.execute(
            "SELECT id FROM evidence WHERE record_id='record-1'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO lifecycle_products "
            "(lifecycle_id,application_number,product_number,trade_name,ingredient,official_source_url,evidence_status,lifecycle_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "life-existing",
                "NDA000001",
                "001",
                "Existing Product",
                "Existing Ingredient",
                "https://example.test/orange-book",
                "official",
                "Active",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO patent_documents "
            "(patent_document_id,publication_number,jurisdiction,family_status,source_name,source_authority,"
            "official_source_url,google_patents_url,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "patent-existing",
                "US1234567A1",
                "US",
                "not resolved",
                "USPTO",
                "official",
                "https://example.test/patent",
                "https://patents.google.com/patent/US1234567A1",
                "official",
                NOW,
                NOW,
            ),
        )

    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    result = conn.ensure_migrations()
    assert result["schema_version"] == 17
    assert result["newly_applied"] == [17]
    assert conn.execute(
        "SELECT trade_name FROM lifecycle_products WHERE lifecycle_id='life-existing'"
    ).fetchone()["trade_name"] == "Existing Product"
    assert conn.execute(
        "SELECT publication_number FROM patent_documents WHERE patent_document_id='patent-existing'"
    ).fetchone()["publication_number"] == "US1234567A1"
    assert conn.execute(
        "SELECT title FROM evidence WHERE id=?", (evidence_id,)
    ).fetchone()["title"] == "Existing title"
    assert all(
        conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        for table in PR_B_TABLES
    )


def test_product_api_identity_relationships_aliases_identifiers_and_evidence(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-b-integrity.sqlite")
    with conn.transaction():
        product_id, api_id = _insert_product_and_api(conn)
        conn.execute(
            "INSERT INTO product_api_relationships "
            "(product_api_relationship_id,product_id,api_id,relationship_type,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,observed_at,verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "product-api-1",
                product_id,
                api_id,
                "active-ingredient",
                "FDA Orange Book",
                "NDA000001:001",
                "https://example.test/orange-book",
                "official",
                "Source-reported ingredient",
                "reported",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO pharmaceutical_entity_aliases "
            "(entity_alias_id,product_id,alias_name,normalized_alias,alias_type,source_type,source_record_id,"
            "evidence_url,evidence_status,verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "alias-product-1",
                product_id,
                "Example Brand",
                "example brand",
                "brand",
                "FDA Orange Book",
                "NDA000001:001",
                "https://example.test/orange-book",
                "official",
                "reported",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO pharmaceutical_entity_identifiers "
            "(entity_identifier_id,api_id,identifier_namespace,identifier_value,normalized_identifier,jurisdiction,"
            "source_type,source_record_id,evidence_url,evidence_status,verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "identifier-api-1",
                api_id,
                "UNII",
                "ABC123",
                "ABC123",
                "US",
                "FDA",
                "unii:ABC123",
                "https://example.test/unii",
                "official",
                "reported",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO evidence "
            "(opportunity_id,source_type,source_name,record_id,title,url) VALUES (?,?,?,?,?,?)",
            (
                "opp-1",
                "official",
                "FDA Orange Book",
                "NDA000001:001",
                "Product evidence",
                "https://example.test/orange-book",
            ),
        )
        evidence_id = conn.execute(
            "SELECT id FROM evidence WHERE record_id='NDA000001:001'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO pharmaceutical_evidence_links "
            "(pharmaceutical_evidence_link_id,product_id,evidence_id,source_table,source_record_id,link_type,"
            "evidence_url,evidence_status,evidence_basis,verification_status,observed_at,verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "evidence-product-1",
                product_id,
                evidence_id,
                "evidence",
                str(evidence_id),
                "identifies",
                "https://example.test/orange-book",
                "official",
                "Evidence record explicitly names the product",
                "reported",
                NOW,
                NOW,
            ),
        )

    relationship = conn.execute(
        "SELECT * FROM product_api_relationships WHERE product_api_relationship_id='product-api-1'"
    ).fetchone()
    assert relationship["product_id"] == product_id
    assert relationship["api_id"] == api_id
    assert relationship["verification_status"] == "reported"
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM pharmaceutical_evidence_links WHERE evidence_id=?",
        (evidence_id,),
    ).fetchone()["n"] == 1


def test_foundation_pr_b_constraints_reject_invalid_or_inferred_links(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-b-constraints.sqlite")
    with conn.transaction():
        product_id, api_id = _insert_product_and_api(conn)
        second_product_id, _ = _insert_product_and_api(conn, suffix="-two")

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO product_api_relationships "
            "(product_api_relationship_id,product_id,api_id,relationship_type,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-foreign-key",
                product_id,
                "missing-api",
                "active-ingredient",
                "test",
                "1",
                "https://example.test",
                "test",
                "Invalid",
                "reported",
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO pharmaceutical_entity_aliases "
            "(entity_alias_id,product_id,api_id,alias_name,normalized_alias,alias_type,source_type,source_record_id,"
            "evidence_url,evidence_status,verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-two-targets",
                product_id,
                api_id,
                "Invalid",
                "invalid",
                "alternative",
                "test",
                "2",
                "https://example.test",
                "test",
                "reported",
                NOW,
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO product_api_relationships "
            "(product_api_relationship_id,product_id,api_id,relationship_type,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-inferred",
                product_id,
                api_id,
                "active-ingredient",
                "test",
                "3",
                "https://example.test",
                "test",
                "No direct evidence",
                "inferred",
                NOW,
            ),
        )

    with conn.transaction():
        conn.execute(
            "INSERT INTO pharmaceutical_entity_identifiers "
            "(entity_identifier_id,product_id,identifier_namespace,identifier_value,normalized_identifier,"
            "source_type,source_record_id,evidence_url,evidence_status,verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "identifier-product-1",
                product_id,
                "FDA_APPLICATION_PRODUCT",
                "NDA000001:001",
                "NDA000001:001",
                "FDA",
                "NDA000001:001",
                "https://example.test",
                "official",
                "reported",
                NOW,
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO pharmaceutical_entity_identifiers "
            "(entity_identifier_id,product_id,identifier_namespace,identifier_value,normalized_identifier,"
            "source_type,source_record_id,evidence_url,evidence_status,verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "identifier-product-duplicate",
                second_product_id,
                "FDA_APPLICATION_PRODUCT",
                "NDA000001:001",
                "NDA000001:001",
                "FDA",
                "NDA000001:001",
                "https://example.test",
                "official",
                "reported",
                NOW,
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO pharmaceutical_evidence_links "
            "(pharmaceutical_evidence_link_id,product_id,evidence_id,source_table,source_record_id,link_type,"
            "evidence_url,evidence_status,evidence_basis,verification_status,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-evidence",
                product_id,
                999999,
                "evidence",
                "999999",
                "identifies",
                "https://example.test",
                "test",
                "Missing evidence",
                "reported",
                NOW,
            ),
        )

