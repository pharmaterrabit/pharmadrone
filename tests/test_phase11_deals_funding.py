from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pharmadrone import db
from pharmadrone.connectors import commercial_signals, openalex
from pharmadrone.connectors.base import ConnectorResult
from pharmadrone.pipeline import commercial_intelligence
from pharmadrone.scheduler import config, repository


def _ingest(conn, records):
    with conn.transaction():
        repository.ingest_source_records(conn, run_id="phase11-source", source_name="phase11-test", records=records)


def _records():
    return [
        {"source_type": "licensing", "source_name": "Company press release", "record_id": "LIC-1",
         "title": "Example Pharma licenses Drug X", "url": "https://examplepharma.com/licensing-1", "raw_text": "",
         "entities": {"deal_type": "Licensing", "party_a": "Example Pharma", "party_b": "Partner Biotech",
                      "subject": "Drug X", "announcement_date": "2026-07-01", "status": "Announced",
                      "value_amount": 50_000_000, "currency": "USD", "primary_source_verified": True}},
        {"source_type": "commercial_signal", "source_name": "Commercial Signal Discovery", "record_id": "WEB-1",
         "title": "Buyer may acquire Target", "url": "https://news.example/acquisition", "raw_text": "",
         "entities": {"deal_type": "M&A", "party_a": "Buyer Pharma", "subject": "Possible Target acquisition",
                      "primary_source_verified": False}},
        {"source_type": "commercial_partnership", "source_name": "Corporate disclosure", "record_id": "PARTNER-1",
         "title": "Company A and Company B enter partnership", "url": "https://companya.com/partner", "raw_text": "",
         "entities": {"party_a": "Company A", "party_b": "Company B", "subject": "Manufacturing partnership",
                      "primary_source_verified": True}},
        {"source_type": "corporate_financing", "source_name": "Corporate disclosure", "record_id": "FUND-1",
         "title": "Company C announces Series A financing", "url": "https://companyc.com/series-a", "raw_text": "",
         "entities": {"party_a": "Company C", "subject": "Series A", "primary_source_verified": True}},
        {"source_type": "paper", "source_name": "OpenAlex", "record_id": "10.1000/grant-paper",
         "title": "Grant-funded formulation study", "url": "https://doi.org/10.1000/grant-paper", "raw_text": "",
         "entities": {"doi": "10.1000/grant-paper", "publication_title": "Grant-funded formulation study",
                      "grants": [{"funder": "National Research Agency", "award_id": "GRANT-123"}]}},
    ]


def test_phase11_schema_and_weekly_jobs_are_installed(tmp_path):
    conn = db.connect(tmp_path / "phase11.db")
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"commercial_events", "funding_awards", "commercial_event_observations", "commercial_monitor_runs"}.issubset(tables)
    assert config.source_spec("deal_discovery").cadence == "weekly"
    assert config.source_spec("commercial_intelligence").cadence == "weekly"
    assert config.source_spec("deal_discovery").enabled_env == "TAVILY_API_KEY"


def test_commercial_classifier_keeps_event_families_separate():
    assert commercial_signals.classify("Company acquires biotech target") == "M&A"
    assert commercial_signals.classify("Exclusive licensing agreement announced") == "Licensing"
    assert commercial_signals.classify("Series A financing") == "Corporate financing"
    assert commercial_signals.classify("Strategic collaboration agreement") == "Commercial partnership"
    assert commercial_signals.classify("Ordinary product update") == ""


def test_deal_discovery_only_marks_matching_company_domain_as_primary():
    web = ConnectorResult("Web (Tavily)", "q", True, count=2, records=[
        {"source_type": "web", "title": "Example Pharma licensing agreement", "url": "https://examplepharma.com/news/license", "raw_text": "", "entities": {}},
        {"source_type": "web", "title": "Example Pharma acquisition rumour", "url": "https://thirdparty.example/rumour", "raw_text": "", "entities": {}},
    ])
    with patch("pharmadrone.connectors.commercial_signals.tavily_search.search", return_value=web):
        result = commercial_signals.discover("Example Pharma", "https://www.examplepharma.com")
    assert result.count == 2
    assert result.records[0]["entities"]["primary_source_verified"] is True
    assert result.records[1]["entities"]["primary_source_verified"] is False
    assert all(record["source_type"] == "commercial_signal" for record in result.records)


def test_openalex_retains_explicit_grant_metadata():
    payload = {"results": [{"id": "https://openalex.org/W1", "title": "Study", "publication_year": 2026,
                            "grants": [{"funder_display_name": "Research Council", "funder": "https://openalex.org/F1", "award_id": "AW-1"}]}]}
    with patch("pharmadrone.connectors.openalex.get_json", return_value=payload):
        record = openalex.search("formulation", 1).records[0]
    assert record["entities"]["grants"] == [{"funder": "Research Council", "funder_id": "https://openalex.org/F1", "award_id": "AW-1"}]


def test_projection_separates_transactions_signals_and_research_grants(tmp_path):
    conn = db.connect(tmp_path / "projection.db")
    _ingest(conn, _records())
    result = commercial_intelligence.sync(conn, run_id="phase11-week-1", observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc))
    assert result == {"events_seen": 4, "events_changed": 4, "licensing_seen": 1,
                      "mergers_acquisitions_seen": 1, "partnerships_seen": 1,
                      "financing_seen": 1, "grants_seen": 1, "primary_verification_required": 1}
    licensing = dict(conn.execute("SELECT * FROM commercial_events WHERE event_type='Licensing'").fetchone())
    assert licensing["party_b_name"] == "Partner Biotech"
    assert licensing["value_amount"] == 50_000_000
    assert licensing["currency"] == "USD"
    assert licensing["primary_source_verified"] == 1
    signal = dict(conn.execute("SELECT * FROM commercial_events WHERE event_type='M&A'").fetchone())
    assert signal["party_b_name"] == ""
    assert signal["value_amount"] is None
    assert signal["validation_status"] == "Primary-source verification required"
    grant = dict(conn.execute("SELECT * FROM funding_awards").fetchone())
    assert grant["funder_name"] == "National Research Agency"
    assert grant["recipient_name"] == ""
    assert grant["amount_value"] is None
    assert "Recipient, award value" in grant["validation_status"]


def test_monitor_history_is_append_only_and_idempotent(tmp_path):
    conn = db.connect(tmp_path / "monitor.db")
    _ingest(conn, _records())
    when = datetime(2026, 7, 16, tzinfo=timezone.utc)
    first = commercial_intelligence.sync(conn, run_id="week-1", observed_at=when)
    second = commercial_intelligence.sync(conn, run_id="week-2", observed_at=when)
    assert first["events_changed"] == 4
    assert second["events_changed"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM commercial_event_observations").fetchone()["n"] == 4
    assert conn.execute("SELECT COUNT(*) AS n FROM commercial_monitor_runs").fetchone()["n"] == 2


def test_phase11_routes_workflow_and_evidence_boundaries_are_exposed():
    app = Path("pharmatune_ui/app.py").read_text()
    pages = Path("pharmatune_ui/pages.py").read_text()
    workflow = Path(".github/workflows/pharmatune_refresh.yml").read_text()
    assert '"Deals & Funding":lambda:pages.deals_funding(_navigate)' in app
    assert '"Deal Detail":lambda:pages.deal_detail(_navigate)' in app
    assert "research_innovation commercial_intelligence" in workflow
    assert "A web result is a discovery signal—not a confirmed transaction" in pages
    assert "Research-grant metadata is kept separate from corporate financing" in pages
    assert "does not infer undisclosed deal value" in pages
