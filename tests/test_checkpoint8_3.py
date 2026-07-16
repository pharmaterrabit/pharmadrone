import json
from datetime import datetime, timezone

from pharmadrone import db
from pharmadrone.connectors import clinicaltrials
from pharmadrone.pipeline import account_intelligence
from pharmadrone.scheduler.config import source_spec


def _source(conn, *, source_type="trial", source_id="NCT123", entities=None, url="https://clinicaltrials.gov/study/NCT123"):
    payload = {
        "source_type": source_type,
        "record_id": source_id,
        "title": "Example medicine programme",
        "url": url,
        "entities": entities or {},
    }
    conn.execute(
        "INSERT INTO source_records (source_type,source_id,source_name,official_source_url,content_checksum,"
        "record_json,first_seen_at,last_seen_at,last_changed_at,active) VALUES (?,?,?,?,?,?,?,?,?,1)",
        (source_type, source_id, source_type, url, source_id, json.dumps(payload),
         "2026-07-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00", "2026-07-01T00:00:00+00:00"),
    )


def test_8_3a_schema_and_weekly_job_are_installed(tmp_path):
    conn = db.connect(tmp_path / "account.db")
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {
        "account_organisations", "account_aliases", "account_relationships",
        "account_contact_routes", "account_contacts", "account_monitor_runs",
    }.issubset(tables)
    assert source_spec("account_intelligence").cadence == "weekly"


def test_8_3b_projects_organisation_products_signals_and_routes(tmp_path):
    conn = db.connect(tmp_path / "projection.db")
    _source(conn, entities={
        "company": "Example Pharma Ltd", "product": "Example tablets",
        "country": "United Kingdom", "issue_category": "medicine recall",
    }, url="https://www.gov.uk/example-recall")
    result = account_intelligence.sync_account_intelligence(
        conn, run_id="weekly-1", observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc)
    )
    assert result["organisations_seen"] == 1
    org = dict(conn.execute("SELECT * FROM account_organisations").fetchone())
    assert org["canonical_name"] == "Example Pharma Ltd"
    assert org["country"] == "United Kingdom"
    relationship = dict(conn.execute("SELECT * FROM account_relationships").fetchone())
    assert relationship["object_name"] == "Example tablets"
    assert relationship["evidence_url"] == "https://www.gov.uk/example-recall"
    route = dict(conn.execute("SELECT * FROM account_contact_routes").fetchone())
    assert route["contact_function"] == "Quality / CMC"
    assert "named person not guaranteed" in route["route_status"]


def test_8_3c_named_contact_requires_person_and_official_evidence(tmp_path):
    conn = db.connect(tmp_path / "contacts.db")
    _source(conn, entities={
        "sponsor": "Research University", "product": "Study Drug",
        "contacts": [{"name": "Dr Jane Example", "role": "Study Chair", "email": "jane@example.edu"}],
    })
    _source(
        conn, source_type="trial", source_id="NCT-NO-URL",
        entities={"sponsor": "No Evidence Institute", "product": "Drug B", "contacts": [{"name": "Unverified Person"}]},
        url="",
    )
    account_intelligence.sync_account_intelligence(
        conn, run_id="weekly-contacts", observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc)
    )
    contacts = [dict(row) for row in conn.execute("SELECT * FROM account_contacts").fetchall()]
    assert [row["person_name"] for row in contacts] == ["Dr Jane Example"]
    assert contacts[0]["verification_status"] == "listed in an official public source"
    assert "must still be confirmed" in contacts[0]["confidence_note"]


def test_8_3c_clinicaltrials_preserves_public_central_contact():
    study = {
        "protocolSection": {
            "identificationModule": {"nctId": "NCT123", "briefTitle": "Bioavailability formulation study"},
            "statusModule": {"overallStatus": "TERMINATED", "whyStopped": "formulation issue"},
            "designModule": {"studyType": "INTERVENTIONAL"},
            "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "Drug X"}]},
            "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Example Pharma"}},
            "contactsLocationsModule": {"centralContacts": [{
                "name": "Jane Example", "role": "CONTACT", "email": "jane@example.com", "phone": "+1 555",
            }]},
        }
    }
    record, _meta = clinicaltrials._row(study, "formulation")
    assert record["entities"]["contacts"] == [{
        "name": "Jane Example", "role": "CONTACT", "phone": "+1 555",
        "email": "jane@example.com", "source_scope": "central study contact",
    }]


def test_8_3d_monitor_is_append_only_and_flags_expired_unseen_contact(tmp_path):
    conn = db.connect(tmp_path / "monitor.db")
    _source(conn, entities={
        "company": "Monitor Pharma", "product": "Monitor drug",
        "contacts": [{"name": "Listed Person", "role": "Clinical Contact"}],
    })
    account_intelligence.sync_account_intelligence(
        conn, run_id="week-1", observed_at=datetime(2026, 7, 1, tzinfo=timezone.utc)
    )
    first_observations = conn.execute("SELECT COUNT(*) AS n FROM account_organisation_observations").fetchone()["n"]
    conn.execute("UPDATE source_records SET active=0")
    result = account_intelligence.sync_account_intelligence(
        conn, run_id="week-2", observed_at=datetime(2026, 7, 10, tzinfo=timezone.utc)
    )
    assert result["contacts_due_review"] == 1
    assert conn.execute("SELECT verification_status FROM account_contacts").fetchone()[0] == "weekly revalidation due"
    assert conn.execute("SELECT COUNT(*) AS n FROM account_organisation_observations").fetchone()["n"] == first_observations
    assert conn.execute("SELECT COUNT(*) AS n FROM account_monitor_runs").fetchone()["n"] == 2
