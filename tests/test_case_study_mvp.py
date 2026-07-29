from pathlib import Path
from unittest.mock import patch

import pytest

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
        report = case_study_mvp.build(
            conn,
            "Poor solubility",
            "Company opportunity pitch",
            company="Example Pharma",
            mode="Company-specific pitch",
        )
    for section in case_study_mvp.REPORT_SECTIONS:
        assert f"## {section}" in report["markdown"]
    assert report["markdown"].startswith(
        "# Example Pharma — Poor solubility opportunity case study"
    )
    assert "## Target company" in report["markdown"]
    assert "## Potential pitch angle" in report["markdown"]
    assert "potential fit" in report["markdown"]
    assert "may justify review" in report["markdown"]
    assert "possible opportunity area" in report["markdown"]
    assert "requires analyst validation" in report["markdown"]
    assert "Example Product" in report["markdown"]
    assert "Research Council" in report["markdown"]
    assert report["direct_patents"] == []
    assert report["patent_routes"]
    assert report["case_readiness"] == "Pitch-ready draft"
    conn.close()


def test_company_is_required_only_for_company_specific_pitch(tmp_path):
    conn = db.connect(tmp_path / "company-required.db")
    with pytest.raises(ValueError, match="Target company is required"):
        case_study_mvp.build(
            conn,
            "Poor solubility",
            "Company opportunity pitch",
            mode="Company-specific pitch",
        )
    exploration = case_study_mvp.build(
        conn,
        "Poor solubility",
        "Company opportunity pitch",
        mode="Theme-only exploration",
    )
    assert exploration["mode"] == "Theme-only exploration"
    assert exploration["exploration_warning"] == (
        "Exploration only — not suitable for company pitch."
    )
    conn.close()


