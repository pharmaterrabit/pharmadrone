"""openFDA drug enforcement (recalls) — no key required.

Checkpoint 5A.1 uses two bounded discovery paths:
1. broad, atomic recall-reason queries with wildcard support; and
2. a bounded recent-recall sweep when taxonomy queries are sparse.

Duplicate hits are merged by recall_number. Distinct FDA recall events remain
separate even when the same firm or product is involved.
"""
from __future__ import annotations

from collections import OrderedDict, Counter
import math
from urllib.parse import quote

from .base import get_json, record, ConnectorResult, describe_error

NAME = "openFDA (Enforcement/Recalls)"
URL = "https://api.fda.gov/drug/enforcement.json"

# User-facing category groups retained for compatibility/documentation.
RECALL_REASON_CATEGORIES = OrderedDict([
    ("dissolution / release performance", [
        "dissolution", "failed dissolution", "release rate", "release profile",
    ]),
    ("stability / degradation", [
        "stability", "degradation", "shelf life", "temperature excursion",
    ]),
    ("impurity / nitrosamine", [
        "impurity", "nitrosamine", "related substances", "foreign substance",
    ]),
    ("sterility / contamination", [
        "sterility", "microbial contamination", "endotoxin", "contamination",
    ]),
    ("particulate / foreign matter", [
        "particulate", "visible particles", "foreign particles",
    ]),
    ("assay / potency / uniformity", [
        "assay", "potency", "subpotent", "superpotent", "content uniformity",
    ]),
    ("specification / manufacturing", [
        "failed specifications", "out of specification", "manufacturing",
        "process deviation", "batch variability",
    ]),
    ("packaging / container closure", [
        "container closure", "packaging defect", "leakage", "seal integrity",
        "vial defect", "syringe defect",
    ]),
    ("physical / formulation change", [
        "crystallization", "precipitation", "aggregation", "phase separation",
    ]),
])

# Atomic broad queries are more reliable than one large exact-phrase OR query.
# Wildcards are supported by openFDA and materially improve recall coverage.
RECALL_QUERY_SPECS: list[tuple[str, str]] = [
    ("dissolution / release performance", "reason_for_recall:dissolution*"),
    ("stability / degradation", "reason_for_recall:stabil*"),
    ("stability / degradation", "reason_for_recall:degrad*"),
    ("impurity / nitrosamine", "reason_for_recall:impurit*"),
    ("impurity / nitrosamine", "reason_for_recall:nitrosamin*"),
    ("assay / potency / uniformity", "reason_for_recall:assay"),
    ("assay / potency / uniformity", "reason_for_recall:potency"),
    ("assay / potency / uniformity", "reason_for_recall:subpotent*"),
    ("assay / potency / uniformity", "reason_for_recall:superpotent*"),
    ("sterility / contamination", "reason_for_recall:steril*"),
    ("sterility / contamination", "reason_for_recall:contamin*"),
    ("particulate / foreign matter", "reason_for_recall:particulat*"),
    ("particulate / foreign matter", "reason_for_recall:particle*"),
    ("specification / manufacturing", "reason_for_recall:specification*"),
    ("specification / manufacturing", "reason_for_recall:manufactur*"),
    ("specification / manufacturing", "reason_for_recall:process*"),
    ("packaging / container closure", "reason_for_recall:packag*"),
    ("packaging / container closure", "reason_for_recall:container*"),
    ("packaging / container closure", "reason_for_recall:leak*"),
    ("physical / formulation change", "reason_for_recall:precipitat*"),
    ("physical / formulation change", "reason_for_recall:crystall*"),
]

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


def classify_recall_reason(reason: str) -> str:
    """Map official recall reason text to a cautious deterministic category."""
    text = _clean(reason).lower()
    if not text:
        return "other FDA recall issue"
    if "dissolution" in text or "release rate" in text or "release profile" in text:
        return "dissolution / release performance"
    if any(x in text for x in ("stabil", "degrad", "shelf life", "temperature excursion")):
        return "stability issue"
    if any(x in text for x in ("impurit", "nitrosamin", "related substance")):
        return "impurity issue"
    if any(x in text for x in ("steril", "microbial", "endotoxin", "contamin")):
        return "sterility/contamination issue"
    if any(x in text for x in ("particulate", "visible particle", "foreign particle", "foreign matter")):
        return "particulate / quality issue"
    if any(x in text for x in ("assay", "potency", "subpotent", "superpotent", "content uniformity")):
        return "assay/potency issue"
    if any(x in text for x in ("out of specification", "failed specification", "specification")):
        return "quality / specification issue"
    if any(x in text for x in ("container closure", "packag", "leak", "seal integrity", "vial", "syringe")):
        return "packaging / container-closure issue"
    if any(x in text for x in ("manufactur", "process deviation", "batch variability", "cgmp")):
        return "manufacturing variability"
    if any(x in text for x in ("precipitat", "crystall", "aggregation", "phase separation")):
        return "formulation / physical stability issue"
    if any(x in text for x in ("label", "misbrand", "incorrect strength", "incorrect product")):
        return "labeling / product mix-up issue"
    return "other FDA recall issue"




