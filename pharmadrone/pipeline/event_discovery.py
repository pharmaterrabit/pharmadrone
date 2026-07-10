"""Event-first Failure/Rescue discovery with bounded source expansion.

Checkpoint 5A deepens official structured sources without changing scoring,
report caps, or the stable lead-ID function. Distinct official events are kept
separate by recall number, NCT ID, or shortage key.
"""
from __future__ import annotations

from .. import settings
from ..connectors import (
    openfda_enforcement, clinicaltrials, openfda_shortages, tavily_search,
)

TRIAL_STOP_TOPICS = [
    "bioavailability formulation",
    "relative bioavailability",
    "bioequivalence pharmacokinetics",
    "pharmacokinetic bridging",
    "food effect oral formulation",
    "solubility dissolution",
    "oral drug delivery formulation",
    "modified release formulation",
    "extended release formulation",
    "delayed release formulation",
    "formulation optimization",
    "reformulation drug product",
    "topical formulation drug delivery",
    "transdermal drug delivery",
    "inhaled formulation",
    "injectable formulation",
    "long acting injectable formulation",
    "pediatric formulation",
    "fixed dose combination formulation",
    "formulation comparison",
]


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
    if "AU" in regions_active_codes:
        q.append("site:tga.gov.au recall medicine stability")
    if "UK" in regions_active_codes:
        q.append("site:gov.uk drug alert recall stability")
    if "SA" in regions_active_codes:
        q.append("site:sfda.gov.sa recall medicine")
    return q


def _int_env(name: str, default: int, *, minimum: int = 1, maximum: int = 10000) -> int:
    try:
        value = int(settings.env(name, str(default)) or default)
    except Exception:
        value = default
    return max(minimum, min(value, maximum))


def _event_identity(rec: dict) -> str:
    ent = rec.get("entities") or {}
    rf = ent.get("recall_fields") or {}
    source_type = str(rec.get("source_type") or "").lower()
    source_name = str(rec.get("source_name") or "").lower()
    for prefix, value in (
        ("recall", rf.get("recall_number")),
        ("trial", ent.get("trial_id") or ent.get("nct_id")),
        ("shortage", ent.get("package_ndc") or ent.get("shortage_key")),
        ("event", ent.get("source_event_id")),
        (source_type or source_name or "record", rec.get("record_id")),
    ):
        if value:
            return f"{prefix}:{str(value).strip().lower()}"
    return f"url:{str(rec.get('url') or '').strip().lower()}|title:{str(rec.get('title') or '').strip().lower()}"


def _dedupe_event_records(records: list[dict]) -> list[dict]:
    unique: dict[str, dict] = {}
    for rec in records:
        key = _event_identity(rec)
        if key not in unique:
            unique[key] = rec
    return list(unique.values())


