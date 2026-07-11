from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from pharmadrone import db
from pharmadrone.pipeline import human_audit, validation_study


class Checkpoint6BTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "audit.sqlite"
        self.conn = db.connect(self.db_path)
        human_audit.ensure_schema(self.conn)
        self.record = {
            "stable_lead_id": "stable-1",
            "source_type": "FDA recall",
            "source_id": "D-TEST-1",
            "target_company": "Example Pharma",
            "company": "Example Pharma",
            "product": "Example Tablets",
            "molecule": "Example",
            "region": "United States",
            "signal_tier": "A",
            "specific_problem_subcategory": "dissolution failure",
            "external_case_study_eligible": True,
            "company_match_warning": False,
            "target_is_distributor_or_repackager_only": False,
            "official_source_url": "https://api.fda.gov/drug/enforcement.json?search=recall_number:%22D-TEST-1%22",
            "seller_fit_strength": "Strong fit",
            "has_full_report": True,
            "opportunity_score": 55,
            "original_row_hash": "immutable-hash",
            "audit_key": "fda recall|D-TEST-1",
        }

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _complete_external(self):
        return {
            "action": "Approve for external case study",
            "reviewer_name": "Auditor One",
            "audit_notes": "Checked official source and identities.",
            "evidence_checked": True,
            "company_identity_checked": True,
            "product_identity_checked": True,
            "problem_signal_checked": True,
            "evidence_supports_problem": True,
            "unresolved_warnings_acknowledged": True,
            "company_warning_resolved": True,
            "current_relevance_status": "unknown",
        }

    def test_original_source_records_remain_immutable(self):
        before = deepcopy(self.record)
        human_audit.save_audit_version(self.conn, self.record, self._complete_external())
        self.assertEqual(self.record, before)

    def test_audit_decisions_persist_and_history_is_versioned(self):
        first = human_audit.save_audit_version(self.conn, self.record, {
            "action": "Save review progress", "reviewer_name": "A", "audit_notes": "Started"
        })
        second = human_audit.save_audit_version(self.conn, self.record, {
            "action": "Approve for internal use", "reviewer_name": "B", "audit_notes": "Approved"
        })
        self.assertEqual(first["audit_version"], 1)
        self.assertEqual(second["audit_version"], 2)
        history = human_audit.audit_history(self.conn, self.record["audit_key"])
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["reviewer_name"], "B")

    def test_external_approval_requires_all_mandatory_checks(self):
        incomplete = self._complete_external()
        incomplete["product_identity_checked"] = False
        with self.assertRaises(human_audit.AuditValidationError):
            human_audit.save_audit_version(self.conn, self.record, incomplete)

    def test_outreach_requires_external_and_outreach_checks(self):
        payload = self._complete_external()
        payload.update({"action": "Approve for outreach", "technical_fit_checked": True})
        with self.assertRaises(human_audit.AuditValidationError):
            human_audit.save_audit_version(self.conn, self.record, payload)
        payload.update({
            "current_relevance_checked": True,
            "current_relevance_status": "current/relevant",
            "target_company_site_checked": True,
            "outreach_wording_reviewed": True,
        })
        saved = human_audit.save_audit_version(self.conn, self.record, payload)
        self.assertEqual(saved["outreach_gate_passed"], 1)

    def test_tier_d_cannot_be_externally_approved(self):
        bad = dict(self.record, signal_tier="D")
        with self.assertRaises(human_audit.AuditValidationError):
            human_audit.save_audit_version(self.conn, bad, self._complete_external())

    def test_unresolved_distributor_warning_blocks_external_approval(self):
        warned = dict(self.record, company_match_warning=True, target_is_distributor_or_repackager_only=True)
        payload = self._complete_external()
        payload["company_warning_resolved"] = False
        with self.assertRaises(human_audit.AuditValidationError):
            human_audit.save_audit_version(self.conn, warned, payload)

    def test_exports_include_only_approved_records(self):
        approved = human_audit.save_audit_version(self.conn, self.record, self._complete_external())
        row = dict(self.record, **approved)
        pending = dict(self.record, audit_key="fda recall|D-PENDING", source_id="D-PENDING", audit_status="pending")
        payload = human_audit.export_external_approved([row, pending]).decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(payload)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_id"], "D-TEST-1")

    def test_golden_csv_import_is_immutable_and_queueable(self):
        csv_bytes = (
            "source_type,source_id,target_company,product,signal_tier,external_case_study_eligible\n"
            "FDA recall,D-GOLD-1,Golden Pharma,Golden Tablets,A,True\n"
        ).encode("utf-8-sig")
        result = human_audit.import_benchmark_csv(self.conn, csv_bytes)
        self.assertEqual(result["row_count"], 1)
        rows = human_audit.benchmark_rows(self.conn)
        self.assertEqual(rows[0]["source_id"], "D-GOLD-1")
        self.assertTrue(rows[0]["original_row_hash"])

    def test_historical_corrections_are_seeded(self):
        keys = {r["source_id"] for r in human_audit.audit_history(self.conn)}
        self.assertTrue({"D-0202-2025", "D-0386-2024", "NCT00990444"}.issubset(keys))


    def test_audit_persists_after_database_reconnect(self):
        human_audit.save_audit_version(self.conn, self.record, {
            "action": "Approve for internal use", "reviewer_name": "Persistent Reviewer"
        })
        self.conn.close()
        self.conn = db.connect(self.db_path)
        history = human_audit.audit_history(self.conn, self.record["audit_key"])
        self.assertEqual(history[0]["reviewer_name"], "Persistent Reviewer")
        self.assertEqual(history[0]["audit_status"], "approved")

    def test_opportunity_index_row_is_not_overwritten_by_audit(self):
        indexed = {
            "stable_lead_id": "immutable-index-id",
            "company": "Immutable Pharma",
            "product": "Immutable Tablets",
            "problem_category": "dissolution failure",
            "source_type": "FDA recall",
            "source_id": "D-IMMUTABLE",
            "region": "United States",
            "score": 55,
            "grade": "B",
            "lead_status": "monitor only",
            "queue_status": "waiting",
            "has_full_report": 0,
            "evidence_hash": "frozen-evidence-hash",
            "data_json": json.dumps({"source_id": "D-IMMUTABLE", "fact": "original"}),
        }
        db.upsert_index_record(self.conn, indexed)
        before = dict(self.conn.execute(
            "SELECT * FROM opportunity_index WHERE stable_lead_id=?", ("immutable-index-id",)
        ).fetchone())
        audit_record = dict(self.record, stable_lead_id="immutable-index-id", source_id="D-IMMUTABLE", audit_key="fda recall|D-IMMUTABLE")
        human_audit.save_audit_version(self.conn, audit_record, {
            "action": "Approve for internal use", "reviewer_name": "Immutable Auditor"
        })
        after = dict(self.conn.execute(
            "SELECT * FROM opportunity_index WHERE stable_lead_id=?", ("immutable-index-id",)
        ).fetchone())
        self.assertEqual(before, after)

    def test_checkpoint6a_validation_output_is_not_modified(self):
        row = {
            "stable_lead_id": "stable-validation",
            "company": "Example Pharma",
            "product": "Example Tablets",
            "problem_category": "dissolution failure",
            "source_type": "FDA recall",
            "source_id": "D-VAL-1",
            "region": "United States",
            "score": 55,
            "grade": "B",
            "lead_status": "monitor only",
            "queue_status": "waiting",
            "has_full_report": 0,
            "seller_fit_strength": "Strong fit",
            "seller_capability": "dissolution testing",
            "evidence_links_json": json.dumps(["https://api.fda.gov/drug/enforcement.json?search=recall_number:%22D-VAL-1%22"]),
            "data_json": json.dumps({"company": "Example Pharma", "product": "Example Tablets", "source_id": "D-VAL-1", "source_type": "FDA recall"}),
        }
        result = validation_study.build_validation_study(
            [row], capability_categories=["dissolution testing"], problem_signals=["dissolution failure"], maximum_targets=1
        )
        before = validation_study.export_validation_csv(result)
        human_audit.ensure_schema(self.conn)
        after = validation_study.export_validation_csv(result)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
