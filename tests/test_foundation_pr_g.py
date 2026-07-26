from __future__ import annotations

from pathlib import Path

import pytest

from pharmadrone import db
from pharmadrone import canonicalisation
from pharmadrone.canonicalisation import (
    CanonicalisationError,
    CanonicalisationService,
    MATCH_RULES,
    MAX_BATCH_SIZE,
)
from pharmadrone.storage import configured_database, open_connection
from pharmadrone.storage import migrations
from pharmadrone.storage.migrations import (
    MIGRATIONS,
    _foundation_pr_g_canonicalisation_review_schema,
)


NOW = "2026-07-27T00:00:00+00:00"
PR_G_TABLES = {
    "canonicalisation_runs",
    "canonicalisation_candidates",
    "canonicalisation_decisions",
    "canonical_record_links",
}
ADMIN = {
    "role": "platform_admin",
    "display_name": "Platform Administrator",
    "organisation_id": "",
    "workspace_id": "",
}
REVIEWER = {
    "role": "analyst_reviewer",
    "display_name": "Human Reviewer",
    "organisation_id": "",
    "workspace_id": "",
}


def _insert_product(
    conn,
    product_id: str,
    name: str,
    *,
    status: str = "source-derived",
) -> None:
    conn.execute(
        """INSERT INTO product_profiles
        (product_id,canonical_name,normalized_name,product_type,identity_status,
        evidence_status,last_verified_at,next_review_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            product_id,
            name,
            " ".join(name.split()).casefold(),
            "medicinal-product",
            status,
            "official product identity",
            NOW,
            NOW,
        ),
    )


def _seed_canonicalisation_records(conn) -> None:
    with conn.transaction():
        for source_id, product, dev_code in (
            ("legacy-exact", "Exact Product", ""),
            ("legacy-alias", "Legacy Brand", ""),
            ("legacy-identifier", "", "NDC-001"),
            ("legacy-review", "Review Product", ""),
            ("legacy-adapter", "", ""),
        ):
            conn.execute(
                """INSERT INTO opportunities
                (id,company,product,dev_code,problem_signal,data_json)
                VALUES (?,?,?,?,?,?)""",
                (source_id, "", product, dev_code, "", "{}"),
            )
        for lead_id, company, product, source_id in (
            ("stable-adapter", "", "", ""),
            ("ambiguous-lead", "Shared Provider Alias", "", ""),
            ("bounded-01", "", "Exact Product", ""),
            ("bounded-02", "", "Exact Product", ""),
            ("bounded-03", "", "Exact Product", ""),
        ):
            conn.execute(
                """INSERT INTO opportunity_index
                (stable_lead_id,company,product,source_type,source_id)
                VALUES (?,?,?,?,?)""",
                (lead_id, company, product, "test", source_id),
            )

        _insert_product(conn, "product-exact", "Exact Product")
        _insert_product(conn, "product-review", "Review Product", status="requires-review")
        conn.execute(
            """INSERT INTO pharmaceutical_entity_aliases
            (entity_alias_id,product_id,alias_name,normalized_alias,alias_type,
            source_type,source_record_id,evidence_url,evidence_status,
            verification_status,observed_at,last_verified_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "alias-product-legacy",
                "product-exact",
                "Legacy Brand",
                "legacy brand",
                "brand",
                "official",
                "alias-source",
                "https://example.test/product-alias",
                "official product alias",
                "source-derived",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            """INSERT INTO pharmaceutical_entity_aliases
            (entity_alias_id,product_id,alias_name,normalized_alias,alias_type,
            source_type,source_record_id,evidence_url,evidence_status,
            verification_status,observed_at,last_verified_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "alias-product-canonical",
                "product-exact",
                "Exact Product",
                "exact product",
                "alternative",
                "official",
                "alias-source-2",
                "https://example.test/product-alias-2",
                "official product alias",
                "human-verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            """INSERT INTO pharmaceutical_entity_identifiers
            (entity_identifier_id,product_id,identifier_namespace,identifier_value,
            normalized_identifier,jurisdiction,source_type,source_record_id,
            evidence_url,evidence_status,verification_status,observed_at,last_verified_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "identifier-product-ndc",
                "product-exact",
                "ndc",
                "NDC-001",
                "ndc-001",
                "US",
                "official",
                "identifier-source",
                "https://example.test/product-identifier",
                "official product identifier",
                "human-verified",
                NOW,
                NOW,
            ),
        )

        for suffix in ("one", "two"):
            conn.execute(
                """INSERT INTO organisation_profiles
                (organisation_profile_id,canonical_name,normalized_name,
                organisation_type,identity_status,evidence_status,last_verified_at,
                next_review_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    f"organisation-{suffix}",
                    f"Provider {suffix.title()}",
                    f"provider {suffix}",
                    "technology-provider",
                    "source-derived",
                    "official organisation identity",
                    NOW,
                    NOW,
                ),
            )
            conn.execute(
                """INSERT INTO organisation_aliases
                (organisation_alias_id,organisation_profile_id,alias_name,
                normalized_alias,alias_type,source_type,source_record_id,
                evidence_url,evidence_status,verification_status,observed_at,
                last_verified_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"organisation-alias-{suffix}",
                    f"organisation-{suffix}",
                    "Shared Provider Alias",
                    "shared provider alias",
                    "alternative",
                    "official",
                    f"organisation-source-{suffix}",
                    f"https://example.test/provider-{suffix}",
                    "official organisation alias",
                    "source-derived",
                    NOW,
                    NOW,
                ),
            )

        for opportunity_id, source_column, source_value in (
            ("opportunity-stable", "stable_lead_id", "stable-adapter"),
            ("opportunity-legacy", "legacy_opportunity_id", "legacy-adapter"),
        ):
            conn.execute(
                """INSERT INTO opportunity_profiles
                (opportunity_profile_id,canonical_key,title,opportunity_type,
                lifecycle_status,source_type,source_record_id,evidence_url,
                evidence_status,evidence_basis,verification_status,observed_at,
                verified_at,next_review_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    opportunity_id,
                    opportunity_id,
                    opportunity_id.replace("-", " ").title(),
                    "technology-partnership",
                    "under-review",
                    "official",
                    opportunity_id,
                    f"https://example.test/{opportunity_id}",
                    "official opportunity identity",
                    "Exact legacy adapter",
                    "human-verified",
                    NOW,
                    NOW,
                    NOW,
                ),
            )
            values = {
                "stable_lead_id": None,
                "legacy_opportunity_id": None,
            }
            values[source_column] = source_value
            conn.execute(
                """INSERT INTO opportunity_identifiers
                (opportunity_identifier_id,opportunity_profile_id,identifier_type,
                identifier_namespace,identifier_value,normalized_identifier,
                legacy_opportunity_id,stable_lead_id,source_type,source_record_id,
                evidence_url,evidence_status,evidence_basis,verification_status,
                observed_at,verified_at,next_review_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"identifier-{opportunity_id}",
                    opportunity_id,
                    (
                        "stable-lead-id"
                        if source_column == "stable_lead_id"
                        else "legacy-opportunity-id"
                    ),
                    source_column.replace("_", "-"),
                    source_value,
                    source_value,
                    values["legacy_opportunity_id"],
                    values["stable_lead_id"],
                    "official",
                    f"source-{opportunity_id}",
                    f"https://example.test/{opportunity_id}",
                    "official opportunity identifier",
                    "Exact identifier adapter",
                    "human-verified",
                    NOW,
                    NOW,
                    NOW,
                ),
            )


def _service(tmp_path, filename: str = "foundation-pr-g.sqlite"):
    conn = db.connect(tmp_path / filename)
    _seed_canonicalisation_records(conn)
    return conn, CanonicalisationService(conn)


def _all_candidates(service, principal=REVIEWER, status=""):
    return service.list_candidates(
        principal, page=1, page_size=50, status=status
    ).candidates


def test_migration_20_is_fresh_additive_and_rerunnable(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-g-fresh.sqlite")
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert PR_G_TABLES.issubset(tables)
    assert [migration.version for migration in MIGRATIONS] == list(range(1, 21))
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=20"
    ).fetchone()["n"] == 1
    assert conn.execute(
        "SELECT MAX(version) AS version FROM schema_migrations"
    ).fetchone()["version"] == 20
    assert all(
        conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        for table in PR_G_TABLES
    )
    with conn.transaction():
        _foundation_pr_g_canonicalisation_review_schema(conn)
    assert conn.ensure_migrations()["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=20"
    ).fetchone()["n"] == 1


def test_upgrade_from_19_preserves_existing_production_records(tmp_path, monkeypatch):
    conn = open_connection(configured_database(tmp_path / "foundation-pr-g-upgrade.sqlite"))
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:-1])
    assert conn.ensure_migrations()["schema_version"] == 19
    with conn.transaction():
        conn.execute(
            "INSERT INTO opportunities (id,company,product) VALUES (?,?,?)",
            ("preserved-opportunity", "Preserved Company", "Preserved Product"),
        )
        conn.execute(
            """INSERT INTO patent_documents
            (patent_document_id,publication_number,jurisdiction,family_status,
            source_name,source_authority,official_source_url,google_patents_url,
            evidence_status,last_verified_at,next_review_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "preserved-patent",
                "US123",
                "US",
                "unknown",
                "official",
                "USPTO",
                "https://example.test/patent",
                "https://patents.google.com/patent/US123",
                "official",
                NOW,
                NOW,
            ),
        )
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    result = conn.ensure_migrations()
    assert result["schema_version"] == 20
    assert result["newly_applied"] == [20]
    assert conn.execute(
        "SELECT company FROM opportunities WHERE id='preserved-opportunity'"
    ).fetchone()["company"] == "Preserved Company"
    assert conn.execute(
        "SELECT publication_number FROM patent_documents "
        "WHERE patent_document_id='preserved-patent'"
    ).fetchone()["publication_number"] == "US123"
    assert all(
        conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        for table in PR_G_TABLES
    )


def test_exact_name_alias_identifier_and_opportunity_adapter_generation(tmp_path):
    conn, service = _service(tmp_path, "foundation-pr-g-rules.sqlite")
    legacy = service.generate_candidates(
        ADMIN,
        source_table="opportunities",
        max_records=100,
        permitted_rules=MATCH_RULES,
    )
    indexed = service.generate_candidates(
        ADMIN,
        source_table="opportunity_index",
        max_records=100,
        permitted_rules=MATCH_RULES,
    )
    assert legacy["status"] == indexed["status"] == "completed"
    candidates = _all_candidates(service)
    rules = {row["match_rule"] for row in candidates}
    assert {
        "exact-normalized-name",
        "exact-governed-alias",
        "exact-governed-identifier",
        "exact-stable-lead-id",
        "exact-legacy-opportunity-id",
    }.issubset(rules)
    assert all(row["review_status"] == "pending-review" for row in candidates)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM canonical_record_links"
    ).fetchone()["n"] == 0


def test_ambiguous_aliases_stay_pending_and_requires_review_targets_are_excluded(tmp_path):
    conn, service = _service(tmp_path, "foundation-pr-g-ambiguous.sqlite")
    service.generate_candidates(
        ADMIN,
        source_table="opportunity_index",
        max_records=100,
        permitted_rules=("exact-governed-alias", "exact-normalized-name"),
    )
    service.generate_candidates(
        ADMIN,
        source_table="opportunities",
        max_records=100,
        permitted_rules=("exact-normalized-name",),
    )
    candidates = _all_candidates(service)
    ambiguous = [
        row for row in candidates if row["source_record_id"] == "ambiguous-lead"
    ]
    assert len(ambiguous) == 2
    assert all(row["ambiguous"] == 1 for row in ambiguous)
    assert all(row["review_status"] == "pending-review" for row in ambiguous)
    assert not any(
        row["proposed_canonical_id"] == "product-review" for row in candidates
    )
    with pytest.raises(Exception):
        conn.execute(
            """UPDATE canonicalisation_candidates SET review_status='accepted'
            WHERE canonicalisation_candidate_id=?""",
            (ambiguous[0]["canonicalisation_candidate_id"],),
        )


def test_explicit_accept_reject_more_evidence_and_decision_history(tmp_path):
    conn, service = _service(tmp_path, "foundation-pr-g-decisions.sqlite")
    service.generate_candidates(
        ADMIN,
        source_table="opportunities",
        max_records=100,
        permitted_rules=("exact-normalized-name", "exact-governed-alias"),
    )
    candidates = _all_candidates(service)
    exact = next(
        row
        for row in candidates
        if row["source_record_id"] == "legacy-exact"
        and row["match_rule"] == "exact-normalized-name"
    )
    alias = next(
        row for row in candidates if row["source_record_id"] == "legacy-alias"
    )
    accepted = service.accept_candidate(
        REVIEWER,
        exact["canonicalisation_candidate_id"],
        "Verified against the retained product record.",
    )
    assert accepted["canonical_record_link_id"]
    link = conn.execute(
        """SELECT verification_status,active FROM canonical_record_links
        WHERE canonical_record_link_id=?""",
        (accepted["canonical_record_link_id"],),
    ).fetchone()
    assert tuple(link.values()) == ("human-verified", 1)
    with pytest.raises(Exception):
        conn.execute(
            """INSERT INTO canonical_record_links
            (canonical_record_link_id,scope_key,source_table,source_record_id,
            canonical_entity_type,canonical_id,canonicalisation_candidate_id,
            accepted_decision_id,evidence_status,evidence_basis)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                "mismatched-canonical-link",
                "platform",
                "opportunities",
                "different-source-record",
                "product",
                "product-exact",
                exact["canonicalisation_candidate_id"],
                accepted["decision_id"],
                "test",
                "Mismatched source must be rejected.",
            ),
        )

    more = service.request_more_evidence(
        REVIEWER,
        alias["canonicalisation_candidate_id"],
        "Need a second identifier source.",
    )
    assert more["decision_status"] == "requires-more-evidence"
    rejected = service.reject_candidate(
        REVIEWER,
        alias["canonicalisation_candidate_id"],
        "Additional evidence disproved the alias.",
    )
    assert rejected["decision_status"] == "rejected"
    history = service.decision_history(
        REVIEWER, alias["canonicalisation_candidate_id"]
    )
    assert [row["decision_status"] for row in history] == [
        "requires-more-evidence",
        "rejected",
    ]
    assert "disproved" in history[-1]["reviewer_notes"]
    assert all(row["reviewer_name"] == "Human Reviewer" for row in history)


