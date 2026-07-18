import pytest

from pharmadrone import db
from pharmadrone.storage.migrations import MIGRATIONS, _foundation_pr_a_schema


def _seed_ids(conn):
    rows = conn.execute(
        "SELECT taxonomy_namespace,code,term_id FROM intelligence_taxonomy_terms"
    ).fetchall()
    return {(row["taxonomy_namespace"], row["code"]): row["term_id"] for row in rows}


def test_foundation_pr_a_migration_is_additive_idempotent_and_seeded(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-a.sqlite")
    before = {
        table: set(conn.columns(table))
        for table in ("opportunities", "research_publications", "account_organisations", "commercial_events", "patent_documents")
    }
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    expected = {
        "intelligence_taxonomy_terms", "pharmaceutical_problems",
        "technology_solutions", "technology_problem_relationships",
    }
    assert expected.issubset(tables)
    assert max(m.version for m in MIGRATIONS) >= 16
    assert {
        "problem_domain", "solution_domain", "solution_type",
    } == {row["taxonomy_namespace"] for row in conn.execute(
        "SELECT DISTINCT taxonomy_namespace FROM intelligence_taxonomy_terms"
    ).fetchall()}
    assert {"technology", "tool", "service", "platform", "process", "capability"}.issubset(
        {row["code"] for row in conn.execute(
            "SELECT code FROM intelligence_taxonomy_terms WHERE taxonomy_namespace='solution_type'"
        ).fetchall()}
    )
    assert "poor-solubility" in {
        row["code"] for row in conn.execute(
            "SELECT code FROM intelligence_taxonomy_terms WHERE taxonomy_namespace='problem_domain'"
        ).fetchall()
    }
    assert "scale-up-variability" in {
        row["code"] for row in conn.execute(
            "SELECT code FROM intelligence_taxonomy_terms WHERE taxonomy_namespace='problem_domain'"
        ).fetchall()
    }
    counts_before = {
        table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in expected
    }
    with conn.transaction():
        _foundation_pr_a_schema(conn)
    assert before == {table: set(conn.columns(table)) for table in before}
    assert counts_before == {
        table: conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        for table in expected
    }


def test_domain_neutral_problem_solution_and_general_relationship(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-a-relationship.sqlite")
    ids = _seed_ids(conn)
    now = "2026-07-18T00:00:00+00:00"
    with conn.transaction():
        conn.execute(
            "INSERT INTO pharmaceutical_problems "
            "(problem_id,canonical_key,display_name,taxonomy_term_id,definition,identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            ("problem-scale-up", "scale-up-variability", "Scale-up variability", ids[("problem_domain", "scale-up-variability")],
             "A reproducibility problem during scale-up.", "controlled", "taxonomy seed", now, now),
        )
        conn.execute(
            "INSERT INTO technology_solutions "
            "(technology_id,canonical_key,display_name,taxonomy_term_id,solution_type_term_id,mechanism_summary,scope_note,maturity_status,identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("solution-process-platform", "process-analytics-platform", "Process analytics platform",
             ids[("solution_domain", "analytical-and-quality-control-technologies")], ids[("solution_type", "platform")],
             "A platform for process measurement.", "Cross-domain capability.", "commercial", "controlled", "curated seed", now, now),
        )
        conn.execute(
            "INSERT INTO technology_problem_relationships "
            "(relationship_id,technology_id,problem_id,relationship_type,relationship_statement,source_type,source_id,evidence_url,evidence_status,inference_status,confidence_score,confidence_basis,verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("rel-process-scale-up", "solution-process-platform", "problem-scale-up", "may-address",
             "The platform may support investigation of scale-up variability.", "curated", "seed-1",
             "https://example.test/evidence", "curated seed", "inferred", 0.6, "Representative cross-domain seed", now, now),
        )
    row = conn.execute("SELECT * FROM technology_problem_relationships WHERE relationship_id='rel-process-scale-up'").fetchone()
    assert row["relationship_type"] == "may-address"
    assert row["inference_status"] == "inferred"
    assert row["confidence_score"] == 0.6


def test_duplicate_and_validation_constraints_are_enforced(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-a-constraints.sqlite")
    ids = _seed_ids(conn)
    now = "2026-07-18T00:00:00+00:00"
    with conn.transaction():
        conn.execute(
            "INSERT INTO pharmaceutical_problems "
            "(problem_id,canonical_key,display_name,taxonomy_term_id,definition,identity_status,evidence_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("problem-1", "analytical-limitation", "Analytical limitation", ids[("problem_domain", "assay-method-limitation")],
             "A measurement limitation.", "controlled", "curated", now, now),
        )
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO pharmaceutical_problems "
            "(problem_id,canonical_key,display_name,taxonomy_term_id,definition,identity_status,evidence_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("problem-2", "analytical-limitation", "Duplicate analytical limitation", ids[("problem_domain", "assay-method-limitation")],
             "Duplicate.", "controlled", "curated", now, now),
        )
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO intelligence_taxonomy_terms "
            "(term_id,taxonomy_namespace,term_kind,code,label,definition,version,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("duplicate-term", "problem_domain", "domain", "analytical-and-quality-control", "Duplicate", "Duplicate", "1.0", now, now),
        )
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO technology_problem_relationships "
            "(relationship_id,technology_id,problem_id,relationship_type,relationship_statement,source_type,source_id,evidence_url,evidence_status,inference_status,confidence_score,confidence_basis,verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("bad-confidence", "missing-solution", "problem-1", "supports", "Invalid", "test", "1",
             "https://example.test/evidence", "test", "reported", 1.5, "Invalid", now, now),
        )


def test_existing_reads_and_new_foundation_are_isolated(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-a-compatibility.sqlite")
    assert conn.execute("SELECT COUNT(*) AS n FROM opportunities").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM patent_documents").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM research_publications").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM technology_solutions").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM pharmaceutical_problems").fetchone()["n"] == 0
