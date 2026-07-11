from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from pharmadrone import db
from pharmadrone.pipeline import validation_study


class Checkpoint6A52LiveProductionParityTests(unittest.TestCase):
    def test_sparse_live_index_row_exports_required_oral_insulin_trace(self):
        # Mirrors the production symptom: the old indexed row retained the NCT
        # identity/intervention and official URL, but not the newer structured
        # brief-summary/detailed-description fields used by classifier fixtures.
        evidence = {
            "source_type": "trial",
            "source_name": "ClinicalTrials.gov",
            "record_id": "NCT00990444",
            "title": "A Two Part Study of Peroral Insulin in Type 2 Diabetes",
            "url": "https://clinicaltrials.gov/study/NCT00990444",
            "raw_text": (
                "Title: A Two Part Study of Peroral Insulin in Type 2 Diabetes. "
                "Sponsor: Bows Pharmaceuticals AG. Interventions: Insulin in Dextran Matrix."
            ),
            "entities": {
                "company": "Bows Pharmaceuticals AG",
                "product": "Insulin in Dextran Matrix",
                "trial_id": "NCT00990444",
                "nct_id": "NCT00990444",
                "source_event_id": "NCT00990444",
                "intervention_names": ["Insulin in Dextran Matrix"],
                "overall_status": "SUSPENDED",
                "official_source_url": "https://clinicaltrials.gov/study/NCT00990444",
            },
        }
        indexed = {
            "stable_lead_id": "unchanged-live-stable-id",
            "company": "Bows Pharmaceuticals AG",
            "product": "Insulin in Dextran Matrix",
            "molecule": "Insulin in Dextran Matrix",
            "problem_category": "bioavailability issue",
            "source_type": "ClinicalTrials.gov trial",
            "source_id": "NCT00990444",
            "region": "",
            "score": 55,
            "grade": "B",
            "lead_status": "needs validation",
            "queue_status": "waiting",
            "has_full_report": 0,
            "evidence_quality": "not checked",
            "best_evidence_tier": "not checked",
            "source_coverage_count": 0,
            "seller_fit_strength": "Strong fit",
            "seller_capability": "formulation CDMO; solubility enhancement",
            "why_fit": "Possible technical fit; requires validation.",
            "what_evidence_does_not_prove": (
                "The evidence does not prove current customer need, commercial urgency, "
                "or a product-specific root cause."
            ),
            "evidence_links_json": json.dumps(["https://clinicaltrials.gov/study/NCT00990444"]),
            "evidence_hash": "sparse-live-record-hash",
            "novelty_status": "seen",
            "data_json": json.dumps({
                "company": "Bows Pharmaceuticals AG",
                "product": "Insulin in Dextran Matrix",
                "problem_category": "bioavailability issue",
                "source_type": "ClinicalTrials.gov trial",
                "source_id": "NCT00990444",
                "evidence": [evidence],
            }),
        }
        with tempfile.TemporaryDirectory() as tmp:
            conn = db.connect(Path(tmp) / "production_like.sqlite")
            db.upsert_index_record(conn, indexed)
            production_rows = db.fetch_index_records(conn, include_hidden=False)
            conn.close()

        result = validation_study.build_validation_study(
            production_rows,
            capability_categories=["formulation CDMO", "solubility enhancement"],
            problem_signals=["low bioavailability", "formulation challenge"],
            maximum_targets=1,
            include_monitor_only=True,
            include_preview_only=True,
            include_low_priority_archive=True,
        )
        payload = validation_study.export_validation_csv(result).decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(payload)))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["source_id"], "NCT00990444")
        self.assertEqual(row["signal_tier"], "A")
        self.assertEqual(row["clinical_trial_signal_code"], "explicit_delivery_optimization")
        self.assertIn("oral insulin delivery optimisation", row["clinical_trial_signal_reason"].lower())
        self.assertTrue(row["clinical_trial_evidence_field"])
        evidence_text = row["clinical_trial_evidence_text"].lower()
        self.assertTrue("poor oral bioavailability" in evidence_text or "degraded" in evidence_text)
        self.assertIn("dextran matrix", evidence_text)
        self.assertIn("oral insulin delivery", evidence_text)
        self.assertEqual(row["external_case_study_eligible"], "True")
        self.assertEqual(row["opportunity_score"], "55")
        self.assertEqual(indexed["stable_lead_id"], "unchanged-live-stable-id")