def test_duplicate_and_conflicting_active_links_are_prevented(tmp_path):
    conn, service = _service(tmp_path, "foundation-pr-g-conflicts.sqlite")
    service.generate_candidates(
        ADMIN,
        source_table="opportunities",
        max_records=100,
        permitted_rules=("exact-normalized-name", "exact-governed-alias"),
    )
    product_candidates = [
        row
        for row in _all_candidates(service)
        if row["source_record_id"] == "legacy-exact"
        and row["proposed_entity_type"] == "product"
    ]
    assert {row["match_rule"] for row in product_candidates} == {
        "exact-normalized-name",
        "exact-governed-alias",
    }
    service.accept_candidate(
        REVIEWER, product_candidates[0]["canonicalisation_candidate_id"], "Accepted"
    )
    with pytest.raises(CanonicalisationError, match="already exists"):
        service.accept_candidate(
            REVIEWER,
            product_candidates[1]["canonicalisation_candidate_id"],
            "Duplicate",
        )

    service.generate_candidates(
        ADMIN,
        source_table="opportunity_index",
        max_records=100,
        permitted_rules=("exact-governed-alias",),
    )
    ambiguous = [
        row
        for row in _all_candidates(service)
        if row["source_record_id"] == "ambiguous-lead"
    ]
    service.accept_candidate(
        REVIEWER, ambiguous[0]["canonicalisation_candidate_id"], "Selected after review"
    )
    with pytest.raises(CanonicalisationError, match="conflicting"):
        service.accept_candidate(
            REVIEWER, ambiguous[1]["canonicalisation_candidate_id"], "Wrong target"
        )
    assert conn.execute(
        """SELECT COUNT(*) AS n FROM canonical_record_links
        WHERE source_record_id='ambiguous-lead' AND active=1"""
    ).fetchone()["n"] == 1


