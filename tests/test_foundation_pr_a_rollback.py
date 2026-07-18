from pharmadrone import db
from pharmadrone.storage.migrations import MIGRATIONS


def test_schema_version_15_starts_without_activating_foundation_pr_a(tmp_path):
    conn = db.connect(tmp_path / "schema-v15.sqlite")
    assert [migration.version for migration in MIGRATIONS] == list(range(1, 16))
    assert conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()["version"] == 15
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert not {
        "intelligence_taxonomy_terms", "pharmaceutical_problems",
        "technology_solutions", "technology_problem_relationships",
    }.intersection(tables)
