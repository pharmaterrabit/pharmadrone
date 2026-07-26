import pytest

from pharmadrone import db
from pharmadrone.storage import configured_database, open_connection
from pharmadrone.storage import migrations
from pharmadrone.storage.migrations import MIGRATIONS, _foundation_pr_c_organisation_provider_schema


PR_C_TABLES = {
    "organisation_profiles",
    "organisation_aliases",
    "organisation_identifiers",
    "capability_profiles",
    "organisation_capability_relationships",
    "organisation_solution_relationships",
    "organisation_product_relationships",
    "organisation_api_relationships",
    "organisation_evidence_links",
}
NOW = "2026-07-26T00:00:00+00:00"


def _insert_foundation_entities(conn):
    solution_domain = conn.execute(
        "SELECT term_id FROM intelligence_taxonomy_terms "
        "WHERE taxonomy_namespace='solution_domain' ORDER BY term_id LIMIT 1"
    ).fetchone()["term_id"]
    solution_type = conn.execute(
        "SELECT term_id FROM intelligence_taxonomy_terms "
        "WHERE taxonomy_namespace='solution_type' ORDER BY term_id LIMIT 1"
    ).fetchone()["term_id"]
    conn.execute(
        "INSERT INTO organisation_profiles "
        "(organisation_profile_id,canonical_name,normalized_name,organisation_type,"
        "identity_status,evidence_status,last_verified_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "org-1",
            "Example Technology Partner",
            "example technology partner",
            "technology-provider",
            "source-derived",
            "official provider source",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO capability_profiles "
        "(capability_profile_id,canonical_name,normalized_name,capability_type,description,"
        "identity_status,evidence_status,last_verified_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "capability-1",
            "Specialist Process Development",
            "specialist process development",
            "service",
            "A provider-reported process-development capability.",
            "source-derived",
            "official provider source",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO technology_solutions "
        "(technology_id,canonical_key,display_name,taxonomy_term_id,solution_type_term_id,"
        "maturity_status,identity_status,evidence_status,last_verified_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "technology-1",
            "example-technology",
            "Example Technology",
            solution_domain,
            solution_type,
            "commercial",
            "source-derived",
            "official source",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO product_profiles "
        "(product_id,canonical_name,normalized_name,product_type,identity_status,"
        "evidence_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "product-1",
            "Example Product",
            "example product",
            "medicinal-product",
            "source-derived",
            "official source",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO api_profiles "
        "(api_id,canonical_name,normalized_name,substance_type,identity_status,"
        "evidence_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "api-1",
            "Example API",
            "example api",
            "active-substance",
            "source-derived",
            "official source",
            NOW,
            NOW,
        ),
    )


def _relationship_values(relationship_id, target_id, relationship_type="provides"):
    return (
        relationship_id,
        "org-1",
        target_id,
        relationship_type,
        "official-provider-page",
        f"record-{relationship_id}",
        "https://example.test/provider",
        "official",
        "The named organisation explicitly reports this relationship.",
        "requires-review",
        NOW,
    )


def test_foundation_pr_c_fresh_migration_is_additive_and_rerunnable(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-c-fresh.sqlite")
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert PR_C_TABLES.issubset(tables)
    assert [migration.version for migration in MIGRATIONS] == list(range(1, 21))
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=18"
    ).fetchone()["n"] == 1
    assert conn.execute(
        "SELECT MAX(version) AS version FROM schema_migrations"
    ).fetchone()["version"] == 20
    assert all(
        conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        for table in PR_C_TABLES
    )

    with conn.transaction():
        _foundation_pr_c_organisation_provider_schema(conn)
    assert conn.ensure_migrations()["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=18"
    ).fetchone()["n"] == 1


