from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from pharmadrone import db
from pharmadrone.pipeline import human_audit
from pharmadrone.storage import DatabaseConfigurationError, dispose_engines
from pharmadrone.storage.backup import build_audit_backup
from pharmadrone.storage.config import configured_database
from pharmadrone.storage.import_sqlite import import_sqlite_audit
from pharmadrone.storage.migrations import MIGRATIONS, migration_manifest


class Checkpoint6CTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "local.sqlite"
        self.conn = db.connect(self.db_path)
        human_audit.ensure_schema(self.conn)

    def tearDown(self):
        try:
            self.conn.close()
        except Exception:
            pass
        dispose_engines()
        self.tmp.cleanup()

    def test_sqlite_local_fallback_is_explicit(self):
        cfg = configured_database(self.db_path)
        self.assertEqual(cfg.backend, "sqlite")
        self.assertTrue(str(cfg.sqlite_path).endswith("local.sqlite"))

    def test_production_missing_database_url_fails_closed(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "DATABASE_BACKEND": "", "DATABASE_URL": ""}, clear=False):
            with self.assertRaises(DatabaseConfigurationError):
                configured_database()

    def test_migrations_ordered_and_idempotent(self):
        versions = [m.version for m in MIGRATIONS]
        self.assertEqual(versions, sorted(versions))
        first = self.conn.ensure_migrations()
        second = self.conn.ensure_migrations()
        self.assertEqual(first["schema_version"], versions[-1])
        self.assertEqual(second["newly_applied"], [])
        rows = self.conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
        self.assertEqual([r["version"] for r in rows], versions)


    def test_postgresql_schema_ddl_uses_postgres_identity_and_constraints(self):
        from pharmadrone.storage.migrations import _core_schema, _audit_schema, _infrastructure_schema
        class FakePostgres:
            backend = "postgresql"
            def __init__(self): self.scripts = []
            def executescript(self, script): self.scripts.append(script)
        fake = FakePostgres()
        _core_schema(fake); _audit_schema(fake); _infrastructure_schema(fake)
        ddl = "\n".join(fake.scripts)
        self.assertIn("BIGSERIAL PRIMARY KEY", ddl)
        self.assertNotIn("AUTOINCREMENT", ddl)
        self.assertIn("CHECK (outreach_approved = 0 OR external_use_approved = 1)", ddl)
        self.assertIn("schema", "schema")

    def test_postgresql_migration_manifest_contains_complete_schemas(self):
        manifest = migration_manifest()
        self.assertEqual([m["version"] for m in manifest], [1, 2, 3, 4])
        names = " ".join(m["name"] for m in manifest)
        self.assertIn("core_schema", names)
        self.assertIn("audit_schema", names)
        self.assertIn("import", names)

    def test_atomic_audit_transaction_rolls_back_on_correction_failure(self):
        record = {
            "source_type": "FDA recall", "source_id": "D-TEST-ROLLBACK",
            "target_company": "Test Co", "product": "Test Product",
            "signal_tier": "A", "external_case_study_eligible": True,
            "company_match_warning": False, "target_is_distributor_or_repackager_only": False,
        }
        payload = {
            "action": "Save review progress", "reviewer_name": "Auditor",
            "correction_type": "company role", "corrected_value": "corrected",
            "evidence_checked": True,
        }
        # Force the second insert to fail with an invalid column using a wrapper.
        original_execute = self.conn.execute
        calls = {"correction": 0}
        def failing_execute(sql, params=None):
            if "INSERT INTO human_audit_corrections" in sql:
                calls["correction"] += 1
                raise RuntimeError("forced correction failure")
            return original_execute(sql, params)
        self.conn.execute = failing_execute
        with self.assertRaises(RuntimeError):
            human_audit.save_audit_version(self.conn, record, payload)
        self.conn.execute = original_execute
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM human_audit_versions WHERE source_id=?", ("D-TEST-ROLLBACK",)
        ).fetchone()
        self.assertEqual(row["n"], 0)

    def test_database_constraint_rejects_outreach_without_external(self):
        with self.assertRaises(Exception):
            self.conn.execute(
                """INSERT INTO human_audit_versions
                (audit_key, source_type, source_id, audit_version, audit_status,
                 external_use_approved, outreach_approved, created_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                ("fda recall|BAD", "FDA recall", "BAD", 1, "approved", 0, 1, "2026-01-01T00:00:00+00:00"),
            )
            self.conn.commit()

    def test_backup_export_has_schema_counts_and_checksums(self):
        payload = build_audit_backup(self.conn)
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            self.assertIn("manifest.json", zf.namelist())
            self.assertIn("audit_backup.json", zf.namelist())
            manifest = json.loads(zf.read("manifest.json"))
            backup = json.loads(zf.read("audit_backup.json"))
        self.assertEqual(manifest["schema_version"], max(m.version for m in MIGRATIONS))
        self.assertIn("human_audit_versions", manifest["record_counts"])
        self.assertIn("file_sha256", manifest)
        self.assertEqual(backup["record_counts"]["human_audit_corrections"], 3)


    def test_unavailable_postgresql_is_controlled_and_does_not_fallback(self):
        from pharmadrone.storage.config import DatabaseConfig
        from pharmadrone.storage.database import open_connection, DatabaseUnavailableError
        cfg = DatabaseConfig(
            backend="postgresql",
            url="postgresql+psycopg://invalid:invalid@127.0.0.1:1/missing",
            app_env="test", connect_timeout=1, connect_retries=1,
        )
        with self.assertRaises(DatabaseUnavailableError) as ctx:
            open_connection(cfg)
        message = str(ctx.exception).lower()
        self.assertIn("unavailable", message)
        self.assertNotIn("invalid:invalid", message)

    def test_sqlite_audit_import_is_repeat_safe(self):
        source_path = Path(self.tmp.name) / "source.sqlite"
        source = db.connect(source_path)
        human_audit.ensure_schema(source)
        csv_payload = (
            "source_type,source_id,target_company,product,signal_tier,external_case_study_eligible\n"
            "FDA recall,D-IMPORT-1,Import Co,Import Product,A,TRUE\n"
        ).encode()
        human_audit.import_benchmark_csv(source, csv_payload, "golden.csv")
        rows = human_audit.benchmark_rows(source)
        record = next(r for r in rows if r["source_id"] == "D-IMPORT-1")
        human_audit.save_audit_version(source, record, {
            "action": "Approve for internal use", "reviewer_name": "Importer",
            "evidence_checked": True, "company_identity_checked": True,
            "product_identity_checked": True, "problem_signal_checked": True,
            "evidence_supports_problem": True,
        })
        source_seed = source.execute(
            "SELECT reviewed_at FROM human_audit_versions WHERE source_id='D-0202-2025' AND audit_version=1"
        ).fetchone()["reviewed_at"]
        source.close()

        target_path = Path(self.tmp.name) / "target.sqlite"
        target = db.connect(target_path)
        human_audit.ensure_schema(target)
        first = import_sqlite_audit(source_path, source_label="legacy-production", destination_conn=target, require_postgresql=False)
        second = import_sqlite_audit(source_path, source_label="legacy-production", destination_conn=target, require_postgresql=False)
        self.assertGreaterEqual(first["destination_after"]["audit_queue_records"], 1)
        self.assertTrue(second["already_imported"])
        versions = target.execute("SELECT COUNT(*) AS n FROM human_audit_versions WHERE source_id='D-IMPORT-1'").fetchone()["n"]
        self.assertEqual(versions, 1)
        imported_seed = target.execute(
            "SELECT reviewed_at FROM human_audit_versions WHERE source_id='D-0202-2025' AND audit_version=1"
        ).fetchone()["reviewed_at"]
        self.assertEqual(imported_seed, source_seed)
        target.close()

    def test_health_status_reports_safe_counts(self):
        with patch.dict(os.environ, {
            "APP_ENV": "test", "DATABASE_BACKEND": "sqlite", "SQLITE_PATH": str(self.db_path), "DATABASE_URL": ""
        }, clear=False):
            status = db.database_status()
        self.assertEqual(status["backend"], "sqlite")
        self.assertEqual(status["connection_status"], "healthy")
        self.assertGreaterEqual(status["correction_count"], 3)
        self.assertNotIn("url", status)
        self.assertNotIn("password", json.dumps(status).lower())


class OptionalPostgresIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not configured")
    def test_postgresql_schema_creation_and_repeat_migrations(self):
        from pharmadrone.storage.config import normalize_postgres_url, DatabaseConfig
        from pharmadrone.storage.database import open_connection, get_engine
        cfg = DatabaseConfig(backend="postgresql", url=normalize_postgres_url(os.environ["TEST_DATABASE_URL"]), app_env="test")
        conn = open_connection(cfg)
        first = conn.ensure_migrations()
        second = conn.ensure_migrations()
        self.assertEqual(first["schema_version"], max(m.version for m in MIGRATIONS))
        self.assertEqual(second["newly_applied"], [])
        for table in ("audit_benchmark_batches", "audit_queue_records", "human_audit_versions", "human_audit_corrections"):
            self.assertTrue(conn.has_table(table))
        conn.close()


if __name__ == "__main__":
    unittest.main()
