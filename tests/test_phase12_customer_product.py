from pathlib import Path
from unittest.mock import patch

import pytest

from pharmadrone import db
from pharmadrone.pipeline import customer_product
from pharmadrone.scheduler import config

REAL_CONNECT = db.connect

ANALYST = {"role": "analyst_reviewer", "display_name": "Phase 12 Analyst", "organisation_id": "", "workspace_id": ""}
EXECUTIVE = {"role": "read_only_executive", "display_name": "Executive", "organisation_id": "", "workspace_id": ""}


def _connect(path):
    return lambda *args, **kwargs: REAL_CONNECT(path)


def test_phase12_schema_and_daily_alert_job_are_installed(tmp_path):
    conn = db.connect(tmp_path / "phase12.db")
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {
        "customer_saved_lists", "customer_saved_items", "customer_alert_rules",
        "customer_alert_events", "customer_exports", "customer_activity_events",
    }.issubset(tables)
    assert config.source_spec("customer_alerts").cadence == "daily"
    assert conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()["version"] >= 13


def test_saved_lists_are_scope_isolated_and_read_only_role_cannot_write(tmp_path):
    path = tmp_path / "lists.db"
    db.connect(path).close()
    with patch("pharmadrone.pipeline.customer_product.db.connect", side_effect=_connect(path)):
        list_id = customer_product.create_list(ANALYST, "Priority accounts", "BD qualification")
        customer_product.add_item(
            ANALYST, list_id, record_type="organisation", record_id="org-1",
            record_label="Example Pharma", source_url="https://example.com",
        )
        assert customer_product.saved_lists(ANALYST)[0]["item_count"] == 1
        other = {**ANALYST, "organisation_id": "org-other", "workspace_id": "ws-other"}
        assert customer_product.saved_lists(other) == []
        with pytest.raises(PermissionError):
            customer_product.create_list(EXECUTIVE, "Forbidden")


def test_alert_evaluation_is_stored_only_and_idempotent(tmp_path):
    path = tmp_path / "alerts.db"
    conn = db.connect(path)
    conn.execute(
        "INSERT INTO opportunity_index (stable_lead_id,company,product,problem_category,source_type,source_id,region,evidence_links_json,last_updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("lead-1", "Example Pharma", "Drug X", "medicine shortage", "EMA medicine shortage", "EMA-1", "European Union", '["https://ema.example/1"]', "2026-07-16T10:00:00+00:00"),
    )
    conn.commit(); conn.close()
    with patch("pharmadrone.pipeline.customer_product.db.connect", side_effect=_connect(path)):
        customer_product.create_alert_rule(
            ANALYST, name="EMA shortage watch", record_type="regulatory",
            search_term="shortage", source_filter="EMA", severity="high", cadence="daily",
        )
        first = customer_product.evaluate_alerts(ANALYST)
        second = customer_product.evaluate_alerts(ANALYST)
        assert first == {"rules_evaluated": 1, "alerts_created": 1}
        assert second == {"rules_evaluated": 1, "alerts_created": 0}
        inbox = customer_product.alert_inbox(ANALYST)
        assert inbox[0]["record_id"] == "lead-1"
        assert inbox[0]["severity"] == "high"


def test_external_export_excludes_every_record_without_latest_external_approval(tmp_path):
    path = tmp_path / "exports.db"
    conn = db.connect(path)
    conn.execute(
        "INSERT INTO human_audit_versions (audit_key,stable_lead_id,audit_version,audit_status,external_use_approved,created_at) VALUES (?,?,?,?,?,?)",
        ("audit-1", "lead-approved", 1, "completed", 1, "2026-07-16T10:00:00+00:00"),
    )
    conn.commit(); conn.close()
    with patch("pharmadrone.pipeline.customer_product.db.connect", side_effect=_connect(path)):
        list_id = customer_product.create_list(ANALYST, "External shortlist")
        customer_product.add_item(ANALYST, list_id, record_type="opportunity", record_id="lead-approved", record_label="Approved opportunity")
        customer_product.add_item(ANALYST, list_id, record_type="opportunity", record_id="lead-unreviewed", record_label="Unreviewed opportunity")
        customer_product.add_item(ANALYST, list_id, record_type="commercial_event", record_id="deal-1", record_label="Unapproved deal")
        payload, metadata = customer_product.export_saved_list(ANALYST, list_id, "external")
        text = payload.decode("utf-8")
        assert "Approved opportunity" in text
        assert "Unreviewed opportunity" not in text
        assert "Unapproved deal" not in text
        assert metadata["record_count"] == 1
        assert metadata["excluded_count"] == 2
        assert len(metadata["checksum"]) == 64


def test_workspace_export_policy_and_role_are_enforced(tmp_path):
    path = tmp_path / "permissions.db"
    db.connect(path).close()
    with patch("pharmadrone.pipeline.customer_product.db.connect", side_effect=_connect(path)):
        assert customer_product.capabilities(ANALYST)["can_export"] is True
        assert customer_product.capabilities(EXECUTIVE)["can_export"] is False


def test_phase12_customer_routes_truth_boundaries_and_scheduler_are_exposed():
    app = Path("pharmatune_ui/app.py").read_text()
    pages = Path("pharmatune_ui/pages.py").read_text()
    workflow = Path(".github/workflows/pharmatune_refresh.yml").read_text()
    assert '"My Workspace":lambda:pages.customer_workspace(principal,_navigate)' in app
    assert '"Saved Lists":lambda:pages.saved_lists(principal,_navigate)' in app
    assert '"Alerts":lambda:pages.customer_alerts(principal,_navigate)' in app
    assert "Latest human audit must explicitly approve the opportunity" in pages
    assert "not proof of customer need, urgency, causality, ownership or commercial intent" in pages
    assert "customer_alerts" in workflow


def test_every_major_intelligence_detail_can_be_saved_to_workspace():
    pages = Path("pharmatune_ui/pages.py").read_text()
    for record_type in ("opportunity", "organisation", "regulatory", "patent", "research", "commercial_event"):
        assert f'_save_to_workspace("{record_type}"' in pages
