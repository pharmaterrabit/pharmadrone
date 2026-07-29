"""Read-only USPTO PatentsView / PatentSearch discovery connector."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from .base import ConnectorResult, USER_AGENT, describe_error, record


NAME = "USPTO / PatentsView"
SEARCH_URL = "https://search.patentsview.org/api/v1/patent/"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _party_names(values: Any) -> str:
    names: list[str] = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        name = _text(
            item.get("assignee_organization")
            or item.get("assignee_name_first")
            or item.get("assignee_name_last")
            or item.get("organization")
        )
        if name and name not in names:
            names.append(name)
    return "; ".join(names)


def parse_response(payload: dict[str, Any], *, query: str = "") -> ConnectorResult:
    records: list[dict[str, Any]] = []
    for item in payload.get("patents") or []:
        if not isinstance(item, dict):
            continue
        number = _text(item.get("patent_id"))
        if not number:
            continue
        title = _text(item.get("patent_title")) or number
        abstract = _text(item.get("patent_abstract"))
        applicant = _party_names(item.get("assignees") or item.get("assignee"))
        date = _text(item.get("patent_date"))
        crosswalk = item.get("granted_pregrant_crosswalk") or {}
        if isinstance(crosswalk, list):
            crosswalk = crosswalk[0] if crosswalk else {}
        application_number = _text(crosswalk.get("application_number")) if isinstance(crosswalk, dict) else ""
        official = f"https://ppubs.uspto.gov/pubwebapp/external.html?q={quote(number)}&type=PATENT"
        entities = {
            "publication_number": f"US{number}",
            "application_number": application_number,
            "jurisdiction": "US",
            "document_kind": "",
            "title": title,
            "abstract": abstract,
            "publication_date": date,
            "applicant": applicant,
            "official_source_url": official,
            "query_context": query,
        }
        records.append(record(
            "uspto_patent_document", NAME, f"US{number}", title, official,
            "\n".join(part for part in (title, abstract) if part),
            source_category="patent", entities=entities,
        ))
    return ConnectorResult(
        NAME, query, ok=True, count=len(records), records=records,
        stats={"documents": len(records), "source_authority": "official USPTO PatentsView"},
    )


def search(query: str, *, limit: int = 20) -> ConnectorResult:
    text = _text(query)
    if not text:
        return ConnectorResult(NAME, query, ok=True, count=0, records=[])
    clauses = [
        {"patent_title": {"_contains": text}},
        {"patent_abstract": {"_contains": text}},
    ]
    params = {
        "q": json.dumps({"_or": clauses}, separators=(",", ":")),
        "f": json.dumps([
            "patent_id", "patent_title", "patent_date", "patent_abstract",
            "assignees.assignee_organization",
            "granted_pregrant_crosswalk.application_number",
        ], separators=(",", ":")),
        "o": json.dumps({"size": max(1, min(int(limit), 20))}, separators=(",", ":")),
    }
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.get(
                SEARCH_URL, params=params,
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            return parse_response(response.json(), query=query)
    except Exception as exc:
        return ConnectorResult(NAME, query, ok=False, error=describe_error(exc))
