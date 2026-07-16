"""Bounded Tavily discovery for potential commercial events.

Results are discovery signals unless the URL matches a retained official company
domain. A signal is never treated as a completed transaction without primary
evidence and human validation.
"""
from __future__ import annotations

from urllib.parse import urlparse

from .base import ConnectorResult
from . import tavily_search

NAME = "Commercial Signal Discovery"


def classify(text: str) -> str:
    value = " ".join(str(text or "").lower().split())
    if any(term in value for term in ("acquisition", "acquires", "acquired", "merger", "merge with")):
        return "M&A"
    if any(term in value for term in ("license agreement", "licensing agreement", "licenses", "licensed", "exclusive license")):
        return "Licensing"
    if any(term in value for term in ("series a", "series b", "series c", "financing", "funding round", "raises", "investment")):
        return "Corporate financing"
    if any(term in value for term in ("partnership", "partners with", "collaboration agreement", "strategic collaboration")):
        return "Commercial partnership"
    if any(term in value for term in ("supply agreement", "manufacturing agreement", "commercial launch", "distribution agreement")):
        return "Commercial signal"
    return ""


def _domain(url: str) -> str:
    value = urlparse(str(url or "")).netloc.casefold().split(":")[0]
    return value[4:] if value.startswith("www.") else value


def discover(organisation_name: str, official_url: str = "", *, max_results: int = 3, cost=None) -> ConnectorResult:
    query = f'"{organisation_name}" licensing partnership acquisition financing pharmaceutical'
    result = tavily_search.search(query, max_results=max_results, cost=cost)
    if not result.ok:
        return ConnectorResult(NAME, query, ok=False, error=result.error, warnings=result.warnings)
    official_domain = _domain(official_url)
    records = []
    for item in result.records:
        event_type = classify(f"{item.get('title', '')} {item.get('raw_text', '')}")
        if not event_type:
            continue
        url = str(item.get("url") or "")
        primary = bool(official_domain and (_domain(url) == official_domain or _domain(url).endswith("." + official_domain)))
        transformed = dict(item)
        transformed.update({
            "source_type": "commercial_signal", "source_category": "commercial",
            "source_name": NAME, "record_id": url,
            "entities": {
                "deal_type": event_type, "party_a": organisation_name,
                "party_b": "", "subject": item.get("title") or "",
                "primary_source_verified": primary,
                "signal_origin": "official company domain" if primary else "web discovery",
            },
        })
        records.append(transformed)
    return ConnectorResult(NAME, query, ok=True, count=len(records), records=records, warnings=result.warnings)
