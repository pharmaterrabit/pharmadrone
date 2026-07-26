import pytest

from pharmadrone import db
from pharmadrone.storage import configured_database, open_connection
from pharmadrone.storage import migrations
from pharmadrone.storage.migrations import MIGRATIONS, _foundation_pr_d_opportunity_commercial_schema


PR_D_TABLES = {
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
}
NOW = "2026-07-26T00:00:00+00:00"


def _insert_foundation_entities(conn):
    problem_term = conn.execute(
        "SELECT term_id FROM intelligence_taxonomy_terms "
        "WHERE taxonomy_namespace='problem_domain' ORDER BY term_id LIMIT 1"
    ).fetchone()["term_id"]
    solution_domain = conn.execute(
        "SELECT term_id FROM intelligence_taxonomy_terms "
        "WHERE taxonomy_namespace='solution_domain' ORDER BY term_id LIMIT 1"
    ).fetchone()["term_id"]
    solution_type = conn.execute(
        "SELECT term_id FROM intelligence_taxonomy_terms "
        "WHERE taxonomy_namespace='solution_type' ORDER BY term_id LIMIT 1"
    ).fetchone()["term_id"]
    conn.execute(
        "INSERT INTO pharmaceutical_problems "
        "(problem_id,canonical_key,display_name,taxonomy_term_id,definition,identity_status,"
        "evidence_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "problem-d",
            "problem-d",
            "Example Pharmaceutical Problem",
            problem_term,
            "A directly evidenced pharmaceutical development problem.",
            "source-derived",
            "official",
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
            "technology-d",
            "technology-d",
            "Example Technology Solution",
            solution_domain,
            solution_type,
            "commercial",
            "source-derived",
            "official",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO organisation_profiles "
        "(organisation_profile_id,canonical_name,normalized_name,organisation_type,"
        "identity_status,evidence_status,last_verified_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "organisation-d",
            "Example Organisation",
            "example organisation",
            "pharmaceutical-company",
            "source-derived",
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
            "product-d",
            "Example Product",
            "example product",
            "medicinal-product",
            "source-derived",
            "official",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO api_profiles "
        "(api_id,canonical_name,normalized_name,substance_type,identity_status,"
        "evidence_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?)",
        (
            "api-d",
            "Example API",
            "example api",
            "active-substance",
            "source-derived",
            "official",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO opportunities (id,company,product) VALUES (?,?,?)",
        ("legacy-opportunity-d", "Example Organisation", "Example Product"),
    )
    conn.execute(
        "INSERT INTO opportunity_index "
        "(stable_lead_id,company,product,problem_category,source_type,source_id) "
        "VALUES (?,?,?,?,?,?)",
        (
            "stable-lead-d",
            "Example Organisation",
            "Example Product",
            "Example problem",
            "official",
            "source-d",
        ),
    )
    conn.execute(
        "INSERT INTO commercial_events "
        "(commercial_event_id,event_type,evidence_class,source_type,source_name,source_id,"
        "evidence_url,evidence_status,validation_status,last_verified_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "legacy-event-d",
            "Licensing",
            "transaction",
            "official",
            "Company disclosure",
            "event-d",
            "https://example.test/event",
            "official",
            "requires review",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO funding_awards "
        "(funding_award_id,funding_type,source_type,source_name,source_id,evidence_url,"
        "evidence_status,validation_status,last_verified_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "legacy-funding-d",
            "grant",
            "official",
            "Funding body",
            "funding-d",
            "https://example.test/funding",
            "official",
            "requires review",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO evidence "
        "(opportunity_id,source_type,source_name,record_id,title,url) "
        "VALUES (?,?,?,?,?,?)",
        (
            "legacy-opportunity-d",
            "official",
            "Company disclosure",
            "evidence-d",
            "Opportunity and transaction evidence",
            "https://example.test/evidence",
        ),
    )


def _insert_opportunity_and_event_identity(conn):
    conn.execute(
        "INSERT INTO opportunity_profiles "
        "(opportunity_profile_id,canonical_key,title,opportunity_type,lifecycle_status,"
        "source_type,source_record_id,evidence_url,evidence_status,evidence_basis,"
        "verification_status,observed_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "opportunity-d",
            "opportunity-d",
            "Example technology partnership opportunity",
            "technology-partnership",
            "under-review",
            "official",
            "source-d",
            "https://example.test/evidence",
            "official",
            "The source explicitly describes a potential partnership.",
            "requires-review",
            NOW,
            NOW,
        ),
    )
    conn.execute(
        "INSERT INTO commercial_event_identity_links "
        "(commercial_event_identity_id,canonical_event_key,commercial_event_id,event_type,"
        "lifecycle_status,source_type,source_record_id,evidence_url,evidence_status,"
        "evidence_basis,verification_status,observed_at,next_review_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "event-identity-d",
            "event-identity-d",
            "legacy-event-d",
            "licensing",
            "under-review",
            "official",
            "event-d",
            "https://example.test/event",
            "official",
            "The existing commercial event is retained as the observed transaction.",
            "requires-review",
            NOW,
            NOW,
        ),
    )


