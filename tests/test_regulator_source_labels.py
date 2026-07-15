from pharmadrone import db
from pharmadrone.pipeline import opportunity_index


def test_mhra_recall_keeps_its_regulator_label():
    opportunity = {"evidence": [{
        "source_type": "recall", "source_name": "MHRA Medicines Recalls",
        "entities": {"regulator": "MHRA"},
    }]}
    assert opportunity_index.source_type(opportunity) == "MHRA medicine recall"


def test_legacy_mhra_rows_are_relabelled_without_touching_fda(tmp_path):
    conn = db.connect(tmp_path / "labels.sqlite")
    conn.execute(
        "INSERT INTO source_records (source_type,source_id,source_name,content_checksum,record_json,first_seen_at,last_seen_at,last_changed_at,active) "
        "VALUES (?,?,?,?,?,?,?,?,1)",
        ("recall", "EL-UK-1", "mhra_medicine_recalls", "x", "{}", "2026-01-01", "2026-01-01", "2026-01-01"),
    )
    base = {
        "company": "Example", "product": "Product", "problem_category": "recall", "region": "United Kingdom",
        "score": 70, "grade": "B", "lead_status": "new", "evidence": [{"source_type": "recall", "source_name": "FDA"}],
    }
    first = {**base, "stable_lead_id": "mhra-lead", "source_id": "EL-UK-1"}
    second = {**base, "stable_lead_id": "fda-lead", "source_id": "D-US-1"}
    for row in (first, second):
        conn.execute(
            "INSERT INTO opportunity_index (stable_lead_id,company,product,problem_category,source_type,source_id,region,score,grade,lead_status,data_json,created_at,last_seen_at,last_checked_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["stable_lead_id"], row["company"], row["product"], row["problem_category"], "FDA recall", row["source_id"],
             row["region"], row["score"], row["grade"], row["lead_status"], "[]", "2026-01-01", "2026-01-01", "2026-01-01"),
        )
    assert opportunity_index.repair_regulator_source_labels(conn) == 1
    labels = {row["stable_lead_id"]: row["source_type"] for row in conn.execute("SELECT stable_lead_id,source_type FROM opportunity_index").fetchall()}
    assert labels == {"mhra-lead": "MHRA medicine recall", "fda-lead": "FDA recall"}
