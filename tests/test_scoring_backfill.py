import json

from pharmadrone import db
from pharmadrone.pipeline import opportunity_index
from pharmadrone.scheduler import orchestrator


def _candidate(record_id="NCT02630927"):
    evidence = [{
        "source_type": "trial", "source_category": "trial", "source_name": "ClinicalTrials.gov",
        "record_id": record_id, "title": "Terminated AG-519 study", "url": f"https://clinicaltrials.gov/study/{record_id}",
        "entities": {"trial_id": record_id, "product": "AG-519", "event_type": "TERMINATED"},
    }]
    return {"company": "Agios Pharmaceuticals, Inc.", "product": "AG-519", "region": "United Kingdom",
            "problem_category": "development discontinued", "failure_event_confirmed": True,
            "valid_target_type": "company", "evidence": evidence}


def test_scheduler_scores_new_indexed_previews(monkeypatch, tmp_path):
    conn = db.connect(tmp_path / "new-score.db")
    monkeypatch.setattr(orchestrator.discover, "discover_candidates", lambda records, min_cluster_evidence=1: ([_candidate()], {}))
    result = orchestrator._generate_opportunities(conn, _candidate()["evidence"])
    row = dict(conn.execute("SELECT score,grade,has_full_report FROM opportunity_index").fetchone())
    assert result["opportunities_created"] == 1
    assert row["score"] is not None and row["grade"] in {"A", "B", "C", "D"}
    assert row["has_full_report"] == 0


def test_backfill_scores_existing_preview_without_generating_report(tmp_path):
    conn = db.connect(tmp_path / "backfill.db")
    candidate = _candidate()
    rec = opportunity_index.make_index_record(candidate, has_full_report=False)
    db.upsert_index_record(conn, rec)
    before = dict(conn.execute("SELECT score,grade,has_full_report FROM opportunity_index").fetchone())
    assert before["score"] is None and before["grade"] == ""
    assert opportunity_index.backfill_missing_scores(conn) == 1
    after = dict(conn.execute("SELECT score,grade,has_full_report,data_json FROM opportunity_index").fetchone())
    assert after["score"] is not None and after["grade"]
    assert after["has_full_report"] == 0
    assert json.loads(after["data_json"])["score"] == after["score"]
    assert opportunity_index.backfill_missing_scores(conn) == 0