def _relationship_values(relationship_id, target_id, relationship_type):
    return (
        relationship_id,
        "opportunity-d",
        target_id,
        relationship_type,
        "official",
        f"source-{relationship_id}",
        "https://example.test/evidence",
        "official",
        "The source explicitly supports this relationship.",
        "requires-review",
        NOW,
        NOW,
    )


def test_foundation_pr_d_fresh_migration_is_additive_and_rerunnable(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-d-fresh.sqlite")
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert PR_D_TABLES.issubset(tables)
    assert [migration.version for migration in MIGRATIONS] == list(range(1, 20))
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=19"
    ).fetchone()["n"] == 1
    assert conn.execute(
        "SELECT MAX(version) AS version FROM schema_migrations"
    ).fetchone()["version"] == 19
    assert all(
        conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        for table in PR_D_TABLES
    )

    with conn.transaction():
        _foundation_pr_d_opportunity_commercial_schema(conn)
    assert conn.ensure_migrations()["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=19"
    ).fetchone()["n"] == 1


def test_foundation_pr_d_upgrades_schema_18_and_preserves_production_records(tmp_path, monkeypatch):
    conn = open_connection(configured_database(tmp_path / "foundation-pr-d-upgrade.sqlite"))
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:-1])
    assert conn.ensure_migrations()["schema_version"] == 18

    with conn.transaction():
        conn.execute(
            "INSERT INTO opportunities (id,company,product) VALUES (?,?,?)",
            ("existing-opportunity", "Existing Company", "Existing Product"),
        )
        conn.execute(
            "INSERT INTO opportunity_index "
            "(stable_lead_id,company,product,queue_status,report_path,report_opportunity_id) "
            "VALUES (?,?,?,?,?,?)",
            (
                "existing-lead",
                "Existing Company",
                "Existing Product",
                "waiting",
                "/reports/existing.md",
                "existing-opportunity",
            ),
        )
        conn.execute(
            "INSERT INTO commercial_events "
            "(commercial_event_id,event_type,evidence_class,source_type,source_name,source_id,"
            "evidence_url,evidence_status,validation_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "existing-event",
                "Partnership",
                "transaction",
                "official",
                "Existing source",
                "existing-event",
                "https://example.test/event",
                "official",
                "verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO funding_awards "
            "(funding_award_id,funding_type,source_type,source_name,source_id,evidence_url,"
            "evidence_status,validation_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "existing-funding",
                "grant",
                "official",
                "Existing funder",
                "existing-funding",
                "https://example.test/funding",
                "official",
                "verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO customer_saved_lists "
            "(saved_list_id,scope_key,name,created_by) VALUES (?,?,?,?)",
            ("existing-list", "scope", "Existing List", "test"),
        )

    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    result = conn.ensure_migrations()
    assert result["schema_version"] == 19
    assert result["newly_applied"] == [19]
    assert conn.execute(
        "SELECT company FROM opportunities WHERE id='existing-opportunity'"
    ).fetchone()["company"] == "Existing Company"
    lead = conn.execute(
        "SELECT queue_status,report_path,report_opportunity_id FROM opportunity_index "
        "WHERE stable_lead_id='existing-lead'"
    ).fetchone()
    assert tuple(lead.values()) == ("waiting", "/reports/existing.md", "existing-opportunity")
    assert conn.execute(
        "SELECT event_type FROM commercial_events WHERE commercial_event_id='existing-event'"
    ).fetchone()["event_type"] == "Partnership"
    assert conn.execute(
        "SELECT funding_type FROM funding_awards WHERE funding_award_id='existing-funding'"
    ).fetchone()["funding_type"] == "grant"
    assert conn.execute(
        "SELECT name FROM customer_saved_lists WHERE saved_list_id='existing-list'"
    ).fetchone()["name"] == "Existing List"
    assert all(
        conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        for table in PR_D_TABLES
    )


