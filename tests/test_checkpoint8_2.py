import json

from pharmadrone import db
from pharmadrone.pipeline import opportunity_index
from pharmatune_ui import data


def _record(**overrides):
    record = {
        "company": "Example Pharma",
        "product": "Example tablets",
        "problem_category": "medicine recall",
        "source_type": "MHRA medicine recall",
        "evidence_links_json": json.dumps(["https://www.gov.uk/example"]),
    }
    record.update(overrides)
    return record


def test_8_2a_ready_target_is_p1_with_quality_contact_route():
    result = opportunity_index.commercial_qualification(_record())

    assert result["priority_tier"] == "P1 · Ready to qualify"
    assert result["readiness"] == "Ready to qualify"
    assert result["recommended_contact_role"] == "Quality / CMC"
    assert result["missing_requirements"] == []
    assert "does not" not in result["contact_rationale"].lower()
    assert "not commercial urgency" in result["qualification_basis"]


def test_8_2a_missing_company_is_p2_account_research_without_fabrication():
    result = opportunity_index.commercial_qualification(_record(
        company="", problem_category="supply / availability signal",
        source_type="EMA medicine shortage",
    ))

    assert result["priority_tier"] == "P2 · Account research"
    assert result["recommended_contact_role"] == "Supply Chain / Procurement"
    assert result["missing_requirements"] == ["responsible company / authorisation holder"]


def test_8_2a_missing_official_link_is_p3_evidence_repair():
    result = opportunity_index.commercial_qualification(_record(evidence_links_json="[]"))

    assert result["priority_tier"] == "P3 · Evidence repair"
    assert "working official evidence link" in result["missing_requirements"]


def test_8_2b_database_filters_and_orders_qualification_rows(tmp_path, monkeypatch):
    path = tmp_path / "commercial.db"
    conn = db.connect(path)
    rows = [
        ("p3", "Example Pharma", "Broken evidence", "recall", "FDA recall", "US", "[]"),
        ("p2", "", "Short product", "supply / availability signal", "EMA medicine shortage", "European Union", json.dumps(["https://ema.europa.eu/example"])),
        ("p1", "Example Pharma", "Recall product", "medicine recall", "MHRA medicine recall", "United Kingdom", json.dumps(["https://www.gov.uk/example"])),
    ]
    for lead_id, company, product, problem, source, region, links in rows:
        conn.execute(
            "INSERT INTO opportunity_index (stable_lead_id,company,product,problem_category,source_type,"
            "source_id,region,score,grade,evidence_links_json,data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (lead_id, company, product, problem, source, lead_id, region, 41, "C", links, "{}"),
        )
    conn.commit()
    conn.close()

    monkeypatch.setattr(data, "connection", lambda: db.connect(path))
    data.opportunity_page.clear()
    p1 = data.opportunity_page(priority="P1 · Ready to qualify")
    shortage = data.opportunity_page(contact_role="Supply Chain / Procurement")
    all_rows = data.opportunity_page()
    data.opportunity_page.clear()

    assert [row["stable_lead_id"] for row in p1["rows"]] == ["p1"]
    assert [row["stable_lead_id"] for row in shortage["rows"]] == ["p2"]
    assert [row["stable_lead_id"] for row in all_rows["rows"]] == ["p1", "p2", "p3"]


def test_8_2c_scheduler_brief_persists_contact_route_and_gaps(tmp_path):
    conn = db.connect(tmp_path / "brief.db")
    conn.execute(
        "INSERT INTO opportunity_index (stable_lead_id,company,product,problem_category,source_type,source_id,"
        "region,score,grade,evidence_links_json,data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("trial", "Agios", "AG-519", "terminated trial", "ClinicalTrials.gov trial", "NCT02630927",
         "United States", 41, "C", json.dumps(["https://clinicaltrials.gov/study/NCT02630927"]), "{}"),
    )

    assert opportunity_index.build_sales_qualification_briefs(conn) == 1
    payload = json.loads(conn.execute(
        "SELECT data_json FROM opportunity_index WHERE stable_lead_id='trial'"
    ).fetchone()["data_json"])["sales_qualification_brief"]

    assert payload["priority_tier"] == "P1 · Ready to qualify"
    assert payload["recommended_contact_role"] == "Clinical Development / Business Development"
    assert payload["missing_requirements"] == []
    assert opportunity_index.build_sales_qualification_briefs(conn) == 0
