"""Official MHRA medicines recall/notification connector via GOV.UK Search API."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any

from .base import ConnectorResult, describe_error, get_json, record

NAME = "MHRA Medicines Recalls"
URL = "https://www.gov.uk/api/search.json"
GOVUK = "https://www.gov.uk"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _iso(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return ""


def _identity(link: str) -> str:
    return link.strip("/").split("/")[-1]


def _title_fields(title: str) -> tuple[str, str, str, str]:
    clean_title = re.sub(r"^UPDATE:\s*", "", title, flags=re.I)
    alert_class = clean_title.split(":", 1)[0].strip() if ":" in clean_title else "Medicines recall/notification"
    tail = clean_title.split(":", 1)[1].strip() if ":" in clean_title else ""
    parts = [part.strip() for part in tail.split(",") if part.strip()]
    reference = ""
    if parts and re.search(r"\bEL\s*\(?\d{2,4}\)?", parts[-1], re.I):
        reference = parts.pop()
    company = parts.pop(0) if parts else ""
    product = ", ".join(parts)
    return alert_class, company, product, reference


def _description_company(description: str) -> str:
    """Recover the company from older notices whose title has no CSV fields."""
    text = _clean(description)
    parenthesised = re.search(r"^\((.+)\)\s*(?:Recall|Class\s+\d+\s+action)\b", text, flags=re.I)
    if parenthesised:
        return parenthesised.group(1).strip(" ,.-")
    patterns = (
        r"^(.{2,120}?)\s+(?:are|is)\s+recalling\b",
        r"^(.{2,120}?)\s+(?:has|have)\s+(?:informed|notified|identified|reported)\b",
        r"^(.{2,120}?)\s+(?:is|are)\s+(?:initiating|reporting)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(1).strip(" ,.-")
    return ""


def _legacy_product(title: str) -> str:
    text = re.sub(r"^UPDATE:\s*", "", _clean(title), flags=re.I)
    text = re.sub(r"^(?:Class\s+\d+\s+)?Medicines?\s+(?:Recall|Defect Notification)\s*:?\s*", "", text, flags=re.I)
    text = re.sub(r"^Recall\s+of\s+", "", text, flags=re.I)
    return re.sub(r"^(?:specific\s+)?batches?\s+of\s+", "", text, flags=re.I).strip(" ,.-")


def _issue_category(description: str) -> str:
    text = description.casefold()
    rules = (
        (("steril", "microbial", "contaminat", "pathogen"), "sterility / contamination"),
        (("particle", "particulate", "precipitat"), "particulate / precipitation"),
        (("label", "carton", "leaflet", "packaging"), "labelling / packaging"),
        (("impurit", "nitrosamine", "degrad"), "impurity / degradation"),
        (("dissolution", "release specification"), "dissolution / release performance"),
        (("potency", "assay", "specification", "out of specification"), "quality specification"),
    )
    for terms, category in rules:
        if any(term in text for term in terms):
            return category
    return "medicine recall / defect notification"


def parse_payload(payload: dict[str, Any], *, max_results: int = 1000) -> ConnectorResult:
    rows = payload.get("results") or []
    if not isinstance(rows, list):
        return ConnectorResult(NAME, "MHRA medicines recalls", ok=False, error="response results were not a list")
    records = []
    rejected = 0
    for row in rows[:max(0, int(max_results))]:
        link = _clean(row.get("link"))
        title = _clean(row.get("title"))
        alert_types = row.get("alert_type") or []
        if "medicines-recall-notification" not in alert_types or not link or not title:
            rejected += 1
            continue
        source_id = _identity(link)
        description = _clean(row.get("description"))
        alert_class, company, product, reference = _title_fields(title)
        if not company:
            company = _description_company(description)
        if not product:
            product = _legacy_product(title)
        published = _iso(row.get("public_timestamp"))
        url = f"{GOVUK}{link}"
        direct = bool(description and company and product)
        category = _issue_category(description)
        item = record(
            "recall", NAME, source_id, title, url,
            f"Official MHRA {alert_class}. Company: {company or 'not parsed from title'}. "
            f"Product: {product or 'not parsed from title'}. Published: {published}. "
            f"MHRA description: {description or 'not stated'}.",
            source_category="regulatory",
            entities={
                "company": company or None, "product": product or None,
                "source_event_id": source_id, "event_type": "medicine recall / defect notification",
                "event_reason": description or None, "direct_problem_evidence": direct,
                "quality_problem_supported": direct, "issue_category": category,
                "alert_class": alert_class, "mhra_reference": reference or None,
                "publication_date": published or None, "last_update_date": published or None,
                "regulator": "MHRA", "country": "United Kingdom", "region": "United Kingdom",
                "official_source_url": url,
                "recall_fields": {
                    "recall_number": reference or source_id,
                    "reason_for_recall": description,
                    "report_date": published.replace("-", "") if published else "",
                    "recalling_firm": company,
                    "product_description": product,
                },
            },
        )
        item["region_hint"] = "United Kingdom"
        records.append(item)
    return ConnectorResult(
        NAME, "MHRA medicines recalls", ok=True, count=len(records), records=records,
        warnings=["Only explicit MHRA medicine recall/defect descriptions can support a problem signal; other alerts remain context."],
        stats={"api_total": int(payload.get("total") or len(rows)), "returned": len(rows), "accepted": len(records), "rejected": rejected},
    )


def fetch(*, max_results: int = 1000) -> ConnectorResult:
    try:
        payload = get_json(URL, {
            "filter_organisations": "medicines-and-healthcare-products-regulatory-agency",
            "filter_content_store_document_type": "medical_safety_alert",
            "filter_alert_type": "medicines-recall-notification",
            "order": "-public_timestamp", "count": min(1500, max_results),
            "fields": "title,link,description,public_timestamp,alert_type",
        })
        return parse_payload(payload, max_results=max_results)
    except Exception as exc:
        return ConnectorResult(NAME, "MHRA medicines recalls", ok=False, error=describe_error(exc))


def search(term: str, max_results: int = 10) -> ConnectorResult:
    try:
        payload = get_json(URL, {
            "filter_organisations": "medicines-and-healthcare-products-regulatory-agency",
            "filter_content_store_document_type": "medical_safety_alert",
            "filter_alert_type": "medicines-recall-notification",
            "q": term, "order": "-public_timestamp", "count": min(100, max_results),
            "fields": "title,link,description,public_timestamp,alert_type",
        })
        return parse_payload(payload, max_results=max_results)
    except Exception as exc:
        return ConnectorResult(NAME, term, ok=False, error=describe_error(exc))
