from __future__ import annotations

import csv
import io
import unittest

from pharmadrone.connectors import clinicaltrials, openfda_enforcement
from pharmadrone.pipeline import opportunity_index, validation_study


def _trial_study(nct: str, company: str, product: str, title: str, summary: str, detail: str):
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct,
                "briefTitle": title,
                "officialTitle": title,
            },
            "statusModule": {"overallStatus": "TERMINATED"},
            "designModule": {"studyType": "INTERVENTIONAL", "phases": ["PHASE1"]},
            "descriptionModule": {
                "briefSummary": summary,
                "detailedDescription": detail,
            },
            "armsInterventionsModule": {
                "interventions": [{
                    "type": "DRUG",
                    "name": product,
                    "description": detail,
                }],
            },
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": company}},
            "conditionsModule": {"conditions": ["Clinical pharmacology"]},
        }
    }


def _indexed_trial(study: dict, topic: str, problem_category: str) -> dict:
    evidence, _meta = clinicaltrials._row(study, topic)
    ent = evidence["entities"]
    candidate = {
        "company": ent["company"],
        "product": ent["product"],
        "problem_category": problem_category,
        "region": "United States",
        "evidence": [evidence],
        "score": 55,
        "grade": "B",
        "lead_status": "needs validation",
    }
    row = opportunity_index.make_index_record(candidate)
    row.update({
        "evidence_quality": "Tier 1 / high",
        "best_evidence_tier": "Tier 1 / high",
        "source_coverage_count": 1,
        "corroboration_status": "regulator-confirmed",
    })
    # The production DB row exposes source facts through data_json. Do not retain
    # a convenient top-level evidence list in this integration fixture.
    row.pop("evidence", None)
    return row


def _indexed_recalls() -> list[dict]:
    raw = [
        {
            "recall_number": "D-0816-2016",
            "recalling_firm": "Actavis Elizabeth LLC",
            "product_description": (
                "Drug tablets. Manufactured by: Actavis Elizabeth LLC, Elizabeth, NJ. "
                "Distributed by: Actavis, Inc., Parsippany, NJ."
            ),
            "reason_for_recall": "Failed dissolution specifications",
            "country": "United States",
        },
        {
            "recall_number": "D-0880-2022",
            "recalling_firm": "American Health Packaging",
            "product_description": (
                "Nitrofurantoin Capsules, USP, unit-dose package. "
                "Distributed by: American Health Packaging, Columbus, OH."
            ),
            "reason_for_recall": "Failed dissolution specifications",
            "country": "United States",
        },
    ]
    rows = []
    for evidence in openfda_enforcement._parse(raw, "dissolution / release performance"):
        ent = evidence["entities"]
        candidate = {
            "company": ent["company"],
            "product": ent["product"],
            "problem_category": "dissolution failure",
            "region": "United States",
            "evidence": [evidence],
            "score": 52,
            "grade": "B",
            "lead_status": "monitor only",
        }
        row = opportunity_index.make_index_record(candidate)
        row.update({
            "evidence_quality": "Tier 1 / high",
            "best_evidence_tier": "Tier 1 / high",
            "source_coverage_count": 1,
            "corroboration_status": "regulator-confirmed",
        })
        rows.append(row)
    return rows


class Checkpoint6A51ProductionParityTests(unittest.TestCase):
    def test_actual_validation_builder_and_csv_have_source_id_parity(self):
        rows = [
            _indexed_trial(
                _trial_study(
                    "NCT00990444",
                    "Bows Pharmaceuticals",
                    "Insulin in Dextran Matrix",
                    "Oral Insulin in a Dextran Matrix",
                    (
                        "Insulin has poor oral bioavailability and is degraded in the gastrointestinal tract; "
                        "it is currently administered by injection."
                    ),
                    (
                        "Insulin in a dextran matrix is intended to enable oral administration "
                        "as a non-injection delivery approach."
                    ),
                ),
                "bioavailability",
                "bioavailability issue",
            ),
            _indexed_trial(
                _trial_study(
                    "NCT03471559",
                    "Clinical trial sponsor",
                    "Cannabidiol",
                    "A Comparative Bioavailability Study",
                    "A comparative bioavailability study.",
                    (
                        "Cannabidiol capsules are the reference formulation and intranasal cannabidiol gel "
                        "is the test formulation for comparative bioavailability."
                    ),
                ),
                "bioavailability",
                "bioavailability issue",
            ),
            *_indexed_recalls(),
        ]
        result = validation_study.build_validation_study(
            rows,
            capability_categories=["formulation CDMO", "solubility enhancement", "dissolution testing"],
            problem_signals=["low bioavailability", "formulation challenge", "dissolution failure"],
            maximum_targets=10,
            include_monitor_only=True,
            include_preview_only=True,
            include_low_priority_archive=True,
        )
        payload = validation_study.export_validation_csv(result).decode("utf-8-sig")
        exported = list(csv.DictReader(io.StringIO(payload)))
        by_key = {(row["source_type"], row["source_id"]): row for row in exported}

        insulin = by_key[("ClinicalTrials.gov trial", "NCT00990444")]
        self.assertEqual(insulin["signal_tier"], "A")
        self.assertEqual(insulin["clinical_trial_signal_code"], "explicit_delivery_optimization")
        evidence = insulin["clinical_trial_evidence_text"].lower()
        self.assertTrue("poor oral bioavailability" in evidence or "degraded" in evidence)
        self.assertIn("dextran matrix", evidence)
        self.assertIn("oral administration", evidence)
        self.assertEqual(insulin["external_case_study_eligible"], "True")

        actavis = by_key[("FDA recall", "D-0816-2016")]
        self.assertEqual(actavis["technical_manufacturer_differs"], "False")
        self.assertEqual(actavis["target_is_distributor_or_repackager_only"], "False")

        ahp = by_key[("FDA recall", "D-0880-2022")]
        self.assertEqual(ahp["company_identity_mismatch"], "False")
        self.assertEqual(ahp["target_is_distributor_or_repackager_only"], "True")
        self.assertEqual(ahp["company_match_warning"], "True")
        self.assertEqual(ahp["external_case_study_eligible"], "False")
        self.assertIn("technical product ownership requires validation", ahp["company_role_note"].lower())

        cannabidiol = by_key[("ClinicalTrials.gov trial", "NCT03471559")]
        cbd_evidence = cannabidiol["clinical_trial_evidence_text"].lower()
        self.assertEqual(cannabidiol["signal_tier"], "A")
        self.assertIn(cannabidiol["clinical_trial_signal_code"], {"formulation_comparison", "relative_bioavailability"})
        self.assertIn("capsules", cbd_evidence)
        self.assertIn("intranasal", cbd_evidence)
        self.assertIn("gel", cbd_evidence)


if __name__ == "__main__":
    unittest.main()
