import json
from datetime import datetime, timezone

from pharmadrone import db
from pharmadrone.pipeline import regulatory_intelligence
from pharmatune_ui import data


def _insert(conn, lead_id, company, product, problem, source, region, url, checked="2026-07-15T00:00:00+00:00"):
    conn.execute(
        "INSERT INTO opportunity_index (stable_lead_id,company,product,problem_category,source_type,source_id,"
        "region,score,grade,evidence_links_json,last_checked_at,data_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (lead_id, company, product, problem, source, lead_id, region, 41, "C", json.dumps([url] if url else []), checked, "{}"),
    )


def test_8_4a_event_taxonomy_is_regulator_specific_and_deterministic():
    assert regulatory_intelligence.regulator("EMA medicine shortage") == "EMA"
    assert regulatory_intelligence.event_family("EMA medicine shortage") == "Medicine shortage"
    assert regulatory_intelligence.event_family("MHRA medicine recall") == "Recall / quality defect"
    assert regulatory_intelligence.event_family("EMA safety communication") == "Safety communication"
    assert regulatory_intelligence.event_family("EMA safety referral") == "Safety review / referral"
    assert regulatory_intelligence.event_family("EMA post-authorisation withdrawal") == "Post-authorisation withdrawal"


def test_8_4a_database_filters_paginate_only_regulatory_events(tmp_path, monkeypatch):
    path = tmp_path / "regulatory.db"
    conn = db.connect(path)
    _insert(conn, "mhra", "GSK", "Avandia", "recall", "MHRA medicine recall", "United Kingdom", "https://www.gov.uk/avandia")
    _insert(conn, "ema", "Example MAH", "Drug A", "supply / availability signal", "EMA medicine shortage", "European Union", "https://ema.europa.eu/shortage")
    _insert(conn, "trial", "Sponsor", "Drug B", "terminated trial", "ClinicalTrials.gov trial", "United States", "https://clinicaltrials.gov/study/x")
    conn.commit(); conn.close()
    monkeypatch.setattr(data, "connection", lambda: db.connect(path))
    data.regulatory_page.clear(); data.regulatory_facets.clear()
    result = data.regulatory_page(regulator="EMA", event_family="Medicine shortage")
    facets = data.regulatory_facets()
    data.regulatory_page.clear(); data.regulatory_facets.clear()
    assert result["total"] == 1
    assert result["rows"][0]["stable_lead_id"] == "ema"
    assert "ClinicalTrials.gov trial" not in facets["source"]
    assert set(facets["regulator"]) == {"FDA", "EMA", "MHRA"}


def test_8_4a_workspace_quality_excludes_clinical_trials(tmp_path):
    conn = db.connect(tmp_path / "quality.db")
    _insert(conn, "ema", "MAH", "Drug", "safety communication", "EMA safety communication", "European Union", "https://ema.europa.eu/a")
    _insert(conn, "trial", "Sponsor", "Drug", "terminated", "ClinicalTrials.gov trial", "United States", "https://clinicaltrials.gov/a")
    assert data.opportunity_index.regulator_data_quality(conn, include_trials=False)["total"] == 1
    assert data.opportunity_index.regulator_data_quality(conn, include_trials=True)["total"] == 2


def test_8_4b_official_evidence_supports_string_and_structured_links():
    assert regulatory_intelligence.evidence_urls({
        "evidence_links_json": json.dumps(["https://ema.europa.eu/a", {"url": "https://www.gov.uk/b"}, "not-a-url"])
    }) == ["https://ema.europa.eu/a", "https://www.gov.uk/b"]


def test_8_4c_action_routes_do_not_claim_commercial_intent():
    recall = regulatory_intelligence.action_route({"source_type": "FDA recall", "problem_category": "impurity issue"})
    shortage = regulatory_intelligence.action_route({"source_type": "EMA medicine shortage"})
    safety = regulatory_intelligence.action_route({"source_type": "EMA safety communication"})
    assert recall["responsible_function"] == "Quality / CMC"
    assert shortage["responsible_function"] == "Supply Chain / Procurement"
    assert safety["responsible_function"] == "Pharmacovigilance / Regulatory Affairs"
    assert "does not prove" in recall["commercial_boundary"]


def test_8_4c_freshness_has_current_review_due_stale_and_missing_states():
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    assert regulatory_intelligence.freshness("2026-07-15T00:00:00+00:00", now) == "Current"
    assert regulatory_intelligence.freshness("2026-07-01T00:00:00+00:00", now) == "Review due"
    assert regulatory_intelligence.freshness("2026-05-01T00:00:00+00:00", now) == "Stale"
    assert regulatory_intelligence.freshness("", now) == "Review date missing"


def test_8_4d_app_exposes_workspace_and_hidden_detail_route():
    text = open("pharmatune_ui/app.py", encoding="utf-8").read()
    assert '"Regulatory Signals":lambda:pages.regulatory_signals(_navigate)' in text
    assert 'HIDDEN_ROUTE_PARENT["Regulatory Detail"] = "Regulatory Signals"' in text
