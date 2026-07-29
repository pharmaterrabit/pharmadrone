from __future__ import annotations

from pathlib import Path

import httpx

from pharmadrone import db
from pharmadrone.connectors import tavily_search
from pharmadrone.connectors.base import ConnectorResult
from pharmadrone.pipeline import patent_discovery


NOW = "2026-07-27T00:00:00+00:00"


def _routes(query: str, mode: str) -> dict[str, dict[str, str]]:
    return {
        row["source_label"]: row
        for row in patent_discovery.external_discovery_routes(query, mode)
    }


def _insert_document(conn) -> None:
    with conn.transaction():
        conn.execute(
            """INSERT INTO patent_documents
            (patent_document_id,publication_number,application_number,jurisdiction,
            document_kind,title,abstract_text,family_status,source_name,source_authority,
            official_source_url,google_patents_url,evidence_status,last_verified_at,next_review_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "discovery-ep-1",
                "EP1234567A1",
                "EP2024001",
                "EP",
                "A1",
                "Amorphous solid dispersion for dissolution innovation",
                "A formulation approach for poor solubility and bioavailability enhancement.",
                "Family not established",
                "EPO / EP",
                "official",
                "https://register.epo.org/example",
                "https://patents.google.com/patent/EP1234567A1/en",
                "official bibliographic evidence",
                NOW,
                NOW,
            ),
        )


def test_broad_innovation_query_searches_retained_abstracts_and_page_has_modes(tmp_path):
    conn = db.connect(tmp_path / "patent-discovery.db")
    _insert_document(conn)
    rows = patent_discovery.stored_records(
        conn, "dissolution innovation", "Innovation / problem theme"
    )
    assert len(rows) == 1
    assert rows[0]["title"].startswith("Amorphous solid dispersion")
    assert set(rows[0]["matched_query_terms"]) == {"dissolution", "innovation"}
    page = Path("pharmatune_ui/pages.py").read_text()
    assert "Patent & Innovation Discovery" in page
    assert "patent_discovery.SEARCH_MODES" in page
    assert patent_discovery.SEARCH_MODES[0] == "Innovation / problem theme"
    assert "amorphous solid dispersion" in patent_discovery.EXAMPLE_QUERIES


def test_generated_routes_preserve_query_for_google_epo_wipo_uk_and_uspto():
    routes = _routes("poor solubility formulation", "Innovation / problem theme")
    assert routes["Google Patents discovery"]["external_link"] == (
        "https://patents.google.com/?q=poor+solubility+formulation"
    )
    assert "poor+solubility+formulation" in routes["EPO official route"]["external_link"]
    assert "patentscope.wipo.int" in routes["WIPO Patentscope"]["external_link"]
    assert routes["UK IPO official route"]["external_link"] == "https://www.gov.uk/search-for-patent"
    assert "ppubs.uspto.gov" in routes["USPTO"]["external_link"]
    assert routes["Google Patents discovery"]["evidence_status"].startswith("Discovery/cross-check only")


def test_fda_and_orange_book_routes_are_only_generated_for_relevant_lifecycle_modes():
    innovation = _routes("dissolution innovation", "Innovation / problem theme")
    assert "FDA / Drugs@FDA lifecycle" not in innovation
    assert "Orange Book lifecycle" not in innovation
    product = _routes("Example Drug", "Product / ingredient")
    assert "accessdata.fda.gov" in product["FDA / Drugs@FDA lifecycle"]["external_link"]
    assert "Orange Book lifecycle" in product
    application = _routes("012345", "Application number")
    assert "ApplNo=012345" in application["FDA / Drugs@FDA lifecycle"]["external_link"]


def test_orange_book_fallback_does_not_block_discovery_or_fabricate_records(tmp_path):
    conn = db.connect(tmp_path / "patent-discovery-fallback.db")
    with conn.transaction():
        conn.execute(
            """INSERT INTO lifecycle_products
            (lifecycle_id,application_number,product_number,trade_name,ingredient,
            official_source_url,evidence_status,lifecycle_status,dataset_mode,last_verified_at,next_review_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "fallback-product",
                "012345",
                "001",
                "EXAMPLE DRUG",
                "EXAMPLINE",
                "https://www.accessdata.fda.gov/scripts/cder/daf/",
                "Official Drugs@FDA product fallback",
                "Lifecycle evidence unavailable",
                "Drugs@FDA product fallback",
                NOW,
                NOW,
            ),
        )
    rows = patent_discovery.stored_records(conn, "EXAMPLE DRUG", "Product / ingredient")
    assert len(rows) == 1
    assert rows[0]["source_label"] == "FDA / Drugs@FDA lifecycle"
    assert "patent and exclusivity records are unavailable" in rows[0]["snippet"]
    assert conn.execute("SELECT COUNT(*) AS n FROM lifecycle_patents").fetchone()["n"] == 0
    page = Path("pharmatune_ui/pages.py").read_text()
    assert "patent discovery remains available" in page
    assert "No retained patent records match this query yet." in page


