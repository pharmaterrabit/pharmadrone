"""Official EMA medicines JSON connector for Phase 4A.

The EMA feed is a medicine catalogue and regulatory-status source. It must not be
treated as evidence that a product has a quality problem or commercial need.
"""
from __future__ import annotations

from datetime import datetime
import html
from typing import Any

from .base import ConnectorResult, describe_error, get_json, record

NAME = "European Medicines Agency (Medicines)"
URL = "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"


def _clean(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).replace("\xa0", " ").split()).strip()


def _date_key(value: Any) -> tuple[int, int, int]:
    text = _clean(value)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.year, dt.month, dt.day
        except ValueError:
            continue
    return 0, 0, 0


def _iso_date(value: Any) -> str:
    text = _clean(value)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_payload(payload: dict[str, Any], *, max_results: int = 5000, term: str = "") -> ConnectorResult:
    meta = payload.get("meta") or {}
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        return ConnectorResult(NAME, term or "EMA medicines", ok=False, error="response data was not a list")
    needle = _clean(term).casefold()
    if needle:
        rows = [row for row in rows if needle in " ".join(_clean(row.get(key)) for key in (
            "name_of_medicine", "active_substance", "international_non_proprietary_name_common_name",
            "marketing_authorisation_developer_applicant_holder", "therapeutic_area_mesh",
        )).casefold()]
    rows = sorted(rows, key=lambda row: _date_key(row.get("last_updated_date")), reverse=True)[:max(0, int(max_results))]
    records = []
    rejected = 0
    categories: dict[str, int] = {}
    for row in rows:
        product_number = _clean(row.get("ema_product_number"))
        medicine = _clean(row.get("name_of_medicine"))
        if not product_number or not medicine:
            rejected += 1
            continue
        category = _clean(row.get("category")) or "Not stated"
        categories[category] = categories.get(category, 0) + 1
        company = _clean(row.get("marketing_authorisation_developer_applicant_holder"))
        molecule = _clean(row.get("active_substance") or row.get("international_non_proprietary_name_common_name"))
        status = _clean(row.get("medicine_status"))
        url = _clean(row.get("medicine_url"))
        raw_text = (
            f"Official EMA medicine record. Category: {category}. Medicine: {medicine}. "
            f"EMA product number: {product_number}. Active substance: {molecule}. "
            f"Marketing authorisation applicant or holder: {company}. Status: {status}. "
            f"Therapeutic area: {_clean(row.get('therapeutic_area_mesh'))}. "
            f"Last updated by EMA: {_clean(row.get('last_updated_date'))}."
        )
        records.append(record(
            "ema_medicine", NAME, product_number, f"EMA medicine: {medicine}", url, raw_text,
            source_category="regulatory",
            entities={
                "company": company or None,
                "product": medicine,
                "molecule": molecule or None,
                "source_event_id": product_number,
                "ema_product_number": product_number,
                "medicine_category": category,
                "medicine_status": status or None,
                "therapeutic_area": _clean(row.get("therapeutic_area_mesh")) or None,
                "therapeutic_indication": _clean(row.get("therapeutic_indication")) or None,
                "additional_monitoring": _clean(row.get("additional_monitoring")) or None,
                "orphan_medicine": _clean(row.get("orphan_medicine")) or None,
                "marketing_authorisation_date": _iso_date(row.get("marketing_authorisation_date")) or None,
                "last_update_date": _iso_date(row.get("last_updated_date")) or None,
                "regulator": "EMA",
                "region": "European Union",
                "official_source_url": url,
                "direct_problem_evidence": False,
            },
        ))
    return ConnectorResult(
        NAME, term or "EMA medicines", ok=True, count=len(records), records=records,
        warnings=["Official regulatory catalogue context only; not evidence of product failure or commercial need."],
        stats={
            "feed_timestamp": _clean(meta.get("timestamp")),
            "feed_total_records": int(meta.get("total_records") or len(payload.get("data") or [])),
            "accepted_records": len(records), "rejected_records": rejected,
            "categories": categories, "dataset_url": URL,
        },
    )


def fetch(*, max_results: int = 5000) -> ConnectorResult:
    try:
        return parse_payload(get_json(URL), max_results=max_results)
    except Exception as exc:
        return ConnectorResult(NAME, "EMA medicines", ok=False, error=describe_error(exc))


def search(term: str, max_results: int = 10) -> ConnectorResult:
    try:
        return parse_payload(get_json(URL), max_results=max_results, term=term)
    except Exception as exc:
        return ConnectorResult(NAME, term, ok=False, error=describe_error(exc))