def test_company_and_theme_are_both_required_for_specific_evidence(tmp_path):
    conn = db.connect(tmp_path / "company-filter.db")
    _seed(conn)
    conn.execute(
        """INSERT INTO opportunity_index
        (stable_lead_id,company,product,molecule,problem_category,source_type,source_id,
         region,evidence_links_json,score,grade,last_updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "lead-other-company", "Other Pharma", "Other Product", "Other API",
            "Poor solubility", "research", "SOURCE-OTHER", "Global",
            '["https://example.org/other"]', 91, "A", "2026-07-29T11:00:00+00:00",
        ),
    )
    conn.commit()
    report = case_study_mvp.build(
        conn,
        "Poor solubility",
        "Company opportunity pitch",
        company="Example Pharma",
        mode="Company-specific pitch",
    )
    assert [row["stable_lead_id"] for row in report["opportunities"]] == [
        "lead-solubility-1"
    ]
    assert all(row.get("name") != "Other Product" for row in report["products"])
    assert any(row.get("title") == "Other Product" for row in report["theme_sources"])
    assert report["case_readiness"] == "Pitch-ready draft"
    conn.close()


def test_retained_company_aliases_expand_company_specific_search(tmp_path):
    conn = db.connect(tmp_path / "company-alias.db")
    now = "2026-07-29T10:00:00+00:00"
    conn.execute(
        """INSERT INTO organisation_profiles
        (organisation_profile_id,canonical_name,normalized_name,organisation_type,
         identity_status,evidence_status,last_verified_at,next_review_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (
            "org-example", "Example Pharmaceuticals", "example pharmaceuticals",
            "pharmaceutical-company", "source-derived", "official company source",
            now, now,
        ),
    )
    conn.execute(
        """INSERT INTO organisation_aliases
        (organisation_alias_id,organisation_profile_id,alias_name,normalized_alias,
         alias_type,source_type,source_record_id,evidence_url,evidence_status,
         verification_status,observed_at,last_verified_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "alias-example", "org-example", "Example Pharma", "example pharma",
            "trading-name", "official-company-page", "COMPANY-1",
            "https://example.org/company", "official company source",
            "human-verified", now, now,
        ),
    )
    conn.execute(
        """INSERT INTO opportunity_index
        (stable_lead_id,company,product,molecule,problem_category,source_type,source_id,
         region,evidence_links_json,score,grade,last_updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "lead-alias", "Example Pharma", "Alias Product", "Alias API",
            "Particle size and dissolution", "research", "SOURCE-ALIAS", "Global",
            '["https://example.org/alias-evidence"]', 80, "A", now,
        ),
    )
    conn.commit()
    report = case_study_mvp.build(
        conn,
        "Particle properties",
        "Company opportunity pitch",
        company="Example Pharmaceuticals",
    )
    assert report["company"]["known_company"] is True
    assert report["company_terms"] == ["Example Pharmaceuticals", "Example Pharma"]
    assert report["opportunities"][0]["stable_lead_id"] == "lead-alias"
    assert report["case_readiness"] == "Pitch-ready draft"
    conn.close()


def test_manual_company_entry_and_retained_company_options(tmp_path):
    conn = db.connect(tmp_path / "company-options.db")
    _seed(conn)
    options = case_study_mvp.company_options(conn, "Example", limit=10)
    assert [row["company_name"] for row in options] == ["Example Pharma"]
    report = case_study_mvp.build(
        conn,
        "Poor solubility",
        "Company opportunity pitch",
        company="Example Pharma",
    )
    assert report["company"]["requested_name"] == "Example Pharma"
    assert report["company"]["identity_status"] == "manual company entry; requires review"
    conn.close()


def test_theme_only_evidence_is_labelled_and_never_makes_pitch_ready(tmp_path):
    conn = db.connect(tmp_path / "prospecting-shell.db")
    _seed(conn)
    report = case_study_mvp.build(
        conn,
        "Poor solubility",
        "Company opportunity pitch",
        company="Unrepresented Pharma",
        mode="Company-specific pitch",
    )
    assert report["case_readiness"] == "Prospecting shell only"
    assert report["company_specific_count"] == 0
    assert report["theme_sources"]
    assert all(
        row["source_status"] == "Theme-level evidence only — not company-specific."
        for row in report["theme_sources"]
    )
    assert "Theme-level evidence only — not company-specific." in report["markdown"]
    assert (
        "No retained company-specific evidence was found for this target. "
        "The report is a prospecting shell only."
    ) in report["markdown"]
    conn.close()


def test_company_specific_readiness_can_be_partial_or_empty(tmp_path):
    conn = db.connect(tmp_path / "readiness.db")
    conn.execute(
        """INSERT INTO funding_awards
        (funding_award_id,funding_type,funder_name,recipient_name,award_id,programme_name,
         source_type,source_name,source_id,evidence_url,evidence_status,validation_status,
         last_verified_at,next_review_at,active)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (
            "grant-target", "Research grant", "Research Council", "Target Pharma",
            "AWARD-TARGET", "Spray drying research", "paper", "OpenAlex", "WORK-TARGET",
            "https://example.org/target", "Published funding metadata",
            "Requires review", "2026-07-29T10:00:00+00:00",
            "2026-10-29T10:00:00+00:00",
        ),
    )
    conn.commit()
    partial = case_study_mvp.build(
        conn,
        "Spray drying",
        "Partner/BD outreach brief",
        company="Target Pharma",
        mode="Company-specific pitch",
    )
    assert partial["case_readiness"] == "Partial company evidence"
    empty = case_study_mvp.build(
        conn,
        "Modified release",
        "Company opportunity pitch",
        company="Missing Pharma",
        mode="Company-specific pitch",
    )
    assert empty["case_readiness"] == "Not enough evidence"
    conn.close()


def test_case_study_is_resilient_when_patent_and_stored_sources_are_empty(tmp_path):
    conn = db.connect(tmp_path / "empty-case-study.db")
    report = case_study_mvp.build(
        conn, "Unrepresented problem", "Product rescue",
        mode="Theme-only exploration",
    )
    assert "No patent-source results were returned" in report["markdown"]
    assert "Generated official search routes" in report["markdown"]
    assert "Internal retained records: ready" in report["markdown"]
    assert "EPO OPS:" in report["markdown"]
    assert "USPTO / PatentsView:" in report["markdown"]
    assert "Tavily fallback:" in report["markdown"]
    assert "No matching product/API evidence was found" in report["markdown"]
    assert any(item.startswith("No reviewed canonical link exists yet") for item in report["limitations"])
    assert report["case_readiness"] == "Not enough retained evidence yet"
    conn.close()


def test_approved_case_themes_expand_deterministically():
    assert case_study_mvp.CASE_TYPES == (
        "Company opportunity pitch", "Product rescue scan",
        "Formulation technology fit", "Lifecycle/patent landscape",
        "Partner/BD outreach brief",
    )
    assert case_study_mvp.THEMES == (
        "Particle properties", "Poor solubility", "Dissolution innovation",
        "Amorphous solid dispersion", "Modified release",
        "Bioavailability enhancement", "Particle size reduction",
        "Nanocrystals / nanosuspensions", "Cocrystals / salt forms",
        "Lipid-based formulations", "Spray drying", "Hot-melt extrusion",
    )
    particle = case_study_mvp.expand_query_terms("Particle properties")
    assert {
        "particle size", "particle morphology", "particle engineering",
        "crystal habit", "polymorph", "solid state", "micronization",
        "nanomilling", "wet milling", "dry milling", "nanocrystal",
        "nanosuspension", "spray drying", "hot-melt extrusion", "flowability",
        "powder properties", "compressibility", "dissolution", "solubility",
        "bioavailability",
    }.issubset(set(particle))
    poor = case_study_mvp.expand_query_terms("Poor solubility")
    assert {
        "dissolution", "bioavailability", "ASD", "nanosuspension",
        "cocrystal", "lipid formulation",
    }.issubset(set(poor))
    dissolution = case_study_mvp.expand_query_terms("Dissolution innovation")
    assert {
        "dissolution", "drug release", "amorphous solid dispersion",
        "modified release",
    }.issubset(set(dissolution))
    assert case_study_mvp.expand_query_terms("Unmapped theme") == ["Unmapped theme"]


def test_expanded_terms_retrieve_and_rank_related_retained_evidence(tmp_path):
    conn = db.connect(tmp_path / "expanded.db")
    conn.execute(
        """INSERT INTO opportunity_index
        (stable_lead_id,company,product,molecule,problem_category,source_type,source_id,
         region,evidence_links_json,score,grade,last_updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "lead-expanded", "Formulation Co", "Candidate X", "API X",
            "Dissolution rate and bioavailability limitation", "research", "EXP-1",
            "Global", '["https://example.org/expanded"]', 76, "B",
            "2026-07-30T10:00:00+00:00",
        ),
    )
    conn.commit()
    report = case_study_mvp.build(
        conn, "Poor solubility", "Formulation innovation",
        mode="Theme-only exploration",
    )
    assert report["evidence_counts"]["opportunities"] == 1
    assert report["evidence_counts"]["products_apis"] == 2
    assert {"dissolution", "dissolution rate", "bioavailability"}.issubset(
        set(report["opportunities"][0]["matched_terms"])
    )
    assert "Expanded search terms used" in report["markdown"]
    assert "Evidence was retrieved using the case theme and related formulation/problem terms." in report["markdown"]
    assert report["case_readiness"] == "Ready for analyst review"
    conn.close()


def test_one_retained_bucket_is_partial_evidence(tmp_path):
    conn = db.connect(tmp_path / "partial.db")
    conn.execute(
        """INSERT INTO funding_awards
        (funding_award_id,funding_type,funder_name,recipient_name,award_id,programme_name,
         source_type,source_name,source_id,evidence_url,evidence_status,validation_status,
         last_verified_at,next_review_at,active)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)""",
        (
            "grant-partial", "Research grant", "Research Council", "University",
            "GRANT-ASD", "Amorphous solid dispersion research", "paper", "OpenAlex",
            "WORK-ASD", "https://example.org/asd-grant", "Published funding metadata",
            "Requires review", "2026-07-30T10:00:00+00:00",
            "2026-10-30T10:00:00+00:00",
        ),
    )
    conn.commit()
    report = case_study_mvp.build(
        conn, "Poor solubility", "Formulation innovation",
        mode="Theme-only exploration",
    )
    assert report["evidence_counts"]["research_grants"] == 1
    assert report["case_readiness"] == "Partial evidence only"
    conn.close()


def test_expanded_terms_search_problem_solution_and_relationship_fields(tmp_path):
    conn = db.connect(tmp_path / "technology-expanded.db")
    now = "2026-07-30T10:00:00+00:00"
    conn.execute(
        """INSERT INTO pharmaceutical_problems
        (problem_id,canonical_key,display_name,taxonomy_term_id,definition,identity_status,
         evidence_status,last_verified_at,next_review_at)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            "problem-absorption", "absorption-limitation", "Absorption limitation",
            "problem-poor-solubility", "A bioavailability and dissolution rate limitation.",
            "controlled", "curated evidence", now, now,
        ),
    )
    conn.execute(
        """INSERT INTO technology_solutions
        (technology_id,canonical_key,display_name,taxonomy_term_id,solution_type_term_id,
         mechanism_summary,scope_note,maturity_status,identity_status,evidence_status,
         last_verified_at,next_review_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "solution-nanocrystal", "nanocrystal-platform", "Nanocrystal platform",
            "solution-domain-formulation", "solution-type-technology",
            "Particle size reduction may increase dissolution.", "Retained test evidence.",
            "development", "controlled", "curated evidence", now, now,
        ),
    )
    conn.execute(
        """INSERT INTO technology_problem_relationships
        (relationship_id,technology_id,problem_id,relationship_type,relationship_statement,
         source_type,source_id,evidence_url,evidence_status,inference_status,confidence_score,
         confidence_basis,verified_at,next_review_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            "relationship-nanocrystal-absorption", "solution-nanocrystal",
            "problem-absorption", "addresses",
            "Reported nanocrystal use for bioavailability enhancement.", "publication",
            "PUB-1", "https://example.org/nanocrystal", "published evidence",
            "reported", 0.8, "Source-reported relationship", now, now,
        ),
    )
    conn.commit()
    report = case_study_mvp.build(
        conn, "Poor solubility", "Formulation innovation",
        mode="Theme-only exploration",
    )
    assert report["evidence_counts"]["pharmaceutical_problems"] == 1
    assert report["evidence_counts"]["technology_relationships"] == 1
    assert {"nanocrystal", "dissolution", "bioavailability"}.intersection(
        set(report["technologies"][0]["matched_terms"])
    )
    assert report["case_readiness"] == "Ready for analyst review"
    conn.close()


def test_exact_theme_ranks_first_and_bucket_is_capped(tmp_path):
    conn = db.connect(tmp_path / "ranking.db")
    for index in range(12):
        exact = index == 11
        conn.execute(
            """INSERT INTO opportunity_index
            (stable_lead_id,company,product,molecule,problem_category,source_type,source_id,
             region,evidence_links_json,score,grade,last_updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f"lead-rank-{index}", f"Company {index}", f"Product {index}", f"API {index}",
                "Poor solubility" if exact else "Dissolution rate limitation",
                "research", f"RANK-{index}", "Global",
                f'["https://example.org/rank-{index}"]',
                1 if exact else 99, "A", f"2026-07-{index + 1:02d}T10:00:00+00:00",
            ),
        )
    conn.commit()
    report = case_study_mvp.build(
        conn, "Poor solubility", "Formulation innovation",
        mode="Theme-only exploration",
    )
    assert len(report["opportunities"]) == case_study_mvp.BUCKET_LIMIT
    assert report["opportunities"][0]["stable_lead_id"] == "lead-rank-11"
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
    assert "data.live_patent_discovery(discovery_query)" in pages
    assert "Download Markdown" in pages
    assert "Download plain text" in pages
    assert "No reviewed canonical link exists yet" in pages
    assert 'with st.expander("Search strategy")' in pages
    assert "Generic theme-only evidence is not enough for a pitch." in pages
    assert "Open company" in pages
    assert "Open related Products" in pages
    assert "Open Patent & Innovation Discovery" in pages
    assert "Open Human Validation" in pages
    assert case_study_mvp.EXAMPLE_CASES == (
        ("Pfizer", "Poor solubility"),
        ("Novartis", "Particle properties"),
        ("AstraZeneca", "Dissolution innovation"),
        ("Roche", "Amorphous solid dispersion"),
        ("Sanofi", "Modified release"),
    )


def test_case_study_change_has_no_migration_or_direct_ui_sql():
    pages = Path("pharmatune_ui/pages.py").read_text()
    module = Path("pharmadrone/pipeline/case_study_mvp.py").read_text()
    assert "conn.execute" not in pages
    assert "LIMIT ?" in module
    assert "SELECT *" not in module
    assert "patent_source_discovery(" not in module
    assert "INSERT INTO" not in module
    assert "UPDATE " not in module
