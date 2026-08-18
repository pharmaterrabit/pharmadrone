from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = ROOT / "apps" / "pharmadrone-ai"
START_SCRIPT = APP_ROOT / "start-production.sh"
SMOKE_SCRIPT = APP_ROOT / "smoke_test.py"


def _production_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in (
        "DATABASE_URL",
        "DATABASE_BACKEND",
        "SQLITE_PATH",
        "APP_ENV",
        "PHARMADRONE_AI_AUTH_SECRET",
        "PHARMADRONE_AI_ALLOWED_ORIGINS",
    ):
        environment.pop(key, None)
    environment.update(
        {
            "DATABASE_URL": "postgresql://user:password@db.example/pharmadrone",
            "PHARMADRONE_AI_AUTH_SECRET": "a-unique-production-secret-over-32-characters",
            "PHARMADRONE_AI_ALLOWED_ORIGINS": "https://ai.example.com",
        }
    )
    return environment


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location("pharmadrone_ai_smoke_test", SMOKE_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_container_uses_guarded_port_8000_startup():
    dockerfile = (APP_ROOT / "Dockerfile").read_text()
    compose = yaml.safe_load((APP_ROOT / "docker-compose.production.yml").read_text())
    service = compose["services"]["pharmadrone-ai"]

    assert "APP_ENV=production" in dockerfile
    assert "PHARMADRONE_AI_API_DOCS=0" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert 'ENTRYPOINT ["/app/apps/pharmadrone-ai/start-production.sh"]' in dockerfile
    assert 'CMD ["python", "-m", "uvicorn", "pharmadrone_ai.app:app"' in dockerfile
    assert "/api/health" in dockerfile
    assert service["ports"] == ["8000:8000"]
    assert service["environment"]["APP_ENV"] == "production"
    assert service["environment"]["DATABASE_URL"].startswith("${DATABASE_URL:?")
    assert "DATABASE_BACKEND" not in service["environment"]
    assert "SQLITE_PATH" not in service["environment"]
    assert "/api/health" in " ".join(service["healthcheck"]["test"])


def test_production_startup_rejects_missing_or_sqlite_database():
    environment = _production_environment()
    environment.pop("DATABASE_URL")
    missing = subprocess.run(
        ["sh", str(START_SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing.returncode != 0
    assert "DATABASE_URL is required" in missing.stderr

    environment = _production_environment()
    environment["DATABASE_URL"] = "sqlite:////tmp/production.sqlite"
    sqlite_url = subprocess.run(
        ["sh", str(START_SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sqlite_url.returncode != 0
    assert "SQLite is local/test only" in sqlite_url.stderr

    environment = _production_environment()
    environment["DATABASE_BACKEND"] = "sqlite"
    sqlite_backend = subprocess.run(
        ["sh", str(START_SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert sqlite_backend.returncode != 0
    assert "must not select SQLite" in sqlite_backend.stderr


def test_production_startup_executes_exact_uvicorn_command(tmp_path):
    fake_python = tmp_path / "python"
    fake_python.write_text("#!/bin/sh\nprintf '%s\\n' \"$APP_ENV|$*\"\n")
    fake_python.chmod(0o755)
    environment = _production_environment()
    environment["PATH"] = f"{tmp_path}:{environment['PATH']}"

    result = subprocess.run(
        ["sh", str(START_SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == (
        "production|-m uvicorn pharmadrone_ai.app:app "
        "--host 0.0.0.0 --port 8000"
    )

    migration = subprocess.run(
        ["sh", str(START_SCRIPT), "python", "-m", "pharmadrone.storage.migrate"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert migration.returncode == 0
    assert migration.stdout.strip() == "production|-m pharmadrone.storage.migrate"


def test_production_environment_docs_and_warnings_are_complete():
    deployment = (ROOT / "docs" / "pharmadrone_ai_deployment.md").read_text()
    environment = (APP_ROOT / ".env.production.example").read_text()
    frontend = (APP_ROOT / "index.html").read_text() + (APP_ROOT / "assets" / "app.js").read_text()
    required = (
        "DATABASE_URL",
        "PHARMADRONE_AI_AUTH_SECRET",
        "PHARMADRONE_AI_ALLOWED_ORIGINS",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "TAVILY_API_KEY",
        "EPO_OPS_CLIENT_ID",
        "EPO_OPS_CLIENT_SECRET",
    )
    for name in required:
        assert name in deployment
        assert name in environment
        assert name not in frontend
    for warning in (
        "Stripe billing is not complete",
        "Email verification is not complete",
        "Password reset is not complete",
        "MFA is not complete",
        "Production rate limiting is not complete",
        "Server-side token revocation is not complete",
    ):
        assert warning in deployment
    assert "python -m pharmadrone.storage.migrate" in deployment
    assert "register the first user" in deployment.casefold()


def test_authenticated_smoke_script_covers_required_production_flow():
    smoke = _load_smoke_module()
    calls: list[tuple[str, str, str]] = []
    responses = {
        "/api/health": {
            "status": "healthy",
            "database_backend": "postgresql",
            "schema_version": 21,
        },
        "/api/auth/register": {"access_token": "registration-token"},
        "/api/auth/login": {"access_token": "login-token"},
        "/api/ai/generate-bd-leads": {
            "data": [{"lead_id": "retained-lead-1", "target_company": "Company A"}],
        },
        "/api/ai/build-company-pitch": {
            "data": {
                "report_id": "retained-report-1",
                "markdown_report": "# Evidence-grounded report",
            },
        },
        "/api/ai/save-lead": {"data": {"saved_lead_id": "saved-lead-1"}},
        "/api/ai/save-report": {"data": {"saved_report_id": "saved-report-1"}},
        "/api/ai/export-report": "# Exported evidence-grounded report",
    }

    def fake_request(base_url, path, *, method="GET", payload=None, token=""):
        calls.append((method, path, token))
        return responses[path]

    result = smoke.run_smoke_test(
        base_url="https://ai.example.com",
        email="deployment-check@example.com",
        password="correct horse battery",
        register=True,
        display_name="Deployment Check",
        workspace_name="Production Validation",
        theme="Particle properties",
        company="Pfizer",
        request_fn=fake_request,
    )

    assert result["status"] == "passed"
    assert result["database_backend"] == "postgresql"
    assert [path for _, path, _ in calls] == [
        "/api/health",
        "/api/auth/register",
        "/api/auth/login",
        "/api/ai/generate-bd-leads",
        "/api/ai/build-company-pitch",
        "/api/ai/save-lead",
        "/api/ai/save-report",
        "/api/ai/export-report",
    ]
    assert all(token == "login-token" for _, _, token in calls[3:])


def test_smoke_script_refuses_non_postgresql_health():
    smoke = _load_smoke_module()

    def sqlite_health(*args, **kwargs):
        return {"status": "healthy", "database_backend": "sqlite", "schema_version": 21}

    with pytest.raises(smoke.SmokeTestError, match="PostgreSQL"):
        smoke.run_smoke_test(
            base_url="https://ai.example.com",
            email="deployment-check@example.com",
            password="correct horse battery",
            register=False,
            display_name="Deployment Check",
            workspace_name="Production Validation",
            theme="Particle properties",
            company="Pfizer",
            request_fn=sqlite_health,
        )