def discover_events(profile: dict, cost, per_source: int = 8, log=None,
                    expanded: bool = True) -> tuple[list[dict], dict]:
    """Run bounded official event discovery plus optional Tavily context."""
    say = log or (lambda m: None)
    enabled = {k for k, v in profile.get("sources", {}).items() if v.get("enabled")}
    active_codes = {r.get("code") for r in profile.get("regions", []) if r.get("active")}
    evidence: list[dict] = []
    coverage = {
        "openFDA (Enforcement/Recalls)": _blank(),
        "ClinicalTrials.gov": _blank(),
        "openFDA (Drug Shortages)": _blank(),
        "Web (Tavily)": _blank(),
    }

    max_per_source = _int_env("MAX_DISCOVERY_RECORDS_PER_SOURCE", 300, maximum=1000)

    # 1) FDA recall enforcement: category queries + bounded skip pagination.
    if "openfda_enforcement" in enabled:
        if expanded:
            page_size = _int_env("OPENFDA_RECALL_PAGE_SIZE", 50, maximum=100)
            max_pages = _int_env("OPENFDA_RECALL_MAX_PAGES_PER_CATEGORY", 3, maximum=10)
            res = openfda_enforcement.discover_taxonomy(
                page_size=page_size,
                max_pages=max_pages,
                max_results=max_per_source,
            )
            _absorb(coverage["openFDA (Enforcement/Recalls)"], res, evidence,
                    region="United States", say=say)
            coverage["openFDA (Enforcement/Recalls)"]["settings"] = {
                "OPENFDA_RECALL_PAGE_SIZE": page_size,
                "OPENFDA_RECALL_MAX_PAGES_PER_CATEGORY": max_pages,
                "MAX_DISCOVERY_RECORDS_PER_SOURCE": max_per_source,
            }
        else:
            for term in list(openfda_enforcement.RECALL_REASON_TERMS)[:8]:
                res = openfda_enforcement.discover_events(term, max_results=per_source)
                _absorb(coverage["openFDA (Enforcement/Recalls)"], res, evidence,
                        region="United States", say=say)

    # 2) ClinicalTrials.gov: bounded token pagination with medicinal-intervention gates.
    if "clinicaltrials" in enabled:
        trial_page_size = _int_env("CLINICALTRIALS_PAGE_SIZE", 50, maximum=100)
        trial_pages = _int_env("CLINICALTRIALS_MAX_PAGES_PER_TOPIC", 2, maximum=10)
        if not expanded:
            trial_page_size, trial_pages = max(1, per_source), 1
        seen = 0
        for topic in TRIAL_STOP_TOPICS:
            remaining = max_per_source - seen
            if remaining <= 0:
                break
            res = clinicaltrials.discover_stopped(
                topic, max_results=min(remaining, trial_page_size * trial_pages),
                page_size=trial_page_size, max_pages=trial_pages,
            )
            _absorb(coverage["ClinicalTrials.gov"], res, evidence, region=None, say=say)
            if res.ok:
                seen = len({
                    _event_identity(x) for x in evidence
                    if x.get("source_name") == clinicaltrials.NAME
                })

    # 3) FDA drug shortages: official supply/manufacturing/discontinuation context.
    if "openfda_shortages" in enabled:
        shortage_page_size = _int_env("OPENFDA_SHORTAGE_PAGE_SIZE", 50, maximum=100)
        shortage_pages = _int_env("OPENFDA_SHORTAGE_MAX_PAGES", 6, maximum=10)
        if not expanded:
            shortage_page_size, shortage_pages = max(1, per_source), 1
        res = openfda_shortages.discover_shortages(
            max_results=max_per_source if expanded else per_source,
            page_size=shortage_page_size, max_pages=shortage_pages,
        )
        _absorb(coverage["openFDA (Drug Shortages)"], res, evidence,
                region="United States", say=say)

    # 4) Existing optional web source remains corroborative, never required.
    if "tavily" in enabled:
        for q in web_event_queries(active_codes):
            res = tavily_search.search(q, max_results=per_source, cost=cost)
            _absorb(coverage["Web (Tavily)"], res, evidence, region=None, say=say,
                    query_text=q)

    evidence = _dedupe_event_records(evidence)
    for source_name, cov in coverage.items():
        cov["evidence"] = sum(1 for rec in evidence if rec.get("source_name") == source_name)
    return evidence, coverage


def _blank():
    return {
        "queries": 0, "ok": 0, "failed": 0, "evidence": 0,
        "raw_results": 0, "source_rejected": 0,
        "rejection_reasons": {}, "connector_stats": [],
        "errors": [], "warnings": [], "settings": {},
    }


def _absorb(cov, res, evidence, region=None, say=None, query_text=None):
    stats = getattr(res, "stats", {}) or {}
    cov["queries"] += int(stats.get("query_count") or 1)
    cov["raw_results"] += int(stats.get("raw_results") or res.count or 0)
    cov["source_rejected"] += int(stats.get("records_rejected") or 0)
    for reason, count in (stats.get("rejection_reasons") or {}).items():
        cov["rejection_reasons"][reason] = cov["rejection_reasons"].get(reason, 0) + int(count or 0)
    if stats:
        cov.setdefault("connector_stats", []).append(stats)
    if res.ok:
        cov["ok"] += int(stats.get("successful_queries") or 1)
        cov["failed"] += int(stats.get("failed_queries") or 0)
        cov["evidence"] += res.count
        cov.setdefault("warnings", []).extend(getattr(res, "warnings", []) or [])
        for rec in res.records:
            rec["region_hint"] = rec.get("region_hint") or region
            rec["query_text"] = rec.get("query_text") or query_text or res.query
        evidence.extend(res.records)
    else:
        cov["failed"] += int(stats.get("failed_queries") or stats.get("query_count") or 1)
        msg = f"{res.source} failed on '{str(res.query)[:40]}': {res.error}"
        cov["errors"].append(msg)
        cov.setdefault("warnings", []).extend(getattr(res, "warnings", []) or [])
        if say:
            say("  ⚠ " + msg)


