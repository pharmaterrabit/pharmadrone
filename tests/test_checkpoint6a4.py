from __future__ import annotations

import copy
import csv
import io
import unittest

from pharmadrone.pipeline import precision_validation, validation_study
from tests.test_checkpoint6a3 import trial_record, recall_record


class Checkpoint6A4Tests(unittest.TestCase):
    def test_evidence_span_is_centred_on_relative_bioavailability_phrase(self):
        generic = (
            "This first-in-human oncology study evaluates safety, tolerability, dose escalation, "
            "maximum tolerated dose and general pharmacokinetics in participants with advanced malignancies. "
        ) * 5
        qualifying = (
            "A later study part compares the relative bioavailability of a Liquid Service Formulation "
            "with BAY1238097 tablets under matched dosing conditions."
        )
        row = trial_record(
            "Bayer", "BAY1238097", "NCT02369029",
            title="First-in-human study of BAY1238097",
            summary=generic + qualifying,
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO solubility enhancement")
        self.assertEqual(out["signal_tier"], "A")
        self.assertEqual(out["clinical_trial_signal_code"], "relative_bioavailability")
        snippet = out["clinical_trial_evidence_text"]
        self.assertIn("relative bioavailability", snippet.lower())
        self.assertIn("liquid service formulation", snippet.lower())
        self.assertTrue(precision_validation.validate_clinical_trial_trace(out))
        self.assertFalse(snippet.lower().startswith("this first-in-human oncology study"))

    def test_every_tier_a_trace_validates_and_is_not_duplicated(self):
        fixtures = [
            trial_record(
                "Blade Therapeutics", "BLD-2660", "NCT04001998",
                title="Study of BLD-2660 tablet and capsule formulations",
                summary="Compare tablet versus capsule formulations and assess food effect.",
            ),
            trial_record(
                "Cerecin", "AC-1202", "NCT05028114",
                title="Pharmacokinetic study of a liquid formulation of AC-1202",
                summary="Evaluate the liquid formulation under fed and fasted conditions and assess food effect.",
            ),
            trial_record(
                "Boehringer", "Asasantin ER", "NCT02273544",
                title="A Pharmacokinetic Study of Three New Formulations of Asasantin ER",
            ),
        ]
        for row in fixtures:
            out = precision_validation.annotate_record(row, seller_profile="formulation CDMO solubility enhancement")
            self.assertEqual(out["signal_tier"], "A", row["source_id"])
            self.assertTrue(precision_validation.validate_clinical_trial_trace(out), row["source_id"])
            reason_parts = [x.strip() for x in out["clinical_trial_signal_reason"].split(";") if x.strip()]
            field_parts = [x.strip() for x in out["clinical_trial_evidence_field"].split(";") if x.strip()]
            text_parts = [x.strip() for x in out["clinical_trial_evidence_text"].split(" | ") if x.strip()]
            self.assertEqual(len(reason_parts), len(set(reason_parts)), row["source_id"])
            self.assertEqual(len(field_parts), len(set(field_parts)), row["source_id"])
            self.assertEqual(len(text_parts), len(set(text_parts)), row["source_id"])

    def test_asasantin_multi_formulation_title_is_tier_a(self):
        title = "A Pharmacokinetic Study of Three New Formulations of Asasantin ER"
        row = trial_record("Boehringer", "Asasantin ER", "NCT02273544", title=title)
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
        self.assertEqual(out["signal_tier"], "A")
        self.assertEqual(out["clinical_trial_signal_code"], "formulation_comparison")
        self.assertEqual(out["clinical_trial_signal_reason"], "explicit comparison of three Asasantin ER formulations")
        self.assertEqual(out["clinical_trial_evidence_field"], "official_title")
        self.assertIn(title.lower(), out["clinical_trial_evidence_text"].lower())
        self.assertTrue(out["external_case_study_eligible"])

    def test_amerisource_audit_correction_reaches_production_export(self):
        row = recall_record(
            "American Health Packaging", "American Health Packaging",
            "Unit-dose drug product", "D-0202-2025", "Failed dissolution specifications."
        )
        annotated = precision_validation.annotate_record(row, seller_profile="dissolution testing formulation CDMO")
        self.assertTrue(annotated["company_match_warning"])
        self.assertTrue(annotated["company_identity_mismatch"])
        self.assertIn("Amerisource", annotated["company_role_note"])
        self.assertIn("American Health Packaging", annotated["company_role_note"])
        self.assertIn("D-0202-2025", annotated["audit_correction_note"])
        self.assertFalse(annotated["external_case_study_eligible"])

        result = validation_study.build_validation_study(
            [row], capability_categories=["dissolution testing"],
            problem_signals=["dissolution failure"], maximum_targets=1,
            include_monitor_only=True, include_preview_only=True,
            include_low_priority_archive=True,
        )
        payload = validation_study.export_validation_csv(result).decode("utf-8-sig")
        exported = list(csv.DictReader(io.StringIO(payload)))[0]
        self.assertEqual(exported["company_match_warning"], "True")
        self.assertEqual(exported["company_identity_mismatch"], "True")
        self.assertEqual(exported["external_case_study_eligible"], "False")
        self.assertIn("D-0202-2025", exported["audit_correction_note"])

    def test_actavis_elizabeth_is_bound_to_manufacturer_not_distributor(self):
        reason = (
            "Manufactured by: Actavis Elizabeth LLC. Distributed by: Actavis, Inc. "
            "Recall initiated because of failed dissolution specifications."
        )
        row = recall_record(
            "Actavis Elizabeth LLC", "Actavis Elizabeth LLC",
            "Drug tablets", "D-0816-2016", reason,
        )
        out = precision_validation.annotate_record(row, seller_profile="dissolution testing")
        self.assertFalse(out["company_identity_mismatch"])
        self.assertFalse(out["target_is_distributor_or_repackager_only"])
        self.assertFalse(out["company_match_warning"])
        self.assertIn("named technical manufacturer: Actavis Elizabeth LLC", out["company_role_note"])
        self.assertIn("named distributor: Actavis, Inc", out["company_role_note"])

    def test_existing_corrections_and_tier_d_rows_remain(self):
        bronco = recall_record("Global Corporation", "Global Corporation", "Broncochem syrup", "D-0386-2024", "Failed assay specifications.")
        bronco_out = precision_validation.annotate_record(bronco, seller_profile="formulation CDMO")
        self.assertFalse(bronco_out["external_case_study_eligible"])
        self.assertTrue(bronco_out["company_match_warning"])

        for product in ("CariFree oral-care kit", "Placebo control", "Dental hygiene sample boxes", "Administration of investigation of Eurofarma drug"):
            row = trial_record("Sponsor", product, "NCT99990000", title=product)
            out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
            self.assertEqual(out["signal_tier"], "D", product)
            self.assertFalse(out["external_case_study_eligible"], product)

    def test_scores_and_ids_are_not_mutated(self):
        row = trial_record(
            "Bayer", "BAY1238097", "NCT02369029",
            title="Relative bioavailability of Liquid Service Formulation compared with tablets",
        )
        before = copy.deepcopy((row["stable_lead_id"], row["score"], row["opportunity_score"]))
        precision_validation.annotate_record(row, seller_profile="formulation CDMO")
        self.assertEqual(before, (row["stable_lead_id"], row["score"], row["opportunity_score"]))


if __name__ == "__main__":
    unittest.main()