def test_tenant_isolation_bounded_pagination_and_batch_cap(tmp_path):
    conn, service = _service(tmp_path, "foundation-pr-g-scope.sqlite")
    tenant_admin = {
        **ADMIN,
        "workspace_id": "workspace-one",
    }
    tenant_reviewer = {
        **REVIEWER,
        "workspace_id": "workspace-one",
    }
    other_reviewer = {
        **REVIEWER,
        "workspace_id": "workspace-two",
    }
    result = service.generate_candidates(
        tenant_admin,
        source_table="opportunity_index",
        max_records=999,
        permitted_rules=("exact-normalized-name",),
    )
    assert result["max_records"] == MAX_BATCH_SIZE
    first = service.list_candidates(
        tenant_reviewer, page=1, page_size=1, status=""
    )
    second = service.list_candidates(
        tenant_reviewer, page=2, page_size=1, status=""
    )
    assert len(first.candidates) == len(second.candidates) == 1
    assert first.has_more is True
    assert first.candidates[0]["canonicalisation_candidate_id"] != second.candidates[0][
        "canonicalisation_candidate_id"
    ]
    assert service.list_candidates(
        other_reviewer, page=1, page_size=50, status=""
    ).candidates == ()
    with pytest.raises(CanonicalisationError):
        service.candidate_detail(
            {"role": "read_only_executive", "display_name": "Reader"},
            first.candidates[0]["canonicalisation_candidate_id"],
        )


