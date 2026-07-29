"""Read-only patent discovery helpers for the Patent & Innovation Discovery page."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus, urldefrag, urlparse

from .. import settings
from ..pipeline.query_safety import normalise_query
from ..connectors import tavily_search
from ..connectors import epo_ops, patentsview
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


_PATENT_QUERY_SEEDS = {
    "dissolution innovation": (
        "dissolution patent",
        "dissolution formulation",
        "drug dissolution patent",
    ),
    "poor solubility": (
        "poor solubility patent",
        "solubility formulation",
        "bioavailability patent",
    ),
    "amorphous solid dispersion": (
        "amorphous solid dispersion patent",
        "solid dispersion formulation",
        "bioavailability solid dispersion",
    ),
    "modified release": (
        "modified release",
        "modified release formulation",
        "drug release",
    ),
    "bioavailability enhancement": (
        "bioavailability enhancement",
        "bioavailability formulation",
        "drug absorption patent",
    ),
}


def _safe_patent_terms(query: str) -> str:
    text = normalise_query(query)
    text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.I)
    text = re.sub(r"\b(?:site|inurl|intitle):\S+", " ", text, flags=re.I)
    text = re.sub(r"\b[\w.-]+\.(?:com|org|int|gov|uk)(?:/\S*)?\b", " ", text, flags=re.I)
    text = text.replace('"', " ").replace("'", " ")
    text = re.sub(r"[^\w\s-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:55]


def patent_focused_queries(query: str) -> list[str]:
    """Build at most three short Tavily-safe patent discovery queries."""
    text = _safe_patent_terms(query)
    if not text:
        return []
    seeded = _PATENT_QUERY_SEEDS.get(text.casefold())
    if seeded:
        return list(seeded)
    variants = (f"{text} patent", f"{text} formulation", f"{text} drug delivery")
    return list(dict.fromkeys(item[:80].strip() for item in variants))


def patent_source_health() -> dict[str, dict[str, Any]]:
    epo_configured = bool(
        settings.env("EPO_OPS_CLIENT_ID") and settings.env("EPO_OPS_CLIENT_SECRET")
    )
    tavily_configured = bool(settings.env("TAVILY_API_KEY"))
    return {
        "internal": {
            "label": "Internal retained records", "status": "ready",
            "message": "Stored records are searched first; no external call is needed.",
        },
        "epo_ops": {
            "label": "EPO OPS",
            "status": "configured" if epo_configured else "not_configured",
            "message": (
                "EPO OPS is configured and available on request."
                if epo_configured else
                "EPO OPS is not configured. Add EPO_OPS_CLIENT_ID and EPO_OPS_CLIENT_SECRET to enable direct EPO patent search."
            ),
        },
        "patentsview": {
            "label": "USPTO / PatentsView", "status": "available",
            "message": "Public USPTO / PatentsView search route is available on request.",
        },
        "tavily": {
            "label": "Tavily fallback",
            "status": "configured" if tavily_configured else "not_configured",
            "message": (
                "Optional fallback; direct patent providers are tried first."
                if tavily_configured else "Optional Tavily fallback is not configured."
            ),
        },
        "official_routes": {
            "label": "Official search routes", "status": "available",
            "message": "Generated EPO, WIPO, UK IPO, USPTO and Google discovery links remain available.",
        },
    }


def _direct_record_row(item: dict[str, Any], *, provider: str, query: str) -> dict[str, Any]:
    entities = item.get("entities") if isinstance(item.get("entities"), dict) else {}
    publication = _text(entities.get("publication_number"))
    official = _text(item.get("url") or entities.get("official_source_url"))
    label = "EPO OPS" if provider == "epo_ops" else "USPTO / PatentsView"
    domain = urlparse(official).netloc.casefold().removeprefix("www.")
    title = _text(item.get("title") or entities.get("title")) or publication
    snippet = _snippet(item.get("raw_text") or entities.get("abstract"))
    applicant = _text(entities.get("applicant") or entities.get("assignee"))
    if not applicant and isinstance(entities.get("parties"), list):
        applicant = "; ".join(_text(row.get("party_name")) for row in entities["parties"] if isinstance(row, dict) and _text(row.get("party_name")))
    return {
        "title": title,
        "source_label": label,
        "source_domain": domain,
        "snippet": snippet,
        "matched_query_terms": _matched_terms(query, title, snippet),
        "publication_number": publication,
        "application_number": _text(entities.get("application_number")),
        "assignee_applicant": applicant,
        "date": _text(entities.get("publication_date") or entities.get("patent_date")),
        "source_type": "Direct official patent discovery",
        "evidence_status": (
            "Official EPO OPS bibliographic discovery; not imported or a legal conclusion"
            if provider == "epo_ops" else
            "Official USPTO / PatentsView discovery; not imported or a legal conclusion"
        ),
        "external_link": official,
        "ranking_score": _rank_result(query, title, snippet, 65 if provider == "epo_ops" else 60),
    }


def _merge_discovery_rows(rows: list[dict[str, Any]], *, query: str, limit: int = 20) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        publication = re.sub(r"[^A-Z0-9]", "", _text(row.get("publication_number")).upper())
        url = _canonical_url(row.get("external_link"))
        key = f"publication:{publication}" if publication else f"url:{url}"
        if key == "url:" or key in unique:
            continue
        row = dict(row)
        row["external_link"] = url
        row["ranking_score"] = _rank_result(
            query, _text(row.get("title")), _text(row.get("snippet")), int(row.get("ranking_score") or 0)
        )
        unique[key] = row
    return sorted(
        unique.values(),
        key=lambda row: (-int(row.get("ranking_score") or 0), _text(row.get("title")).casefold(), row["external_link"]),
    )[:max(1, min(int(limit), 20))]


def patent_source_discovery(query: str, *, max_results: int = 20) -> dict[str, Any]:
    """Search direct patent sources first, then optionally use Tavily as fallback."""
    text = _text(query)
    health = patent_source_health()
    queries = patent_focused_queries(text)
    rows: list[dict[str, Any]] = []
    provider_results = {key: dict(value) for key, value in health.items()}
    direct_errors = False

    if health["epo_ops"]["status"] == "configured":
        epo_rows = []
        for focused_query in queries:
            result = epo_ops.search(
                focused_query,
                range_begin=1,
                range_end=20,
                key=settings.env("EPO_OPS_CLIENT_ID"),
                secret=settings.env("EPO_OPS_CLIENT_SECRET"),
            )
            if not result.ok:
                direct_errors = True
                continue
            epo_rows.extend(_direct_record_row(item, provider="epo_ops", query=text) for item in result.records)
        rows.extend(epo_rows)
        provider_results["epo_ops"].update(status="available" if epo_rows else "no_results", count=len(epo_rows))
    else:
        provider_results["epo_ops"].update(count=0)

    uspto_rows = []
    for focused_query in queries:
        result = patentsview.search(focused_query, limit=20)
        if not result.ok:
            direct_errors = True
            continue
        uspto_rows.extend(_direct_record_row(item, provider="patentsview", query=text) for item in result.records)
    rows.extend(uspto_rows)
    provider_results["patentsview"].update(
        status="available" if uspto_rows else ("provider_error" if direct_errors else "no_results"),
        count=len(uspto_rows),
    )

    direct_rows = _merge_discovery_rows(rows, query=text, limit=max_results)
    if health["tavily"]["status"] == "configured":
        provider_results["tavily"].update(status="not_run", message="Direct patent providers were tried first.", count=0)
    if not direct_rows and health["tavily"]["status"] == "configured":
        tavily_result = live_external_discovery(text, max_results=max_results)
        tavily_rows = tavily_result.get("results") or []
        direct_rows = _merge_discovery_rows(tavily_rows, query=text, limit=max_results)
        provider_results["tavily"].update(
            status=tavily_result.get("status") or "provider_error",
            count=len(tavily_rows),
            message="Optional Tavily fallback was attempted after direct providers returned no results.",
        )

    if direct_rows:
        status = "partial_results" if direct_errors else "available"
        message = "Direct patent-source results are available."
    else:
        status = "no_results"
        message = "No direct patent-source results were returned. Use the official search routes below."
    return {
        "provider": "Direct patent sources",
        "configured": True,
        "status": status,
        "message": message,
        "error": "",
        "results": direct_rows,
        "queries": queries,
        "providers": provider_results,
    }


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
            "status": "no_results",
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
    rejected_queries: list[str] = []
    failed_queries: list[str] = []
    successful_queries = 0
    for focused_query in queries:
        result = tavily_search.search(
            focused_query,
            max_results=per_query,
            include_domains=list(_TAVILY_INCLUDE_DOMAINS),
        )
        if not result.ok:
            errors.append(f"{focused_query}: {_text(result.error) or 'provider error'}")
            if result.stats.get("rejected"):
                rejected_queries.append(focused_query)
            else:
                failed_queries.append(focused_query)
            continue
        successful_queries += 1
        records.extend(result.records)

    if not successful_queries and rejected_queries and not failed_queries:
        return {
            "provider": "Tavily",
            "configured": True,
            "status": "provider_rejected_query",
            "message": "Live discovery could not run this query. PharmaTune has simplified the search and kept official patent-search links below.",
            "error": "; ".join(errors),
            "results": [],
            "queries": queries,
        }
    if not successful_queries:
        return {
            "provider": "Tavily",
            "configured": True,
            "status": "provider_error",
            "message": "Live discovery is currently unavailable for this query. Use the official search routes below.",
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
    status = "partial_results" if errors else ("available" if rows else "no_results")
    return {
        "provider": "Tavily",
        "configured": True,
        "status": status,
        "message": (
            "Live trusted patent discovery results are available."
            if rows
            else "Tavily was available but returned no trusted patent-discovery results."
        ),
        "error": "; ".join(errors),
        "results": rows,
        "queries": queries,
        "rejected_queries": rejected_queries,
        "failed_queries": failed_queries,
    }