_RELEVANT_SWEEP_CATEGORIES = {
    "dissolution / release performance",
    "stability issue",
    "impurity issue",
    "sterility/contamination issue",
    "particulate / quality issue",
    "assay/potency issue",
    "quality / specification issue",
    "packaging / container-closure issue",
    "manufacturing variability",
    "formulation / physical stability issue",
}


def _is_relevant_sweep_row(row: dict) -> bool:
    """Keep only seller-relevant quality/drug-product recalls in the broad sweep.

    The fallback sweep exists to deepen coverage when exact taxonomy queries are
    sparse. It must not inflate the index with unrelated labelling, distributor,
    storage, or generic market-withdrawal records.
    """
    reason = _clean(row.get("reason_for_recall"))
    return classify_recall_reason(reason) in _RELEVANT_SWEEP_CATEGORIES


def _parse(results, query_label: str | None = None):
    out = []
    for raw_row in results:
        rid = _clean(raw_row.get("recall_number"))
        firm = _clean(raw_row.get("recalling_firm"))
        reason = _clean(raw_row.get("reason_for_recall"))
        product = _clean(raw_row.get("product_description"))
        classification = _clean(raw_row.get("classification"))
        status = _clean(raw_row.get("status"))
        issue_category = classify_recall_reason(reason)

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
            "termination_date": _fmt_date(raw_row.get("termination_date")),
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
            "recall", NAME, rid or recall_fields["event_id"], title, recall_url(rid), raw_text,
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
                "issue_category": issue_category,
                "discovery_query_category": query_label or None,
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


def _fetch_paginated(*, query: str | None, query_label: str, page_size: int,
                      max_pages: int, max_results: int) -> ConnectorResult:
    page_size = max(1, min(int(page_size), 100))
    max_pages = max(1, int(max_pages))
    max_results = max(0, int(max_results))
    if max_results == 0:
        return ConnectorResult(NAME, query_label, ok=True, count=0, records=[])

    unique_raw: dict[str, dict] = {}
    pages_run = 0
    raw_results = 0
    try:
        for page in range(max_pages):
            remaining = max_results - len(unique_raw)
            if remaining <= 0:
                break
            limit = min(page_size, remaining)
            params = {"limit": limit, "skip": page * page_size}
            if query:
                params["search"] = query
            try:
                data = get_json(URL, params)
            except Exception as exc:
                msg = describe_error(exc)
                if "404" in msg:
                    break
                raise
            pages_run += 1
            rows = data.get("results", []) or []
            raw_results += len(rows)
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
        return ConnectorResult(NAME, query_label, ok=False, error=describe_error(exc),
                               stats={"query_count": 1, "raw_results": raw_results})

    records = _parse(list(unique_raw.values()), query_label)
    return ConnectorResult(
        NAME, query_label, ok=True, count=len(records), records=records,
        warnings=[f"bounded pagination: {pages_run} page(s), {len(records)} unique recall event(s)"],
        stats={
            "query_count": 1,
            "raw_results": raw_results,
            "unique_records": len(records),
            "duplicates_removed": max(0, raw_results - len(records)),
            "pages_run": pages_run,
        },
    )