def test_accepted_link_can_be_rolled_back_without_deleting_audit_history(tmp_path):
    conn, service = _service(tmp_path, "foundation-pr-g-rollback.sqlite")
    service.generate_candidates(
        ADMIN,
        source_table="opportunities",
        max_records=100,
        permitted_rules=("exact-normalized-name",),
    )
    candidate = next(
        row
        for row in _all_candidates(service)
        if row["source_record_id"] == "legacy-exact"
    )
    accepted = service.accept_candidate(
        REVIEWER, candidate["canonicalisation_candidate_id"], "Accepted"
    )
    rolled_back = service.supersede_link(
        ADMIN,
        accepted["canonical_record_link_id"],
        "Rollback after controlled identity review.",
    )
    assert rolled_back["status"] == "rolled-back"
    link = conn.execute(
        """SELECT active,link_status,rollback_reason FROM canonical_record_links
        WHERE canonical_record_link_id=?""",
        (accepted["canonical_record_link_id"],),
    ).fetchone()
    assert link["active"] == 0
    assert link["link_status"] == "rolled-back"
    assert "controlled identity review" in link["rollback_reason"]
    history = service.decision_history(
        REVIEWER, candidate["canonicalisation_candidate_id"]
    )
    assert [row["decision_status"] for row in history] == [
        "accepted",
        "superseded",
    ]


def test_ui_uses_services_and_does_not_generate_or_write_during_page_load():
    admin_source = Path("pharmatune_admin/pages.py").read_text()
    customer_source = Path("pharmatune_ui/pages.py").read_text()
    data_source = Path("pharmatune_ui/data.py").read_text()
    assert "Generate bounded candidate batch" in admin_source
    assert "CanonicalisationService(conn).generate_candidates" in admin_source
    assert "Accept canonical link" in customer_source
    assert "Reject candidate" in customer_source
    assert "Requires more evidence" in customer_source
    assert ".execute(" not in admin_source
    assert ".execute(" not in customer_source
    assert "CanonicalisationService" in data_source
    service_source = Path(canonicalisation.__file__).read_text().casefold()
    for forbidden in (
        "tavily",
        "requests.get",
        "httpx",
        "openai",
        "embedding",
        "semantic similarity",
    ):
        assert forbidden not in service_source
