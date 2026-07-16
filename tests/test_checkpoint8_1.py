import json

from pharmadrone import db
from pharmadrone.connectors import mhra_alerts
from pharmadrone.pipeline import opportunity_index
from pharmadrone.scheduler import repository


def _mhra_record():
    return mhra_alerts.parse_payload({"results": [{
        "title": "Recall of Avandia 4mg, 8mg, Avandamet 1mg/500mg",
        "link": "/drug-device-alerts/drug-alert-recall-of-avandia",
        "alert_type": ["medicines-recall-notification"],
        "description": "(GlaxoSmithKline (GSK)) Recall of all undispensed UK packs.",
        "public_timestamp": "2014-12-17T00:00:00Z",
    }]}).records[0]


def test_8_1b_legacy_entities_are_repaired_from_authoritative_source_record(tmp_path):
    conn = db.connect(tmp_path / "entities.db")
    official = _mhra_record()
    repository.ingest_source_records(conn, run_id="mhra", source_name="mhra_medicine_recalls", records=[official])
    source_id = official["record_id"]
    legacy = {"evidence": [{
        "record_id": source_id,
        "entities": {"regulator": "MHRA", "product": "Recall of Avandia"},
    }]}
    conn.execute(
        "INSERT INTO opportunity_index (stable_lead_id,company,product,source_type,source_id,data_json) "
        "VALUES (?,?,?,?,?,?)",
        ("avandia", "", "Recall of Avandia", "MHRA medicine recall", source_id, json.dumps(legacy)),
    )

    counts = opportunity_index.repair_regulator_entities(conn)
    row = dict(conn.execute(
        "SELECT company,product,region,data_json FROM opportunity_index WHERE stable_lead_id='avandia'"
    ).fetchone())

    assert counts["regulator_entities"] == 1
    assert row["company"] == "GlaxoSmithKline (GSK)"
    assert row["product"] == "Avandia 4mg, 8mg, Avandamet 1mg/500mg"
    assert row["region"] == "United Kingdom"
    assert json.loads(row["data_json"])["evidence"][0]["entities"]["company"] == "GlaxoSmithKline (GSK)"


def test_8_1a_quality_metrics_expose_missing_fields_without_fabrication(tmp_path):
    conn = db.connect(tmp_path / "quality.db")
    conn.execute(
        "INSERT INTO opportunity_index (stable_lead_id,company,product,problem_category,source_type,source_id,"
        "region,score,grade,evidence_links_json,data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("complete", "GSK", "Avandia", "recall", "MHRA medicine recall", "EL-1",
         "United Kingdom", 41, "C", json.dumps(["https://www.gov.uk/example"]), "{}"),
    )
    conn.execute(
        "INSERT INTO opportunity_index (stable_lead_id,company,product,problem_category,source_type,source_id,"
        "region,evidence_links_json,data_json) VALUES (?,?,?,?,?,?,?,?,?)",
        ("incomplete", "", "Example", "", "EMA medicine shortage", "EMA-1", "", json.dumps(["EMA-1"]), "{}"),
    )

    quality = opportunity_index.regulator_data_quality(conn)

    assert quality["total"] == 2
    assert quality["missing_company"] == 1
    assert quality["missing_official_link"] == 1
    assert quality["missing_score_or_grade"] == 1
    ema = next(row for row in quality["sources"] if row["source_type"] == "EMA medicine shortage")
    assert ema["missing_region"] == 1
    assert ema["missing_problem"] == 1


def test_8_1d_builds_truthful_sales_brief_without_claiming_full_report(tmp_path):
    conn = db.connect(tmp_path / "brief.db")
    data = {"evidence": [{"record_id": "EL-1", "entities": {"regulator": "MHRA"}}]}
    conn.execute(
        "INSERT INTO opportunity_index (stable_lead_id,company,product,problem_category,source_type,source_id,"
        "region,score,grade,has_full_report,data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("brief", "GSK", "Avandia", "medicine recall", "MHRA medicine recall", "EL-1",
         "United Kingdom", 41, "C", 0, json.dumps(data)),
    )

    assert opportunity_index.build_sales_qualification_briefs(conn) == 1
    row = dict(conn.execute(
        "SELECT has_full_report,data_json FROM opportunity_index WHERE stable_lead_id='brief'"
    ).fetchone())
    brief = json.loads(row["data_json"])["sales_qualification_brief"]

    assert row["has_full_report"] == 0
    assert brief["target_account"] == "GSK"
    assert brief["qualification_status"] == "Human validation required"
    assert "does not prove" in brief["commercial_limit"]
    assert opportunity_index.build_sales_qualification_briefs(conn) == 0
