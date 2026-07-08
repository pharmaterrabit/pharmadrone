"""openFDA drug enforcement (recalls) — no key required. Regulatory failure signal.
Docs: https://open.fda.gov/apis/drug/enforcement/
"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error

NAME = "openFDA (Enforcement/Recalls)"
URL = "https://api.fda.gov/drug/enforcement.json"


def search(term: str, max_results: int = 10) -> ConnectorResult:
    try:
        data = get_json(URL, {
            "search": f'(product_description:"{term}" OR reason_for_recall:"{term}")',
            "limit": min(max_results, 25)})
    except Exception as e:
        msg = describe_error(e)
        # openFDA returns 404 when a query legitimately has zero matches.
        if "404" in msg:
            return ConnectorResult(NAME, term, ok=True, count=0, records=[])
        return ConnectorResult(NAME, term, ok=False, error=msg)
    out = []
    for r in data.get("results", [])[:max_results]:
        rid = r.get("recall_number", "")
        firm = r.get("recalling_firm", "")
        raw = (f"Recall {rid}. Firm: {firm}. Product: {r.get('product_description','')}. "
               f"Reason: {r.get('reason_for_recall','')}. "
               f"Classification: {r.get('classification','')}. "
               f"Status: {r.get('status','')}. Initiated: {r.get('recall_initiation_date','')}.")
        title = f"Recall {rid}: {firm}".strip(": ")
        url = ("https://www.accessdata.fda.gov/scripts/ires/index.cfm"
               if rid else "https://open.fda.gov/apis/drug/enforcement/")
        out.append(record("recall", NAME, rid, title, url, raw,
                          source_category="regulatory"))
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)
