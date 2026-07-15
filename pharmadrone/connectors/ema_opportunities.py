"""Official EMA event feeds that can create EU opportunity signals."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import html
from typing import Any, Callable

from .base import ConnectorResult, describe_error, get_json, record


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    source_type: str
    product_fields: tuple[str, ...]
    id_fields: tuple[str, ...]
    url_field: str
    date_fields: tuple[str, ...]
    issue_field: str
    issue_label: str
    include: Callable[[dict[str, Any]], bool] = lambda _row: True


def _clean(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).replace("\xa0", " ").split()).strip()


def _first(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    return next((_clean(row.get(field)) for field in fields if _clean(row.get(field))), "")


def _iso_date(value: Any) -> str:
    text = _clean(value)
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return ""


BASE = "https://www.ema.europa.eu/en/documents/report"
FEEDS: dict[str, Feed] = {
    "ema_shortages": Feed(
        "EMA medicine shortage", f"{BASE}/shortages-output-json-report_en.json", "shortage",
        ("medicine_affected", "international_non_proprietary_name_inn_or_common_name"),
        ("shortage_url", "medicine_affected"), "shortage_url",
        ("last_updated_date", "first_published_date", "start_of_shortage_date"),
        "supply_shortage_status", "drug shortage",
    ),
    "ema_dhpc": Feed(
        "EMA direct healthcare professional communication", f"{BASE}/dhpc-output-json-report_en.json", "recall",
        ("name_of_medicine", "active_substances"), ("procedure_number", "dhpc_url"), "dhpc_url",
        ("last_updated_date", "dissemination_date", "first_published_date"), "dhpc_type", "safety communication",
    ),
    "ema_safety_referrals": Feed(
        "EMA safety referral", f"{BASE}/referrals-output-json-report_en.json", "recall",
        ("associated_names_centrally_authorised_medicines", "associated_names_non_centrally_authorised_medicines",
         "international_non_proprietary_name_inn_common_name", "referral_name"),
        ("reference_number", "referral_url"), "referral_url",
        ("last_updated_date", "first_published_date", "procedure_start_date"), "current_status", "regulatory safety review",
        include=lambda row: _clean(row.get("safety_referral")).casefold() == "yes",
    ),
    "ema_psusa_outcomes": Feed(
        "EMA periodic safety assessment outcome",
        f"{BASE}/medicines-output-periodic_safety_update_report_single_assessments-output-json-report_en.json", "recall",
        ("related_medicines", "active_substance", "active_substances_in_scope_of_procedure"),
        ("procedure_number", "psusa_url"), "psusa_url", ("last_updated_date", "first_published_date"),
        "regulatory_outcome", "regulatory safety outcome",
        include=lambda row: _clean(row.get("regulatory_outcome")).casefold() not in {"", "maintenance"},
    ),
    "ema_post_authorisation_withdrawals": Feed(
        "EMA withdrawn post-authorisation application",
        f"{BASE}/medicines-output-post_authorisation_json-report_en.json", "recall",
        ("name_of_medicine", "international_non_proprietary_name_common_name", "active_substance"),
        ("ema_product_number", "medicine_url"), "medicine_url",
        ("last_updated_date", "first_published_date", "withdrawal_of_application_date"),
        "post_authorisation_procedure_status", "withdrawn post-authorisation application",
        include=lambda row: "withdraw" in _clean(row.get("post_authorisation_procedure_status")).casefold(),
    ),
}


def parse_payload(feed_name: str, payload: dict[str, Any], *, max_results: int = 5000) -> ConnectorResult:
    feed = FEEDS[feed_name]
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        return ConnectorResult(feed.name, feed_name, ok=False, error="response data was not a list")
    records = []
    rejected = 0
    for row in rows:
        if not isinstance(row, dict) or not feed.include(row):
            continue
        product = _first(row, feed.product_fields)
        url = _clean(row.get(feed.url_field))
        identifier = _first(row, feed.id_fields)
        if not identifier:
            identifier = hashlib.sha256(f"{product}|{url}".encode()).hexdigest()[:20]
        if not product or not url:
            rejected += 1
            continue
        official_detail = _clean(row.get(feed.issue_field))
        event_date = _first(row, feed.date_fields)
        molecule = _first(row, ("active_substance", "active_substances", "international_non_proprietary_name_inn_or_common_name",
                                "international_non_proprietary_name_inn_common_name"))
        company = _clean(row.get("marketing_authorisation_developer_applicant_holder"))
        reason = f"{feed.issue_label}: {official_detail}" if official_detail else feed.issue_label
        entities = {
            "company": company or None, "product": product, "molecule": molecule or None,
            "source_event_id": identifier, "event_reason": reason, "issue_category": feed.issue_label,
            "last_update_date": _iso_date(event_date) or None, "regulator": "EMA", "region": "European Union",
            "official_source_url": url, "direct_problem_evidence": True,
        }
        if feed.source_type == "recall":
            entities["recall_fields"] = {"recall_number": identifier, "reason_for_recall": reason}
        if feed.source_type == "shortage":
            entities.update({"shortage_key": identifier, "shortage_reason": reason, "quality_problem_supported": False})
        raw = ". ".join(f"{key.replace('_', ' ').title()}: {_clean(value)}" for key, value in row.items() if _clean(value))
        item = record(feed.source_type, feed.name, identifier, f"{feed.name}: {product}", url, raw,
                      source_category="regulatory", entities=entities)
        item["region_hint"] = "European Union"
        records.append(item)
        if len(records) >= max(0, int(max_results)):
            break
    meta = payload.get("meta") or {}
    return ConnectorResult(feed.name, feed_name, ok=True, count=len(records), records=records, stats={
        "feed_timestamp": _clean(meta.get("timestamp")), "feed_total_records": int(meta.get("total_records") or len(rows)),
        "accepted_records": len(records), "rejected_records": rejected, "dataset_url": feed.url,
    })


def fetch(feed_name: str, *, max_results: int = 5000) -> ConnectorResult:
    feed = FEEDS[feed_name]
    try:
        return parse_payload(feed_name, get_json(feed.url), max_results=max_results)
    except Exception as exc:
        return ConnectorResult(feed.name, feed_name, ok=False, error=describe_error(exc))
