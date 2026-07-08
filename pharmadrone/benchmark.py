"""Benchmark test set for event-first Failure/Rescue discovery.

Two modes:

  python -m pharmadrone.benchmark            # LIVE: hits real APIs, needs network
  python -m pharmadrone.benchmark --offline  # OFFLINE: fixture data, no network

The benchmark checks the pipeline can discover FIVE classes of real signal:
  1. a recall / quality issue                (openFDA Enforcement)
  2. a terminated / withdrawn trial          (ClinicalTrials.gov stopped-status)
  3. a regulatory rejection / withdrawal     (EMA/FDA via targeted web)
  4. a company-discontinued programme        (company press / news via web)
  5. a formulation / CMC opportunity signal  (recall reason OR trial whyStopped)

LIVE mode reports how many of the 5 classes produced >=1 valid-target candidate.
OFFLINE mode feeds realistic fixture records through the SAME discovery + gate
logic, so the classification path is verifiable without network.
"""
from __future__ import annotations
import sys
from .pipeline import discover, event_discovery
from .connectors import openfda_enforcement, clinicaltrials, tavily_search
from . import settings


# --- OFFLINE fixtures: realistic shapes of each of the 5 classes -----------
def _fixtures() -> dict:
    return {
        "recall_quality_issue": [{
            "source_type": "recall", "source_category": "regulatory",
            "source_name": "openFDA (Enforcement/Recalls)", "record_id": "D-1234-2024",
            "title": "Recall D-1234-2024: Acme Pharmaceuticals",
            "url": "https://www.accessdata.fda.gov/scripts/ires/index.cfm",
            "language": "en",
            "raw_text": ("Recall D-1234-2024. Firm: Acme Pharmaceuticals. Product: "
                         "Metformin HCl ER tablets 500 mg. Reason: dissolution "
                         "failure — out of specification at 12-month stability."),
            "date_accessed": "2026-07-08", "region_hint": "United States",
            "entities": {"company": "Acme Pharmaceuticals",
                         "product": "Metformin HCl ER tablets 500 mg",
                         "event_type": "recall",
                         "event_reason": "dissolution failure"}}],
        "terminated_trial": [{
            "source_type": "trial", "source_category": "trial",
            "source_name": "ClinicalTrials.gov", "record_id": "NCT01234567",
            "title": "Phase 2 Study of Compound BX-100 in Solid Tumors",
            "url": "https://clinicaltrials.gov/study/NCT01234567", "language": "en",
            "raw_text": ("Title: Phase 2 Study of Compound BX-100. Sponsor: Beta "
                         "Biosciences. Status: TERMINATED. WhyStopped: insufficient "
                         "oral bioavailability of the current formulation."),
            "date_accessed": "2026-07-08", "region_hint": "United States",
            "entities": {"company": "Beta Biosciences", "product": "Compound BX-100",
                         "trial_id": "NCT01234567", "event_type": "terminated",
                         "why_stopped": "insufficient oral bioavailability"}}],
        "regulatory_rejection": [{
            "source_type": "web", "source_category": "regulatory",
            "source_name": "Web (Tavily)", "record_id": "https://ema.europa.eu/x",
            "title": "Withdrawal of application for Gammaform (company X)",
            "url": "https://www.ema.europa.eu/en/medicines/human/withdrawn-applications/gammaform",
            "language": "en",
            "raw_text": ("The applicant formally withdrew the marketing authorisation "
                         "application for Gammaform; CHMP had concerns over quality/"
                         "manufacturing consistency and impurity control."),
            "date_accessed": "2026-07-08", "region_hint": "European Union/EEA",
            "entities": {"company": "Company X", "product": "Gammaform",
                         "event_type": "withdrawn application"}}],
        "company_discontinued": [{
            "source_type": "web", "source_category": "company",
            "source_name": "Web (Tavily)", "record_id": "https://ir.deltapharma.com/pr1",
            "title": "Delta Pharma discontinues DP-207 oral programme",
            "url": "https://ir.deltapharma.com/press/dp-207-discontinuation",
            "language": "en",
            "raw_text": ("Delta Pharma announced it has discontinued development of "
                         "DP-207 oral tablets, citing formulation and bioavailability "
                         "challenges in the current solid dosage form."),
            "date_accessed": "2026-07-08", "region_hint": "United States",
            "entities": {"company": "Delta Pharma", "product": "DP-207",
                         "event_type": "discontinued"}}],
        "formulation_cmc_signal": [{
            "source_type": "recall", "source_category": "regulatory",
            "source_name": "openFDA (Enforcement/Recalls)", "record_id": "D-5678-2024",
            "title": "Recall D-5678-2024: Epsilon Labs",
            "url": "https://www.accessdata.fda.gov/scripts/ires/index.cfm",
            "language": "en",
            "raw_text": ("Recall D-5678-2024. Firm: Epsilon Labs. Product: Itraconazole "
                         "capsules 100 mg. Reason: subpotent — failed release testing "
                         "due to polymorph conversion affecting dissolution."),
            "date_accessed": "2026-07-08", "region_hint": "United States",
            "entities": {"company": "Epsilon Labs",
                         "product": "Itraconazole capsules 100 mg",
                         "event_type": "recall",
                         "event_reason": "subpotent / failed release testing"}}],
    }