def test_live_discovery_filters_ranks_deduplicates_and_never_imports(monkeypatch, tmp_path):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    calls = []

    def fake_search(query: str, max_results: int, *, include_domains: list[str]):
        calls.append((query, max_results, include_domains))
        return ConnectorResult(
            "Web (Tavily)",
            query,
            ok=True,
            records=[
                {
                    "title": "Particle size patent",
                    "url": "https://patents.google.com/patent/US1234567B2",
                    "raw_text": "Applicant: Example Pharma. A patent result for particle size reduction dated 2025-01-20.",
                },
                {
                    "title": "Particle size patent duplicate",
                    "url": "https://patents.google.com/patent/US1234567B2#claims",
                    "raw_text": "Duplicate URL for the same result.",
                },
                {
                    "title": "Official particle size publication",
                    "url": "https://register.epo.org/espacenet/application?number=EP1234567",
                    "raw_text": "EP1234567A1 formulation patent for particle size reduction.",
                },
                {
                    "title": "Untrusted SEO page",
                    "url": "https://example.invalid/patent",
                    "raw_text": "Not an approved source route.",
                },
            ],
        )

    monkeypatch.setattr(patent_discovery.tavily_search, "search", fake_search)
    conn = db.connect(tmp_path / "patent-discovery-live.db")
    before = conn.execute("SELECT COUNT(*) AS n FROM patent_documents").fetchone()["n"]
    result = patent_discovery.live_external_discovery("particle size reduction")
    after = conn.execute("SELECT COUNT(*) AS n FROM patent_documents").fetchone()["n"]
    assert result["status"] == "available"
    assert len(result["results"]) == 2
    assert result["results"][0]["source_label"] == "EPO Register"
    assert result["results"][0]["publication_number"] == "EP1234567A1"
    google = next(row for row in result["results"] if row["source_label"] == "Google Patents discovery")
    assert google["assignee_applicant"] == "Example Pharma"
    assert google["date"] == "2025-01-20"
    assert "discovery/cross-check only" in google["evidence_status"]
    assert all(call[2] for call in calls)
    assert [call[0] for call in calls] == patent_discovery.patent_focused_queries(
        "particle size reduction"
    )
    assert after == before


