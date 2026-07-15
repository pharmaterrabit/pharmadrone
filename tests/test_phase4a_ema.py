from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pharmadrone import db
from pharmadrone.connectors import ema_medicines
from pharmadrone.pipeline import pharmaceutical_memory
from pharmadrone.scheduler import config, repository, sources


def _payload():
    return {
        "meta": {"total_records": 2, "timestamp": "2026-07-15T06:02:43Z"},
        "data": [
            {
                "category": "Human", "name_of_medicine": "Example Human", "ema_product_number": "EMEA/H/C/000001",
                "medicine_status": "Authorised", "active_substance": "example substance",
                "marketing_authorisation_developer_applicant_holder": "Example Pharma Ltd",
                "therapeutic_area_mesh": "Example condition", "therapeutic_indication": "Example indication",
                "marketing_authorisation_date": "01/02/2020", "last_updated_date": "14/07/2026",
                "medicine_url": "https://www.ema.europa.eu/en/medicines/human/EPAR/example-human",
            },
            {
                "category": "Veterinary", "name_of_medicine": "Example Vet", "ema_product_number": "EMEA/V/C/000002",
                "medicine_status": "Authorised", "active_substance": "vet substance",
                "marketing_authorisation_developer_applicant_holder": "Example Animal Health",
                "last_updated_date": "13/07/2026",
                "medicine_url": "https://www.ema.europa.eu/en/medicines/veterinary/EPAR/example-vet",
            },
        ],
    }


def test_official_payload_is_normalised_without_problem_claims():
    result = ema_medicines.parse_payload(_payload())
    assert result.ok and result.count == 2
    assert result.stats["feed_timestamp"] == "2026-07-15T06:02:43Z"
    assert result.stats["categories"] == {"Human": 1, "Veterinary": 1}
    human = result.records[0]
    assert human["record_id"] == "EMEA/H/C/000001"
    assert human["entities"]["last_update_date"] == "2026-07-14"
    assert human["entities"]["direct_problem_evidence"] is False
    assert "not evidence of product failure" in result.warnings[0]


def test_search_filters_official_fields_only():
    result = ema_medicines.parse_payload(_payload(), term="animal health")
    assert result.count == 1
    assert result.records[0]["record_id"] == "EMEA/V/C/000002"


def test_scheduler_registers_daily_ema_job_and_preserves_feed_cursor(tmp_path: Path):
    conn = db.connect(tmp_path / "ema-scheduler.sqlite")
    repository.ensure_source_states(conn)
    assert config.source_spec("ema_medicines").cadence == "daily"
    with patch("pharmadrone.scheduler.sources.ema_medicines.fetch", return_value=ema_medicines.parse_payload(_payload())):
        fetched = sources.fetch_ema_medicines(conn, repository.source_state(conn, "ema_medicines"), config.guardrails(), force=True)
    assert len(fetched["records"]) == 2
    assert fetched["cursor_after"] == "feed:2026-07-15T06:02:43Z"
    assert fetched["watermark_after"] == "2026-07-14"


def test_ingested_ema_facts_project_into_memory_without_problem_relationship(tmp_path: Path):
    conn = db.connect(tmp_path / "ema-memory.sqlite")
    records = ema_medicines.parse_payload(_payload()).records
    with conn.transaction():
        repository.ingest_source_records(conn, run_id="ema-run-1", source_name="ema_medicines", records=records)
    metrics = pharmaceutical_memory.sync_ema_medicines(conn)
    assert metrics["entities"] == 6
    relationships = [dict(row) for row in conn.execute("SELECT * FROM memory_relationships").fetchall()]
    assert len(relationships) == 4
    assert {row["relationship_type"] for row in relationships} == {"ema_authorisation_holder_for", "has_active_substance"}
    assert all(row["evidence_status"] == "official EMA catalogue fact" for row in relationships)
    assert not any("problem" in row["relationship_type"] for row in relationships)


def test_workflow_exposes_manual_ema_refresh():
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/pharmatune_refresh.yml").read_text()
    assert "- ema_medicines" in workflow
