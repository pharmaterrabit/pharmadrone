from __future__ import annotations

from pathlib import Path

from pharmadrone import db
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


def test_live_discovery_filters_to_trusted_domains_and_never_imports(monkeypatch, tmp_path):
    def fake_search(query: str, max_results: int):
        assert query == "particle size reduction patent"
        assert max_results == 12
        return ConnectorResult(
            "Web (Tavily)",
            query,
            ok=True,
            records=[
                {
                    "title": "Particle size patent",
                    "url": "https://patents.google.com/patent/US123",
                    "raw_text": "A patent result for particle size reduction.",
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
    assert result["available"] is True
    assert [row["source_label"] for row in result["results"]] == ["Google Patents discovery"]
    assert after == before


def test_page_explains_external_discovery_and_no_automatic_import_or_page_load_fetch():
    page = Path("pharmatune_ui/pages.py").read_text()
    discovery = Path("pharmadrone/pipeline/patent_discovery.py").read_text()
    assert "Open external discovery search" in page
    assert "Generated links preserve your query but do not fetch or import records." in page
    assert "Live patent discovery is not configured; use the generated official search links below." in page
    assert "tavily_search" not in page
    assert "INSERT INTO" not in discovery
    assert "UPDATE " not in discovery