def test_foundation_pr_c_upgrades_schema_17_and_preserves_existing_data(tmp_path, monkeypatch):
    conn = open_connection(configured_database(tmp_path / "foundation-pr-c-upgrade.sqlite"))
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:-3])
    assert conn.ensure_migrations()["schema_version"] == 17

    with conn.transaction():
        conn.execute(
            "INSERT INTO opportunities (id,company,product) VALUES (?,?,?)",
            ("existing-opportunity", "Existing Company", "Existing Product"),
        )
        conn.execute(
            "INSERT INTO account_organisations "
            "(organisation_id,canonical_key,canonical_name) VALUES (?,?,?)",
            ("existing-account", "existing-account", "Existing Account"),
        )
        conn.execute(
            "INSERT INTO research_organisations "
            "(research_organisation_id,canonical_name,identity_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?)",
            ("existing-research", "Existing Institute", "source-derived", NOW, NOW),
        )
        conn.execute(
            "INSERT INTO patent_documents "
            "(patent_document_id,publication_number,jurisdiction,family_status,source_name,"
            "source_authority,official_source_url,google_patents_url,evidence_status,"
            "last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "existing-patent",
                "EP1234567A1",
                "EP",
                "not resolved",
                "EPO",
                "official",
                "https://example.test/patent",
                "https://patents.google.com/patent/EP1234567A1",
                "official",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO product_profiles "
            "(product_id,canonical_name,normalized_name,product_type,identity_status,"
            "evidence_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "existing-product",
                "Existing Product",
                "existing product",
                "medicinal-product",
                "source-derived",
                "official",
                NOW,
                NOW,
            ),
        )

    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:-2])
    result = conn.ensure_migrations()
    assert result["schema_version"] == 18
    assert result["newly_applied"] == [18]
    assert conn.execute(
        "SELECT company FROM opportunities WHERE id='existing-opportunity'"
    ).fetchone()["company"] == "Existing Company"
    assert conn.execute(
        "SELECT canonical_name FROM account_organisations "
        "WHERE organisation_id='existing-account'"
    ).fetchone()["canonical_name"] == "Existing Account"
    assert conn.execute(
        "SELECT canonical_name FROM research_organisations "
        "WHERE research_organisation_id='existing-research'"
    ).fetchone()["canonical_name"] == "Existing Institute"
    assert conn.execute(
        "SELECT publication_number FROM patent_documents "
        "WHERE patent_document_id='existing-patent'"
    ).fetchone()["publication_number"] == "EP1234567A1"
    assert conn.execute(
        "SELECT canonical_name FROM product_profiles WHERE product_id='existing-product'"
    ).fetchone()["canonical_name"] == "Existing Product"
    assert all(
        conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        for table in PR_C_TABLES
    )