def test_opportunity_identity_participants_relationships_and_evidence(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-d-integrity.sqlite")
    with conn.transaction():
        _insert_foundation_entities(conn)
        _insert_opportunity_and_event_identity(conn)
        conn.execute(
            "INSERT INTO opportunity_identifiers "
            "(opportunity_identifier_id,opportunity_profile_id,identifier_type,"
            "identifier_namespace,identifier_value,normalized_identifier,stable_lead_id,"
            "source_type,source_record_id,evidence_url,evidence_status,evidence_basis,"
            "verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "identifier-d",
                "opportunity-d",
                "stable-lead-id",
                "PHARMATUNE_STABLE_LEAD",
                "stable-lead-d",
                "stable-lead-d",
                "stable-lead-d",
                "PharmaTune",
                "stable-lead-d",
                "https://example.test/evidence",
                "internal evidence",
                "Adapter to the existing opportunity queue record.",
                "requires-review",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO opportunity_participants "
            "(opportunity_participant_id,opportunity_profile_id,organisation_profile_id,"
            "participant_role,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "participant-d",
                "opportunity-d",
                "organisation-d",
                "problem-owner",
                "official",
                "participant-d",
                "https://example.test/evidence",
                "official",
                "The organisation is explicitly named as the problem owner.",
                "requires-review",
                NOW,
                NOW,
            ),
        )
        relationship_specs = (
            (
                "opportunity_problem_relationships",
                "opportunity_problem_relationship_id",
                "problem_id",
                _relationship_values("problem-relationship-d", "problem-d", "has-problem"),
            ),
            (
                "opportunity_solution_relationships",
                "opportunity_solution_relationship_id",
                "technology_id",
                _relationship_values("solution-relationship-d", "technology-d", "seeks"),
            ),
            (
                "opportunity_product_relationships",
                "opportunity_product_relationship_id",
                "product_id",
                _relationship_values("product-relationship-d", "product-d", "concerns"),
            ),
            (
                "opportunity_api_relationships",
                "opportunity_api_relationship_id",
                "api_id",
                _relationship_values("api-relationship-d", "api-d", "concerns"),
            ),
        )
        for table, id_column, target_column, values in relationship_specs:
            conn.execute(
                f"INSERT INTO {table} "
                f"({id_column},opportunity_profile_id,{target_column},relationship_type,"
                "source_type,source_record_id,evidence_url,evidence_status,evidence_basis,"
                "verification_status,observed_at,next_review_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )
        conn.execute(
            "INSERT INTO commercial_event_participants "
            "(commercial_event_participant_id,commercial_event_identity_id,"
            "organisation_profile_id,participant_role,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,"
            "observed_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "event-participant-d",
                "event-identity-d",
                "organisation-d",
                "licensor",
                "official",
                "event-participant-d",
                "https://example.test/event",
                "official",
                "The organisation is explicitly named as licensor.",
                "requires-review",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO opportunity_commercial_event_relationships "
            "(opportunity_commercial_event_relationship_id,opportunity_profile_id,"
            "commercial_event_identity_id,relationship_type,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,"
            "observed_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "opportunity-event-d",
                "opportunity-d",
                "event-identity-d",
                "associated-with",
                "official",
                "opportunity-event-d",
                "https://example.test/event",
                "official",
                "The source explicitly associates the opportunity and observed transaction.",
                "requires-review",
                NOW,
                NOW,
            ),
        )
        evidence_id = conn.execute(
            "SELECT id FROM evidence WHERE record_id='evidence-d'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO opportunity_evidence_links "
            "(opportunity_evidence_link_id,opportunity_commercial_event_relationship_id,"
            "evidence_id,source_table,source_record_id,link_type,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "evidence-link-d",
                "opportunity-event-d",
                evidence_id,
                "evidence",
                str(evidence_id),
                "supports-relationship",
                "https://example.test/evidence",
                "official",
                "Existing evidence supports the governed relationship.",
                "requires-review",
                NOW,
                NOW,
            ),
        )

    assert conn.execute(
        "SELECT COUNT(*) AS n FROM opportunity_participants"
    ).fetchone()["n"] == 1
    assert conn.execute(
        "SELECT participant_role FROM commercial_event_participants"
    ).fetchone()["participant_role"] == "licensor"
    assert conn.execute(
        "SELECT inference_status FROM opportunity_problem_relationships"
    ).fetchone()["inference_status"] == "not-inferred"
    assert conn.execute(
        "SELECT evidence_id FROM opportunity_evidence_links"
    ).fetchone()["evidence_id"] == evidence_id


