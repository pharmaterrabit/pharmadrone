from pathlib import Path
from unittest.mock import patch

from pharmadrone import db
from pharmadrone.pipeline import case_study_mvp, commercial_intelligence
from pharmatune_ui import data


def _seed(conn) -> None:
    conn.execute(
        """INSERT INTO opportunity_index
        (stable_lead_id,company,product,molecule,problem_category,source_type,source_id,
         region,evidence_links_json,score,grade,last_updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "lead-solubility-1", "Example Pharma", "Example Product", "Example API",
            "Poor solubility", "research", "SOURCE-1", "Global",
            '["https://example.org/evidence"]', 82, "A", "2026-07-29T10:00:00+00:00",
        ),
    )
    conn.execute(
        """INSERT INTO funding_awards
        (funding_award_id,funding_type,funder_name,recipient_name,award_id,programme_name,
         source_type,source_name,source_id,evidence_url,evidence_status,validation_status,
         last_verified_at,next_review_at,active)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (
            "grant-1", "Research grant", "Research Council", "Example University", "AWARD-1",
            "Poor solubility research", "paper", "OpenAlex", "WORK-1",
            "https://example.org/grant", "Published funding metadata",
            "Recipient and scope require review", "2026-07-29T10:00:00+00:00",
            "2026-10-29T10:00:00+00:00",
        ),
    )
    conn.commit()


def test_case_study_report_has_complete_structure_and_retained_evidence(tmp_path):
    conn = db.connect(tmp_path / "case-study.db")
    _seed(conn)
    with patch(
        "pharmadrone.pipeline.patent_discovery.patent_source_discovery",
        side_effect=AssertionError("report rendering must not call the network"),
    ):
        report = case_study_mvp.build(conn, "Poor solubility", "Formulation innovation")
    for section in case_study_mvp.REPORT_SECTIONS:
        assert f"## {section}" in report["markdown"]
    assert "Example Product" in report["markdown"]
    assert "Research Council" in report["markdown"]
    assert report["direct_patents"] == []
    assert report["patent_routes"]
    conn.close()


def test_case_study_is_resilient_when_patent_and_stored_sources_are_empty(tmp_path):
    conn = db.connect(tmp_path / "empty-case-study.db")
    report = case_study_mvp.build(conn, "Unrepresented problem", "Product rescue")
    assert "No patent-source results were returned" in report["markdown"]
    assert "Generated official search routes" in report["markdown"]
    assert "Internal retained records: ready" in report["markdown"]
    assert "EPO OPS:" in report["markdown"]
    assert "USPTO / PatentsView:" in report["markdown"]
    assert "Tavily fallback:" in report["markdown"]
    assert "No matching product/API evidence was found" in report["markdown"]
    assert any(item.startswith("No reviewed canonical link exists yet") for item in report["limitations"])
    conn.close()


def test_funding_profile_is_bounded_and_source_traceable(tmp_path):
    conn = db.connect(tmp_path / "grant.db")
    _seed(conn)
    profile = commercial_intelligence.funding_profile(conn, "grant-1")
    assert profile["award_id"] == "AWARD-1"
    assert profile["evidence_url"] == "https://example.org/grant"
    assert commercial_intelligence.funding_profile(conn, "missing") is None
    conn.close()


def test_product_and_problem_drilldown_reads_preserve_evidence(tmp_path):
    path = tmp_path / "drilldowns.db"
    conn = db.connect(path)
    _seed(conn)
    conn.close()
    data.product_directory.clear()
    data.product_detail.clear()
    data.problem_directory.clear()
    data.problem_detail.clear()
    with patch("pharmatune_ui.data.connection", side_effect=lambda: db.connect(path)):
        products = data.product_directory("Example")
        product = data.product_detail("Example Product")
        problems = data.problem_directory("solubility")
        problem = data.problem_detail("Poor solubility")
    assert products[0]["name"] == "Example Product"
    assert product["opportunities"][0]["evidence_url"] == "https://example.org/evidence"
    assert problems[0]["name"] == "Poor solubility"
    assert problem["products"] == ["Example Product"]


def test_ui_registers_drilldowns_and_explicit_only_live_discovery():
    app = Path("pharmatune_ui/app.py").read_text()
    pages = Path("pharmatune_ui/pages.py").read_text()
    assert '"Case Study Builder":lambda:pages.case_study_builder(principal,_navigate)' in app
    assert '"Product Detail":lambda:pages.product_detail(_navigate)' in app
    assert '"Problem Detail":lambda:pages.problem_detail(_navigate)' in app
    assert '"Grant Detail":lambda:pages.grant_detail(_navigate)' in app
    assert "Run patent source discovery for this case" in pages
    assert "data.live_patent_discovery(query)" in pages
    assert "Download Markdown" in pages
    assert "Download plain text" in pages
    assert "No reviewed canonical link exists yet" in pages


def test_case_study_change_has_no_migration_or_direct_ui_sql():
    pages = Path("pharmatune_ui/pages.py").read_text()
    module = Path("pharmadrone/pipeline/case_study_mvp.py").read_text()
    assert "conn.execute" not in pages
    assert "LIMIT ?" in module
    assert "SELECT *" not in module
    assert "patent_source_discovery(" not in module
    assert "INSERT INTO" not in module
    assert "UPDATE " not in module
