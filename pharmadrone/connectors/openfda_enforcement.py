"""openFDA drug enforcement (recalls) — no key required. Regulatory failure signal.
Docs: https://open.fda.gov/apis/drug/enforcement/

Two entry points:
  - search(term):           legacy free-text search (kept for the generic path)
  - discover_events():      EVENT-FIRST discovery — queries recall REASON fields
                            for concrete quality-failure terms (dissolution
                            failure, stability, impurity, sterility, cGMP, …) so
                            we surface real recall records instead of matching a
                            generic literature phrase against recall text.
"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error

NAME = "openFDA (Enforcement/Recalls)"
URL = "https://api.fda.gov/drug/enforcement.json"

# Concrete recall-reason terms (CMC / formulation / quality / packaging events).
RECALL_REASON_TERMS = [
    "dissolution failure", "failed dissolution", "stability", "impurity",
    "impurities", "degradation", "sterility", "particulate matter",
    "contamination", "failed specifications", "out of specification",
    "subpotent", "superpotent", "packaging defect", "labeling mix-up",
    "label mix-up", "container closure", "leakage", "crystallization",
    "precipitation", "failed release testing", "cGMP", "manufacturing defect",
    "assay", "dissolution", "microbial contamination",
]


def _parse(results, term, max_results):
    out = []
    for r in results[:max_results]:
        rid = r.get("recall_number", "")
        firm = r.get("recalling_firm", "")
        reason = r.get("reason_for_recall", "")
        raw = (f"Recall {rid}. Firm: {firm}. Product: {r.get('product_description','')}. "
               f"Reason: {reason}. Classification: {r.get('classification','')}. "
               f"Status: {r.get('status','')}. Initiated: {r.get('recall_initiation_date','')}.")
        title = f"Recall {rid}: {firm}".strip(": ") or "Drug recall"
        url = ("https://www.accessdata.fda.gov/scripts/ires/index.cfm"
               if rid else "https://open.fda.gov/apis/drug/enforcement/")
        out.append(record("recall", NAME, rid, title, url, raw,
                          source_category="regulatory",
                          entities={
                              "company": firm or None,
                              "product": (r.get("product_description", "") or "")[:80] or None,
                              "trial_id": None,
                              "dosage_form": None,
                              "event_type": "recall",
                              "event_reason": reason or None,
                          }))
    return out


def search(term: str, max_results: int = 10) -> ConnectorResult:
    """Legacy free-text search over product + reason fields."""
    try:
        data = get_json(URL, {
            "search": f'(product_description:"{term}" OR reason_for_recall:"{term}")',
            "limit": min(max_results, 25)})
    except Exception as e:
        msg = describe_error(e)
        if "404" in msg:  # openFDA 404 == zero matches
            return ConnectorResult(NAME, term, ok=True, count=0, records=[])
        return ConnectorResult(NAME, term, ok=False, error=msg)
    return ConnectorResult(NAME, term, ok=True,
                           count=len(data.get("results", [])),
                           records=_parse(data.get("results", []), term, max_results))


def discover_events(reason_term: str, max_results: int = 10) -> ConnectorResult:
    """EVENT-FIRST: search the recall REASON field for a concrete quality term.
    This is what actually surfaces real recalls (a recall's reason literally
    contains 'dissolution failure', 'subpotent', 'cGMP', etc.)."""
    query = f'reason_for_recall:"{reason_term}"'
    try:
        data = get_json(URL, {"search": query, "limit": min(max_results, 25)})
    except Exception as e:
        msg = describe_error(e)
        if "404" in msg:
            return ConnectorResult(NAME, reason_term, ok=True, count=0, records=[])
        return ConnectorResult(NAME, reason_term, ok=False, error=msg)
    recs = _parse(data.get("results", []), reason_term, max_results)
    return ConnectorResult(NAME, reason_term, ok=True, count=len(recs), records=recs)
