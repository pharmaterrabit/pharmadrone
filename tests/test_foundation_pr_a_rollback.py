from pharmadrone import db
from pharmadrone.storage.migrations import MIGRATIONS


def test_foundation_pr_a_remains_active_after_later_migrations(tmp_path):
    conn = db.connect(tmp_path / "foundation-pr-a-active.sqlite")
    assert [migration.version for migration in MIGRATIONS] == list(range(1, 18))
    assert conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()["version"] == 17
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {
        "intelligence_taxonomy_terms", "pharmaceutical_problems",
        "technology_solutions", "technology_problem_relationships",
    }.issubset(tables)