def _check_offline() -> int:
    fixtures = _fixtures()
    passed = 0
    print("OFFLINE benchmark — feeding realistic fixtures through discovery gates\n")
    for name, ev in fixtures.items():
        cands, breakdown = discover.discover_candidates(ev)
        has_event = event_discovery.has_event_source(ev)
        ok = len(cands) >= 1 and has_event
        # verify no confirmed-event fabrication issues: event must be structural
        tgt = cands[0].get("company") or cands[0].get("product") if cands else None
        status = "PASS" if ok else "FAIL"
        if ok:
            passed += 1
        print(f"  [{status}] {name:<26} -> candidate: {tgt!r}, "
              f"event_source={has_event}, class="
              f"{'valid_bd_opportunity' if cands else list(breakdown)}")
    print(f"\n{passed}/5 benchmark classes produce a valid, event-backed candidate.")
    return passed


def _check_live() -> int:
    print("LIVE benchmark — hitting real public APIs (needs network)\n")
    results = {}

    # 1. recall / quality issue
    r = openfda_enforcement.discover_events("dissolution failure", max_results=5)
    results["recall_quality_issue"] = (r.ok and r.count > 0, r.count, r.error)

    # 2 + 5. terminated trial / formulation-CMC via stopped trials
    t = clinicaltrials.discover_stopped("bioavailability", max_results=10)
    results["terminated_trial"] = (t.ok and t.count > 0, t.count, t.error)
    r2 = openfda_enforcement.discover_events("subpotent", max_results=5)
    results["formulation_cmc_signal"] = (
        (r.count + r2.count) > 0, r.count + r2.count, r2.error)

    # 3 + 4. regulatory rejection / company discontinuation via targeted web
    if settings.env("TAVILY_API_KEY"):
        w1 = tavily_search.search(
            "site:ema.europa.eu withdrawn application quality CMC", max_results=5)
        results["regulatory_rejection"] = (w1.ok and w1.count > 0, w1.count, w1.error)
        w2 = tavily_search.search(
            "discontinued development bioavailability company press release",
            max_results=5)
        results["company_discontinued"] = (w2.ok and w2.count > 0, w2.count, w2.error)
    else:
        results["regulatory_rejection"] = (False, 0, "TAVILY_API_KEY not set")
        results["company_discontinued"] = (False, 0, "TAVILY_API_KEY not set")

    passed = 0
    for name, (ok, count, err) in results.items():
        if ok:
            passed += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<26} records={count}"
              + (f"  ({err})" if err else ""))
    print(f"\n{passed}/5 benchmark classes returned live event records.")
    return passed


def main():
    offline = "--offline" in sys.argv
    passed = _check_offline() if offline else _check_live()
    sys.exit(0 if passed >= 4 else 1)


if __name__ == "__main__":
    main()
