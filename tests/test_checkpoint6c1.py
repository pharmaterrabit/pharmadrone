from __future__ import annotations

from datetime import timedelta
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from pharmadrone import db
from pharmadrone.connectors.base import record
from pharmadrone.pipeline import human_audit
from pharmadrone.scheduler import config, repository
from pharmadrone.scheduler.errors import SchedulerError
from pharmadrone.scheduler.orchestrator import run_sources
from pharmadrone.storage import dispose_engines
from pharmadrone.storage.migrations import MIGRATIONS


class Checkpoint6C1Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "scheduler.sqlite"
        self.real_connect = db.connect
        self.conn = self.real_connect(self.path)
        human_audit.ensure_schema(self.conn)
        repository.ensure_source_states(self.conn)

    def tearDown(self):
        try: self.conn.close()
        except Exception: pass
        dispose_engines()
        self.tmp.cleanup()

    def _factory(self, *args, **kwargs):
        return self.real_connect(self.path)

    def _recall(self, rid="D-SCHED-1", reason="Failed dissolution specifications"):
        return record(
            "recall", "openFDA (Enforcement/Recalls)", rid, f"Recall {rid}: Test Pharma",
            f"https://api.fda.gov/drug/enforcement.json?search=recall_number:%22{rid}%22",
            f"Recall {rid}. Firm: Test Pharma. Product: Test Tablets. Reason: {reason}.",
            source_category="regulatory",
            entities={
                "company": "Test Pharma", "product": "Test Tablets", "source_event_id": rid,
                "event_type": "recall", "event_reason": reason, "direct_problem_evidence": True,
                "issue_category": "dissolution / release performance", "country": "United States",
                "official_source_url": f"https://api.fda.gov/drug/enforcement.json?search=recall_number:%22{rid}%22",
                "recall_fields": {"recall_number": rid, "reason_for_recall": reason, "report_date": "2026-07-01"},
            },
        )

    def test_migration_5_operational_tables(self):
        self.assertGreaterEqual(max(m.version for m in MIGRATIONS), 5)
        for table in (
            "source_refresh_state", "refresh_runs", "source_refresh_runs", "source_records",
            "source_record_changes", "source_url_checks", "scheduler_notifications", "opportunity_refresh_flags",
        ):
            self.assertTrue(self.conn.has_table(table), table)

    def test_cadence_and_next_due_calculation(self):
        now = config.utc_now()
        for cadence, days in (("daily",1),("every_two_days",2),("weekly",7),("monthly",30)):
            due = config.parse_time(config.next_due(cadence, from_time=now))
            self.assertAlmostEqual((due-now).total_seconds(), days * 86400, delta=2)

    def test_initial_daily_every_two_day_weekly_monthly_selection(self):
        due = set(repository.due_source_names(self.conn))
        self.assertIn("openfda_enforcement", due)
        self.assertIn("clinicaltrials", due)
        self.assertIn("europepmc", due)
        self.assertIn("monthly_maintenance", due)

    def test_ensure_source_states_does_not_repeat_unchanged_updates(self):
        before = self.conn.execute(
            "SELECT updated_at FROM source_refresh_state WHERE source_name='openfda_enforcement'"
        ).fetchone()["updated_at"]
        repository.ensure_source_states(self.conn)
        after = self.conn.execute(
            "SELECT updated_at FROM source_refresh_state WHERE source_name='openfda_enforcement'"
        ).fetchone()["updated_at"]
        self.assertEqual(after, before)

    def test_scheduler_summary_is_read_only_for_source_refresh_state(self):
        before = self.conn.execute(
            "SELECT updated_at FROM source_refresh_state WHERE source_name='openfda_enforcement'"
        ).fetchone()["updated_at"]
        repository.scheduler_summary(self.conn)
        after = self.conn.execute(
            "SELECT updated_at FROM source_refresh_state WHERE source_name='openfda_enforcement'"
        ).fetchone()["updated_at"]
        self.assertEqual(after, before)

    def test_dry_run_does_not_write_refresh_run(self):
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory):
            result = run_sources(selected=["openfda_enforcement"], force=True, dry_run=True)
        self.assertEqual(result["status"], "Dry run")
        row = self.conn.execute("SELECT COUNT(*) AS n FROM refresh_runs").fetchone()
        self.assertEqual(row["n"], 0)

    def test_repeat_safe_ingestion_and_duplicate_prevention(self):
        rec = self._recall()
        with self.conn.transaction():
            first = repository.ingest_source_records(self.conn, run_id="r1", source_name="openfda_enforcement", records=[rec])
        with self.conn.transaction():
            second = repository.ingest_source_records(self.conn, run_id="r2", source_name="openfda_enforcement", records=[rec])
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(second["duplicates_prevented"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS n FROM source_records").fetchone()["n"], 1)

    def test_unchanged_source_record_updates_last_seen_once_per_day(self):
        rec = self._recall()
        with self.conn.transaction(): repository.ingest_source_records(self.conn, run_id="r1", source_name="openfda_enforcement", records=[rec])
        first = dict(self.conn.execute("SELECT * FROM source_records").fetchone())
        with self.conn.transaction(): repository.ingest_source_records(self.conn, run_id="r2", source_name="openfda_enforcement", records=[rec])
        same_day = dict(self.conn.execute("SELECT * FROM source_records").fetchone())
        self.assertEqual(same_day["last_seen_at"], first["last_seen_at"])
        self.assertEqual(same_day["last_refresh_run_id"], first["last_refresh_run_id"])
        self.conn.execute("UPDATE source_records SET last_seen_at='2026-01-01T00:00:00+00:00'")
        self.conn.commit()
        with self.conn.transaction(): repository.ingest_source_records(self.conn, run_id="r3", source_name="openfda_enforcement", records=[rec])
        next_day = dict(self.conn.execute("SELECT * FROM source_records").fetchone())
        self.assertEqual(next_day["last_refresh_run_id"], "r3")

    def test_change_detection_preserves_history(self):
        rec1 = self._recall(reason="Failed dissolution specifications")
        rec2 = self._recall(reason="Failed dissolution specifications; status updated")
        with self.conn.transaction(): repository.ingest_source_records(self.conn, run_id="r1", source_name="openfda_enforcement", records=[rec1])
        with self.conn.transaction(): changed = repository.ingest_source_records(self.conn, run_id="r2", source_name="openfda_enforcement", records=[rec2])
        self.assertEqual(changed["updated"], 1)
        rows = self.conn.execute("SELECT * FROM source_record_changes ORDER BY id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertIn("raw_text", json.loads(rows[-1]["fields_changed_json"]))

    def test_source_lock_prevents_overlap(self):
        with repository.source_lock(self.conn, "clinicaltrials") as first:
            with repository.source_lock(self.conn, "clinicaltrials") as second:
                self.assertTrue(first)
                self.assertFalse(second)

    def test_cursor_rolls_back_after_failure(self):
        self.conn.execute("UPDATE source_refresh_state SET last_cursor='old', last_watermark='2026-01-01' WHERE source_name='openfda_enforcement'")
        self.conn.commit()
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", side_effect=SchedulerError("permanent schema error", "source schema change", retryable=False)):
            result = run_sources(selected=["openfda_enforcement"], force=True)
        self.assertEqual(result["status"], "Failed")
        state = repository.source_state(self.conn, "openfda_enforcement")
        self.assertEqual(state["last_cursor"], "old")
        self.assertEqual(state["last_watermark"], "2026-01-01")

    def test_temporary_retry_then_success(self):
        calls = {"n": 0}
        def fetch(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise SchedulerError("timeout", "temporary network failure", retryable=True)
            return {"records": [], "cursor_after": "c", "watermark_after": "w", "metadata": {}}
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", side_effect=fetch):
            result = run_sources(selected=["openfda_enforcement"], force=True)
        self.assertEqual(result["status"], "Healthy")
        self.assertEqual(calls["n"], 3)

    def test_permanent_error_is_not_retried_indefinitely(self):
        calls = {"n": 0}
        def fetch(*args, **kwargs):
            calls["n"] += 1
            raise SchedulerError("unauthorized", "authentication failure", retryable=False)
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", side_effect=fetch):
            result = run_sources(selected=["openfda_enforcement"], force=True)
        self.assertEqual(result["status"], "Failed")
        self.assertEqual(calls["n"], 1)

    def test_partial_budget_status(self):
        payload = {"records": [], "cursor_after": "c", "watermark_after": "w", "partial": True, "estimated_spend": 2.0, "metadata": {}}
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", return_value=payload):
            result = run_sources(selected=["tavily"], force=True)
        self.assertEqual(result["status"], "Partial")
        self.assertEqual(result["results"][0]["status"], "Partial")

    def test_new_recall_creates_indexed_opportunity_without_report(self):
        payload = {"records": [self._recall()], "cursor_after": "c", "watermark_after": "2026-07-01", "metadata": {}}
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", return_value=payload):
            result = run_sources(selected=["openfda_enforcement"], force=True)
        self.assertEqual(result["status"], "Healthy")
        idx = self.conn.execute("SELECT * FROM opportunity_index").fetchall()
        self.assertEqual(len(idx), 1)
        self.assertEqual(idx[0]["has_full_report"], 0)
        self.assertEqual(idx[0]["queue_status"], "waiting")

    def test_frozen_benchmark_isolation(self):
        csv_payload = (
            "source_type,source_id,target_company,product,signal_tier,external_case_study_eligible\n"
            "FDA recall,D-GOLD-1,Gold Co,Gold Product,A,TRUE\n"
        ).encode()
        human_audit.import_benchmark_csv(self.conn, csv_payload, "golden.csv")
        before = self.conn.execute("SELECT COUNT(*) AS n FROM audit_queue_records").fetchone()["n"]
        payload = {"records": [self._recall()], "cursor_after": "c", "watermark_after": "w", "metadata": {}}
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", return_value=payload):
            run_sources(selected=["openfda_enforcement"], force=True)
        after = self.conn.execute("SELECT COUNT(*) AS n FROM audit_queue_records").fetchone()["n"]
        self.assertEqual(before, after)

    def test_github_workflow_parses_and_contains_schedule_dispatch(self):
        path = Path(__file__).resolve().parents[1] / ".github/workflows/pharmatune_refresh.yml"
        text = path.read_text()
        data = yaml.load(text, Loader=yaml.BaseLoader)
        self.assertIn("schedule", data["on"])
        self.assertIn("workflow_dispatch", data["on"])
        self.assertIn('17 3 * * *', text)
        self.assertNotIn("secrets.DATABASE_URL }}\n        run:", text)

    def test_due_selection_respects_each_cadence_group(self):
        future = config.iso(config.utc_now() + timedelta(days=10))
        past = config.iso(config.utc_now() - timedelta(minutes=1))
        self.conn.execute("UPDATE source_refresh_state SET next_due_at=?", (future,))
        for name in ("openfda_enforcement", "clinicaltrials", "europepmc", "monthly_maintenance"):
            self.conn.execute("UPDATE source_refresh_state SET next_due_at=? WHERE source_name=?", (past, name))
        self.conn.commit()
        due = set(repository.due_source_names(self.conn))
        self.assertEqual(due, {"openfda_enforcement", "clinicaltrials", "europepmc", "monthly_maintenance"})

    def test_success_persists_cursor_watermark_and_future_due(self):
        payload = {"records": [], "cursor_after": "cursor-2", "watermark_after": "2026-07-12", "metadata": {}}
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", return_value=payload):
            result = run_sources(selected=["openfda_enforcement"], force=True)
        self.assertEqual(result["status"], "Healthy")
        state = repository.source_state(self.conn, "openfda_enforcement")
        self.assertEqual(state["last_cursor"], "cursor-2")
        self.assertEqual(state["last_watermark"], "2026-07-12")
        self.assertGreater(config.parse_time(state["next_due_at"]), config.utc_now())

    def test_retry_failed_selects_degraded_source_even_before_next_due(self):
        future = config.iso(config.utc_now() + timedelta(days=5))
        self.conn.execute("UPDATE source_refresh_state SET last_status='Degraded', next_due_at=? WHERE source_name='openfda_enforcement'", (future,))
        self.conn.commit()
        self.assertIn("openfda_enforcement", repository.due_source_names(self.conn, include_failed_only=True))

    def test_scheduler_path_never_calls_llm(self):
        payload = {"records": [self._recall()], "cursor_after": "c", "watermark_after": "w", "metadata": {}}
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", return_value=payload), \
             patch("pharmadrone.llm.complete", side_effect=AssertionError("LLM called")):
            result = run_sources(selected=["openfda_enforcement"], force=True)
        self.assertEqual(result["status"], "Healthy")

    def test_memory_sync_runs_once_after_successful_cycle_with_material_records(self):
        payload = {"records": [self._recall()], "cursor_after": "c", "watermark_after": "w", "metadata": {}}
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", return_value=payload), \
             patch("pharmadrone.scheduler.orchestrator.pharmaceutical_memory.sync_from_opportunity_index") as sync_opportunities, \
             patch("pharmadrone.scheduler.orchestrator.pharmaceutical_memory.sync_ema_medicines") as sync_ema, \
             patch("pharmadrone.scheduler.orchestrator.pharmaceutical_memory.sync_fda_orange_book") as sync_fda:
            result = run_sources(selected=["openfda_enforcement"], force=True)
        self.assertEqual(result["status"], "Healthy")
        sync_opportunities.assert_called_once()
        sync_ema.assert_called_once()
        sync_fda.assert_called_once()

    def test_memory_sync_skips_successful_cycle_without_material_records(self):
        payload = {"records": [], "cursor_after": "c", "watermark_after": "w", "metadata": {}}
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", return_value=payload), \
             patch("pharmadrone.scheduler.orchestrator.pharmaceutical_memory.sync_from_opportunity_index") as sync_opportunities:
            result = run_sources(selected=["openfda_enforcement"], force=True)
        self.assertEqual(result["status"], "Healthy")
        sync_opportunities.assert_not_called()

    def test_memory_sync_skips_partial_cycle_with_material_records(self):
        payload = {"records": [self._recall()], "cursor_after": "c", "watermark_after": "w", "partial": True, "metadata": {}}
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", return_value=payload), \
             patch("pharmadrone.scheduler.orchestrator.pharmaceutical_memory.sync_from_opportunity_index") as sync_opportunities, \
             patch("pharmadrone.scheduler.orchestrator.pharmaceutical_memory.sync_ema_medicines") as sync_ema, \
             patch("pharmadrone.scheduler.orchestrator.pharmaceutical_memory.sync_fda_orange_book") as sync_fda:
            result = run_sources(selected=["openfda_enforcement"], force=True)
        self.assertEqual(result["status"], "Partial")
        sync_opportunities.assert_not_called()
        sync_ema.assert_not_called()
        sync_fda.assert_not_called()

    def test_postgresql_scheduler_schema_uses_supported_ddl(self):
        from pharmadrone.storage.migrations import _scheduler_schema
        class FakePostgres:
            backend = "postgresql"
            def __init__(self): self.scripts = []
            def executescript(self, script): self.scripts.append(script)
        fake = FakePostgres()
        _scheduler_schema(fake)
        ddl = "\n".join(fake.scripts)
        self.assertIn("BIGSERIAL PRIMARY KEY", ddl)
        self.assertIn("FOREIGN KEY (run_id) REFERENCES refresh_runs(run_id)", ddl)
        self.assertNotIn("AUTOINCREMENT", ddl)


    def test_monthly_url_check_history_is_persisted(self):
        self.conn.execute(
            "INSERT INTO source_records (source_type, source_id, source_name, official_source_url, source_updated_at, "
            "content_checksum, record_json, first_seen_at, last_seen_at, last_changed_at, last_refresh_run_id, active) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
            ("recall", "D-URL-1", "openFDA", "https://example.test/official", "2026-07-01",
             "checksum", "{}", config.iso(), config.iso(), config.iso(), None),
        )
        self.conn.commit()
        payload = {
            "records": [], "cursor_after": "url-checks:1", "watermark_after": "",
            "metadata": {"url_checks": [{
                "source_type": "recall", "source_id": "D-URL-1",
                "official_source_url": "https://example.test/official",
                "status": "available", "http_status": 200, "error_summary": "",
            }]},
        }
        with patch("pharmadrone.scheduler.orchestrator.db.connect", side_effect=self._factory), \
             patch("pharmadrone.scheduler.sources.fetch_source", return_value=payload):
            result = run_sources(selected=["monthly_maintenance"], force=True)
        self.assertIn(result["status"], {"Healthy", "Partial"})
        row = self.conn.execute("SELECT * FROM source_url_checks WHERE source_id='D-URL-1'").fetchone()
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["http_status"], 200)

    def test_overdue_source_creates_notification_ready_event(self):
        overdue = config.iso(config.utc_now() - timedelta(days=3))
        self.conn.execute(
            "UPDATE source_refresh_state SET next_due_at=? WHERE source_name='openfda_enforcement'",
            (overdue,),
        )
        self.conn.commit()
        summary = repository.scheduler_summary(self.conn)
        events = [e for e in summary["notification_ready_events"] if e.get("source_name") == "openfda_enforcement"]
        self.assertTrue(events)
        self.assertEqual(events[0]["event_type"], "source_refresh_overdue")

    def test_scheduler_status_contains_no_credentials(self):
        summary = repository.scheduler_summary(self.conn)
        blob = json.dumps(summary).lower()
        self.assertNotIn("postgresql://", blob)
        self.assertNotIn("password", blob)


if __name__ == "__main__":
    unittest.main()
