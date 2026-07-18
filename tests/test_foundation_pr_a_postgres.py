import os

import pytest

from pharmadrone.storage.migrations import _foundation_pr_a_schema


class _PostgresDDLRecorder:
    backend = "postgresql"

    def __init__(self):
        self.executed = []
        self.scripts = []

    def executescript(self, script):
        self.scripts.append(script)

    def execute(self, statement, params=None):
        self.executed.append(statement)


def test_postgres_trigger_ddl_avoids_invalid_update_of_combination():
    conn = _PostgresDDLRecorder()
    _foundation_pr_a_schema(conn)
    trigger_sql = "\n".join(conn.executed)
    assert "CREATE OR REPLACE FUNCTION foundation_pr_a_validate()" in trigger_sql
    assert "BEFORE INSERT OR UPDATE ON intelligence_taxonomy_terms" in trigger_sql
    assert "BEFORE INSERT OR UPDATE ON pharmaceutical_problems" in trigger_sql
    assert "BEFORE INSERT OR UPDATE ON technology_solutions" in trigger_sql
    assert "BEFORE INSERT OR UPDATE ON technology_problem_relationships" in trigger_sql
    assert "UPDATE OF" not in trigger_sql


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured")
def test_real_postgresql_migration_16_is_rerunnable():
    from pharmadrone.storage.config import DatabaseConfig, normalize_postgres_url
    from pharmadrone.storage.database import open_connection

    conn = open_connection(DatabaseConfig(
        backend="postgresql",
        url=normalize_postgres_url(os.environ["TEST_DATABASE_URL"]),
        app_env="test",
    ))
    first = conn.ensure_migrations()
    second = conn.ensure_migrations()
    assert first["schema_version"] >= 16
    assert second["newly_applied"] == []
    for table in (
        "intelligence_taxonomy_terms", "pharmaceutical_problems",
        "technology_solutions", "technology_problem_relationships",
    ):
        assert conn.has_table(table)
    conn.close()
