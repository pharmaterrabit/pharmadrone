"""Test each connector in isolation so you can see exactly which source works.

CLI:
    python -m pharmadrone.test_connectors                 # default sample query
    python -m pharmadrone.test_connectors "apixaban food effect"

The dashboard's "④ Connectors" tab calls check_all() and shows the same results.
"""
from __future__ import annotations
import sys
from .connectors import (clinicaltrials, openfda, openfda_enforcement, europepmc,
                        openalex, crossref, tavily_search)
from . import settings

DEFAULT_QUERY = "poorly soluble oral small molecule"

CHECKS = [
    ("ClinicalTrials.gov", clinicaltrials.search, False),
    ("openFDA (Drug Label)", openfda.search, False),
    ("openFDA (Enforcement/Recalls)", openfda_enforcement.search, False),
    ("Europe PMC", europepmc.search, False),
    ("OpenAlex", openalex.search, False),
    ("Crossref", crossref.search, False),
    ("Web (Tavily)", tavily_search.search, True),
]


def check_all(query: str = DEFAULT_QUERY, per_source: int = 3) -> list[dict]:
    """Run every connector once. Returns a list of result dicts (no raw records)."""
    out = []
    for label, fn, needs_key in CHECKS:
        res = fn(query, per_source)
        sample = ""
        if res.records:
            r0 = res.records[0]
            sample = f"{r0.get('title','')[:70]}  <{r0.get('url','')[:60]}>"
        out.append({
            "source": label,
            "status": "OK" if res.ok else "FAIL",
            "count": res.count,
            "error": res.error or "",
            "needs_key": needs_key,
            "sample": sample,
        })
    return out


def _cli():
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    print(f"Testing connectors with query: {query!r}\n")
    if not settings.HAS_TAVILY:
        print("  note: TAVILY_API_KEY not set — Tavily will report a clear error.\n")
    results = check_all(query)
    width = max(len(r["source"]) for r in results)
    for r in results:
        mark = "✓" if r["status"] == "OK" else "✗"
        line = f"  {mark} {r['source']:<{width}}  {r['status']:<4} count={r['count']}"
        if r["error"]:
            line += f"  → {r['error']}"
        print(line)
        if r["sample"]:
            print(f"      e.g. {r['sample']}")
    ok = sum(1 for r in results if r["status"] == "OK")
    print(f"\n{ok}/{len(results)} sources OK.")


if __name__ == "__main__":
    _cli()
EOF
