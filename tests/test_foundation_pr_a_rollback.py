from pharmadrone import db
from pharmadrone.storage.migrations import MIGRATIONS


def test_schema_version_16_starts_with_foundation_pr_a_active(tmp_path):
    conn = db.connect(tmp_path / "schema-v16.sqlite")
    assert [migration.version for migration in MIGRATIONS] == list(range(1, 17))
    assert conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()["version"] == 16
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {
        "intelligence_taxonomy_terms", "pharmaceutical_problems",
        "technology_solutions", "technology_problem_relationships",
    }.issubset(tables)
