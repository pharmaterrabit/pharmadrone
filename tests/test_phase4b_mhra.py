from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pharmadrone import db
from pharmadrone.connectors import mhra_alerts
from pharmadrone.scheduler import config, repository, sources


def _payload():
    return {
        "total": 2,
        "results": [
            {
                "title": "Class 2 Medicines Recall: Example Pharma Ltd, Example Oral Solution, EL(26)A/34",
                "link": "/drug-device-alerts/example-recall-el-26-a-slash-34",
                "description": "Example Pharma Ltd is recalling one batch due to visible particles in some bottles.",
                "public_timestamp": "2026-07-13T12:03:35Z",
                "alert_type": ["medicines-recall-notification"],
            },
            {
                "title": "Field Safety Notices: 6 to 10 July 2026",
                "link": "/drug-device-alerts/field-safety-notices-6-to-10-july-2026",
                "description": "List of device field safety notices.",
                "public_timestamp": "2026-07-14T09:19:00Z",
                "alert_type": ["field-safety-notices"],
            },
        ],
    }


def test_medicine_recall_is_normalised_and_device_notice_excluded():
    result = mhra_alerts.parse_payload(_payload())
    assert result.ok and result.count == 1
    recall = result.records[0]
    assert recall["source_type"] == "recall"
    assert recall["entities"]["company"] == "Example Pharma Ltd"
    assert recall["entities"]["product"] == "Example Oral Solution"
    assert recall["entities"]["mhra_reference"] == "EL(26)A/34"
    assert recall["entities"]["issue_category"] == "particulate / precipitation"
    assert recall["entities"]["direct_problem_evidence"] is True
    assert recall["entities"]["regulator"] == "MHRA"


def test_missing_product_does_not_become_direct_problem_evidence():
    payload = {"total": 1, "results": [{
        "title": "Medicines Recall", "link": "/drug-device-alerts/ambiguous",
        "description": "A precautionary notice.", "public_timestamp": "2026-07-01T00:00:00Z",
        "alert_type": ["medicines-recall-notification"],
    }]}
    recall = mhra_alerts.parse_payload(payload).records[0]
    assert recall["entities"]["direct_problem_evidence"] is False
    assert recall["entities"]["company"] is None


def test_scheduler_registers_mhra_and_uses_publication_watermark(tmp_path: Path):
    conn = db.connect(tmp_path / "mhra.sqlite")
    repository.ensure_source_states(conn)
    assert config.source_spec("mhra_medicine_recalls").creates_opportunities is True
    with patch("pharmadrone.scheduler.sources.mhra_alerts.fetch", return_value=mhra_alerts.parse_payload(_payload())):
        fetched = sources.fetch_mhra_medicine_recalls(conn, repository.source_state(conn, "mhra_medicine_recalls"), config.guardrails(), force=True)
    assert len(fetched["records"]) == 1
    assert fetched["watermark_after"] == "2026-07-13"


def test_repeat_safe_mhra_ingestion_preserves_change_history(tmp_path: Path):
    conn = db.connect(tmp_path / "mhra-history.sqlite")
    recall = mhra_alerts.parse_payload(_payload()).records[0]
    with conn.transaction(): first = repository.ingest_source_records(conn, run_id="r1", source_name="mhra_medicine_recalls", records=[recall])
    with conn.transaction(): second = repository.ingest_source_records(conn, run_id="r2", source_name="mhra_medicine_recalls", records=[recall])
    assert first["created"] == 1
    assert second["unchanged"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM source_record_changes").fetchone()["n"] == 1


def test_workflow_exposes_manual_mhra_refresh():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/pharmatune_refresh.yml").read_text()
    assert "- mhra_medicine_recalls" in workflow
