from datetime import datetime, timezone

import pytest

from pharmadrone import db
from pharmadrone.pipeline import patent_lifecycle
from pharmadrone.storage.migrations import MIGRATIONS, _canonical_patent_foundation_schema


def _legacy_document(conn, *, publication="EP1234567A1", family_id="42"):
    conn.execute(
        """INSERT INTO patent_documents
        (patent_document_id,publication_number,application_number,jurisdiction,document_kind,title,abstract_text,
         filing_date,publication_date,grant_date,family_id,family_status,legal_status_summary,legal_status_as_of,
         source_name,source_authority,official_source_url,google_patents_url,uk_register_url,evidence_status,
         first_seen_at,last_verified_at,next_review_at,attributes_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("legacy-doc", publication, "EP1234567", "EP", "A1", "Example patent", "Abstract", "2025-01-01",
         "2026-01-02", "", family_id, "Official family evidence", "Latest reported legal event", "2026-03-04",
         "EPO OPS", "official", "https://official.example/patent", "https://patents.google.com/patent/EP1234567A1/en",
         "", "Official patent-office evidence", "2026-07-16", "2026-07-16", "2026-07-23", "{}"),
    )
    conn.execute(
        """INSERT INTO patent_parties
        (patent_party_id,patent_document_id,party_type,party_name,country_code,sequence_number,evidence_status,
         official_source_url,first_seen_at,last_verified_at,next_review_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        ("legacy-party", "legacy-doc", "applicant", "Example Pharma AG", "CH", "1",
         "Officially reported party; current ownership not inferred", "https://official.example/patent",
         "2026-07-16", "2026-07-16", "2026-07-23"),
    )
    conn.execute(
        """INSERT INTO patent_family_members
        (patent_family_member_id,family_id,patent_document_id,publication_number,jurisdiction,relationship_type,
         evidence_status,official_source_url,observed_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        ("legacy-family-member", family_id, "legacy-doc", publication, "EP", "family member",
         "Official patent-office family evidence", "https://official.example/patent", "2026-07-16"),
    )


def _document_entities():
    return {
        "publication_number": "EP1234567A1", "application_number": "EP1234567", "jurisdiction": "EP",
        "document_kind": "A1", "title": "Example patent", "abstract": "Abstract",
        "publication_date": "2026-01-02", "family_id": "42", "family_status": "Official family evidence",
        "legal_status_code": "17P", "legal_status_summary": "Request for examination filed",
        "legal_status_as_of": "2026-03-04", "official_source_url": "https://official.example/patent",
        "parties": [{"party_type": "applicant", "party_name": "Example Pharma AG", "country_code": "CH"}],
        "family_members": [{"publication_number": "EP1234567A1", "jurisdiction": "EP"}],
        "legal_events": [{"event_code": "17P", "event_date": "2026-03-04", "event_text": "Request for examination filed", "authority": "EPO OPS"}],
    }


def test_canonical_foundation_migration_backfills_and_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "foundation-backfill.sqlite")
    _legacy_document(conn)
    before = conn.execute("SELECT COUNT(*) AS n FROM patent_documents").fetchone()["n"]
    _canonical_patent_foundation_schema(conn)
    conn.commit()
    _canonical_patent_foundation_schema(conn)
    conn.commit()

    assert max(m.version for m in MIGRATIONS) >= 15
    assert {"patent_families", "patent_document_sources"}.issubset(
        {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    )
    assert {"normalized_publication_number", "expiry_basis", "last_source_refresh_id"}.issubset(
        conn.columns("patent_documents")
    )
    assert {"normalized_party_name", "party_identity_key"}.issubset(conn.columns("patent_parties"))
    assert {"evidence_basis", "verification_status", "verified_at"}.issubset(conn.columns("patent_product_links"))
    assert conn.execute("SELECT COUNT(*) AS n FROM patent_documents").fetchone()["n"] == before
    doc = dict(conn.execute("SELECT * FROM patent_documents WHERE patent_document_id='legacy-doc'").fetchone())
    assert doc["normalized_publication_number"] == "EP1234567A1"
    assert doc["normalized_application_number"] == "EP1234567"
    assert doc["publication_kind"] == "A1"
    assert doc["legal_status_code"] == ""
    assert doc["legal_status_label"] == "Latest reported legal event"
    assert doc["legal_status_basis"]
    assert conn.execute("SELECT COUNT(*) AS n FROM patent_document_sources").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM patent_families WHERE family_id='42'").fetchone()["n"] == 1
    party = dict(conn.execute("SELECT * FROM patent_parties WHERE patent_party_id='legacy-party'").fetchone())
    assert party["normalized_party_name"] == "example pharma ag"
    assert party["party_identity_key"] == "example pharma ag"


def test_canonical_identity_and_multi_source_provenance_are_duplicate_safe(tmp_path):
    conn = db.connect(tmp_path / "foundation-identity.sqlite")
    observed = "2026-07-16T00:00:00+00:00"
    entities = _document_entities()
    first = patent_lifecycle._upsert_document(
        conn, entities, source_name="EPO OPS", authority="official", observed=observed,
        next_review="2026-07-23T00:00:00+00:00", source_record_id="epo-1", source_refresh_id="refresh-1",
    )
    second = patent_lifecycle._upsert_document(
        conn, entities, source_name="EPO OPS", authority="official", observed=observed,
        next_review="2026-07-23T00:00:00+00:00", source_record_id="epo-2", source_refresh_id="refresh-2",
    )
    assert first == second
    assert conn.execute("SELECT COUNT(*) AS n FROM patent_documents").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM patent_document_sources").fetchone()["n"] == 2
    doc = dict(conn.execute("SELECT * FROM patent_documents").fetchone())
    assert doc["normalized_publication_number"] == "EP1234567A1"
    assert doc["last_source_refresh_id"] == "refresh-2"

    with pytest.raises(Exception):
        conn.execute(
            """INSERT INTO patent_documents
            (patent_document_id,publication_number,application_number,jurisdiction,document_kind,title,abstract_text,
             filing_date,publication_date,grant_date,family_id,family_status,legal_status_summary,legal_status_as_of,
             source_name,source_authority,official_source_url,google_patents_url,uk_register_url,evidence_status,
             first_seen_at,last_verified_at,next_review_at,attributes_json,normalized_publication_number)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("duplicate-doc", "EP-1234567A1", "", "EP", "A1", "Duplicate", "", "", "", "", "", "Family not established",
             "", "", "EPO OPS", "official", "https://official.example/duplicate", "", "", "official", observed, observed,
             observed, "{}", "EP1234567A1"),
        )


def test_family_and_existing_global_reads_remain_compatible(tmp_path):
    conn = db.connect(tmp_path / "foundation-compatibility.sqlite")
    observed = datetime(2026, 7, 16, tzinfo=timezone.utc)
    patent_lifecycle._upsert_document(
        conn, _document_entities(), source_name="EPO OPS", authority="official", observed=observed.isoformat(),
        next_review="2026-07-23T00:00:00+00:00", source_record_id="epo-1", source_refresh_id="refresh-1",
    )
    patent_lifecycle._upsert_document(
        conn, _document_entities(), source_name="EPO OPS", authority="official", observed=observed.isoformat(),
        next_review="2026-07-23T00:00:00+00:00", source_record_id="epo-1", source_refresh_id="refresh-1",
    )
    assert conn.execute("SELECT COUNT(*) AS n FROM patent_families").fetchone()["n"] == 1
    rows = patent_lifecycle.global_documents(conn)
    assert len(rows) == 1
    assert rows[0]["publication_number"] == "EP1234567A1"
    profile = patent_lifecycle.global_document_profile(conn, rows[0]["patent_document_id"])
    assert profile and profile["family_id"] == "42"
    assert len(profile["family_members"]) == 1
    assert profile["parties"][0]["party_name"] == "Example Pharma AG"


def test_product_link_evidence_and_verification_fields_are_additive(tmp_path):
    conn = db.connect(tmp_path / "foundation-links.sqlite")
    with conn.transaction():
        conn.execute(
            "INSERT INTO lifecycle_products (lifecycle_id,application_number,product_number,trade_name,official_source_url,evidence_status,lifecycle_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("life-1", "012345", "001", "Example", "https://www.fda.gov/orange-book-data", "official", "Unexpired listed protection", "2026-07-16", "2026-07-23"),
        )
        conn.execute(
            "INSERT INTO lifecycle_patents (lifecycle_patent_id,lifecycle_id,patent_number,expiry_date,ownership_status,family_status,official_source_url,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?)",
            ("lp-1", "life-1", "9876543", "2030-01-02", "not established", "not established", "https://www.fda.gov/orange-book-data", "2026-07-16", "2026-07-23"),
        )
    patent_lifecycle.sync_global(conn, run_id="foundation-links", observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc))
    link = dict(conn.execute("SELECT * FROM patent_product_links").fetchone())
    assert link["evidence_basis"] == "Orange Book application/product listing"
    assert link["evidence_source_record_id"] == "life-1:9876543"
    assert link["verification_status"] == "verified"
    assert link["verified_at"] == "2026-07-16T00:00:00+00:00"
    assert "ownership" in link["verification_basis"]
