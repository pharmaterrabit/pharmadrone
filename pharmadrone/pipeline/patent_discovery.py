"""Read-only patent discovery helpers for the Patent & Innovation Discovery page."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus, urlparse

from ..connectors import tavily_search
from . import patent_lifecycle


SEARCH_MODES = (
    "Innovation / problem theme",
    "Product / ingredient",
    "Company / assignee",
    "Patent publication number",
    "Application number",
)

EXAMPLE_QUERIES = (
    "dissolution innovation",
    "poor solubility",
    "amorphous solid dispersion",
    "modified release",
    "bioavailability enhancement",
    "taste masking",
    "polymorph stability",
    "particle size reduction",
)

_TRUSTED_HOSTS = {
    "patents.google.com": "Google Patents discovery",
    "worldwide.espacenet.com": "EPO official route",
    "register.epo.org": "EPO official route",
    "epo.org": "EPO official route",
    "patentscope.wipo.int": "WIPO Patentscope",
    "uspto.gov": "USPTO",
    "gov.uk": "UK IPO official route",
    "accessdata.fda.gov": "FDA / Drugs@FDA lifecycle",
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _snippet(value: Any, limit: int = 320) -> str:
    text = _text(value)
    return text if len(text) <= limit else f"{text[:limit - 1].rstrip()}…"


def _matched_terms(query: str, *values: Any) -> list[str]:
    haystack = " ".join(_text(value).casefold() for value in values)
    terms = []
    for term in _text(query).casefold().split():
        if len(term) >= 3 and term in haystack and term not in terms:
            terms.append(term)
    return terms[:12]


def _host_label(url: str) -> str:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    for trusted_host, label in _TRUSTED_HOSTS.items():
        if host == trusted_host or host.endswith(f".{trusted_host}"):
            return label
    return ""


def external_discovery_routes(query: str, mode: str) -> list[dict[str, str]]:
    """Build search links only; callers must never represent these as fetched hits."""
    text = _text(query)
    encoded = quote_plus(text or "pharmaceutical patent")
    routes = [
        {
            "source_label": "Google Patents discovery",
            "title": "Open Google Patents discovery search",
            "external_link": patent_lifecycle.google_discovery_url(text),
            "evidence_status": "Discovery/cross-check only; not imported",
            "source_type": "Generated external discovery search",
        },
        {
            "source_label": "EPO official route",
            "title": "Open Espacenet / EPO discovery search",
            "external_link": f"https://worldwide.espacenet.com/patent/search?q={encoded}",
            "evidence_status": "Official patent-office discovery route; not imported",
            "source_type": "Generated external discovery search",
        },
        {
            "source_label": "WIPO Patentscope",
            "title": "Open WIPO Patentscope discovery search",
            "external_link": f"https://patentscope.wipo.int/search/en/result.jsf?query={encoded}",
            "evidence_status": "Official patent-office discovery route; not imported",
            "source_type": "Generated external discovery search",
        },
        {
            "source_label": "UK IPO official route",
            "title": "Open UK IPO patent search",
            "external_link": "https://www.gov.uk/search-for-patent",
            "evidence_status": "Official patent-office discovery route; not imported",
            "source_type": "Generated external discovery search",
        },
        {
            "source_label": "USPTO",
            "title": "Open USPTO patent-publication search",
            "external_link": f"https://ppubs.uspto.gov/pubwebapp/external.html?q={encoded}&type=KEYWORD",
            "evidence_status": "Official patent-office discovery route; not imported",
            "source_type": "Generated external discovery search",
        },
    ]
    if mode in {"Product / ingredient", "Application number"}:
        application_url = (
            patent_lifecycle.fda_application_url(text)
            if mode == "Application number"
            else "https://www.accessdata.fda.gov/scripts/cder/daf/"
        )
        routes.extend((
            {
                "source_label": "FDA / Drugs@FDA lifecycle",
                "title": "Open Drugs@FDA lifecycle search",
                "external_link": application_url,
                "evidence_status": "Official FDA lifecycle route; not imported",
                "source_type": "Generated external discovery search",
            },
            {
                "source_label": "Orange Book lifecycle",
                "title": "Open FDA Orange Book lifecycle data",
                "external_link": patent_lifecycle.FDA_SOURCE,
                "evidence_status": "Regulatory lifecycle route; not patent ownership evidence; not imported",
                "source_type": "Generated external discovery search",
            },
        ))
    return routes


def stored_records(conn, query: str, mode: str) -> list[dict[str, Any]]:
    """Return retained documents first and relevant FDA lifecycle context second."""
    query = _text(query)
    rows: list[dict[str, Any]] = []
    for document in patent_lifecycle.global_documents(conn, search=query, limit=100):
        jurisdiction = _text(document.get("jurisdiction"))
        publication = _text(document.get("publication_number"))
        rows.append({
            "record_kind": "patent-document",
            "title": _text(document.get("title")) or publication,
            "publication_number": publication,
            "application_number": _text(document.get("application_number")),
            "jurisdiction": jurisdiction,
            "assignee_applicant": _text(document.get("reported_parties")),
            "date": _text(document.get("publication_date")),
            "snippet": _snippet(document.get("abstract_text")),
            "matched_query_terms": _matched_terms(
                query,
                publication,
                document.get("title"),
                document.get("abstract_text"),
                document.get("reported_parties"),
            ),
            "source_label": "Internal retained record",
            "source_type": _text(document.get("source_name")) or "Retained patent document",
            "evidence_status": _text(document.get("evidence_status")),
            "external_link": _text(document.get("official_source_url")),
            "google_patents_url": _text(document.get("google_patents_url")),
            "epo_url": (
                _text(document.get("official_source_url"))
                if jurisdiction == "EP" else ""
            ),
            "uk_ipo_url": _text(document.get("uk_register_url")) if jurisdiction == "GB" else "",
            "wipo_url": (
                f"https://patentscope.wipo.int/search/en/result.jsf?query={quote_plus(publication)}"
                if publication else ""
            ),
            "uspto_url": (
                f"https://ppubs.uspto.gov/pubwebapp/external.html?q={quote_plus(publication)}&type=KEYWORD"
                if jurisdiction == "US" and publication else ""
            ),
            "fda_url": _text(document.get("official_source_url"))
            if document.get("source_name") == "FDA Orange Book" else "",
            "patent_document_id": _text(document.get("patent_document_id")),
        })
    if mode not in {"Product / ingredient", "Application number"}:
        return rows
    for product in patent_lifecycle.products(conn, search=query, limit=50):
        dataset_mode = _text(product.get("dataset_mode"))
        fallback = "fallback" in dataset_mode.casefold()
        rows.append({
            "record_kind": "lifecycle-product",
            "title": _text(product.get("trade_name")) or _text(product.get("ingredient")),
            "publication_number": "",
            "application_number": _text(product.get("application_number")),
            "jurisdiction": "US",
            "assignee_applicant": _text(product.get("application_holder")),
            "date": _text(product.get("approval_date")),
            "snippet": (
                "Product-only Drugs@FDA fallback; patent and exclusivity records are unavailable."
                if fallback else "Retained FDA product and lifecycle context."
            ),
            "matched_query_terms": _matched_terms(
                query,
                product.get("trade_name"),
                product.get("ingredient"),
                product.get("application_number"),
            ),
            "source_label": "FDA / Drugs@FDA lifecycle" if fallback else "Orange Book lifecycle",
            "source_type": dataset_mode or "FDA lifecycle",
            "evidence_status": _text(product.get("evidence_status")),
            "external_link": _text(product.get("official_source_url")),
            "google_patents_url": patent_lifecycle.google_discovery_url(
                _text(product.get("trade_name")) or _text(product.get("ingredient"))
            ),
            "epo_url": "",
            "uk_ipo_url": "",
            "wipo_url": "",
            "uspto_url": "",
            "fda_url": patent_lifecycle.fda_application_url(product.get("application_number")),
            "patent_document_id": "",
        })
    return rows


def live_external_discovery(query: str, *, max_results: int = 12) -> dict[str, Any]:
    """Explicit, post-filtered Tavily discovery; it never writes or imports records."""
    query = _text(query)
    if not query:
        return {"available": False, "error": "Enter a patent discovery query first.", "results": []}
    result = tavily_search.search(f"{query} patent", max_results=max(1, min(max_results, 20)))
    if not result.ok:
        return {"available": False, "error": _text(result.error), "results": []}
    rows = []
    for item in result.records:
        url = _text(item.get("url"))
        source_label = _host_label(url)
        if not source_label:
            continue
        rows.append({
            "title": _text(item.get("title")) or source_label,
            "publication_number": "",
            "jurisdiction": "",
            "assignee_applicant": "",
            "date": "",
            "snippet": _snippet(item.get("raw_text")),
            "matched_query_terms": _matched_terms(query, item.get("title"), item.get("raw_text")),
            "source_label": source_label,
            "source_type": "Live trusted patent discovery",
            "evidence_status": "Discovery result only; not imported or treated as a legal conclusion",
            "external_link": url,
        })
    return {"available": True, "error": "", "results": rows}