def has_event_source(evidence: list[dict]) -> bool:
    for item in evidence:
        if item.get("source_type") in {"recall", "shortage"}:
            return True
        if item.get("source_type") == "trial" and (item.get("entities") or {}).get("event_type"):
            return True
        if item.get("source_category") in ("regulatory", "company"):
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
    corroboration searches and ATTACH any hits as extra evidence.

    The debug payload separates skipped search, API failures, zero-hit searches,
    retrieved-but-rejected hits, and accepted attachments so the UI/logs do not
    imply corroboration was performed when Tavily was unavailable.
    """
    say = log or (lambda m: None)
    debug = {
        "searched": 0,              # backward-compatible: leads with queries
        "searched_leads": 0,
        "queries_run": 0,
        "api_failed": 0,
        "no_hits": 0,
        "hits_retrieved": 0,
        "hits": 0,                  # backward-compatible: accepted attachments
        "attached": 0,
        "rejected": 0,
        "skipped_no_tavily": False,
        "web_enrichment_unavailable": False,
        "errors": [],
        "warnings": [],
    }
    from .. import settings as _settings
    from . import root_cause as _rc
    if "tavily" not in enabled or not _settings.env("TAVILY_API_KEY"):
        debug["skipped_no_tavily"] = True
        debug["web_enrichment_unavailable"] = True
        say("Root-cause corroboration: search skipped — Tavily is disabled or TAVILY_API_KEY is missing.")
        return debug

    for opp in candidates[:max_candidates]:
        queries = _reliable_corroboration_queries(opp)
        if not queries:
            continue
        debug["searched"] += 1
        debug["searched_leads"] += 1
        existing_urls = {e.get("url") for e in opp.get("evidence", [])}
        # recall fields for relevance matching
        rf = {}
        for e in opp.get("evidence", []):
            if (e.get("entities") or {}).get("recall_fields"):
                rf = e["entities"]["recall_fields"]
                break
        opp.setdefault("corroboration_debug", [])
        for q in queries:
            debug["queries_run"] += 1
            res = tavily_search.search(q, max_results=3, cost=cost)
            debug["warnings"].extend(getattr(res, "warnings", []) or [])
            if not res.ok:
                debug["api_failed"] += 1
                debug["errors"].append(f"{res.source} failed on {str(res.query)[:80]!r}: {res.error}")
                opp["corroboration_debug"].append({
                    "title": "API failed",
                    "url": "",
                    "class": "api_failed",
                    "matched_fields": [],
                    "accepted": False,
                    "reason": f"Tavily/API failure for query {q!r}: {res.error}",
                })
                continue
            if not res.records:
                debug["no_hits"] += 1
                opp["corroboration_debug"].append({
                    "title": "No hits found",
                    "url": "",
                    "class": "no_hits",
                    "matched_fields": [],
                    "accepted": False,
                    "reason": f"Tavily returned no results for query {q!r}.",
                })
                continue
            debug["hits_retrieved"] += len(res.records)
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
                # recall; classify and record why accepted/rejected.
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
                debug["attached"] += 1

    if debug["api_failed"] and debug["api_failed"] == debug["queries_run"]:
        debug["web_enrichment_unavailable"] = True

    if debug["searched_leads"]:
        status_bits = [
            f"searched {debug['searched_leads']} lead(s)",
            f"ran {debug['queries_run']} query(ies)",
            f"attached {debug['attached']} relevant source(s)",
            f"retrieved {debug['hits_retrieved']} hit(s)",
            f"rejected {debug['rejected']} irrelevant/low-quality hit(s)",
            f"no hits on {debug['no_hits']} query(ies)",
            f"API failed on {debug['api_failed']} query(ies)",
        ]
        say("Root-cause corroboration: " + ", ".join(status_bits) + ".")
        if debug["web_enrichment_unavailable"]:
            say("  ⚠ Web enrichment unavailable for corroboration this run — Tavily/API failed for all corroboration queries.")
    return debug
