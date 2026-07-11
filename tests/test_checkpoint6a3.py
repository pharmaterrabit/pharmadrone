from __future__ import annotations

import copy
import csv
import io
import unittest

from pharmadrone.pipeline import precision_validation, validation_study


def trial_record(company: str, product: str, nct: str, *, title: str, summary: str = "", detail: str = "", arms=None, outcomes=None):
    entities = {
        "company": company,
        "sponsor": company,
        "product": product,
        "trial_id": nct,
        "nct_id": nct,
        "source_event_id": nct,
        "study_type": "INTERVENTIONAL",
        "overall_status": "TERMINATED",
        "intervention_names": [product],
        "intervention_type": "DRUG",
        "official_title": title,
        "brief_title": title,
        "brief_summary": summary,
        "detailed_description": detail,
        "arm_labels": arms or [],
        "arm_descriptions": arms or [],
        "primary_outcomes": outcomes or [],
        "secondary_outcomes": [],
        "official_source_url": f"https://clinicaltrials.gov/study/{nct}",
    }
    evidence = [{
        "source_type": "trial",
        "source_name": "ClinicalTrials.gov",
        "record_id": nct,
        "url": f"https://clinicaltrials.gov/study/{nct}",
        "title": title,
        "raw_text": f"Title: {title}. Sponsor: {company}. Interventions: {product}.",
        "entities": entities,
    }]
    return {
        "stable_lead_id": f"stable-{nct}",
        "company": company,
        "target_company": company,
        "product": product,
        "molecule": product,
        "problem_category": "unspecified product/problem signal",
        "source_type": "ClinicalTrials.gov trial",
        "source_id": nct,
        "region": "United States",
        "opportunity_score": 55,
        "score": 55,
        "grade": "B",
        "lead_status": "needs validation",
        "queue_status": "waiting",
        "has_full_report": False,
        "seller_fit_strength": "Strong fit",
        "seller_capability": "formulation CDMO",
        "evidence_quality": "Tier 1 / high",
        "best_evidence_tier": "Tier 1 / high",
        "source_coverage_count": 1,
        "official_source_url": f"https://clinicaltrials.gov/study/{nct}",
        "what_evidence_does_not_prove": "This does not prove product failure, customer need, or commercial urgency.",
        "evidence": evidence,
    }


def recall_record(target: str, source_company: str, product: str, recall_id: str, reason: str):
    url = f'https://api.fda.gov/drug/enforcement.json?search=recall_number:%22{recall_id}%22'
    entities = {
        "company": source_company,
        "product": product,
        "source_event_id": recall_id,
        "event_reason": reason,
        "official_source_url": url,
        "recall_fields": {
            "recall_number": recall_id,
            "recalling_firm": source_company,
            "product_description": product,
            "reason_for_recall": reason,
        },
    }
    return {
        "stable_lead_id": f"stable-{recall_id}",
        "company": target,
        "target_company": target,
        "product": product,
        "molecule": product,
        "problem_category": "quality issue",
        "source_type": "FDA recall",
        "source_id": recall_id,
        "region": "United States",
        "opportunity_score": 52,
        "score": 52,
        "grade": "B",
        "lead_status": "monitor only",
        "queue_status": "report_generated",
        "has_full_report": True,
        "seller_fit_strength": "Strong fit",
        "seller_capability": "analytical/QC testing",
        "evidence_quality": "Tier 1 / high",
        "best_evidence_tier": "Tier 1 / high",
        "source_coverage_count": 1,
        "official_source_url": url,
        "what_evidence_does_not_prove": "This does not prove product-specific root cause, customer need, or commercial urgency.",
        "evidence": [{
            "source_type": "recall",
            "source_name": "openFDA Enforcement/Recalls",
            "record_id": recall_id,
            "url": url,
            "title": f"Recall {recall_id}: {source_company}",
            "raw_text": f"Recall {recall_id}. Firm: {source_company}. Product: {product}. Reason: {reason}.",
            "entities": entities,
        }],
    }