def discover_taxonomy(*, page_size: int = 50, max_pages: int = 3,
                       max_results: int = 300) -> ConnectorResult:
    """Run all broad query specs fairly, then fill from a bounded recent sweep.

    Fair pagination prevents the first broad term from consuming the complete
    per-source cap. The recent sweep is an official-source fallback, not a new
    source, and ensures sparse taxonomy-query behaviour cannot silently limit
    discovery to a few dozen records.
    """
    page_size = max(1, min(int(page_size), 100))
    max_pages = max(1, int(max_pages))
    max_results = max(0, int(max_results))
    if max_results == 0:
        return ConnectorResult(NAME, "expanded recall taxonomy", ok=True, count=0, records=[])

    query_specs = list(RECALL_QUERY_SPECS)
    fair_page_size = max(5, min(page_size, max(5, math.ceil(max_results / max(1, len(query_specs))))))
    unique: dict[str, tuple[dict, str]] = {}
    raw_results = 0
    query_count = 0
    failed_queries = 0
    pages_run = 0
    rejection_reasons: Counter[str] = Counter()
    per_query_counts: dict[str, int] = {}
    errors: list[str] = []

    # Round-robin pages: every issue group is queried before deeper pages.
    exhausted_queries: set[str] = set()
    for page in range(max_pages):
        for label, query in query_specs:
            if query in exhausted_queries:
                continue
            if len(unique) >= max_results:
                break
            params = {
                "search": query,
                "limit": min(fair_page_size, max_results - len(unique)),
                "skip": page * fair_page_size,
            }
            query_count += 1
            try:
                data = get_json(URL, params)
            except Exception as exc:
                msg = describe_error(exc)
                if "404" in msg:
                    per_query_counts.setdefault(query, 0)
                    exhausted_queries.add(query)
                    continue
                failed_queries += 1
                errors.append(f"{query}: {msg}")
                continue
            pages_run += 1
            rows = data.get("results", []) or []
            raw_results += len(rows)
            per_query_counts[query] = per_query_counts.get(query, 0) + len(rows)
            if len(rows) < params["limit"]:
                exhausted_queries.add(query)
            for row in rows:
                if not _clean(row.get("recall_number") or row.get("event_id")):
                    rejection_reasons["missing recall/event ID"] += 1
                    continue
                if not _clean(row.get("recalling_firm")) and not _clean(row.get("product_description")):
                    rejection_reasons["missing company and product"] += 1
                    continue
                unique.setdefault(_dedupe_key(row), (row, label))
                if len(unique) >= max_results:
                    break
        if len(unique) >= max_results:
            break

    taxonomy_count = len(unique)

    # Bounded recent official-recall sweep. It fills only remaining capacity,
    # deduplicates by recall number, and preserves the official reason text.
    recent_pages = 0
    recent_added = 0
    recent_page_cap = min(6, max(1, math.ceil(max_results / page_size)))
    for page in range(recent_page_cap):
        if len(unique) >= max_results:
            break
        limit = min(page_size, max_results - len(unique))
        query_count += 1
        try:
            data = get_json(URL, {
                "limit": limit,
                "skip": page * page_size,
                "sort": "report_date:desc",
            })
        except Exception as exc:
            msg = describe_error(exc)
            if "404" in msg:
                break
            failed_queries += 1
            errors.append(f"recent sweep page {page + 1}: {msg}")
            break
        recent_pages += 1
        pages_run += 1
        rows = data.get("results", []) or []
        raw_results += len(rows)
        if not rows:
            break
        before = len(unique)
        for row in rows:
            if not _clean(row.get("recall_number") or row.get("event_id")):
                rejection_reasons["missing recall/event ID"] += 1
                continue
            if not _clean(row.get("recalling_firm")) and not _clean(row.get("product_description")):
                rejection_reasons["missing company and product"] += 1
                continue
            if not _is_relevant_sweep_row(row):
                rejection_reasons["recent recall outside approved drug-product/quality taxonomy"] += 1
                continue
            unique.setdefault(_dedupe_key(row), (row, "recent official recall sweep"))
            if len(unique) >= max_results:
                break
        recent_added += max(0, len(unique) - before)
        if len(rows) < limit or len(unique) == before:
            break

    records = []
    for row, label in unique.values():
        records.extend(_parse([row], label))

    warnings = [
        f"Checkpoint 5A.1 recall expansion: {len(query_specs)} broad query spec(s), "
        f"{query_count} API call(s), {taxonomy_count} taxonomy hit(s), "
        f"{recent_added} recent-sweep addition(s), {len(records)} unique recall event(s)",
        f"Configured page_size={page_size}, max_pages_per_query={max_pages}, "
        f"fair_page_size={fair_page_size}, source_cap={max_results}",
    ]
    if errors:
        warnings.append(f"{len(errors)} recall query call(s) failed; successful calls were retained")

    return ConnectorResult(
        NAME, "expanded recall taxonomy + bounded recent sweep",
        ok=bool(records) or failed_queries < query_count,
        count=len(records), records=records,
        error=(errors[0] if errors and not records else None),
        warnings=warnings,
        stats={
            "query_count": query_count,
            "raw_results": raw_results,
            "unique_records": len(records),
            "taxonomy_unique": taxonomy_count,
            "recent_sweep_added": recent_added,
            "duplicates_removed": max(0, raw_results - len(records)),
            "failed_queries": failed_queries,
            "successful_queries": max(0, query_count - failed_queries),
            "pages_run": pages_run,
            "rejection_reasons": dict(rejection_reasons),
            "query_result_counts": per_query_counts,
            "configured_page_size": page_size,
            "configured_max_pages": max_pages,
            "source_cap": max_results,
        },
    )


def search(term: str, max_results: int = 10) -> ConnectorResult:
    cleaned = str(term or "").replace('"', "").strip()
    query = f'(product_description:"{cleaned}" OR reason_for_recall:"{cleaned}")'
    return _fetch_paginated(
        query=query, query_label=cleaned or "recall search",
        page_size=min(max_results, 25), max_pages=1, max_results=max_results,
    )


def discover_events(reason_term: str, max_results: int = 10, *,
                    page_size: int | None = None, max_pages: int = 1) -> ConnectorResult:
    cleaned = str(reason_term or "").replace('"', "").strip()
    return _fetch_paginated(
        query=_query_for_terms([cleaned]), query_label=cleaned,
        page_size=page_size or min(max_results, 50), max_pages=max_pages,
        max_results=max_results,
    )


def discover_category(category: str, terms: list[str], *, page_size: int = 50,
                      max_pages: int = 3, max_results: int = 150) -> ConnectorResult:
    return _fetch_paginated(
        query=_query_for_terms(terms), query_label=category,
        page_size=page_size, max_pages=max_pages, max_results=max_results,
    )
