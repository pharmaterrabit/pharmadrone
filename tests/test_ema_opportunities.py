from pharmadrone.connectors import ema_opportunities
from pharmadrone.pipeline import discover, opportunity_index
from pharmadrone.scheduler import config


def _payload(row):
    return {"meta": {"total_records": 1, "timestamp": "2026-07-15T06:00:00Z"}, "data": [row]}


def test_shortage_becomes_eu_opportunity_with_ema_label():
    result = ema_opportunities.parse_payload("ema_shortages", _payload({
        "medicine_affected": "Azactam", "supply_shortage_status": "Shortage ongoing",
        "international_non_proprietary_name_inn_or_common_name": "aztreonam",
        "first_published_date": "10/07/2026",
        "shortage_url": "https://www.ema.europa.eu/en/medicines/human/shortages/azactam",
    }))
    assert result.ok and result.count == 1
    candidates, _ = discover.discover_candidates(result.records, min_cluster_evidence=1)
    assert len(candidates) == 1
    assert candidates[0]["region"] == "European Union"
    assert candidates[0]["problem_category"] == "drug shortage"
    assert opportunity_index.source_type(candidates[0]) == "EMA medicine shortage"


def test_dhpc_quality_defect_becomes_distinct_ema_signal():
    result = ema_opportunities.parse_payload("ema_dhpc", _payload({
        "name_of_medicine": "Evrysdi", "active_substances": "risdiplam",
        "dhpc_type": "Quality defect", "dissemination_date": "12/06/2026",
        "dhpc_url": "https://www.ema.europa.eu/en/medicines/dhpc/evrysdi-0",
    }))
    candidates, _ = discover.discover_candidates(result.records, min_cluster_evidence=1)
    assert len(candidates) == 1
    assert opportunity_index.source_type(candidates[0]) == "EMA safety communication"


def test_only_safety_referrals_and_changed_psusa_outcomes_are_included():
    referral = {
        "referral_name": "Example product referral", "reference_number": "EMA/REF/1",
        "referral_url": "https://www.ema.europa.eu/ref/1", "safety_referral": "No",
    }
    assert ema_opportunities.parse_payload("ema_safety_referrals", _payload(referral)).count == 0
    referral["safety_referral"] = "Yes"
    assert ema_opportunities.parse_payload("ema_safety_referrals", _payload(referral)).count == 1
    psusa = {"active_substance": "examplemol", "procedure_number": "PSUSA/1",
             "psusa_url": "https://www.ema.europa.eu/psusa/1", "regulatory_outcome": "Maintenance"}
    assert ema_opportunities.parse_payload("ema_psusa_outcomes", _payload(psusa)).count == 0
    psusa["regulatory_outcome"] = "Variation"
    assert ema_opportunities.parse_payload("ema_psusa_outcomes", _payload(psusa)).count == 1


def test_all_ema_event_jobs_create_opportunities():
    names = {"ema_shortages", "ema_dhpc", "ema_safety_referrals", "ema_psusa_outcomes",
             "ema_post_authorisation_withdrawals"}
    assert all(config.source_spec(name).creates_opportunities for name in names)