class Checkpoint6A3Tests(unittest.TestCase):
    def test_generic_pk_trials_are_not_tier_a(self):
        fixtures = [
            trial_record("Bayer", "BAY 1238097", "NCT02369029", title="First-in-human dose escalation study of BAY 1238097", summary="Assess safety, tolerability and pharmacokinetics in patients with advanced malignancies."),
            trial_record("Clovis Oncology", "Lucitanib", "NCT02202746", title="Dose escalation and efficacy study of lucitanib", summary="Evaluate safety, exposure and maximum tolerated dose."),
            trial_record("Denovo", "LY2140023", "NCT01659177", title="Clinical study of LY2140023", summary="Evaluate safety, efficacy and pharmacokinetics."),
            trial_record("Aptose", "CG-806", "NCT03893682", title="Dose escalation study of CG-806", summary="Assess safety, tolerability and pharmacokinetics."),
            trial_record("Sponsor", "Cannabidiol", "NCT03471559", title="Pharmacokinetic study of cannabidiol", summary="Measure pharmacokinetics and bioavailability as study outcomes."),
        ]
        for row in fixtures:
            out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
            self.assertIn(out["signal_tier"], {"B", "C"}, row["source_id"])
            self.assertFalse(out["external_case_study_eligible"], row["source_id"])
            self.assertEqual(out["clinical_trial_signal_code"], "", row["source_id"])
            self.assertEqual(out["clinical_trial_evidence_text"], "", row["source_id"])

    def test_strong_trials_have_specific_attributable_trace(self):
        rows = [
            trial_record("Blade Therapeutics", "BLD-2660", "NCT04001998", title="Study of BLD-2660 tablet and capsule formulations", summary="Compare tablet versus capsule formulations and assess food effect."),
            trial_record("Cerecin", "AC-1202", "NCT05028114", title="Pharmacokinetic study of a liquid formulation of AC-1202", summary="Evaluate the liquid formulation under fed and fasted conditions."),
            trial_record("Boehringer", "Asasantin ER", "NCT02273544", title="Comparison of two formulations of Asasantin ER", summary="Relative bioavailability of the formulations."),
            trial_record("Amgen", "Sotorasib", "NCT06061523", title="Food-effect and relative-bioavailability study of sotorasib", summary="Assess food effect under fed versus fasted conditions."),
            trial_record("BMS", "BMS-931699", "NCT03058822", title="Relative bioavailability of prefilled syringe versus vial presentations", summary="Compare prefilled syringe and vial delivery presentations."),
            trial_record("Eisai", "E7386", "NCT04840927", title="Targeted-release versus immediate-release E7386", summary="Compare targeted-release and immediate-release formulations."),
        ]
        for row in rows:
            out = precision_validation.annotate_record(row, seller_profile="formulation CDMO solubility enhancement")
            self.assertEqual(out["signal_tier"], "A", row["source_id"])
            self.assertIn(out["clinical_trial_signal_code"], precision_validation.TRIAL_TIER_A_SIGNAL_CODES)
            self.assertTrue(out["clinical_trial_signal_reason"])
            self.assertTrue(out["clinical_trial_evidence_field"])
            self.assertTrue(out["clinical_trial_evidence_text"])
            self.assertNotEqual(out["broad_problem_category"], "unspecified product/problem signal")
            self.assertNotEqual(out["specific_problem_subcategory"], "unspecified product/problem signal")

    def test_all_tier_a_trials_have_valid_trace(self):
        row = trial_record("Blade Therapeutics", "BLD-2660", "NCT04001998", title="Study of BLD-2660 tablet and capsule formulations", summary="Compare tablet versus capsule formulations and assess food effect.")
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
        self.assertEqual(out["signal_tier"], "A")
        self.assertIn(out["clinical_trial_signal_code"], precision_validation.TRIAL_TIER_A_SIGNAL_CODES)
        self.assertTrue(out["clinical_trial_evidence_field"])
        self.assertTrue(out["clinical_trial_evidence_text"])

    def test_contract_manufacturer_role_does_not_automatically_mismatch(self):
        fixtures = [
            recall_record("Accord Healthcare", "Intas Pharmaceuticals", "Drug tablets", "D-1001-2025", "Drug manufactured by Intas Pharmaceuticals for Accord Healthcare; failed dissolution specifications."),
            recall_record("Acella Pharmaceuticals", "Acella Pharmaceuticals", "NP Thyroid tablets", "D-1002-2025", "Manufactured for Acella Pharmaceuticals by RLC Labs; subpotent product."),
            recall_record("Astellas", "Kremers Urban", "Product capsules", "D-1003-2025", "Manufactured for Astellas by Kremers Urban; failed assay specification."),
        ]
        for row in fixtures:
            out = precision_validation.annotate_record(row, seller_profile="analytical/QC testing")
            self.assertTrue(out["company_role_difference"], row["source_id"])
            self.assertTrue(out["technical_manufacturer_differs"], row["source_id"])
            self.assertTrue(out["target_is_product_owner_or_sponsor"], row["source_id"])
            self.assertFalse(out["company_identity_mismatch"], row["source_id"])
            self.assertFalse(out["company_match_warning"], row["source_id"])

    def test_distributor_only_and_real_identity_mismatch_remain_warned(self):
        ahp = recall_record("American Health Packaging", "American Health Packaging", "Drug unit-dose package", "D-1004-2025", "Repackaged by American Health Packaging; failed dissolution specifications.")
        out = precision_validation.annotate_record(ahp, seller_profile="dissolution testing")
        self.assertTrue(out["target_is_distributor_or_repackager_only"])
        self.assertTrue(out["company_match_warning"])

        mismatch = recall_record("American Health Packaging", "Amerisource Health Services", "Drug unit-dose package", "D-1005-2025", "Failed dissolution specifications.")
        out2 = precision_validation.annotate_record(mismatch, seller_profile="dissolution testing")
        self.assertTrue(out2["company_identity_mismatch"])
        self.assertTrue(out2["company_match_warning"])

    def test_broncochem_audit_correction_hits_production_validation_path(self):
        row = recall_record("Global Corporation", "Global Corporation", "Broncochem syrup", "D-0386-2024", "Failed assay specifications.")
        row["problem_category"] = "assay/potency issue"
        # Deliberately omit manufacturer/unapproved facts from stored record: the audited correction is source-ID keyed.
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
        self.assertFalse(out["external_case_study_eligible"])
        self.assertTrue(out["company_match_warning"])
        self.assertTrue(out["company_identity_mismatch"])
        self.assertIn("unapproved-product", out["product_type_warning"].lower())
        self.assertNotIn("bulk raw material", out["product_type_warning"].lower())
        self.assertIn("manual audit correction", out["audit_correction_note"].lower())

        result = validation_study.build_validation_study(
            [row], capability_categories=["analytical/QC testing"], problem_signals=["assay/potency issue"], maximum_targets=1,
            include_monitor_only=True, include_preview_only=True, include_low_priority_archive=True,
        )
        self.assertEqual(len(result["rows"]), 1)
        exported = result["rows"][0]
        self.assertFalse(exported["external_case_study_eligible"])
        self.assertTrue(exported["company_match_warning"])
        self.assertIn("D-0386-2024", exported["audit_correction_note"] or "D-0386-2024")

    def test_known_tier_d_and_contextual_rows_stay_conservative(self):
        for product in ("CariFree oral-care kit", "Placebo control", "Dental hygiene sample boxes", "Administration of investigation of Eurofarma drug"):
            row = trial_record("Sponsor", product, "NCT99990000", title=product)
            out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
            self.assertEqual(out["signal_tier"], "D", product)
            self.assertFalse(out["external_case_study_eligible"], product)

        oncology = trial_record("Ahmad Tarhini", "LBH589 + carboplatin/etoposide", "NCT00000014", title="Dose-escalation study in oncology", summary="Safety, pharmacokinetics and maximum tolerated dose.")
        out = precision_validation.annotate_record(oncology, seller_profile="formulation CDMO")
        self.assertIn(out["signal_tier"], {"B", "C"})
        self.assertFalse(out["external_case_study_eligible"])


    def test_validation_precision_layer_does_not_call_api_or_llm(self):
        from unittest.mock import patch
        row = trial_record(
            "Blade Therapeutics", "BLD-2660", "NCT04001998",
            title="Study of BLD-2660 tablet and capsule formulations",
            summary="Compare tablet versus capsule formulations and assess food effect.",
        )
        row["problem_category"] = "formulation challenge"
        with patch("pharmadrone.llm.complete", side_effect=AssertionError("LLM called")), \
             patch("pharmadrone.connectors.clinicaltrials.search", side_effect=AssertionError("API called")), \
             patch("pharmadrone.connectors.openfda_enforcement.search", side_effect=AssertionError("API called")):
            result = validation_study.build_validation_study(
                [row], capability_categories=["formulation CDMO"],
                problem_signals=["formulation challenge"], maximum_targets=1,
            )
        self.assertEqual(len(result["rows"]), 1)

    def test_scores_and_stable_ids_unchanged_and_export_has_trace_columns(self):
        rows = [trial_record("Blade Therapeutics", "BLD-2660", "NCT04001998", title="Study of BLD-2660 tablet and capsule formulations", summary="Compare tablet versus capsule formulations and assess food effect.")]
        rows[0]["problem_category"] = "formulation challenge"
        before = copy.deepcopy([(r["stable_lead_id"], r["score"], r["opportunity_score"]) for r in rows])
        result = validation_study.build_validation_study(rows, capability_categories=["formulation CDMO"], problem_signals=["formulation challenge"], maximum_targets=1)
        self.assertEqual(before, [(r["stable_lead_id"], r["score"], r["opportunity_score"]) for r in rows])
        payload = validation_study.export_validation_csv(result).decode("utf-8-sig")
        parsed = list(csv.DictReader(io.StringIO(payload)))
        self.assertEqual(len(parsed), 1)
        for field in ("clinical_trial_signal_code", "clinical_trial_evidence_field", "clinical_trial_evidence_text", "company_identity_mismatch", "company_role_difference", "audit_correction_note"):
            self.assertIn(field, parsed[0])
        self.assertEqual(parsed[0]["clinical_trial_signal_code"], "tablet_vs_capsule")


if __name__ == "__main__":
    unittest.main()
