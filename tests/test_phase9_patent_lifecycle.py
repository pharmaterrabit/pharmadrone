from datetime import date, datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from zipfile import ZipFile

from pharmadrone import db
from pharmadrone.pipeline import patent_lifecycle
from pharmadrone.scheduler import config, repository

from pharmadrone.connectors import fda_orange_book


def _archive() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("products.txt", (
            "Ingredient~DF;Route~Trade_Name~Applicant~Strength~Appl_Type~Appl_No~Product_No~TE_Code~Approval_Date~RLD~RS~Type~Applicant_Full_Name\n"
            "EXAMPLINE~Tablet;Oral~EXAMPLE DRUG~EXAMPLECO~10MG~N~012345~001~~Jan 02, 2020~RLD~RS~RX~Example Pharma Inc\n"
        ))
        archive.writestr("patent.txt", (
            "Appl_Type~Appl_No~Product_No~Patent_No~Patent_Expire_Date_Text~Drug_Substance_Flag~Drug_Product_Flag~Patent_Use_Code~Delist_Flag~Submission_Date\n"
            "N~012345~001~9876543~Jan 02, 2030~Y~Y~U-123~~Feb 3, 2020\n"
        ))
        archive.writestr("exclusivity.txt", (
            "Appl_Type~Appl_No~Product_No~Exclusivity_Code~Exclusivity_Date\n"
            "N~012345~001~NCE~Jan 02, 2025\n"
        ))
    return stream.getvalue()


def _load(conn):
    record = fda_orange_book.parse_archive(_archive()).records[0]
    with conn.transaction():
        repository.ingest_source_records(
            conn, run_id="orange-book-1", source_name="fda_orange_book", records=[record]
        )
    return record


def test_phase9_schema_and_weekly_monitor_are_installed(tmp_path):
    conn = db.connect(tmp_path / "phase9.db")
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {
        "lifecycle_products", "lifecycle_patents", "lifecycle_exclusivities",
        "lifecycle_observations", "lifecycle_monitor_runs",
    }.issubset(tables)
    assert config.source_spec("patent_lifecycle").cadence == "weekly"


def test_lifecycle_state_uses_stored_expiries_without_legal_conclusions():
    close = [{"expiry_date": "2027-01-01"}]
    distant = [{"expiry_date": "2035-01-01"}]
    assert patent_lifecycle.lifecycle_state(close, [], today=date(2026, 7, 16))[0] == "Expiry within 24 months"
    assert patent_lifecycle.lifecycle_state(distant, [], today=date(2026, 7, 16))[0] == "Unexpired listed protection"
    assert patent_lifecycle.lifecycle_state([], [], today=date(2026, 7, 16))[0] == "No unexpired listed protection"
    assert patent_lifecycle.lifecycle_state([], [], today=date(2026, 7, 16), dataset_mode="Drugs@FDA product fallback")[0] == "Lifecycle evidence unavailable"


def test_orange_book_projection_separates_application_holder_owner_and_family(tmp_path):
    conn = db.connect(tmp_path / "projection.db")
    _load(conn)
    result = patent_lifecycle.sync(
        conn, run_id="phase9-week-1", observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc)
    )
    assert result == {
        "products_seen": 1, "products_changed": 1, "patents_seen": 1,
        "exclusivities_seen": 1, "family_resolution_required": 1,
    }
    product = dict(conn.execute("SELECT * FROM lifecycle_products").fetchone())
    assert product["application_holder"] == "Example Pharma Inc"
    assert product["lifecycle_status"] == "Unexpired listed protection"
    patent = dict(conn.execute("SELECT * FROM lifecycle_patents").fetchone())
    assert patent["application_holder_context"] == "Example Pharma Inc"
    assert patent["ownership_status"] == "Patent owner not established by Orange Book"
    assert patent["family_status"] == "Family resolution required from patent-office evidence"
    assert patent["family_id"] == ""
    assert patent["family_lookup_url"].startswith("https://worldwide.espacenet.com/patent/search")
    assert "Example Pharma" not in patent["ownership_status"]
    exclusivity = dict(conn.execute("SELECT * FROM lifecycle_exclusivities").fetchone())
    assert exclusivity["exclusivity_code"] == "NCE"


def test_monitor_observations_are_append_only_and_snapshot_idempotent(tmp_path):
    conn = db.connect(tmp_path / "history.db")
    _load(conn)
    when = datetime(2026, 7, 16, tzinfo=timezone.utc)
    first = patent_lifecycle.sync(conn, run_id="week-1", observed_at=when)
    second = patent_lifecycle.sync(conn, run_id="week-2", observed_at=when)
    assert first["products_changed"] == 1
    assert second["products_changed"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM lifecycle_observations").fetchone()["n"] == 1
    stored = dict(conn.execute("SELECT * FROM source_records").fetchone())
    record = json.loads(stored["record_json"])
    record["entities"]["patents"][0]["expiry_date"] = "2031-01-02"
    conn.execute("UPDATE source_records SET record_json=?", (json.dumps(record),))
    third = patent_lifecycle.sync(conn, run_id="week-3", observed_at=when)
    assert third["products_changed"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM lifecycle_observations").fetchone()["n"] == 2
    assert conn.execute("SELECT COUNT(*) AS n FROM lifecycle_monitor_runs").fetchone()["n"] == 3


def test_fallback_is_visible_but_does_not_invent_patents(tmp_path):
    conn = db.connect(tmp_path / "fallback.db")
    record = {
        "source_type": "fda_orange_book_product", "record_id": "012345-001",
        "url": "https://www.accessdata.fda.gov/scripts/cder/daf/",
        "entities": {
            "application_number": "012345", "product_number": "001", "product": "EXAMPLE",
            "company": "Example Pharma", "molecule": "EXAMPLINE", "patents": [], "exclusivities": [],
            "dataset_mode": "Drugs@FDA product fallback",
        },
    }
    with conn.transaction():
        repository.ingest_source_records(conn, run_id="fallback", source_name="fda_orange_book", records=[record])
    patent_lifecycle.sync(conn, observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc))
    product = dict(conn.execute("SELECT * FROM lifecycle_products").fetchone())
    assert product["lifecycle_status"] == "Lifecycle evidence unavailable"
    assert "fallback" in product["evidence_status"].lower()
    assert conn.execute("SELECT COUNT(*) AS n FROM lifecycle_patents").fetchone()["n"] == 0


def test_phase9_routes_workflow_and_truth_language_are_exposed():
    app = Path("pharmatune_ui/app.py").read_text()
    pages = Path("pharmatune_ui/pages.py").read_text()
    workflow = Path(".github/workflows/pharmatune_refresh.yml").read_text()
    assert '"Patents":lambda:pages.patents(_navigate)' in app
    assert '"Patent Detail":lambda:pages.patent_detail(_navigate)' in app
    assert "patent_lifecycle" in workflow
    assert "not proof of patent ownership" in pages
    assert "not a validity or freedom-to-operate opinion" in pages