def test_opportunity_constraints_roles_foreign_keys_and_non_inference(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-d-constraints.sqlite")
    with conn.transaction():
        _insert_foundation_entities(conn)
        _insert_opportunity_and_event_identity(conn)

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO opportunity_profiles "
            "(opportunity_profile_id,canonical_key,title,opportunity_type,lifecycle_status,"
            "source_type,source_record_id,evidence_url,evidence_status,evidence_basis,"
            "verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "duplicate-opportunity",
                "opportunity-d",
                "Duplicate",
                "licensing",
                "under-review",
                "test",
                "duplicate",
                "https://example.test",
                "test",
                "duplicate",
                "requires-review",
                NOW,
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO opportunity_participants "
            "(opportunity_participant_id,opportunity_profile_id,organisation_profile_id,"
            "participant_role,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-role",
                "opportunity-d",
                "organisation-d",
                "invented-role",
                "test",
                "bad-role",
                "https://example.test",
                "test",
                "invalid",
                "requires-review",
                NOW,
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO opportunity_solution_relationships "
            "(opportunity_solution_relationship_id,opportunity_profile_id,technology_id,"
            "relationship_type,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            _relationship_values("missing-solution", "missing-solution", "seeks"),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO opportunity_problem_relationships "
            "(opportunity_problem_relationship_id,opportunity_profile_id,problem_id,"
            "relationship_type,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,inference_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "inferred-relationship",
                "opportunity-d",
                "problem-d",
                "has-problem",
                "test",
                "inferred",
                "https://example.test",
                "test",
                "No direct evidence",
                "requires-review",
                "inferred",
                NOW,
                NOW,
            ),
        )


def test_event_adapter_and_evidence_links_enforce_exactly_one_parent(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-d-exactly-one.sqlite")
    with conn.transaction():
        _insert_foundation_entities(conn)
        _insert_opportunity_and_event_identity(conn)

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO commercial_event_identity_links "
            "(commercial_event_identity_id,canonical_event_key,commercial_event_id,"
            "funding_award_id,event_type,lifecycle_status,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,"
            "observed_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-event-two-parents",
                "bad-event-two-parents",
                "legacy-event-d",
                "legacy-funding-d",
                "funding",
                "under-review",
                "test",
                "bad-event",
                "https://example.test",
                "test",
                "invalid",
                "requires-review",
                NOW,
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO opportunity_evidence_links "
            "(opportunity_evidence_link_id,opportunity_profile_id,"
            "commercial_event_identity_id,source_table,source_record_id,link_type,"
            "evidence_url,evidence_status,evidence_basis,verification_status,"
            "observed_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-evidence-two-parents",
                "opportunity-d",
                "event-identity-d",
                "test",
                "bad-evidence",
                "supports-opportunity",
                "https://example.test",
                "test",
                "invalid",
                "requires-review",
                NOW,
                NOW,
            ),
        )

    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO opportunity_evidence_links "
            "(opportunity_evidence_link_id,opportunity_profile_id,evidence_id,"
            "source_table,source_record_id,link_type,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "bad-missing-evidence",
                "opportunity-d",
                999999,
                "evidence",
                "999999",
                "supports-opportunity",
                "https://example.test",
                "test",
                "missing evidence",
                "requires-review",
                NOW,
                NOW,
            ),
        )

