"""openFDA drug enforcement (recalls) — no key required.

Checkpoint 5A adds bounded pagination and a broader, deterministic recall-reason
 taxonomy. Duplicate query hits are merged by recall_number, while distinct FDA
 recall events remain separate even when the same company/product is involved.
"""
from __future__ import annotations

from collections import OrderedDict
from urllib.parse import quote

from .base import get_json, record, ConnectorResult, describe_error

NAME = "openFDA (Enforcement/Recalls)"
URL = "https://api.fda.gov/drug/enforcement.json"

# Broad but explicit quality/problem groups. Queries are made against the FDA
# reason_for_recall field only; these terms are never treated as root-cause proof
# beyond what the official reason text itself states.
RECALL_REASON_CATEGORIES = OrderedDict([
    ("dissolution / release performance", [
        "dissolution", "failed dissolution", "dissolution specification",
        "release rate", "release profile", "failed release testing",
    ]),
    ("stability / potency / assay", [
        "stability", "degradation", "subpotent", "superpotent", "potency",
        "assay", "failed assay", "shelf life", "temperature excursion",
    ]),
    ("impurity / contamination", [
        "impurity", "impurities", "nitrosamine", "related substances",
        "foreign substance", "cross contamination", "contamination",
    ]),
    ("sterility / particulate", [
        "sterility", "lack of sterility assurance", "microbial contamination",
        "endotoxin", "particulate matter", "visible particles", "foreign particles",
    ]),
    ("manufacturing / specification", [
        "failed specifications", "out of specification", "OOS", "cGMP",
        "manufacturing defect", "process deviation", "content uniformity",
        "fill volume", "batch variability",
    ]),
    ("packaging / container closure", [
        "container closure", "packaging defect", "leakage", "seal integrity",
        "moisture ingress", "incorrect container", "vial defect", "syringe defect",
        "labeling mix-up", "label mix-up",
    ]),
    ("physical / formulation change", [
        "crystallization", "precipitation", "aggregation", "phase separation",
    ]),
])

# Backward-compatible flat term list used by connector tests and older callers.
RECALL_REASON_TERMS = list(dict.fromkeys(
    term for terms in RECALL_REASON_CATEGORIES.values() for term in terms
))


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(x) for x in value if x is not None)
    return " ".join(str(value).split()).strip()


def _fmt_date(value):
    value = _clean(value)
    if len(value) == 8 and value.isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def recall_url(recall_number: str) -> str:
    """Direct official openFDA query URL for one recall number."""
    rid = _clean(recall_number)
    if not rid:
        return "https://open.fda.gov/apis/drug/enforcement/"
    return f'{URL}?search=recall_number:%22{quote(rid, safe="-")}%22'


def _dedupe_key(raw: dict) -> str:
    rid = _clean(raw.get("recall_number"))
    if rid:
        return f"recall:{rid.lower()}"
    event_id = _clean(raw.get("event_id"))
    product = _clean(raw.get("product_description"))
    firm = _clean(raw.get("recalling_firm"))
    return f"fallback:{event_id.lower()}|{firm.lower()}|{product.lower()}"


def _parse(results, query_label: str, max_results: int | None = None):
    out = []
    rows = results if max_results is None else results[:max_results]
    for raw_row in rows:
        rid = _clean(raw_row.get("recall_number"))
        firm = _clean(raw_row.get("recalling_firm"))
        reason = _clean(raw_row.get("reason_for_recall"))
        product = _clean(raw_row.get("product_description"))
        classification = _clean(raw_row.get("classification"))
        status = _clean(raw_row.get("status"))

        recall_fields = {
            "recall_number": rid,
            "recalling_firm": firm,
            "product_description": product,
            "reason_for_recall": reason,
            "classification": classification,
            "status": status,
            "report_date": _fmt_date(raw_row.get("report_date")),
            "recall_initiation_date": _fmt_date(raw_row.get("recall_initiation_date")),
            "center_classification_date": _fmt_date(raw_row.get("center_classification_date")),
            "distribution_pattern": _clean(raw_row.get("distribution_pattern")),
            "product_quantity": _clean(raw_row.get("product_quantity")),
            "code_info": _clean(raw_row.get("code_info")),
            "voluntary_mandated": _clean(raw_row.get("voluntary_mandated")),
            "initial_firm_notification": _clean(raw_row.get("initial_firm_notification")),
            "country": _clean(raw_row.get("country")),
            "state": _clean(raw_row.get("state")),
            "city": _clean(raw_row.get("city")),
            "event_id": _clean(raw_row.get("event_id")),
        }
        openfda = raw_row.get("openfda", {}) or {}
        if not product and openfda.get("brand_name"):
            product = _clean(openfda.get("brand_name"))
            recall_fields["product_description"] = product

        raw_text = (
            f"Recall {rid}. Firm: {firm}. Product: {product}. Reason: {reason}. "
            f"Classification: {classification}. Status: {status}. "
            f"Initiated: {recall_fields['recall_initiation_date']}. "
            f"FDA report date: {recall_fields['report_date']}. "
            f"Distribution: {recall_fields['distribution_pattern']}. "
            f"Quantity: {recall_fields['product_quantity']}. "
            f"Code info: {recall_fields['code_info']}."
        )
        product_short = product if len(product) <= 90 else product[:87].rsplit(" ", 1)[0] + "…"
        title = f"Recall {rid}: {firm}".strip(": ") or "Drug recall"
        location = ", ".join(x for x in (
            recall_fields["city"], recall_fields["state"], recall_fields["country"]
        ) if x) or None

        out.append(record(
            "recall", NAME, rid, title, recall_url(rid), raw_text,
            source_category="regulatory",
            entities={
                "company": firm or None,
                "product": product or None,
                "product_short": product_short or None,
                "trial_id": None,
                "dosage_form": None,
                "event_type": "recall",
                "event_reason": reason or None,
                "source_event_id": rid or recall_fields["event_id"] or None,
                "regulator": "FDA",
                "country": recall_fields["country"] or "United States",
                "issue_category": query_label or None,
                "direct_problem_evidence": bool(reason),
                "official_source_url": recall_url(rid),
                "region_location": location,
                "recall_fields": recall_fields,
            },
        ))
    return out


