from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from pharmadrone import db
from pharmadrone.connectors import fda_orange_book
from pharmadrone.pipeline import pharmaceutical_memory
from pharmadrone.scheduler import config, repository, sources


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


def test_archive_joins_product_patent_and_exclusivity_without_problem_signal():
    result = fda_orange_book.parse_archive(_archive())
    assert result.ok and result.count == 1
    item = result.records[0]
    assert item["record_id"] == "012345-001"
    assert item["entities"]["company"] == "Example Pharma Inc"
    assert item["entities"]["patents"][0]["patent_number"] == "9876543"
    assert item["entities"]["exclusivities"][0]["code"] == "NCE"
    assert item["entities"]["direct_problem_evidence"] is False


def test_archive_fails_closed_when_required_file_is_missing():
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("products.txt", "Ingredient~Trade_Name\nX~Y\n")
    result = fda_orange_book.parse_archive(stream.getvalue())
    assert not result.ok
    assert "missing expected file" in result.error


def test_scheduler_registers_monthly_orange_book_and_preserves_archive_cursor(tmp_path: Path):
    conn = db.connect(tmp_path / "fda-ob.sqlite")
    repository.ensure_source_states(conn)
    assert config.source_spec("fda_orange_book").cadence == "monthly"
    parsed = fda_orange_book.parse_archive(_archive())
    with patch("pharmadrone.scheduler.sources.fda_orange_book.fetch", return_value=parsed):
        fetched = sources.fetch_fda_orange_book(conn, repository.source_state(conn, "fda_orange_book"), config.guardrails(), force=True)
    assert fetched["cursor_after"] == "archive:1"
    assert fetched["watermark_after"] == "2020-01-02"


def test_orange_book_projects_governed_lifecycle_relationships_into_memory(tmp_path: Path):
    conn = db.connect(tmp_path / "fda-ob-memory.sqlite")
    record = fda_orange_book.parse_archive(_archive()).records[0]
    with conn.transaction():
        repository.ingest_source_records(conn, run_id="fda-ob-run", source_name="fda_orange_book", records=[record])
    pharmaceutical_memory.sync_fda_orange_book(conn)
    relationships = {row["relationship_type"] for row in conn.execute("SELECT relationship_type FROM memory_relationships").fetchall()}
    assert relationships == {"fda_application_holder_for", "has_active_ingredient", "has_listed_patent", "has_fda_exclusivity"}
    statuses = {row["evidence_status"] for row in conn.execute("SELECT evidence_status FROM memory_relationships").fetchall()}
    assert "applicant-submitted FDA Orange Book listing" in statuses
    assert all("problem" not in relation for relation in relationships)


def test_workflow_exposes_manual_orange_book_refresh():
    workflow = Path(".github/workflows/pharmatune_refresh.yml").read_text()
    assert "- fda_orange_book" in workflow


def test_drugsfda_fallback_retains_products_without_inventing_lifecycle_fields():
    payload = {"meta": {"last_updated": "2026-07-14"}, "results": [{
        "application_number": "NDA012345", "sponsor_name": "Example Pharma Inc",
        "submissions": [{"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20200102"}],
        "products": [{"product_number": "001", "brand_name": "EXAMPLE DRUG",
                      "active_ingredients": [{"name": "EXAMPLINE", "strength": "10MG"}],
                      "reference_drug": "Yes", "reference_standard": "Yes", "dosage_form": "TABLET",
                      "route": "ORAL", "marketing_status": "Prescription", "te_code": "AB"}],
    }]}
    result = fda_orange_book.parse_drugsfda_payload(payload)
    assert result.ok and result.count == 1
    item = result.records[0]
    assert item["record_id"] == "012345-001"
    assert item["entities"]["patents"] == []
    assert item["entities"]["exclusivities"] == []
    assert item["entities"]["dataset_mode"] == "Drugs@FDA product fallback"
    assert item["entities"]["direct_problem_evidence"] is False


def test_fetch_uses_official_drugsfda_fallback_when_archive_is_blocked():
    fallback = fda_orange_book.parse_drugsfda_payload({"results": []})
    response = __import__("httpx").Response(200, content=b"<html>FDA challenge</html>", request=__import__("httpx").Request("GET", fda_orange_book.DEFAULT_ARCHIVE_URL))
    with patch("httpx.Client.get", return_value=response), patch(
        "pharmadrone.connectors.fda_orange_book.fetch_drugsfda_fallback", return_value=fallback
    ):
        result = fda_orange_book.fetch(max_results=10)
    assert result.ok
    assert result.stats["archive_error"]
    assert result.stats["dataset_mode"] == "Drugs@FDA product fallback"
    assert result.stats["source_coverage"] == "Drugs@FDA product-only fallback"
    assert "patents and exclusivities unavailable" in result.stats["fallback_reason"]


def test_successful_archive_reports_complete_orange_book_coverage():
    with patch("httpx.Client.get", return_value=__import__("httpx").Response(
        200, content=_archive(), request=__import__("httpx").Request("GET", fda_orange_book.DEFAULT_ARCHIVE_URL)
    )):
        result = fda_orange_book.fetch(max_results=10)
    assert result.ok
    assert result.stats["dataset_mode"] == "Orange Book archive"
    assert result.stats["source_coverage"] == "Orange Book products, patents and exclusivities"
    assert result.stats["fallback_reason"] == ""
