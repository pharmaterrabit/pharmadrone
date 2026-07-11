from __future__ import annotations

import copy
import unittest

from pharmadrone.pipeline import precision_validation, validation_study


def trial_record(company: str, product: str, nct: str, *, title: str, summary: str = "", detail: str = "", arms=None, outcomes=None, problem="formulation challenge"):
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
        "problem_category": problem,
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
        "evidence_quality": "Tier 1 / high",
        "best_evidence_tier": "Tier 1 / high",
        "source_coverage_count": 1,
        "official_source_url": f"https://clinicaltrials.gov/study/{nct}",
        "what_evidence_does_not_prove": "This does not prove product failure, customer need, or commercial urgency.",
        "evidence": evidence,
    }


def recall_record(company: str, product: str, recall_id: str, reason: str, *, source_product=None):
    source_product = source_product or product
    url = f'https://api.fda.gov/drug/enforcement.json?search=recall_number:%22{recall_id}%22'
    entities = {
        "company": company,
        "product": source_product,
        "source_event_id": recall_id,
        "event_reason": reason,
        "issue_category": "quality issue",
        "official_source_url": url,
        "recall_fields": {
            "recall_number": recall_id,
            "recalling_firm": company,
            "product_description": source_product,
            "reason_for_recall": reason,
        },
    }
    evidence = [{
        "source_type": "recall",
        "source_name": "openFDA Enforcement/Recalls",
        "record_id": recall_id,
        "url": url,
        "title": f"Recall {recall_id}: {company}",
        "raw_text": f"Recall {recall_id}. Firm: {company}. Product: {source_product}. Reason: {reason}.",
        "entities": entities,
    }]
    return {
        "stable_lead_id": f"stable-{recall_id}",
        "company": company,
        "target_company": company,
        "product": product,
        "molecule": product,
        "problem_category": "stability issue",
        "problem_signal": "sterility/contamination issue",
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
        "evidence_quality": "Tier 1 / high",
        "best_evidence_tier": "Tier 1 / high",
        "source_coverage_count": 1,
        "official_source_url": url,
        "what_evidence_does_not_prove": "This does not prove product-specific root cause, customer need, or commercial urgency.",
        "evidence": evidence,
    }


class Checkpoint6A2LiveRows(unittest.TestCase):
    def test_row46_bld2660_is_tier_a_from_structured_registry_fields(self):
        row = trial_record(
            "Blade Therapeutics", "BLD-2660", "NCT04001998",
            title="Study of BLD-2660 tablet and capsule formulations",
            summary="This study compares tablet versus capsule formulations and evaluates the effect of food on pharmacokinetics.",
            arms=["BLD-2660 tablet fasted", "BLD-2660 capsule fed"],
            outcomes=["Relative bioavailability and food effect PK parameters"],
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO solubility enhancement")
        self.assertEqual(out["signal_tier"], "A")
        reason = out["clinical_trial_signal_reason"].lower()
        self.assertIn("formulation comparison", reason)
        self.assertIn("food-effect", reason)

    def test_row55_ac1202_liquid_formulation_is_tier_a(self):
        row = trial_record(
            "Cerecin", "AC-1202", "NCT05028114",
            title="Pharmacokinetic study of a liquid formulation of AC-1202",
            summary="Evaluate the liquid formulation under fed and fasted conditions and assess food effect.",
            outcomes=["PK of liquid AC-1202 formulation"],
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
        self.assertEqual(out["signal_tier"], "A")
        reason = out["clinical_trial_signal_reason"].lower()
        self.assertIn("liquid-formulation", reason)
        self.assertIn("food-effect", reason)

    def test_row79_broncochem_has_company_and_regulatory_warning_not_api(self):
        reason = (
            "Unapproved new drug concern. Broncochem syrup was manufactured by Unipharma Laboratories "
            "and distributed by Global Corporation."
        )
        row = recall_record("Global Corporation", "Broncochem syrup", "D-0079-2024", reason)
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO analytical/QC")
        warning = out["product_type_warning"].lower()
        self.assertNotIn("bulk raw material", warning)
        self.assertNotIn("api / excipient", warning)
        self.assertIn("unapproved-product", warning)
        self.assertTrue(out["company_match_warning"])
        self.assertIn("manufacturer", out["company_match_warning_note"].lower())
        self.assertFalse(out["external_case_study_eligible"])
        exclusion = out["exclusion_reason"].lower()
        self.assertIn("unapproved", exclusion)
        self.assertIn("manufacturer", exclusion)

    def test_row84_taxonomy_uses_structured_fda_reason_and_is_consistent(self):
        row = recall_record(
            "Health Innovations Pharmacy", "Glycerin ophthalmic solution", "D-0084-2024",
            "Lack of Assurance of Sterility: ophthalmic solution may not be sterile due to microbial contamination.",
        )
        row["problem_signal"] = "sterility/contamination issue"
        row["problem_category"] = "stability issue"
        out = precision_validation.annotate_record(row, seller_profile="sterile manufacturing support analytical/QC")
        self.assertEqual(out["broad_problem_category"], "sterility/contamination issue")
        self.assertEqual(out["specific_problem_subcategory"], "sterility / contamination issue")
        self.assertNotIn("shelf-life", out["specific_problem_subcategory"].lower())
        self.assertNotEqual(out["broad_problem_category"], "stability issue")


    def test_oncology_pk_combo_remains_contextual_and_external_false(self):
        row = trial_record(
            "Ahmad Tarhini", "LBH589 + carboplatin/etoposide", "NCT00140000",
            title="Dose-escalation study of LBH589 with carboplatin and etoposide in solid tumors",
            summary="Evaluate safety, pharmacokinetics and maximum tolerated dose in oncology.",
            outcomes=["Pharmacokinetics and maximum tolerated dose"],
        )
        out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
        self.assertIn(out["signal_tier"], {"B", "C"})
        self.assertFalse(out["external_case_study_eligible"])
        self.assertNotIn("food-effect", out["clinical_trial_signal_reason"].lower())
        self.assertNotIn("bioequivalence", out["clinical_trial_signal_reason"].lower())

    def test_known_tier_d_exclusions_remain(self):
        for product in ("CariFree dental sample boxes", "Placebo control", "Administration of investigation of Eurofarma drug"):
            row = trial_record("Example Sponsor", product, "NCT12345678", title=product)
            out = precision_validation.annotate_record(row, seller_profile="formulation CDMO")
            self.assertEqual(out["signal_tier"], "D", product)
            self.assertFalse(out["external_case_study_eligible"], product)

    def test_100_target_validation_builds_without_score_or_id_mutation(self):
        rows = []
        for i in range(100):
            rid = f"D-{1000+i:04d}-2025"
            row = recall_record(f"Company {i}", f"Product {i} tablets", rid, "Failed dissolution specifications")
            row["stable_lead_id"] = f"stable-{i:03d}"
            row["score"] = 50 + (i % 10)
            row["opportunity_score"] = row["score"]
            rows.append(row)
        before = [(r["stable_lead_id"], r["score"]) for r in rows]
        result = validation_study.build_validation_study(
            rows,
            capability_categories=["dissolution testing", "formulation CDMO"],
            problem_signals=["dissolution failure"],
            maximum_targets=100,
            include_monitor_only=True,
            include_preview_only=True,
            include_low_priority_archive=True,
        )
        self.assertEqual(len(result["rows"]), 100)
        self.assertEqual(before, [(r["stable_lead_id"], r["score"]) for r in rows])


if __name__ == "__main__":
    unittest.main()
