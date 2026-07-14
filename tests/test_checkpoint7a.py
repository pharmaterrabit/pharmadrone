from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pharmadrone import db
from pharmadrone.pipeline import seller_case_study
from pharmadrone.storage import dispose_engines
from pharmadrone.storage.migrations import MIGRATIONS


class Checkpoint7ATests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "seller-case-study.sqlite"

    def tearDown(self):
        dispose_engines()
        self.tmp.cleanup()

    @staticmethod
    def _records(approved: bool = True) -> list[dict]:
        problems = [
            "poor solubility",
            "stability issue",
            "impurity issue",
            "dissolution failure",
            "formulation challenge",
        ]
        rows = []
        for index, problem in enumerate(problems, start=1):
            is_approved = approved and index == 1
            rows.append({
                "audit_key": f"audit-{index}",
                "stable_lead_id": f"lead-{index}",
                "target_company": f"Target {index} Pharma",
                "company": f"Target {index} Pharma",
                "product": f"Product {index}",
                "molecule": f"Molecule {index}",
                "problem_category": problem,
                "problem_signal": problem,
                "source_type": "openfda_enforcement",
                "source_id": f"D-{1000 + index}-2026",
                "region": "United States",
                "opportunity_score": 80 - index,
                "score": 80 - index,
                "grade": "A",
                "lead_status": "needs validation",
                "queue_status": "waiting",
                "has_full_report": True,
                "best_evidence_tier": "Tier 1 / high",
                "evidence_quality": "Tier 1 / high",
                "source_coverage_count": 2,
                "audit_status": "approved" if is_approved else "pending",
                "audit_version": 1 if is_approved else 0,
                "external_case_study_eligible": True,
                "external_use_approved": is_approved,
                "external_gate_passed": is_approved,
                "reviewer_name": "Reviewer" if is_approved else "",
            })
        return rows

    def test_real_provider_profile_has_official_capability_sources(self):
        profile = seller_case_study.HOVIONE_PROFILE
        self.assertEqual(profile["provider_name"], "Hovione")
        self.assertEqual(len(profile["capabilities"]), 7)
        self.assertGreaterEqual(len(profile["evidence_sources"]), 4)
        self.assertTrue(all(source["url"].startswith("https://www.hovione.com/") for source in profile["evidence_sources"]))

    def test_customer_export_contains_only_human_approved_targets(self):
        result = seller_case_study.build_real_case_study(self._records(approved=True), limit=5)
        self.assertEqual(result["metrics"]["candidate_count"], 5)
        self.assertEqual(result["metrics"]["approved_count"], 1)
        markdown = seller_case_study.export_customer_markdown(result).decode("utf-8")
        self.assertIn("Target 1 Pharma", markdown)
        self.assertNotIn("Target 2 Pharma", markdown)
        self.assertIn("not proof of customer demand", markdown)

    def test_customer_export_is_locked_without_external_approval(self):
        result = seller_case_study.build_real_case_study(self._records(approved=False), limit=5)
        self.assertEqual(result["status"], "human_validation_required")
        with self.assertRaises(seller_case_study.CustomerExportBlocked):
            seller_case_study.export_customer_html(result)

    def test_migration_7_persists_immutable_case_study_snapshot(self):
        self.assertEqual(max(m.version for m in MIGRATIONS), 7)
        conn = db.connect(self.path)
        for table in ("seller_profiles", "seller_case_studies", "seller_case_study_targets"):
            self.assertTrue(conn.has_table(table), table)
        result = seller_case_study.build_real_case_study(self._records(approved=True), limit=5)
        case_study_id = seller_case_study.save_snapshot(conn, result, created_by="Checkpoint Test")
        saved = conn.execute(
            "SELECT candidate_count,approved_count,created_by FROM seller_case_studies WHERE case_study_id=?",
            (case_study_id,),
        ).fetchone()
        self.assertEqual(saved["candidate_count"], 5)
        self.assertEqual(saved["approved_count"], 1)
        self.assertEqual(saved["created_by"], "Checkpoint Test")
        self.assertEqual(
            conn.execute("SELECT COUNT(*) AS n FROM seller_case_study_targets WHERE case_study_id=?", (case_study_id,)).fetchone()["n"],
            5,
        )
        conn.close()

    def test_customer_ui_routes_case_study_to_principal_and_validation(self):
        text = (Path(__file__).resolve().parents[1] / "pharmatune_ui" / "app.py").read_text(encoding="utf-8")
        self.assertIn("pages.case_studies(principal,_navigate)", text)
        page = (Path(__file__).resolve().parents[1] / "pharmatune_ui" / "pages.py").read_text(encoding="utf-8")
        self.assertIn("Customer exports are locked", page)
        self.assertIn("Open selected candidate in Human Validation", page)


if __name__ == "__main__":
    unittest.main()