def test_organisation_identity_alias_identifier_and_relationship_integrity(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-c-integrity.sqlite")
    with conn.transaction():
        _insert_foundation_entities(conn)
        conn.execute(
            "INSERT INTO organisation_aliases "
            "(organisation_alias_id,organisation_profile_id,alias_name,normalized_alias,"
            "alias_type,source_type,source_record_id,evidence_url,evidence_status,"
            "verification_status,observed_at,last_verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "alias-1",
                "org-1",
                "Example Partner",
                "example partner",
                "trading-name",
                "official-provider-page",
                "provider-alias",
                "https://example.test/provider",
                "official",
                "source-derived",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO organisation_identifiers "
            "(organisation_identifier_id,organisation_profile_id,identifier_namespace,"
            "identifier_value,normalized_identifier,source_type,source_record_id,evidence_url,"
            "evidence_status,verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "identifier-1",
                "org-1",
                "ROR",
                "https://ror.org/example",
                "https://ror.org/example",
                "ROR",
                "example",
                "https://ror.org/example",
                "official",
                "source-derived",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO organisation_capability_relationships "
            "(organisation_capability_relationship_id,organisation_profile_id,"
            "capability_profile_id,relationship_type,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            _relationship_values("org-capability-1", "capability-1"),
        )
        conn.execute(
            "INSERT INTO organisation_solution_relationships "
            "(organisation_solution_relationship_id,organisation_profile_id,technology_id,"
            "relationship_type,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            _relationship_values("org-solution-1", "technology-1", "provides"),
        )
        conn.execute(
            "INSERT INTO organisation_product_relationships "
            "(organisation_product_relationship_id,organisation_profile_id,product_id,"
            "relationship_type,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            _relationship_values("org-product-1", "product-1", "manufacturer"),
        )
        conn.execute(
            "INSERT INTO organisation_api_relationships "
            "(organisation_api_relationship_id,organisation_profile_id,api_id,"
            "relationship_type,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            _relationship_values("org-api-1", "api-1", "supplier"),
        )
        conn.execute(
            "INSERT INTO evidence "
            "(opportunity_id,source_type,source_name,record_id,title,url) "
            "VALUES (?,?,?,?,?,?)",
            (
                "opportunity-1",
                "official",
                "Provider website",
                "relationship-evidence",
                "Provider capability",
                "https://example.test/provider",
            ),
        )
        evidence_id = conn.execute(
            "SELECT id FROM evidence WHERE record_id='relationship-evidence'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO organisation_evidence_links "
            "(organisation_evidence_link_id,organisation_capability_relationship_id,"
            "evidence_id,source_table,source_record_id,link_type,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "org-evidence-1",
                "org-capability-1",
                evidence_id,
                "evidence",
                str(evidence_id),
                "supports-relationship",
                "https://example.test/provider",
                "official",
                "Evidence explicitly names the provider capability.",
                "requires-review",
                NOW,
            ),
        )

    assert conn.execute(
        "SELECT COUNT(*) AS n FROM organisation_capability_relationships"
    ).fetchone()["n"] == 1
    assert conn.execute(
        "SELECT inference_status FROM organisation_solution_relationships"
    ).fetchone()["inference_status"] == "not-inferred"
    assert conn.execute(
        "SELECT evidence_id FROM organisation_evidence_links"
    ).fetchone()["evidence_id"] == evidence_id


def test_organisation_constraints_and_foreign_keys_reject_invalid_writes(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-c-constraints.sqlite")
    with conn.transaction():
        _insert_foundation_entities(conn)

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO organisation_profiles "
            "(organisation_profile_id,canonical_name,normalized_name,organisation_type,"
            "identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "org-duplicate",
                "Duplicate",
                "example technology partner",
                "consultancy",
                "source-derived",
                "official",
                NOW,
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO organisation_aliases "
            "(organisation_alias_id,organisation_profile_id,alias_name,normalized_alias,"
            "alias_type,source_type,source_record_id,evidence_url,evidence_status,"
            "verification_status,observed_at,last_verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-alias",
                "missing-org",
                "Missing",
                "missing",
                "alternative",
                "test",
                "bad-alias",
                "https://example.test",
                "test",
                "requires-review",
                NOW,
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO organisation_solution_relationships "
            "(organisation_solution_relationship_id,organisation_profile_id,technology_id,"
            "relationship_type,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,inference_status,observed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-inference",
                "org-1",
                "technology-1",
                "provides",
                "test",
                "bad-inference",
                "https://example.test",
                "test",
                "Unsupported inference",
                "requires-review",
                "inferred",
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO organisation_product_relationships "
            "(organisation_product_relationship_id,organisation_profile_id,product_id,"
            "relationship_type,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            _relationship_values("missing-product", "missing-product", "manufacturer"),
        )


def test_organisation_evidence_links_require_exactly_one_existing_parent(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-c-evidence.sqlite")
    with conn.transaction():
        _insert_foundation_entities(conn)

    base = (
        999999,
        "evidence",
        "missing",
        "supports-relationship",
        "https://example.test/evidence",
        "test",
        "Direct source evidence",
        "requires-review",
        NOW,
    )
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO organisation_evidence_links "
            "(organisation_evidence_link_id,organisation_profile_id,capability_profile_id,"
            "evidence_id,source_table,source_record_id,link_type,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("bad-two-parents", "org-1", "capability-1", *base),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO organisation_evidence_links "
            "(organisation_evidence_link_id,organisation_profile_id,evidence_id,source_table,"
            "source_record_id,link_type,evidence_url,evidence_status,evidence_basis,"
            "verification_status,observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("bad-missing-evidence", "org-1", *base),
        )
