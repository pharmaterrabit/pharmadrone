"""Official EPO Open Patent Services connector.

OPS is authoritative source evidence for the fields it returns. Search terms
are discovery context only and never establish that a patent protects a drug,
that an applicant is the current owner, or that a right is enforceable.
"""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import quote
import xml.etree.ElementTree as ET

import httpx

from .base import ConnectorResult, USER_AGENT, describe_error, record

NAME = "EPO Open Patent Services"
TOKEN_URL = "https://ops.epo.org/3.2/auth/accesstoken"
SEARCH_URL = "https://ops.epo.org/3.2/rest-services/published-data/search"
SERVICE_URL = "https://ops.epo.org/3.2/rest-services"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in node.iter() if _local(item.tag) == name]


def _first_text(node: ET.Element, name: str, *, lang: str = "") -> str:
    candidates = _children(node, name)
    if lang:
        candidates = [item for item in candidates if item.attrib.get("lang") == lang] or candidates
    for item in candidates:
        text = _clean(" ".join(item.itertext()))
        if text:
            return text
    return ""


def _date(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else ""


def espacenet_url(publication_number: str) -> str:
    number = re.sub(r"[^A-Za-z0-9]", "", publication_number or "").upper()
    return f"https://worldwide.espacenet.com/patent/search?q={quote('pn=' + number)}" if number else ""


def google_patents_url(publication_number: str) -> str:
    number = re.sub(r"[^A-Za-z0-9]", "", publication_number or "").upper()
    return f"https://patents.google.com/patent/{number}/en" if number else ""


def uk_register_url(publication_number: str) -> str:
    number = re.sub(r"[^0-9]", "", publication_number or "")
    return "https://www.gov.uk/search-for-patent" if number else ""


def parse_search_xml(payload: bytes, *, query: str = "") -> ConnectorResult:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        return ConnectorResult(NAME, query, ok=False, error=f"invalid OPS XML: {exc}")
    records: list[dict[str, Any]] = []
    for document in _children(root, "exchange-document"):
        country = _clean(document.attrib.get("country") or _first_text(document, "country")).upper()
        number = _clean(document.attrib.get("doc-number") or _first_text(document, "doc-number"))
        kind = _clean(document.attrib.get("kind") or _first_text(document, "kind")).upper()
        publication = re.sub(r"[^A-Za-z0-9]", "", f"{country}{number}{kind}").upper()
        if not country or not number:
            continue
        title = _first_text(document, "invention-title", lang="en") or _first_text(document, "invention-title")
        abstract = _first_text(document, "abstract", lang="en") or _first_text(document, "abstract")
        parties = []
        for party_type, tag in (("applicant", "applicant"), ("inventor", "inventor")):
            for party in _children(document, tag):
                name = _first_text(party, "name")
                if name:
                    parties.append({
                        "party_type": party_type, "party_name": name,
                        "country_code": _first_text(party, "country"),
                        "sequence_number": _clean(party.attrib.get("sequence")),
                    })
        dates = [_date(_clean(item.text)) for item in _children(document, "date")]
        dates = [item for item in dates if item]
        official = espacenet_url(publication)
        entities = {
            "publication_number": publication, "application_number": "", "jurisdiction": country,
            "document_kind": kind, "title": title, "abstract": abstract,
            "publication_date": dates[0] if dates else "", "filing_date": "", "grant_date": "",
            "family_id": "", "family_status": "Family data not returned by this search response",
            "legal_status_summary": "Not established by bibliographic search response",
            "legal_status_as_of": "", "parties": parties, "family_members": [], "legal_events": [],
            "official_source_url": official, "google_patents_url": google_patents_url(publication),
            "uk_register_url": uk_register_url(publication) if country == "GB" else "",
            "query_context": query,
        }
        records.append(record(
            "epo_patent_document", NAME, publication, title or publication, official,
            "\n".join(part for part in (title, abstract) if part), source_category="patent", entities=entities,
        ))
    return ConnectorResult(NAME, query, ok=True, count=len(records), records=records,
                           stats={"documents": len(records), "source_authority": "official EPO OPS"})


def parse_family_xml(payload: bytes) -> dict[str, Any]:
    """Return only explicit OPS family identifiers and members."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return {"family_id": "", "family_members": []}
    family_id = ""
    members: list[dict[str, str]] = []
    for family in _children(root, "family-member"):
        family_id = family_id or _clean(family.attrib.get("family-id") or family.attrib.get("familyid"))
    seen: set[str] = set()
    for document in _children(root, "exchange-document"):
        country = _clean(document.attrib.get("country") or _first_text(document, "country")).upper()
        number = _clean(document.attrib.get("doc-number") or _first_text(document, "doc-number"))
        kind = _clean(document.attrib.get("kind") or _first_text(document, "kind")).upper()
        publication = re.sub(r"[^A-Za-z0-9]", "", f"{country}{number}{kind}").upper()
        if publication and publication not in seen:
            seen.add(publication)
            members.append({"publication_number": publication, "jurisdiction": country,
                            "relationship_type": "OPS family member"})
    return {"family_id": family_id, "family_members": members}


def parse_legal_xml(payload: bytes) -> list[dict[str, str]]:
    """Retain OPS legal events without interpreting current enforceability."""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    events: list[dict[str, str]] = []
    for event in _children(root, "legal-event"):
        code = _clean(event.attrib.get("code") or _first_text(event, "code"))
        event_date = _date(_clean(event.attrib.get("date") or _first_text(event, "date")))
        text = _clean(event.attrib.get("desc") or event.attrib.get("description") or _first_text(event, "description"))
        if not text:
            text = _clean(" ".join(event.itertext()))
        if code or text:
            events.append({"event_code": code, "event_date": event_date, "event_text": text or code,
                           "authority": "EPO OPS / worldwide legal event data"})
    return events


def _token(key: str, secret: str) -> str:
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        response = client.post(
            TOKEN_URL, data={"grant_type": "client_credentials"}, auth=(key, secret),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        return str(response.json().get("access_token") or "")


def search(query: str, *, range_begin: int = 1, range_end: int = 100,
           key: str | None = None, secret: str | None = None) -> ConnectorResult:
    key = key or os.getenv("EPO_OPS_KEY", "")
    secret = secret or os.getenv("EPO_OPS_SECRET", "")
    if not key or not secret:
        return ConnectorResult(NAME, query, ok=False, error="EPO_OPS_KEY and EPO_OPS_SECRET are required")
    try:
        token = _token(key, secret)
        if not token:
            return ConnectorResult(NAME, query, ok=False, error="EPO OPS returned no OAuth token")
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            response = client.get(
                SEARCH_URL, params={"q": query},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/xml",
                         "User-Agent": USER_AGENT, "X-OPS-Range": f"{range_begin}-{range_end}"},
            )
            response.raise_for_status()
            result = parse_search_xml(response.content, query=query)
            enrich_limit = max(0, min(int(os.getenv("EPO_OPS_ENRICH_LIMIT", "10") or 10), len(result.records)))
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/xml", "User-Agent": USER_AGENT}
            enriched = 0
            for item in result.records[:enrich_limit]:
                entities = item.get("entities") or {}
                publication = str(entities.get("publication_number") or "")
                if not publication:
                    continue
                encoded = quote(publication, safe="")
                try:
                    family_response = client.get(f"{SERVICE_URL}/family/publication/epodoc/{encoded}", headers=headers)
                    family_response.raise_for_status()
                    family = parse_family_xml(family_response.content)
                    entities.update(family)
                    entities["family_status"] = "Official EPO OPS family evidence" if family["family_members"] else entities["family_status"]
                except httpx.HTTPError:
                    pass
                try:
                    legal_response = client.get(f"{SERVICE_URL}/legal/publication/epodoc/{encoded}", headers=headers)
                    legal_response.raise_for_status()
                    events = parse_legal_xml(legal_response.content)
                    entities["legal_events"] = events
                    if events:
                        latest = max(events, key=lambda event: event.get("event_date") or "")
                        entities["legal_status_summary"] = f"Latest reported legal event: {latest['event_text']} (not a current-status opinion)"
                        entities["legal_status_as_of"] = latest.get("event_date") or ""
                    enriched += 1
                except httpx.HTTPError:
                    pass
            result.stats["documents_enriched"] = enriched
        return result
    except Exception as exc:
        return ConnectorResult(NAME, query, ok=False, error=describe_error(exc))
