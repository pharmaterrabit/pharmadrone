"""Read-only patent discovery helpers for the Patent & Innovation Discovery page."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus, urldefrag, urlparse

from .. import settings
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
    "patents.google.com": ("Google Patents discovery", "discovery/cross-check only", 35),
    "worldwide.espacenet.com": ("EPO / Espacenet", "official patent-office discovery", 60),
    "register.epo.org": ("EPO Register", "official patent-office discovery", 65),
    "epo.org": ("EPO", "official patent-office discovery", 60),
    "patentscope.wipo.int": ("WIPO Patentscope", "official patent-office discovery", 60),
    "ppubs.uspto.gov": ("USPTO Patent Public Search", "official patent-office discovery", 65),
    "uspto.gov": ("USPTO", "official patent-office discovery", 60),
    "gov.uk": ("UK IPO", "official patent-office discovery", 60),
    "accessdata.fda.gov": ("FDA / Drugs@FDA lifecycle", "official regulatory lifecycle route", 25),
}

_TAVILY_INCLUDE_DOMAINS = tuple(_TRUSTED_HOSTS)
_PUBLICATION_RE = re.compile(
    r"\b(?:WO|EP|GB)\s*[-/]?\s*\d{5,}(?:\s*[A-Z]\d?)?\b"
    r"|\bUS\s*[-/]?\s*(?:\d{4}\s*[/]\s*)?\d{6,}(?:\s*[A-Z]\d?)?\b",
    re.IGNORECASE,
)
_DATE_RE = re.compile(r"\b(?:19|20)\d{2}[-/](?:0[1-9]|1[0-2])[-/](?:0[1-9]|[12]\d|3[01])\b")
_PARTY_RE = re.compile(
    r"\b(?:assignee|applicant)\s*[:\-]\s*([^.;|\n]{2,120})",
    re.IGNORECASE,
)
_INNOVATION_TERMS = ("pharmaceutical", "patent", "formulation", "drug delivery")


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


def _source_metadata(url: str) -> tuple[str, str, str, int]:
    parsed = urlparse(url)
    host = parsed.netloc.casefold().removeprefix("www.")
    for trusted_host, (label, evidence_status, authority_rank) in _TRUSTED_HOSTS.items():
        if host == trusted_host or host.endswith(f".{trusted_host}"):
            if trusted_host == "gov.uk" and not parsed.path.startswith("/search-for-patent"):
                return "", "", "", 0
            return label, host, evidence_status, authority_rank
    return "", "", "", 0


def _extract_publication_number(*values: Any) -> str:
    match = _PUBLICATION_RE.search(" ".join(_text(value) for value in values))
    return re.sub(r"[\s/-]+", "", match.group(0)).upper() if match else ""


def _extract_party(*values: Any) -> str:
    match = _PARTY_RE.search(" ".join(_text(value) for value in values))
    return _text(match.group(1)) if match else ""


def _extract_date(*values: Any) -> str:
    match = _DATE_RE.search(" ".join(_text(value) for value in values))
    return match.group(0).replace("/", "-") if match else ""


def live_discovery_health() -> dict[str, Any]:
    """Report live-search configuration without making a network request."""
    configured = bool(settings.env("TAVILY_API_KEY"))
    return {
        "provider": "Tavily",
        "configured": configured,
        "status": "configured" if configured else "unconfigured",
        "message": (
            "Tavily is configured. Live discovery runs only when requested."
            if configured
            else "Live patent discovery requires a configured Tavily API key. "
            "Generated official patent-search links are shown below."
        ),
    }


def patent_focused_queries(query: str) -> list[str]:
    """Build a small, deterministic Tavily query set for broad patent discovery."""
    text = _text(query)
    if not text:
        return []
    return [
        f"{text} pharmaceutical patent",
        f"{text} formulation patent",
        f"{text} drug delivery patent",
        f"{text} site:patents.google.com",
        f"{text} site:worldwide.espacenet.com",
        f"{text} site:patentscope.wipo.int",
    ]


def _canonical_url(url: str) -> str:
    clean, _fragment = urldefrag(_text(url))
    return clean.rstrip("/")


def _rank_result(query: str, title: str, snippet: str, authority_rank: int) -> int:
    query_terms = [term for term in _text(query).casefold().split() if len(term) >= 3]
    title_text = title.casefold()
    snippet_text = snippet.casefold()
    score = authority_rank
    score += sum(8 for term in query_terms if term in title_text)
    score += sum(3 for term in query_terms if term in snippet_text)
    score += sum(2 for term in _INNOVATION_TERMS if term in f"{title_text} {snippet_text}")
    return score


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
        return {
            "provider": "Tavily",
            "configured": live_discovery_health()["configured"],
            "status": "empty_query",
            "message": "Enter a patent discovery query first.",
            "error": "",
            "results": [],
            "queries": [],
        }
    health = live_discovery_health()
    if not health["configured"]:
        return {
            **health,
            "error": "",
            "results": [],
            "queries": [],
        }

    limit = max(1, min(int(max_results), 20))
    queries = patent_focused_queries(query)
    per_query = max(2, min(5, (limit + len(queries) - 1) // len(queries)))
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    successful_queries = 0
    for focused_query in queries:
        result = tavily_search.search(
            focused_query,
            max_results=per_query,
            include_domains=list(_TAVILY_INCLUDE_DOMAINS),
        )
        if not result.ok:
            errors.append(f"{focused_query}: {_text(result.error) or 'provider error'}")
            continue
        successful_queries += 1
        records.extend(result.records)

    if not successful_queries:
        return {
            "provider": "Tavily",
            "configured": True,
            "status": "error",
            "message": "Live patent discovery could not run because Tavily returned an error.",
            "error": "; ".join(errors),
            "results": [],
            "queries": queries,
        }

    deduplicated: dict[str, dict[str, Any]] = {}
    for item in records:
        url = _canonical_url(item.get("url"))
        source_label, source_domain, evidence_basis, authority_rank = _source_metadata(url)
        if not source_label or not url or url in deduplicated:
            continue
        title = _text(item.get("title")) or source_label
        snippet = _snippet(item.get("raw_text"))
        deduplicated[url] = {
            "title": title,
            "source_label": source_label,
            "source_domain": source_domain,
            "snippet": snippet,
            "matched_query_terms": _matched_terms(query, title, snippet),
            "publication_number": _extract_publication_number(title, snippet, url),
            "assignee_applicant": _extract_party(title, snippet),
            "date": _extract_date(title, snippet),
            "source_type": "Live trusted patent discovery",
            "evidence_status": (
                f"{evidence_basis}; discovery result only; not imported or treated as a legal conclusion"
            ),
            "external_link": url,
            "ranking_score": _rank_result(query, title, snippet, authority_rank),
        }
    rows = sorted(
        deduplicated.values(),
        key=lambda row: (-int(row["ranking_score"]), row["title"].casefold(), row["external_link"]),
    )[:limit]
    return {
        "provider": "Tavily",
        "configured": True,
        "status": "available" if rows else "no_results",
        "message": (
            "Live trusted patent discovery results are available."
            if rows
            else "Tavily was available but returned no trusted patent-discovery results."
        ),
        "error": "; ".join(errors),
        "results": rows,
        "queries": queries,
    }
