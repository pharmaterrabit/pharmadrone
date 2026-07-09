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


def _clean(s):
    """Collapse whitespace/newlines openFDA sometimes embeds in fields."""
    if not s:
        return ""
    return " ".join(str(s).split()).strip()


def _fmt_date(s):
    """openFDA dates are YYYYMMDD strings — render as YYYY-MM-DD if parseable."""
    s = _clean(s)
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _parse(results, term, max_results):
    out = []
    for r in results[:max_results]:
        rid = _clean(r.get("recall_number", ""))
        firm = _clean(r.get("recalling_firm", ""))
        reason = _clean(r.get("reason_for_recall", ""))
        product = _clean(r.get("product_description", ""))
        classification = _clean(r.get("classification", ""))
        status = _clean(r.get("status", ""))

        # Full structured field set (req 1) — kept untruncated for the report.
        recall_fields = {
            "recall_number": rid,
            "recalling_firm": firm,
            "product_description": product,
            "reason_for_recall": reason,
            "classification": classification,
            "status": status,
            "report_date": _fmt_date(r.get("report_date", "")),
            "recall_initiation_date": _fmt_date(r.get("recall_initiation_date", "")),
            "center_classification_date": _fmt_date(r.get("center_classification_date", "")),
            "distribution_pattern": _clean(r.get("distribution_pattern", "")),
            "product_quantity": _clean(r.get("product_quantity", "")),
            "code_info": _clean(r.get("code_info", "")),
            "voluntary_mandated": _clean(r.get("voluntary_mandated", "")),
            "initial_firm_notification": _clean(r.get("initial_firm_notification", "")),
            "country": _clean(r.get("country", "")),
            "state": _clean(r.get("state", "")),
            "city": _clean(r.get("city", "")),
            "event_id": _clean(r.get("event_id", "")),
        }
        # openFDA sometimes nests firm address under openfda; fill gaps if present.
        of = r.get("openfda", {}) or {}
        if not recall_fields["product_description"] and of.get("brand_name"):
            recall_fields["product_description"] = _clean(", ".join(of.get("brand_name", [])))

        raw = (f"Recall {rid}. Firm: {firm}. Product: {product}. "
               f"Reason: {reason}. Classification: {classification}. "
               f"Status: {status}. "
               f"Initiated: {recall_fields['recall_initiation_date']}. "
               f"FDA report date: {recall_fields['report_date']}. "
               f"Distribution: {recall_fields['distribution_pattern']}. "
               f"Quantity: {recall_fields['product_quantity']}. "
               f"Code info: {recall_fields['code_info']}.")

        # Title: firm + short product label, but never a mid-word truncation.
        prod_label = product if len(product) <= 90 else product[:87].rsplit(" ", 1)[0] + "…"
        title = f"Recall {rid}: {firm}".strip(": ") or "Drug recall"

        loc = ", ".join([x for x in (recall_fields["city"], recall_fields["state"],
                                     recall_fields["country"]) if x]) or None
        url = ("https://www.accessdata.fda.gov/scripts/ires/index.cfm"
               if rid else "https://open.fda.gov/apis/drug/enforcement/")
        out.append(record("recall", NAME, rid, title, url, raw,
                          source_category="regulatory",
                          entities={
                              "company": firm or None,
                              "product": product or None,   # FULL name, not truncated
                              "product_short": prod_label or None,
                              "trial_id": None,
                              "dosage_form": None,
                              "event_type": "recall",
                              "event_reason": reason or None,
                              "region_location": loc,
                              "recall_fields": recall_fields,
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
