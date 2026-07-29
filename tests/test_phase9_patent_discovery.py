from __future__ import annotations

from pathlib import Path

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
    assert failed["status"] == "error"
    assert "Tavily returned an error" in failed["message"]
    assert "HTTP 503" in failed["error"]

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
    assert queries[:3] == [
        "dissolution innovation pharmaceutical patent",
        "dissolution innovation formulation patent",
        "dissolution innovation drug delivery patent",
    ]
    assert queries[3:] == [
        "dissolution innovation site:patents.google.com",
        "dissolution innovation site:worldwide.espacenet.com",
        "dissolution innovation site:patentscope.wipo.int",
    ]


def test_page_explains_live_and_fallback_discovery_without_automatic_import_or_page_load_fetch():
    page = Path("pharmatune_ui/pages.py").read_text()
    discovery = Path("pharmadrone/pipeline/patent_discovery.py").read_text()
    assert "Open external discovery search" in page
    assert "### Live patent discovery results" in page
    assert "### Official search routes" in page
    assert "Run live patent discovery" in page
    assert '"Open source"' in page
    assert '"Likely publication"' in page
    assert '"Likely assignee / applicant"' in page
    assert "Live patent discovery requires a configured Tavily API key." in discovery
    assert "Generated official patent-search links are shown below." in discovery
    assert "data.patent_discovery_health()" in page
    assert "tavily_search" not in page
    assert "INSERT INTO" not in discovery
    assert "UPDATE " not in discovery
