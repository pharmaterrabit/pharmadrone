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


# --- Deeper per-candidate corroboration (Root-Cause layer, req 1/3) ---------
def _reliable_corroboration_queries(opp: dict) -> list[str]:
    """Targeted, reliable-source-only queries to corroborate a specific lead:
    product/molecule/firm/recall-number + problem, plus molecule+dosage-form
    scientific angles. No forums/blogs — site-scoped or scholarly only."""
    ent = {}
    for e in opp.get("evidence", []):
        ent = e.get("entities") or {}
        if ent.get("recall_fields"):
            break
    rf = ent.get("recall_fields") or {}
    firm = (opp.get("company") or rf.get("recalling_firm") or "").strip()
    product = (opp.get("product") or rf.get("product_description") or "").strip()
    recall_no = (rf.get("recall_number") or "").strip()
    problem = (opp.get("problem_category") or opp.get("problem_signal") or "").strip()
    # molecule guess = first token of product (deterministic, cheap)
    molecule = product.split(",")[0].split()[0] if product else ""

    q = []
    if firm:
        q.append(f'site:fda.gov warning letter "{firm}"')
        q.append(f'"{firm}" recall {problem}')
    if recall_no:
        q.append(f'"{recall_no}" recall')
    if product:
        q.append(f'site:accessdata.fda.gov "{product[:60]}"')
    if molecule and problem:
        # scientific corroboration via scholarly/regulatory sources only
        q.append(f'{molecule} {problem} dissolution OR bioavailability')
        q.append(f'{molecule} particle size OR polymorph OR solid-state')
    return [x for x in q if x][:6]


def corroborate_candidates(candidates: list[dict], cost, enabled: set[str],
                           log=None, max_candidates: int = 12) -> dict:
    """For each selected candidate (post-cap), run a few reliable-source
    corroboration searches and ATTACH any hits as extra evidence. This feeds the
    Root-Cause Evidence Matrix (warning letters, company statements, molecule
    literature). Respects the LLM-independent path: it only uses web search, and
    only if Tavily is enabled. Returns a small debug dict."""
    say = log or (lambda m: None)
    debug = {"searched": 0, "hits": 0, "rejected": 0, "skipped_no_tavily": False}
    from .. import settings as _settings
    from . import root_cause as _rc
    if "tavily" not in enabled or not _settings.env("TAVILY_API_KEY"):
        debug["skipped_no_tavily"] = True
        return debug
    for opp in candidates[:max_candidates]:
        queries = _reliable_corroboration_queries(opp)
        if not queries:
            continue
        debug["searched"] += 1
        existing_urls = {e.get("url") for e in opp.get("evidence", [])}
        # recall fields for relevance matching
        rf = {}
        for e in opp.get("evidence", []):
            if (e.get("entities") or {}).get("recall_fields"):
                rf = e["entities"]["recall_fields"]
                break
        opp.setdefault("corroboration_debug", [])
        for q in queries:
            res = tavily_search.search(q, max_results=3, cost=cost)
            if not res.ok:
                continue
            for rec in res.records:
                if rec.get("url") in existing_urls:
                    continue
                candidate_ev = {
                    "source_type": rec.get("source_type", "web"),
                    "source_category": rec.get("source_category", "news"),
                    "source_name": rec.get("source_name", "Web (Tavily)"),
                    "record_id": rec.get("record_id", ""),
                    "title": rec.get("title", ""),
                    "url": rec.get("url", ""),
                    "language": rec.get("language", "en"),
                    "english_summary": (rec.get("raw_text") or "")[:400],
                    "date_accessed": rec.get("date_accessed", ""),
                    "entities": rec.get("entities") or {},
                }
                # STRICT relevance filter — only attach evidence that matches THIS
                # recall; classify and record why accepted/rejected (req 1/2/8).
                verdict = _rc.classify_corroboration(candidate_ev, opp, rf)
                opp["corroboration_debug"].append({
                    "title": (candidate_ev["title"] or "")[:80],
                    "url": candidate_ev["url"],
                    "class": verdict["class"],
                    "matched_fields": verdict["matched_fields"],
                    "accepted": verdict["accepted"],
                    "reason": verdict["reason"],
                })
                existing_urls.add(rec.get("url"))
                if not verdict["accepted"]:
                    debug["rejected"] += 1
                    continue
                candidate_ev["corroboration"] = True
                candidate_ev["evidence_class"] = verdict["class"]
                candidate_ev["causal_source"] = verdict.get("causal_source", False)
                candidate_ev["query_text"] = q
                candidate_ev["supports"] = f"corroboration ({verdict['class']})"
                candidate_ev["does_not_prove"] = ("relevance/root-cause requires "
                    "validation; not this recall's confirmed cause unless a causal "
                    "regulatory source")
                opp.setdefault("evidence", []).append(candidate_ev)
                debug["hits"] += 1
    if debug["searched"]:
        say(f"Root-cause corroboration: searched {debug['searched']} lead(s), "
            f"attached {debug['hits']} relevant source(s), rejected "
            f"{debug['rejected']} irrelevant/low-quality hit(s).")
    return debug