class Checkpoint6A52EvidenceCodeAlignmentTests(unittest.TestCase):
    @staticmethod
    def _indexed_trial(source_id: str, company: str, product: str, title: str, summary: str):
        evidence = {
            "source_type": "trial",
            "source_name": "ClinicalTrials.gov",
            "record_id": source_id,
            "title": title,
            "url": f"https://clinicaltrials.gov/study/{source_id}",
            "raw_text": f"Title: {title}. Sponsor: {company}. Interventions: {product}.",
            "entities": {
                "company": company,
                "product": product,
                "trial_id": source_id,
                "nct_id": source_id,
                "source_event_id": source_id,
                "brief_title": title,
                "official_title": title,
                "brief_summary": summary,
                "intervention_names": [product],
                "overall_status": "COMPLETED",
                "official_source_url": f"https://clinicaltrials.gov/study/{source_id}",
            },
        }
        return {
            "stable_lead_id": f"stable-{source_id}",
            "company": company,
            "product": product,
            "molecule": product,
            "problem_category": "bioavailability issue",
            "source_type": "ClinicalTrials.gov trial",
            "source_id": source_id,
            "region": "",
            "score": 50,
            "grade": "B",
            "lead_status": "needs validation",
            "queue_status": "waiting",
            "has_full_report": 0,
            "seller_fit_strength": "Strong fit",
            "seller_capability": "formulation CDMO; solubility enhancement",
            "why_fit": "Possible technical fit; requires validation.",
            "what_evidence_does_not_prove": "The evidence does not prove customer need or product-specific root cause.",
            "evidence_links_json": json.dumps([f"https://clinicaltrials.gov/study/{source_id}"]),
            "data_json": json.dumps({
                "company": company,
                "product": product,
                "problem_category": "bioavailability issue",
                "source_type": "ClinicalTrials.gov trial",
                "source_id": source_id,
                "evidence": [evidence],
            }),
        }

    def test_generic_relative_bioavailability_without_comparator_is_not_tier_a(self):
        row = self._indexed_trial(
            "NCT05943327",
            "H. Lundbeck A/S",
            "Lu AG06474",
            "Relative Bioavailability of a Capsule Formulation",
            "An open-label cross-over part investigating the relative bioavailability of a capsule formulation.",
        )
        result = validation_study.build_validation_study(
            [row],
            capability_categories=["formulation CDMO"],
            problem_signals=["low bioavailability", "formulation challenge"],
            maximum_targets=1,
            include_monitor_only=True,
            include_preview_only=True,
            include_low_priority_archive=True,
        )
        exported = list(csv.DictReader(io.StringIO(validation_study.export_validation_csv(result).decode("utf-8-sig"))))[0]
        self.assertNotEqual(exported["signal_tier"], "A")
        self.assertNotEqual(exported["clinical_trial_signal_code"], "relative_bioavailability")
        self.assertEqual(exported["external_case_study_eligible"], "False")

    def test_food_effect_is_not_mislabeled_as_relative_bioavailability(self):
        row = self._indexed_trial(
            "NCT04575818",
            "Lakefront Biotherapeutics NV",
            "GLPG4059",
            "Food Effect and Pharmacokinetic Study of GLPG4059",
            "The effect of food on the pharmacokinetics and relative bioavailability of GLPG4059 will be assessed under fed and fasted conditions.",
        )
        result = validation_study.build_validation_study(
            [row],
            capability_categories=["formulation CDMO"],
            problem_signals=["low bioavailability", "formulation challenge"],
            maximum_targets=1,
            include_monitor_only=True,
            include_preview_only=True,
            include_low_priority_archive=True,
        )
        exported = list(csv.DictReader(io.StringIO(validation_study.export_validation_csv(result).decode("utf-8-sig"))))[0]
        self.assertEqual(exported["signal_tier"], "A")
        self.assertEqual(exported["clinical_trial_signal_code"], "food_effect_fed_fasted")
        self.assertIn("food", exported["clinical_trial_evidence_text"].lower())
        self.assertIn("food effect", exported["clinical_trial_evidence_text"].lower())


if __name__ == "__main__":
    unittest.main()
