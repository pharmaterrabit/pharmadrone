"""openFDA Drug Shortages connector — official supply/availability signals.

Shortage records are not treated as formulation failures by default. The
connector preserves the official shortage reason and classifies only a cautious
supply, manufacturing, discontinuation, or availability signal.
"""
from __future__ import annotations

import hashlib
from urllib.parse import quote

from .base import get_json, record, ConnectorResult, describe_error

NAME = "openFDA (Drug Shortages)"
URL = "https://api.fda.gov/drug/shortages.json"


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        value = ", ".join(str(x) for x in value if x is not None)
    return " ".join(str(value).split()).strip()


def _stable_key(row: dict) -> str:
    package_ndc = _clean(row.get("package_ndc"))
    if package_ndc:
        return package_ndc
    payload = "|".join([
        _clean(row.get("company_name")).lower(),
        _clean(row.get("generic_name") or row.get("proprietary_name")).lower(),
        _clean(row.get("dosage_form")).lower(),
        _clean(row.get("presentation")).lower(),
        _clean(row.get("strength")).lower(),
    ])
    return "SHORTAGE-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _official_url(package_ndc: str, generic_name: str) -> str:
    if package_ndc:
        return f'{URL}?search=package_ndc:%22{quote(package_ndc, safe="-")}%22'
    if generic_name:
        return f'{URL}?search=generic_name:%22{quote(generic_name, safe="- ")}%22'
    return "https://open.fda.gov/apis/drug/drugshortages/"


def _signal_category(row: dict) -> tuple[str, bool]:
    reason = _clean(row.get("shortage_reason")).lower()
    status = _clean(row.get("status")).lower()
    availability = _clean(row.get("availability")).lower()
    discontinued = _clean(row.get("discontinued_date")).lower()
    blob = " ".join((reason, status, availability, discontinued))
    if "discontinu" in blob:
        return "discontinuation signal", False
    quality_terms = (
        "manufactur", "quality", "sterility", "contamin", "particulate",
        "specification", "potency", "assay", "impurity", "facility", "production delay",
    )
    if any(term in reason for term in quality_terms):
        return "manufacturing / quality supply signal", True
    if reason:
        return "supply / availability signal", False
    return "availability signal", False


def _parse(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        source_id = _stable_key(row)
        package_ndc = _clean(row.get("package_ndc"))
        generic = _clean(row.get("generic_name"))
        proprietary = _clean(row.get("proprietary_name"))
        product = proprietary or generic
        company = _clean(row.get("company_name"))
        reason = _clean(row.get("shortage_reason"))
        status = _clean(row.get("status"))
        availability = _clean(row.get("availability"))
        category, quality_supported = _signal_category(row)
        url = _official_url(package_ndc, generic)
        raw_text = (
            f"FDA drug shortage record. Company: {company}. Product: {product}. "
            f"Generic name: {generic}. Dosage form: {_clean(row.get('dosage_form'))}. "
            f"Presentation: {_clean(row.get('presentation'))}. Strength: {_clean(row.get('strength'))}. "
            f"Status: {status}. Availability: {availability}. "
            f"Official shortage reason: {reason or 'not stated'}. "
            f"Initial posting date: {_clean(row.get('initial_posting_date'))}. "
            f"Update date: {_clean(row.get('update_date'))}. "
            f"Discontinued date: {_clean(row.get('discontinued_date'))}."
        )
        event_type = "discontinuation" if "discontinu" in (status + " " + _clean(row.get("discontinued_date"))).lower() else "shortage"
        out.append(record(
            "shortage", NAME, source_id,
            f"FDA shortage: {product or generic or source_id}", url, raw_text,
            source_category="regulatory",
            entities={
                "company": company or None,
                "product": product or generic or None,
                "molecule": generic or None,
                "source_event_id": source_id,
                "package_ndc": package_ndc or None,
                "shortage_key": source_id,
                "event_type": event_type,
                "event_reason": reason or None,
                "shortage_reason": reason or None,
                "shortage_status": status or None,
                "availability": availability or None,
                "issue_category": category,
                "quality_problem_supported": quality_supported,
                "direct_problem_evidence": bool(reason),
                "dosage_form": _clean(row.get("dosage_form")) or None,
                "presentation": _clean(row.get("presentation")) or None,
                "strength": _clean(row.get("strength")) or None,
                "initial_posting_date": _clean(row.get("initial_posting_date")) or None,
                "update_date": _clean(row.get("update_date")) or None,
                "discontinued_date": _clean(row.get("discontinued_date")) or None,
                "regulator": "FDA",
                "country": "United States",
                "official_source_url": url,
            },
        ))
    return out


def discover_shortages(*, max_results: int = 300, page_size: int = 50,
                       max_pages: int = 6) -> ConnectorResult:
    max_results = max(0, int(max_results))
    page_size = max(1, min(int(page_size), 100))
    max_pages = max(1, int(max_pages))
    if max_results == 0:
        return ConnectorResult(NAME, "drug shortages", ok=True, count=0, records=[])

    unique: dict[str, dict] = {}
    pages_run = 0
    try:
        for page in range(max_pages):
            remaining = max_results - len(unique)
            if remaining <= 0:
                break
            limit = min(page_size, remaining)
            try:
                data = get_json(URL, {"limit": limit, "skip": page * page_size})
            except Exception as exc:
                msg = describe_error(exc)
                if "404" in msg:
                    break
                raise
            pages_run += 1
            rows = data.get("results", []) or []
            if not rows:
                break
            before = len(unique)
            for row in rows:
                unique.setdefault(_stable_key(row), row)
                if len(unique) >= max_results:
                    break
            if len(rows) < limit or len(unique) == before:
                break
    except Exception as exc:
        return ConnectorResult(NAME, "drug shortages", ok=False, error=describe_error(exc))

    records = _parse(list(unique.values()))
    return ConnectorResult(
        NAME, "drug shortages", ok=True, count=len(records), records=records,
        warnings=[f"bounded pagination: {pages_run} page(s), {len(records)} unique shortage record(s)"],
    )


def search(term: str, max_results: int = 10) -> ConnectorResult:
    """Small connector self-test search; discovery uses discover_shortages()."""
    cleaned = str(term or "").replace('"', "").strip()
    try:
        data = get_json(URL, {
            "search": f'(generic_name:"{cleaned}" OR proprietary_name:"{cleaned}")',
            "limit": min(max_results, 50),
        })
    except Exception as exc:
        msg = describe_error(exc)
        if "404" in msg:
            return ConnectorResult(NAME, cleaned, ok=True, count=0, records=[])
        return ConnectorResult(NAME, cleaned, ok=False, error=msg)
    records = _parse((data.get("results", []) or [])[:max_results])
    return ConnectorResult(NAME, cleaned, ok=True, count=len(records), records=records)