def _query_for_terms(terms: list[str]) -> str:
    clauses = [f'reason_for_recall:"{str(term).replace(chr(34), "").strip()}"' for term in terms if str(term).strip()]
    if not clauses:
        return ""
    return clauses[0] if len(clauses) == 1 else "(" + " OR ".join(clauses) + ")"


def _fetch_paginated(*, query: str, query_label: str, page_size: int,
                      max_pages: int, max_results: int) -> ConnectorResult:
    page_size = max(1, min(int(page_size), 100))
    max_pages = max(1, int(max_pages))
    max_results = max(0, int(max_results))
    if max_results == 0:
        return ConnectorResult(NAME, query_label, ok=True, count=0, records=[])

    unique_raw: dict[str, dict] = {}
    pages_run = 0
    try:
        for page in range(max_pages):
            remaining = max_results - len(unique_raw)
            if remaining <= 0:
                break
            limit = min(page_size, remaining)
            params = {"search": query, "limit": limit, "skip": page * page_size}
            try:
                data = get_json(URL, params)
            except Exception as exc:
                msg = describe_error(exc)
                if "404" in msg:
                    break  # openFDA uses 404 for no matches / end of result set
                raise
            pages_run += 1
            rows = data.get("results", []) or []
            if not rows:
                break
            before = len(unique_raw)
            for row in rows:
                unique_raw.setdefault(_dedupe_key(row), row)
                if len(unique_raw) >= max_results:
                    break
            if len(rows) < limit or len(unique_raw) == before:
                break
    except Exception as exc:
        return ConnectorResult(NAME, query_label, ok=False, error=describe_error(exc))

    records = _parse(list(unique_raw.values()), query_label)
    warnings = [f"bounded pagination: {pages_run} page(s), {len(records)} unique recall event(s)"]
    return ConnectorResult(NAME, query_label, ok=True, count=len(records), records=records, warnings=warnings)


def search(term: str, max_results: int = 10) -> ConnectorResult:
    """Legacy free-text search over product and recall-reason fields."""
    cleaned = str(term or "").replace('"', "").strip()
    query = f'(product_description:"{cleaned}" OR reason_for_recall:"{cleaned}")'
    return _fetch_paginated(
        query=query, query_label=cleaned or "recall search",
        page_size=min(max_results, 25), max_pages=1, max_results=max_results,
    )


def discover_events(reason_term: str, max_results: int = 10, *,
                    page_size: int | None = None, max_pages: int = 1) -> ConnectorResult:
    """Backward-compatible event-first search for one explicit reason term."""
    cleaned = str(reason_term or "").replace('"', "").strip()
    return _fetch_paginated(
        query=_query_for_terms([cleaned]), query_label=cleaned,
        page_size=page_size or min(max_results, 50), max_pages=max_pages,
        max_results=max_results,
    )


def discover_category(category: str, terms: list[str], *, page_size: int = 50,
                      max_pages: int = 3, max_results: int = 150) -> ConnectorResult:
    """Paginated recall discovery for one bounded quality-problem category."""
    return _fetch_paginated(
        query=_query_for_terms(terms), query_label=category,
        page_size=page_size, max_pages=max_pages, max_results=max_results,
    )
