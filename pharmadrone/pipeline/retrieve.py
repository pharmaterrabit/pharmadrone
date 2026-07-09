"""Run enabled connectors for a batch of queries.

Returns (evidence, coverage). `coverage` records, per source: how many queries
ran, how many succeeded/failed, the error messages, and the evidence count — so
the dashboard can show a Source Coverage Summary and any failures loudly.
"""
from __future__ import annotations
from collections import defaultdict
from ..connectors import (clinicaltrials, openfda, openfda_enforcement, europepmc,
                          openalex, crossref, tavily_search)

# Structured databases are English-indexed; run them on English queries only.
STRUCTURED = {
    "clinicaltrials": clinicaltrials.search,
    "openfda": openfda.search,
    "openfda_enforcement": openfda_enforcement.search,
    "europepmc": europepmc.search,
    "openalex": openalex.search,
    "crossref": crossref.search,
}

# Canonical source labels for the coverage summary.
SOURCE_LABELS = {
    "clinicaltrials": "ClinicalTrials.gov",
    "openfda": "openFDA (Drug Label)",
    "openfda_enforcement": "openFDA (Enforcement/Recalls)",
    "europepmc": "Europe PMC",
    "openalex": "OpenAlex",
    "crossref": "Crossref",
    "tavily": "Web (Tavily)",
}


def _blank_cov():
    return {"queries": 0, "ok": 0, "failed": 0, "evidence": 0, "errors": [], "warnings": []}


def retrieve(queries, enabled, cost, per_source=6, progress=None, log=None):
    evidence = []
    coverage = {SOURCE_LABELS[s]: _blank_cov() for s in enabled if s in SOURCE_LABELS}
    say = log or (lambda m: None)
    total = len(queries)

    def _handle(res, region=None, query_text=None):
        cov = coverage[res.source]
        cov["queries"] += 1
        if res.ok:
            cov["ok"] += 1
            cov["evidence"] += res.count
            cov.setdefault("warnings", []).extend(getattr(res, "warnings", []) or [])
            for rec in res.records:
                rec["region_hint"] = region
                rec["query_text"] = rec.get("query_text") or query_text
            evidence.extend(res.records)
        else:
            cov["failed"] += 1
            msg = f"{res.source} failed on '{res.query[:40]}': {res.error}"
            cov["errors"].append(msg)
            cov.setdefault("warnings", []).extend(getattr(res, "warnings", []) or [])
            say("  ⚠ " + msg)

    for i, q in enumerate(queries):
        term, lang = q["query"], q.get("lang", "en")
        plain = q.get("plain_query", term)
        if progress:
            progress(i + 1, total, term)
        if lang == "en":
            for name, fn in STRUCTURED.items():
                if name in enabled:
                    _handle(fn(plain, per_source), region=q.get("region"), query_text=plain)
        if "tavily" in enabled:
            _handle(tavily_search.search(term, per_source, cost=cost),
                   region=q.get("region"), query_text=term)

    return evidence, coverage
