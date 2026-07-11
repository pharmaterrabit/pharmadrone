from __future__ import annotations

import csv
import io
import unittest

from pharmadrone.pipeline import precision_validation, validation_study
from tests.test_checkpoint6a3 import trial_record, recall_record


class Checkpoint6A5Tests(unittest.TestCase):
    def test_bows_oral_insulin_delivery_is_explicit_tier_a(self):
        row = trial_record(
            "Bows Pharmaceuticals", "Insulin in Dextran Matrix", "NCT00990444",
            title="Oral Insulin in a Dextran Matrix",
            summary=(
                "Insulin has poor oral bioavailability and is currently administered by injection. "
                "This study evaluates insulin in a dextran matrix to enable oral administration "
                "as a non-injection delivery approach."
            ),
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO drug delivery")
        self.assertEqual(out["signal_tier"], "A")
        self.assertEqual(out["clinical_trial_signal_code"], "explicit_delivery_optimization")
        self.assertEqual(
            out["clinical_trial_signal_reason"],
            "explicit oral insulin delivery optimisation using a dextran matrix",
        )
        evidence = out["clinical_trial_evidence_text"].lower()
        self.assertIn("poor oral bioavailability", evidence)
        self.assertIn("dextran matrix", evidence)
        self.assertIn("oral administration", evidence)
        self.assertTrue(precision_validation.validate_clinical_trial_trace(out))

    def test_sodium_thiosulfate_route_or_formulation_signal_is_tier_a(self):
        row = trial_record(
            "Insel Gruppe", "Sodium thiosulfate", "NCT02624479",
            title="Pharmacokinetic study of oral sodium thiosulfate formulations",
            summary=(
                "An oral formulation is being developed as an alternative to parenteral administration. "
                "Three gastro-resistant formulations will be evaluated."
            ),
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO drug delivery")
        self.assertEqual(out["signal_tier"], "A")
        self.assertIn(
            out["clinical_trial_signal_code"],
            {"dosage_form_or_route_comparison", "explicit_delivery_optimization"},
        )
        evidence = out["clinical_trial_evidence_text"].lower()
        self.assertIn("oral", evidence)
        self.assertIn("parenteral", evidence)
        self.assertTrue(precision_validation.validate_clinical_trial_trace(out))

    def test_isolated_test_or_prototype_formulation_does_not_validate_comparison(self):
        self.assertFalse(
            precision_validation._trial_signal_evidence_valid(
                "formulation_comparison", "Participants receive one test formulation."
            )
        )
        self.assertFalse(
            precision_validation._trial_signal_evidence_valid(
                "formulation_comparison", "Food affects uptake of a prototype formulation."
            )
        )
        row = trial_record(
            "Example Sponsor", "AG-519", "NCT00000001",
            title="Pharmacokinetic assessment of AG-519",
            summary="Food affects uptake of a prototype formulation.",
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
        self.assertNotEqual(out["clinical_trial_signal_code"], "formulation_comparison")

    def test_ag519_uses_tablet_versus_suspension_evidence(self):
        row = trial_record(
            "Agios", "AG-519", "NCT02630927",
            title="Food effect and formulation study of AG-519",
            summary="Food may affect uptake of a prototype formulation.",
            detail=(
                "The study compares prototype tablet formulations with the suspension formulation "
                "and evaluates food effects on exposure."
            ),
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
        self.assertEqual(out["signal_tier"], "A")
        self.assertEqual(out["clinical_trial_signal_code"], "formulation_comparison")
        evidence = out["clinical_trial_evidence_text"].lower()
        self.assertIn("tablet", evidence)
        self.assertIn("suspension", evidence)
        self.assertTrue(precision_validation.validate_clinical_trial_trace(out))

    def test_estradiol_uses_two_formulation_bioequivalence_evidence(self):
        title = "Bioequivalence comparison of two oral formulations of estradiol and nomegestrol"
        row = trial_record(
            "Example Sponsor", "Estradiol/nomegestrol", "NCT03749733",
            title=title,
            summary="Participants receive one test formulation in one study period.",
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
        self.assertEqual(out["signal_tier"], "A")
        self.assertEqual(out["clinical_trial_signal_code"], "formulation_comparison")
        self.assertEqual(out["clinical_trial_evidence_field"], "official_title")
        evidence = out["clinical_trial_evidence_text"].lower()
        self.assertIn("two oral formulations", evidence)
        self.assertIn("bioequivalence", evidence)
        self.assertTrue(precision_validation.validate_clinical_trial_trace(out))

    def test_actavis_exact_manufacturer_binding_is_internally_consistent(self):
        row = recall_record(
            "Actavis Elizabeth LLC", "Actavis Elizabeth LLC", "Drug tablets", "D-0816-2016",
            "Manufactured by: Actavis Elizabeth LLC. Distributed by: Actavis, Inc. "
            "Recall initiated because of failed dissolution specifications.",
        )
        out = precision_validation.annotate_record(row, seller_profile="dissolution testing")
        self.assertFalse(out["technical_manufacturer_differs"])
        self.assertFalse(out["target_is_distributor_or_repackager_only"])
        self.assertFalse(out["company_match_warning"])
        self.assertIn("named technical manufacturer: Actavis Elizabeth LLC", out["company_role_note"])
        self.assertIn("named distributor: Actavis, Inc", out["company_role_note"])

    def test_safe_company_alias_normalisation_does_not_merge_affiliates(self):
        exact_alias = recall_record(
            "Example Labs, Inc.", "Example Labs, Inc.", "Drug tablets", "D-0001-2026",
            "Manufactured by: Example Laboratories Inc. Distributed by: Distributor Corp. "
            "Failed dissolution specifications.",
        )
        out = precision_validation.annotate_record(exact_alias, seller_profile="dissolution testing")
        self.assertFalse(out["technical_manufacturer_differs"])

        affiliate = recall_record(
            "Actavis Elizabeth LLC", "Actavis Elizabeth LLC", "Drug tablets", "D-0002-2026",
            "Manufactured by: Actavis Inc. Distributed by: Actavis Elizabeth LLC. "
            "Failed dissolution specifications.",
        )
        affiliate_out = precision_validation.annotate_record(affiliate, seller_profile="dissolution testing")
        self.assertTrue(affiliate_out["technical_manufacturer_differs"])
        self.assertTrue(affiliate_out["target_is_distributor_or_repackager_only"])

    def test_american_health_packaging_distributor_caution(self):
        row = recall_record(
            "American Health Packaging", "American Health Packaging", "Nitrofurantoin Capsules", "D-0880-2022",
            "Distributed by: American Health Packaging. Failed dissolution specifications.",
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO dissolution testing")
        self.assertTrue(out["target_is_distributor_or_repackager_only"])
        self.assertTrue(out["company_match_warning"])
        self.assertFalse(out["company_identity_mismatch"])
        self.assertIn("named distributor/unit-dose packager", out["company_role_note"])
        self.assertFalse(out["external_case_study_eligible"])

    def test_production_export_preserves_signal_invariants_and_role_flags(self):
        rows = [
            trial_record(
                "Bows Pharmaceuticals", "Insulin in Dextran Matrix", "NCT00990444",
                title="Oral Insulin in a Dextran Matrix",
                summary=(
                    "Insulin has poor oral bioavailability and is currently administered by injection. "
                    "Insulin in a dextran matrix is intended to enable oral administration."
                ),
            ),
            trial_record(
                "Agios", "AG-519", "NCT02630927",
                title="Food effect and formulation study of AG-519",
                detail="Compare prototype tablet formulations with the suspension formulation.",
            ),
            recall_record(
                "American Health Packaging", "American Health Packaging", "Nitrofurantoin Capsules", "D-0880-2022",
                "Distributed by: American Health Packaging. Failed dissolution specifications.",
            ),
        ]
        result = {
            "rows": [validation_study._validation_row(row, rank) for rank, row in enumerate(rows, 1)]
        }
        payload = validation_study.export_validation_csv(result).decode("utf-8-sig")
        exported = list(csv.DictReader(io.StringIO(payload)))
        self.assertEqual(len(exported), 3)
        for row in exported:
            if row["source_type"].lower().startswith("clinicaltrials") and row["signal_tier"] == "A":
                trace = {
                    "clinical_trial_signal_code": row["clinical_trial_signal_code"],
                    "clinical_trial_signal_reason": row["clinical_trial_signal_reason"],
                    "clinical_trial_evidence_field": row["clinical_trial_evidence_field"],
                    "clinical_trial_evidence_text": row["clinical_trial_evidence_text"],
                }
                self.assertTrue(precision_validation.validate_clinical_trial_trace(trace), row["source_id"])
        ahp = next(x for x in exported if x["source_id"] == "D-0880-2022")
        self.assertEqual(ahp["target_is_distributor_or_repackager_only"], "True")
        self.assertEqual(ahp["external_case_study_eligible"], "False")


if __name__ == "__main__":
    unittest.main()
