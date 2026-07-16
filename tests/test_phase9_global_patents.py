from datetime import datetime, timezone

from pharmadrone import db
from pharmadrone.connectors import epo_ops
from pharmadrone.pipeline import patent_lifecycle
from pharmadrone.scheduler import config, repository


OPS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ops:world-patent-data xmlns:ops="http://ops.epo.org" xmlns:exch="http://www.epo.org/exchange">
  <ops:biblio-search><ops:search-result>
    <exch:exchange-documents>
      <exch:exchange-document country="EP" doc-number="1234567" kind="A1">
        <exch:bibliographic-data>
          <exch:publication-reference><exch:document-id><exch:date>20260102</exch:date></exch:document-id></exch:publication-reference>
          <exch:invention-title lang="en">Example pharmaceutical formulation</exch:invention-title>
          <exch:parties>
            <exch:applicants><exch:applicant sequence="1"><exch:applicant-name><exch:name>Example Pharma AG</exch:name></exch:applicant-name><exch:residence><exch:country>CH</exch:country></exch:residence></exch:applicant></exch:applicants>
            <exch:inventors><exch:inventor sequence="1"><exch:inventor-name><exch:name>Jane Scientist</exch:name></exch:inventor-name></exch:inventor></exch:inventors>
          </exch:parties>
        </exch:bibliographic-data>
        <exch:abstract lang="en"><exch:p>A retained official abstract.</exch:p></exch:abstract>
      </exch:exchange-document>
      <exch:exchange-document country="GB" doc-number="7654321" kind="A">
        <exch:invention-title lang="en">UK formulation patent</exch:invention-title>
      </exch:exchange-document>
    </exch:exchange-documents>
  </ops:search-result></ops:biblio-search>
</ops:world-patent-data>"""

FAMILY_XML = b"""<ops:world-patent-data xmlns:ops="http://ops.epo.org" xmlns:exch="http://www.epo.org/exchange">
<ops:family><ops:family-member family-id="42"><exch:exchange-document country="EP" doc-number="1234567" kind="A1"/></ops:family-member>
<ops:family-member family-id="42"><exch:exchange-document country="GB" doc-number="7654321" kind="A"/></ops:family-member></ops:family>
</ops:world-patent-data>"""

LEGAL_XML = b"""<ops:world-patent-data xmlns:ops="http://ops.epo.org">
<ops:legal-event code="17P" date="20260304" desc="Request for examination filed"/>
</ops:world-patent-data>"""


def test_global_patent_schema_and_weekly_epo_source(tmp_path):
    conn = db.connect(tmp_path / "global-patents.db")
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"patent_documents", "patent_parties", "patent_family_members", "patent_legal_events",
            "patent_product_links", "patent_global_monitor_runs"}.issubset(tables)
    assert config.source_spec("epo_ops_patents").cadence == "weekly"
    assert config.source_spec("epo_ops_patents").enabled_env == "EPO_OPS_KEY"


def test_epo_parser_retains_official_fields_without_inventing_status_or_owner():
    result = epo_ops.parse_search_xml(OPS_XML, query='ctxt="example" and (pn=EP or pn=GB)')
    assert result.ok and result.count == 2
    ep = result.records[0]["entities"]
    assert ep["publication_number"] == "EP1234567A1"
    assert ep["jurisdiction"] == "EP"
    assert ep["parties"][0]["party_type"] == "applicant"
    assert ep["parties"][0]["party_name"] == "Example Pharma AG"
    assert ep["legal_status_summary"] == "Not established by bibliographic search response"
    assert "owner" not in ep["parties"][0]["party_type"]
    gb = result.records[1]["entities"]
    assert gb["publication_number"] == "GB7654321A"
    assert gb["uk_register_url"] == "https://www.gov.uk/search-for-patent"
    assert gb["google_patents_url"].startswith("https://patents.google.com/patent/GB")


def test_epo_family_and_legal_event_parsers_preserve_reported_facts_only():
    family = epo_ops.parse_family_xml(FAMILY_XML)
    assert family["family_id"] == "42"
    assert {item["publication_number"] for item in family["family_members"]} == {"EP1234567A1", "GB7654321A"}
    events = epo_ops.parse_legal_xml(LEGAL_XML)
    assert events == [{
        "event_code": "17P", "event_date": "2026-03-04", "event_text": "Request for examination filed",
        "authority": "EPO OPS / worldwide legal event data",
    }]


def test_global_projection_exposes_ep_gb_google_and_reported_parties(tmp_path):
    conn = db.connect(tmp_path / "projection.db")
    records = epo_ops.parse_search_xml(OPS_XML, query="example").records
    with conn.transaction():
        repository.ingest_source_records(conn, run_id="epo-1", source_name="epo_ops_patents", records=records)
    result = patent_lifecycle.sync_global(
        conn, run_id="global-1", observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc)
    )
    assert result["documents_seen"] == 2
    assert result["eu_documents_seen"] == 1
    assert result["uk_documents_seen"] == 1
    assert result["parties_seen"] == 2
    directory = patent_lifecycle.global_documents(conn)
    assert {row["jurisdiction"] for row in directory} == {"EP", "GB"}
    ep = next(row for row in directory if row["jurisdiction"] == "EP")
    assert ep["reported_parties"] == "Example Pharma AG · Jane Scientist"
    profile = patent_lifecycle.global_document_profile(conn, ep["patent_document_id"])
    assert profile and len(profile["parties"]) == 2
    assert profile["google_patents_url"].startswith("https://patents.google.com/patent/EP")
    assert profile["source_authority"] == "official"


def test_global_database_filters_are_separate_and_fda_links_are_application_specific(tmp_path):
    conn = db.connect(tmp_path / "filters.db")
    with conn.transaction():
        conn.execute(
            "INSERT INTO lifecycle_products (lifecycle_id,application_number,product_number,trade_name,official_source_url,evidence_status,lifecycle_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("life-1", "012345", "001", "Example", "https://www.fda.gov/orange-book-data", "official", "Unexpired listed protection", "2026-07-16", "2026-07-23"),
        )
        conn.execute(
            "INSERT INTO lifecycle_patents (lifecycle_patent_id,lifecycle_id,patent_number,ownership_status,family_status,official_source_url,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?)",
            ("lp-1", "life-1", "9876543", "not established", "not established", "https://www.fda.gov/orange-book-data", "2026-07-16", "2026-07-23"),
        )
    patent_lifecycle.sync_global(conn, observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc))
    us = patent_lifecycle.global_documents(conn, source="FDA Orange Book")
    assert len(us) == 1
    assert "ApplNo=012345" in us[0]["official_source_url"]
    assert patent_lifecycle.global_documents(conn, source="EPO / EP") == []
    pages = open("pharmatune_ui/pages.py", encoding="utf-8").read()
    assert '"FDA Orange Book", "EPO / EP", "UK / GB", "Google Patents"' in pages
    assert "Search this in Google Patents" in pages


def test_google_patents_is_labelled_discovery_only_in_ui():
    pages = open("pharmatune_ui/pages.py", encoding="utf-8").read()
    assert "Google Patents is included for discovery" in pages
    assert "never treated as authority" in pages
    assert "current ownership not inferred" not in pages  # stored evidence language, not a UI claim
