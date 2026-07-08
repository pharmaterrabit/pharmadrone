"""openFDA drug endpoints — no key required for low volume.
Docs: https://open.fda.gov/apis/drug/
"""
from __future__ import annotations
from .base import get_json, record, ConnectorResult, describe_error

NAME = "openFDA (Drug Label)"
LABEL = "https://api.fda.gov/drug/label.json"


def search(term: str, max_results: int = 10) -> ConnectorResult:
    try:
        data = get_json(LABEL, {
            "search": f'(indications_and_usage:"{term}" OR openfda.brand_name:"{term}" '
                      f'OR openfda.generic_name:"{term}")',
            "limit": min(max_results, 25)})
    except Exception as e:
        # openFDA returns 404 when a query legitimately has zero matches.
        msg = describe_error(e)
        if "404" in msg:
            return ConnectorResult(NAME, term, ok=True, count=0, records=[])
        return ConnectorResult(NAME, term, ok=False, error=msg)
    out = []
    for res in data.get("results", [])[:max_results]:
        of = res.get("openfda", {})
        brand = (of.get("brand_name") or [""])[0]
        generic = (of.get("generic_name") or [""])[0]
        route = ", ".join(of.get("route", []) or [])
        app_no = (of.get("application_number") or [""])[0]
        set_id = res.get("set_id", "")
        snippets = []
        for field_ in ("dosage_forms_and_strengths", "clinical_pharmacology",
                       "how_supplied", "boxed_warning", "food_effect"):
            val = res.get(field_)
            if val:
                snippets.append(f"{field_}: {' '.join(val)[:600]}")
        raw = f"Brand: {brand}. Generic: {generic}. Route: {route}. " + " ".join(snippets)
        title = brand or generic or "FDA label"
        url = (f"https://nctr-crs.fda.gov/fdalabel/services/spl/set-ids/{set_id}/spl-doc"
               if set_id else "https://open.fda.gov/apis/drug/label/")
        out.append(record("label", NAME, app_no or set_id, title, url, raw,
                          entities={
                              "company": None,
                              "product": brand or generic or None,
                              "trial_id": None,
                              "dosage_form": route or None,
                              "event_type": None,
                          }))
    return ConnectorResult(NAME, term, ok=True, count=len(out), records=out)