def test_live_discovery_configuration_and_provider_errors_are_explicit(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    health = patent_discovery.live_discovery_health()
    assert health["status"] == "unconfigured"
    assert health["message"] == (
        "Live patent discovery requires a configured Tavily API key. "
        "Generated official patent-search links are shown below."
    )

    def must_not_search(*args, **kwargs):
        raise AssertionError("Tavily must not be called without configuration")

    monkeypatch.setattr(patent_discovery.tavily_search, "search", must_not_search)
    missing = patent_discovery.live_external_discovery("poor solubility")
    assert missing["status"] == "unconfigured"

    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(
        patent_discovery.tavily_search,
        "search",
        lambda query, max_results, *, include_domains: ConnectorResult(
            "Web (Tavily)", query, ok=False, error="HTTP 503: provider unavailable"
        ),
    )
    failed = patent_discovery.live_external_discovery("poor solubility")
    assert failed["status"] == "provider_error"
    assert "official search routes below" in failed["message"]
    assert "HTTP 503" in failed["error"]
    assert _routes("poor solubility", "Innovation / problem theme")

    monkeypatch.setattr(
        patent_discovery.tavily_search,
        "search",
        lambda query, max_results, *, include_domains: ConnectorResult(
            "Web (Tavily)",
            query,
            ok=True,
            records=[{
                "title": "Untrusted result",
                "url": "https://example.invalid/patent",
                "raw_text": "Filtered after retrieval.",
            }],
        ),
    )
    no_results = patent_discovery.live_external_discovery("poor solubility")
    assert no_results["status"] == "no_results"
    assert no_results["results"] == []


def test_tavily_connector_sends_trusted_domain_restrictions(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    captured = {}

    def fake_post(payload: dict):
        captured.update(payload)
        return {"results": []}

    monkeypatch.setattr(tavily_search, "_post_tavily", fake_post)
    result = tavily_search.search(
        "formulation patent",
        max_results=3,
        include_domains=["patents.google.com", "epo.org", "epo.org"],
    )
    assert result.ok is True
    assert captured["include_domains"] == ["patents.google.com", "epo.org"]


def test_tavily_rejection_retries_without_domains_then_with_short_query(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    payloads = []

    def rejected_response():
        response = httpx.Response(
            432,
            request=httpx.Request("POST", tavily_search.URL),
        )
        return httpx.HTTPStatusError("rejected", request=response.request, response=response)

    def fake_post(payload: dict):
        payloads.append(payload)
        if len(payloads) < 3:
            raise rejected_response()
        return {"results": [{"url": "https://patents.google.com/patent/US1234567", "title": "Patent", "content": ""}]}

    monkeypatch.setattr(tavily_search, "_post_tavily", fake_post)
    result = tavily_search.search(
        'dissolution site:patents.google.com "innovation"',
        max_results=2,
        include_domains=["patents.google.com"],
    )
    assert result.ok is True
    assert result.stats["attempts"] == 3
    assert "include_domains" in payloads[0]
    assert "include_domains" not in payloads[1]
    assert "include_domains" not in payloads[2]
    assert "patents.google.com" not in payloads[2]["query"]
    assert "site:" not in payloads[2]["query"]


def test_live_discovery_labels_supported_trusted_routes(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    records = [
        {"title": "Google", "url": "https://patents.google.com/patent/EP1", "raw_text": ""},
        {"title": "Espacenet", "url": "https://worldwide.espacenet.com/patent/search/family/1", "raw_text": ""},
        {"title": "EPO", "url": "https://www.epo.org/en/legal", "raw_text": ""},
        {"title": "WIPO", "url": "https://patentscope.wipo.int/search/en/detail.jsf", "raw_text": ""},
        {"title": "USPTO", "url": "https://ppubs.uspto.gov/pubwebapp/static/pages/landing.html", "raw_text": ""},
        {"title": "UK", "url": "https://www.gov.uk/search-for-patent", "raw_text": ""},
        {"title": "FDA", "url": "https://www.accessdata.fda.gov/scripts/cder/daf/", "raw_text": ""},
        {"title": "Wrong GOV.UK page", "url": "https://www.gov.uk/random-page", "raw_text": ""},
    ]
    call_number = 0

    def fake_search(query: str, max_results: int, *, include_domains: list[str]):
        nonlocal call_number
        call_number += 1
        return ConnectorResult(
            "Web (Tavily)",
            query,
            ok=True,
            records=records if call_number == 1 else [],
        )

    monkeypatch.setattr(patent_discovery.tavily_search, "search", fake_search)
    result = patent_discovery.live_external_discovery("dissolution innovation", max_results=20)
    labels = {row["source_label"] for row in result["results"]}
    assert labels == {
        "Google Patents discovery",
        "EPO / Espacenet",
        "EPO",
        "WIPO Patentscope",
        "USPTO Patent Public Search",
        "UK IPO",
        "FDA / Drugs@FDA lifecycle",
    }


def test_broad_innovation_query_builds_patent_focused_tavily_queries():
    queries = patent_discovery.patent_focused_queries("dissolution innovation")
    assert queries == [
        "dissolution patent",
        "dissolution formulation",
        "drug dissolution patent",
    ]
    assert len(queries) == 3
    assert all(len(item) < 80 for item in queries)
    assert all("site:" not in item for item in queries)
    assert all("patents.google.com" not in item for item in queries)
    assert all("worldwide.espacenet.com" not in item for item in queries)
    assert all("patentscope.wipo.int" not in item for item in queries)


def test_representative_innovation_queries_use_short_safe_variants():
    assert patent_discovery.patent_focused_queries("poor solubility") == [
        "poor solubility patent",
        "solubility formulation",
        "bioavailability patent",
    ]
    assert patent_discovery.patent_focused_queries("amorphous solid dispersion") == [
        "amorphous solid dispersion patent",
        "solid dispersion formulation",
        "bioavailability solid dispersion",
    ]


def test_page_explains_live_and_fallback_discovery_without_automatic_import_or_page_load_fetch():
    page = Path("pharmatune_ui/pages.py").read_text()
    discovery = Path("pharmadrone/pipeline/patent_discovery.py").read_text()
    assert "Open external discovery search" in page
    assert "### Patent source results" in page
    assert "### Official search routes" in page
    assert "Run live patent discovery" in page
    assert '"Open source"' in page
    assert '"Likely publication"' in page
    assert '"Likely assignee / applicant"' in page
    assert "Live patent discovery requires a configured Tavily API key." in discovery
    assert "Generated official patent-search links are shown below." in discovery
    assert "data.patent_source_health()" in page
    assert "Live discovery could not run this query." in page
    assert "Direct patent-source discovery is currently unavailable for this query." in page
    assert 'live_result.get("error")' not in page
    assert "tavily_search" not in page
    assert "INSERT INTO" not in discovery
    assert "UPDATE " not in discovery


def _direct_record(number: str, title: str, *, source: str = "USPTO / PatentsView") -> dict:
    return {
        "title": title,
        "url": f"https://official.example/{number}",
        "raw_text": f"{title} abstract",
        "source_name": source,
        "entities": {
            "publication_number": number,
            "title": title,
            "abstract": f"{title} abstract",
            "publication_date": "2024-01-01",
            "applicant": "Example Pharma",
            "official_source_url": f"https://official.example/{number}",
        },
    }


def test_epo_ops_not_configured_status_is_explicit(monkeypatch):
    monkeypatch.delenv("EPO_OPS_CLIENT_ID", raising=False)
    monkeypatch.delenv("EPO_OPS_CLIENT_SECRET", raising=False)
    health = patent_discovery.patent_source_health()
    assert health["epo_ops"]["status"] == "not_configured"
    assert "EPO_OPS_CLIENT_ID" in health["epo_ops"]["message"]
    assert "EPO_OPS_CLIENT_SECRET" in health["epo_ops"]["message"]


def test_direct_epo_and_uspto_results_are_merged_without_tavily(monkeypatch):
    monkeypatch.setenv("EPO_OPS_CLIENT_ID", "client")
    monkeypatch.setenv("EPO_OPS_CLIENT_SECRET", "secret")
    epo = ConnectorResult("EPO Open Patent Services", "q", ok=True, records=[
        _direct_record("EP1234567A1", "Dissolution formulation patent", source="EPO Open Patent Services"),
    ])
    uspto = ConnectorResult("USPTO / PatentsView", "q", ok=True, records=[
        _direct_record("US1234567", "Dissolution formulation patent"),
    ])
    monkeypatch.setattr(patent_discovery.epo_ops, "search", lambda *args, **kwargs: epo)
    monkeypatch.setattr(patent_discovery.patentsview, "search", lambda *args, **kwargs: uspto)
    monkeypatch.setattr(
        patent_discovery,
        "live_external_discovery",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Tavily must be fallback only")),
    )
    result = patent_discovery.patent_source_discovery("dissolution innovation")
    assert result["status"] == "available"
    assert {row["source_label"] for row in result["results"]} == {"EPO OPS", "USPTO / PatentsView"}
    assert result["providers"]["epo_ops"]["status"] == "available"
    assert result["providers"]["patentsview"]["status"] == "available"


def test_uspto_results_are_ranked_deduplicated_and_tavily_failure_does_not_block_them(monkeypatch, tmp_path):
    monkeypatch.delenv("EPO_OPS_CLIENT_ID", raising=False)
    monkeypatch.delenv("EPO_OPS_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(patent_discovery.patentsview, "search", lambda *args, **kwargs: ConnectorResult(
        "USPTO / PatentsView", "q", ok=True, records=[
            _direct_record("US1234567", "Poor solubility formulation patent"),
            _direct_record("US1234567", "Duplicate poor solubility patent"),
        ]
    ))
    monkeypatch.setattr(
        patent_discovery,
        "live_external_discovery",
        lambda *args, **kwargs: {"status": "provider_error", "results": [], "queries": []},
    )
    conn = db.connect(tmp_path / "direct-discovery.db")
    before = conn.execute("SELECT COUNT(*) AS n FROM patent_documents").fetchone()["n"]
    result = patent_discovery.patent_source_discovery("poor solubility")
    after = conn.execute("SELECT COUNT(*) AS n FROM patent_documents").fetchone()["n"]
    assert result["status"] == "available"
    assert len(result["results"]) == 1
    assert result["results"][0]["title"] == "Poor solubility formulation patent"
    assert after == before


def test_direct_provider_failure_returns_safe_status_and_official_routes_remain(monkeypatch):
    monkeypatch.setattr(patent_discovery.patentsview, "search", lambda *args, **kwargs: ConnectorResult(
        "USPTO / PatentsView", "q", ok=False, error="provider unavailable"
    ))
    monkeypatch.setattr(patent_discovery.epo_ops, "search", lambda *args, **kwargs: ConnectorResult(
        "EPO Open Patent Services", "q", ok=False, error="provider unavailable"
    ))
    monkeypatch.setattr(patent_discovery, "live_external_discovery", lambda *args, **kwargs: {
        "status": "provider_error", "results": [], "queries": []
    })
    result = patent_discovery.patent_source_discovery("modified release")
    assert result["status"] == "no_results"
    routes = _routes("modified release", "Innovation / problem theme")
    assert "Google Patents discovery" in routes
    assert "EPO official route" in routes
