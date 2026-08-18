import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pharmadrone import db
from pharmadrone.pipeline import ai_bd_service, case_study_mvp
from pharmadrone.storage import configured_database, open_connection
from pharmadrone.storage import migrations
from pharmadrone.storage.database import dispose_engines
from pharmadrone.storage.migrations import MIGRATIONS, _pharmadrone_ai_saas_schema
from pharmadrone_ai import chat, repository, security
from pharmadrone_ai.app import create_app


SAAS_TABLES = {
    "saas_users",
    "saas_workspaces",
    "saas_workspace_memberships",
    "saas_conversations",
    "saas_messages",
    "saas_saved_leads",
    "saas_saved_reports",
    "saas_usage_events",
    "saas_subscriptions",
}
NOW = "2026-08-17T10:00:00+00:00"


def _seed_opportunity(
    conn,
    lead_id="lead-pfizer-solubility",
    company="Pfizer",
    product="Candidate P",
    molecule="API P",
    problem="Poor solubility and dissolution rate",
    source_id="SOURCE-PFIZER-1",
    url="https://example.org/pfizer-evidence",
    score=88,
):
    conn.execute(
        """INSERT INTO opportunity_index
        (stable_lead_id,company,product,molecule,problem_category,source_type,source_id,
         region,evidence_links_json,score,grade,last_updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            lead_id, company, product, molecule, problem, "research", source_id,
            "Global", json.dumps([url]), score, "A", NOW,
        ),
    )
    conn.commit()


def _registered(conn, email="owner@example.org", workspace="Example Workspace"):
    return repository.register(
        conn, email, "correct horse battery", "Workspace Owner", workspace,
    )


def test_migration_21_is_additive_rerunnable_and_records_once(tmp_path):
    conn = db.connect(tmp_path / "pharmadrone-ai-migration.sqlite")
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert SAAS_TABLES.issubset(tables)
    assert [migration.version for migration in MIGRATIONS] == list(range(1, 22))
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=21"
    ).fetchone()["n"] == 1
    assert conn.execute(
        "SELECT MAX(version) AS version FROM schema_migrations"
    ).fetchone()["version"] == 21
    with conn.transaction():
        _pharmadrone_ai_saas_schema(conn)
    assert conn.ensure_migrations()["newly_applied"] == []
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM schema_migrations WHERE version=21"
    ).fetchone()["n"] == 1
    conn.close()


def test_upgrade_from_20_preserves_existing_data(tmp_path, monkeypatch):
    conn = open_connection(configured_database(tmp_path / "pharmadrone-ai-upgrade.sqlite"))
    all_migrations = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations[:-1])
    assert conn.ensure_migrations()["schema_version"] == 20
    conn.execute(
        "INSERT INTO opportunities (id,company,product) VALUES (?,?,?)",
        ("existing-ai-upgrade", "Existing Company", "Existing Product"),
    )
    conn.commit()
    monkeypatch.setattr(migrations, "MIGRATIONS", all_migrations)
    result = conn.ensure_migrations()
    assert result["schema_version"] == 21
    assert result["newly_applied"] == [21]
    assert conn.execute(
        "SELECT company FROM opportunities WHERE id='existing-ai-upgrade'"
    ).fetchone()["company"] == "Existing Company"
    assert all(
        conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"] == 0
        for table in SAAS_TABLES
    )
    conn.close()


def test_registration_hashes_password_and_login_returns_workspace(tmp_path):
    conn = db.connect(tmp_path / "pharmadrone-ai-auth.sqlite")
    principal = _registered(conn)
    stored = conn.execute(
        "SELECT password_hash,password_algorithm FROM saas_users WHERE user_id=?",
        (principal["user_id"],),
    ).fetchone()
    assert stored["password_hash"] != "correct horse battery"
    assert stored["password_hash"].startswith("pbkdf2-sha256-v1$")
    assert stored["password_algorithm"] == "pbkdf2-sha256-v1"
    authenticated = repository.authenticate(conn, "OWNER@example.org", "correct horse battery")
    assert authenticated["workspace_id"] == principal["workspace_id"]
    with pytest.raises(security.AuthenticationError):
        repository.authenticate(conn, "owner@example.org", "incorrect password")
    conn.close()


def test_signed_session_rejects_tampering(monkeypatch):
    monkeypatch.setenv("PHARMADRONE_AI_AUTH_SECRET", "a-secure-test-secret-that-is-long-enough")
    token = security.create_session_token("user-1", "workspace-1")
    assert security.decode_session_token(token)["sub"] == "user-1"
    with pytest.raises(security.AuthenticationError):
        security.decode_session_token(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_saved_records_are_scoped_to_authenticated_membership(tmp_path):
    conn = db.connect(tmp_path / "pharmadrone-ai-scope.sqlite")
    first = _registered(conn)
    second = _registered(conn, "second@example.org", "Second Workspace")
    lead = {
        "lead_id": "bd-scope",
        "target_company": "Scope Pharma",
        "theme": "Poor solubility",
        "readiness_status": "Partial company evidence",
        "opportunity_hypothesis": "A possible opportunity requiring validation.",
        "pitch_angle": "Potential fit requiring analyst validation.",
        "evidence_summary": "One retained item.",
        "evidence_counts": {"company_opportunities": 1},
        "source_links": [{"title": "Source", "url": "https://example.org/source"}],
        "limitations": ["Evidence is incomplete."],
        "recommended_next_action": "Validate.",
        "created_from": "retained-pharmadrone-intelligence",
    }
    ai_bd_service.save_lead(first["user_id"], first["workspace_id"], lead, conn=conn)
    assert len(repository.list_saved_leads(conn, first["user_id"], first["workspace_id"])) == 1
    assert repository.list_saved_leads(conn, second["user_id"], second["workspace_id"]) == []
    with pytest.raises(PermissionError):
        ai_bd_service.save_lead(second["user_id"], first["workspace_id"], lead, conn=conn)
    conn.close()


def test_search_and_generate_leads_are_bounded_structured_and_grounded(tmp_path):
    conn = db.connect(tmp_path / "pharmadrone-ai-leads.sqlite")
    _seed_opportunity(conn)
    _seed_opportunity(
        conn,
        lead_id="lead-novartis-particles",
        company="Novartis",
        product="Candidate N",
        molecule="API N",
        problem="Particle morphology, micronization and dissolution",
        source_id="SOURCE-NOVARTIS-1",
        url="https://example.org/novartis-evidence",
        score=79,
    )
    search = ai_bd_service.search_target_companies("Poor solubility", limit=1, conn=conn)
    assert len(search["data"]) == 1
    assert search["data"][0]["company"] == "Pfizer"
    result = ai_bd_service.generate_bd_leads("Poor solubility", limit=10, conn=conn)
    assert len(result["data"]) == 2
    lead = next(item for item in result["data"] if item["target_company"] == "Pfizer")
    assert {
        "lead_id", "target_company", "theme", "readiness_status",
        "opportunity_hypothesis", "pitch_angle", "evidence_summary",
        "evidence_counts", "source_links", "limitations",
        "recommended_next_action", "created_from",
    }.issubset(lead)
    assert lead["target_company"] == "Pfizer"
    assert lead["readiness_status"] == "Pitch-ready draft"
    assert lead["source_links"][0]["url"] == "https://example.org/pfizer-evidence"
    assert json.loads(json.dumps(result))["status"] == "ok"
    conn.close()


def test_explicit_weak_company_is_never_presented_as_supported_lead(tmp_path):
    conn = db.connect(tmp_path / "pharmadrone-ai-weak.sqlite")
    _seed_opportunity(conn)
    result = ai_bd_service.generate_bd_leads(
        "Poor solubility", ["Unknown Pharma"], limit=1, conn=conn,
    )
    assert result["data"][0]["readiness_status"] == "Prospecting shell only"
    assert "no retained company-specific evidence" in " ".join(
        result["data"][0]["limitations"]
    ).casefold()
    conn.close()


def test_company_pitch_reuses_case_study_and_exports_source_table(tmp_path):
    conn = db.connect(tmp_path / "pharmadrone-ai-pitch.sqlite")
    _seed_opportunity(conn)
    with patch.object(case_study_mvp, "build", wraps=case_study_mvp.build) as existing_builder:
        result = ai_bd_service.build_company_pitch(
            "Pfizer", "Poor solubility", "Company opportunity pitch", conn=conn,
        )
    existing_builder.assert_called_once()
    report = result["data"]
    assert report["report_title"] == "Pfizer — Poor solubility opportunity pitch report"
    assert report["readiness_status"] == "Pitch-ready draft"
    assert report["executive_summary"]
    assert report["pitch_angle"]
    assert report["source_table"]
    assert report["markdown_report"].startswith("# Pfizer — Poor solubility opportunity pitch report")
    assert json.loads(json.dumps(report))["target_company"] == "Pfizer"
    conn.close()


def test_deterministic_chat_detects_lead_and_pitch_intents_without_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    conn = db.connect(tmp_path / "pharmadrone-ai-chat.sqlite")
    _seed_opportunity(conn)
    leads = chat.handle_chat(
        "Generate 10 BD leads for particle properties", conn=conn,
    )
    assert leads["intent"] == "generate-bd-leads"
    assert leads["theme"] == "Particle properties"
    assert leads["mode"] == "deterministic"
    pitch = chat.handle_chat(
        "Build a Pfizer poor-solubility pitch report", conn=conn,
    )
    assert pitch["intent"] == "build-company-pitch"
    assert pitch["company"] == "Pfizer"
    assert pitch["theme"] == "Poor solubility"
    assert pitch["result"]["data"]["source_table"]
    assert pitch["result"]["limitations"]
    conn.close()


def test_no_evidence_chat_is_explicit_and_system_prompt_is_strict(tmp_path):
    conn = db.connect(tmp_path / "pharmadrone-ai-no-evidence.sqlite")
    response = chat.handle_chat(
        "Show Unknown Pharma modified release evidence", conn=conn,
    )
    assert response["message"] == "I do not have retained PharmaDrone evidence for that yet."
    assert "must not invent evidence" in chat.SYSTEM_PROMPT.casefold()
    assert "freedom-to-operate" in chat.SYSTEM_PROMPT
    assert "tool outputs only" in chat.SYSTEM_PROMPT
    conn.close()


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    database_path = tmp_path / "pharmadrone-ai-api.sqlite"
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("DATABASE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(database_path))
    monkeypatch.setenv("PHARMADRONE_AI_AUTH_SECRET", "api-test-secret-that-is-at-least-thirty-two")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    dispose_engines()
    conn = db.connect(database_path)
    _seed_opportunity(conn)
    conn.close()
    with TestClient(create_app()) as client:
        yield client
    dispose_engines()


def test_api_auth_chat_save_report_and_export_end_to_end(api_client):
    assert api_client.get("/api/health").json()["schema_version"] == 21
    assert api_client.get("/api/ai/saved-leads").status_code == 401
    registration = api_client.post(
        "/api/auth/register",
        json={
            "email": "api@example.org",
            "password": "correct horse battery",
            "display_name": "API User",
            "workspace_name": "API Workspace",
        },
    )
    assert registration.status_code == 201
    token = registration.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert api_client.get("/api/auth/me", headers=headers).status_code == 200

    chat_response = api_client.post(
        "/api/ai/chat",
        headers=headers,
        json={"prompt": "Build a Pfizer poor-solubility pitch report"},
    )
    assert chat_response.status_code == 200
    report = chat_response.json()["result"]["data"]
    assert report["report_title"].startswith("Pfizer — Poor solubility")
    saved_report = api_client.post(
        "/api/ai/save-report", headers=headers, json={"report": report},
    )
    assert saved_report.status_code == 201
    assert len(api_client.get("/api/ai/saved-reports", headers=headers).json()["data"]) == 1
    exported = api_client.post(
        "/api/ai/export-report", headers=headers, json={"report": report},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/markdown")
    assert "Pfizer" in exported.text

    leads_response = api_client.post(
        "/api/ai/generate-bd-leads",
        headers=headers,
        json={"theme": "Poor solubility", "limit": 10},
    )
    lead = leads_response.json()["data"][0]
    assert api_client.post(
        "/api/ai/save-lead", headers=headers, json={"lead": lead},
    ).status_code == 201
    assert len(api_client.get("/api/ai/saved-leads", headers=headers).json()["data"]) == 1
    usage = api_client.get("/api/billing/status", headers=headers).json()["usage"]
    assert usage["chat_message_count"] == 1
    assert usage["lead_generation_count"] == 1
    assert usage["export_count"] == 1


def test_standalone_client_exists_and_streamlit_navigation_is_unchanged():
    index = Path("apps/pharmadrone-ai/index.html").read_text()
    javascript = Path("apps/pharmadrone-ai/assets/app.js").read_text()
    streamlit_app = Path("pharmatune_ui/app.py").read_text()
    assert "PharmaDrone AI" in index
    assert "AI business-development assistant for pharmaceutical opportunity discovery" in index
    for prompt in chat.STARTER_PROMPTS:
        assert prompt in index
    assert "Saved leads" in index
    assert "Saved reports" in index
    assert "Export Markdown" in javascript
    assert "PharmaDrone AI" not in streamlit_app
    assert "AI Analyst" not in streamlit_app


def test_safety_boundaries_have_no_automatic_external_or_canonical_writes():
    service = Path("pharmadrone/pipeline/ai_bd_service.py").read_text()
    api_source = Path("pharmadrone_ai/app.py").read_text()
    client_source = Path("apps/pharmadrone-ai/assets/app.js").read_text()
    combined = service + api_source + client_source
    assert "google patents" not in combined.casefold()
    assert "canonicalisation_decisions" not in combined
    assert "canonical_record_links" not in combined
    assert "patent_source_discovery(" not in combined
    assert "tavily" not in combined.casefold()
    assert "SELECT *" not in combined
    assert "OPENAI_API_KEY" not in client_source
    assert "fetch(" not in Path("apps/pharmadrone-ai/index.html").read_text()


def test_documented_deployment_and_billing_boundaries_are_honest():
    plan = Path("docs/pharmadrone_ai_standalone_plan.md").read_text()
    backlog = Path("docs/pharmadrone_ai_completion_backlog.md").read_text()
    dockerfile = Path("apps/pharmadrone-ai/Dockerfile").read_text()
    assert "standalone SaaS chatbot" in plan
    assert "not implemented" in plan
    assert "Stripe checkout" in backlog
    assert "HEALTHCHECK" in dockerfile
    assert "pharmadrone_ai.app:app" in dockerfile
