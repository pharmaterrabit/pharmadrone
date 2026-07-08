"""Event-first Failure/Rescue discovery.

The problem this solves: feeding failure-signal *phrases* (e.g. "terminated trial
poor solubility United States") into the connectors returned 0 evidence from
openFDA/ClinicalTrials.gov, because recall and trial records don't contain those
phrases. Result: only literature APIs produced evidence, all correctly rejected
as generic — 0 reports.

This module queries the structured event sources the way they are actually
indexed, BEFORE any generic literature:
  1. openFDA Enforcement — search the recall REASON field for concrete quality
     terms (dissolution failure, subpotent, cGMP, sterility, …).
  2. ClinicalTrials.gov — filter by stopped overallStatus and parse whyStopped.
  3. Tavily — source-targeted queries (site:fda.gov recall …, site:ema.europa.eu
     withdrawn application …, company discontinuation press releases …).

Evidence returned here carries source_category regulatory/trial/company/news and
real entities, so the existing quality gates in discover.py turn them into valid
BD candidates. Academic literature is NOT gathered here — it is added by the
normal path only as supporting mechanism context once a target exists.
"""
from __future__ import annotations
from ..connectors import openfda_enforcement, clinicaltrials, tavily_search

# Concrete recall-reason terms (mirrors openfda_enforcement.RECALL_REASON_TERMS
# but kept explicit here so failure discovery is self-documenting).
RECALL_REASON_TERMS = [
    "dissolution failure", "stability", "impurity", "degradation", "sterility",
    "particulate matter", "contamination", "failed specifications", "subpotent",
    "superpotent", "packaging defect", "labeling mix-up", "container closure",
    "leakage", "crystallization", "precipitation", "failed release testing",
    "cGMP", "manufacturing defect",
]

# Topic hints to narrow stopped-trial discovery toward formulation/CMC relevance.
TRIAL_STOP_TOPICS = [
    "", "drug supply issue", "formulation", "bioavailability",
    "manufacturing", "stability",
]

# Source-targeted web queries (regulatory + company first, never generic lit).
def web_event_queries(regions_active_codes: set[str]) -> list[str]:
    q = [
        "site:fda.gov recall dissolution tablet",
        "site:fda.gov recall stability drug product",
        "site:fda.gov warning letter CMC deficiencies drug product",
        "site:ema.europa.eu withdrawn application quality manufacturing formulation",
        "site:ema.europa.eu refused application quality CMC",
        "site:clinicaltrials.gov terminated drug supply issue",
        "discontinued development bioavailability company press release",
        "pipeline discontinued formulation issue annual report",
        "complete response letter CMC deficiencies drug",
    ]
    # region-specific regulators only when that region is active
    if "AU" in regions_active_codes:
        q.append("site:tga.gov.au recall medicine stability")
    if "UK" in regions_active_codes:
        q.append("site:gov.uk drug alert recall stability")
    if "SA" in regions_active_codes:
        q.append("site:sfda.gov.sa recall medicine")
    return q


def discover_events(profile: dict, cost, per_source: int = 8, log=None) -> tuple[list[dict], dict]:
    """Run event-first discovery across recall, trial-status, and targeted web
    sources. Returns (evidence, coverage). Coverage is per-source stats for the
    summary panel. Every connector failure is captured, not swallowed."""
    say = log or (lambda m: None)
    enabled = {k for k, v in profile.get("sources", {}).items() if v.get("enabled")}
    active_codes = {r.get("code") for r in profile.get("regions", []) if r.get("active")}
    evidence: list[dict] = []
    coverage = {
        "openFDA (Enforcement/Recalls)": _blank(),
        "ClinicalTrials.gov": _blank(),
        "Web (Tavily)": _blank(),
    }

    # 1) Recalls by concrete reason term ------------------------------------
    if "openfda_enforcement" in enabled:
        for term in RECALL_REASON_TERMS:
            res = openfda_enforcement.discover_events(term, max_results=per_source)
            _absorb(coverage["openFDA (Enforcement/Recalls)"], res, evidence,
                    region="United States", say=say)

    # 2) Stopped trials by status (+ topic hints) ---------------------------
    if "clinicaltrials" in enabled:
        for topic in TRIAL_STOP_TOPICS:
            res = clinicaltrials.discover_stopped(topic, max_results=per_source)
            _absorb(coverage["ClinicalTrials.gov"], res, evidence,
                    region=None, say=say)

    # 3) Source-targeted web discovery (regulatory + company first) ---------
    if "tavily" in enabled:
        for q in web_event_queries(active_codes):
            res = tavily_search.search(q, max_results=per_source, cost=cost)
            _absorb(coverage["Web (Tavily)"], res, evidence, region=None, say=say,
                    query_text=q)

    return evidence, coverage


def _blank():
    return {"queries": 0, "ok": 0, "failed": 0, "evidence": 0, "errors": []}


def _absorb(cov, res, evidence, region=None, say=None, query_text=None):
    cov["queries"] += 1
    if res.ok:
        cov["ok"] += 1
        cov["evidence"] += res.count
        for rec in res.records:
            rec["region_hint"] = region or rec.get("region_hint")
            rec["query_text"] = query_text or res.query
        evidence.extend(res.records)
    else:
        cov["failed"] += 1
        msg = f"{res.source} failed on '{str(res.query)[:40]}': {res.error}"
        cov["errors"].append(msg)
        if say:
            say("  ⚠ " + msg)


def has_event_source(evidence: list[dict]) -> bool:
    """Minimum event-source requirement (req 7): at least one item from a
    regulatory recall, a stopped trial, or a company/news event source."""
    for e in evidence:
        if e.get("source_type") == "recall":
            return True
        if e.get("source_type") == "trial" and (e.get("entities") or {}).get("event_type"):
            return True
        if e.get("source_category") in ("regulatory", "company"):
            return True
    return False
