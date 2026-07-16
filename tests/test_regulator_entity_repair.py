import json

from pharmadrone import db
from pharmadrone.connectors import ema_medicines, ema_opportunities, mhra_alerts
from pharmadrone.pipeline import discover, opportunity_index
from pharmadrone.scheduler import repository


def test_legacy_mhra_title_uses_description_company_and_title_product():
    result = mhra_alerts.parse_payload({"results": [{
        "title": "Specific batches of Torrent Tutor (Duloxetine) Gastro-resistant Capsules",
        "link": "/drug-device-alerts/torrent-tutor-recall", "alert_type": ["medicines-recall-notification"],
        "description": "Torrent Pharma UK Limited is recalling specific batches due to an impurity.",
        "public_timestamp": "2026-07-01T00:00:00Z",
    }]})
    entities = result.records[0]["entities"]
    assert entities["company"] == "Torrent Pharma UK Limited"
    assert entities["product"] == "Torrent Tutor (Duloxetine) Gastro-resistant Capsules"


def test_old_mhra_parenthesised_company_and_uk_region_are_recovered():
    result = mhra_alerts.parse_payload({"results": [{
        "title": "Recall of Avandia 4mg, 8mg, Avandamet 1mg/500mg",
        "link": "/drug-device-alerts/drug-alert-recall-of-avandia",
        "alert_type": ["medicines-recall-notification"],
        "description": "(GlaxoSmithKline (GSK)) Recall of all undispensed UK packs purchased through approved suppliers.",
        "public_timestamp": "2014-12-17T00:00:00Z",
    }]})
    recall = result.records[0]
    assert recall["entities"]["company"] == "GlaxoSmithKline (GSK)"
    assert recall["entities"]["product"] == "Avandia 4mg, 8mg, Avandamet 1mg/500mg"
    assert recall["entities"]["region"] == "United Kingdom"
    assert recall["region_hint"] == "United Kingdom"


def test_ema_company_is_joined_only_when_catalogue_holder_is_unique(tmp_path):
    conn = db.connect(tmp_path / "ema-company.db")
    catalogue = ema_medicines.parse_payload({"data": [{
        "category": "Human", "name_of_medicine": "Evrysdi", "ema_product_number": "EMEA/H/C/005249",
        "active_substance": "risdiplam", "marketing_authorisation_developer_applicant_holder": "Roche Registration GmbH",
        "medicine_url": "https://www.ema.europa.eu/en/medicines/human/EPAR/evrysdi",
    }]}).records
    repository.ingest_source_records(conn, run_id="catalogue", source_name="ema_medicines", records=catalogue)
    event = ema_opportunities.parse_payload("ema_dhpc", {"data": [{
        "name_of_medicine": "Evrysdi", "active_substances": "risdiplam", "dhpc_type": "Quality defect",
        "dhpc_url": "https://www.ema.europa.eu/en/medicines/dhpc/evrysdi-0",
    }]}).records
    candidates, _ = discover.discover_candidates(event, min_cluster_evidence=1)
    assert opportunity_index.enrich_ema_companies(conn, candidates) == 1
    assert candidates[0]["company"] == "Roche Registration GmbH"


def test_regulator_repair_removes_product_copied_into_company_and_relabels_mhra(tmp_path):
    conn = db.connect(tmp_path / "repair.db")
    product = "Example 50mg Tablets"
    data = {"company": product, "product": product, "evidence": [{
        "source_type": "recall", "source_name": "Legacy recall", "url": "https://www.gov.uk/drug-device-alerts/example",
        "entities": {"regulator": "MHRA", "company": product, "product": product},
    }]}
    conn.execute(
        "INSERT INTO opportunity_index (stable_lead_id,company,product,source_type,source_id,grade,data_json,first_seen_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("legacy", product, product, "FDA recall", "EL-1", "C", json.dumps(data), "2026-07-01"),
    )
    assert opportunity_index.repair_regulator_source_labels(conn) == 1
    counts = opportunity_index.repair_regulator_entities(conn)
    row = dict(conn.execute("SELECT company,product,source_type FROM opportunity_index").fetchone())
    assert counts["regulator_entities"] == 1
    assert row == {"company": "", "product": product, "source_type": "MHRA medicine recall"}


def test_legacy_opportunity_evidence_url_is_restored_from_source_record(tmp_path):
    conn = db.connect(tmp_path / "evidence-url.db")
    source_id = "drug-alert-recall-of-avandia"
    official_url = f"https://www.gov.uk/drug-device-alerts/{source_id}"
    recall = mhra_alerts.parse_payload({"results": [{
        "title": "Recall of Avandia", "link": f"/drug-device-alerts/{source_id}",
        "alert_type": ["medicines-recall-notification"],
        "description": "(GlaxoSmithKline (GSK)) Recall of all undispensed UK packs.",
        "public_timestamp": "2014-12-17T00:00:00Z",
    }]}).records[0]
    repository.ingest_source_records(conn, run_id="mhra", source_name="mhra_medicine_recalls", records=[recall])
    legacy = {"evidence": [{"record_id": source_id, "entities": {"regulator": "MHRA"}}]}
    conn.execute(
        "INSERT INTO opportunity_index (stable_lead_id,source_id,data_json,evidence_links_json,first_seen_at) "
        "VALUES (?,?,?,?,?)",
        ("avandia", source_id, json.dumps(legacy), json.dumps([source_id]), "2026-07-01"),
    )
    assert opportunity_index.repair_evidence_urls(conn) == 1
    row = dict(conn.execute(
        "SELECT data_json,evidence_links_json FROM opportunity_index WHERE stable_lead_id='avandia'"
    ).fetchone())
    data = json.loads(row["data_json"])
    assert data["evidence"][0]["url"] == official_url
    assert data["evidence"][0]["entities"]["official_source_url"] == official_url
    assert json.loads(row["evidence_links_json"]) == [official_url]
