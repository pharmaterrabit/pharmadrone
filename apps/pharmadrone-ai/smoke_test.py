#!/usr/bin/env python3
"""Authenticated production smoke test for standalone PharmaDrone AI."""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen


class SmokeTestError(RuntimeError):
    """A deployment check failed without exposing credentials."""


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str = "",
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        urljoin(base_url.rstrip("/") + "/", path.lstrip("/")),
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise SmokeTestError(f"{method} {path} returned HTTP {exc.code}: {detail}") from None
    except URLError as exc:
        raise SmokeTestError(f"{method} {path} could not connect: {exc.reason}") from None
    if "json" in content_type:
        return json.loads(raw or "{}")
    return raw


def run_smoke_test(
    *,
    base_url: str,
    email: str,
    password: str,
    register: bool,
    display_name: str,
    workspace_name: str,
    theme: str,
    company: str,
    request_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SmokeTestError("The smoke-test base URL must be HTTP(S).")
    if not email or not password:
        raise SmokeTestError("Smoke-test email and password are required.")
    request = request_fn or _request

    health = request(base_url, "/api/health")
    if health.get("status") != "healthy":
        raise SmokeTestError("The health endpoint did not report healthy.")
    if health.get("database_backend") != "postgresql":
        raise SmokeTestError("Production smoke test requires the shared PostgreSQL backend.")
    if int(health.get("schema_version") or 0) < 21:
        raise SmokeTestError("Production schema is older than Migration 21.")

    if register:
        request(
            base_url,
            "/api/auth/register",
            method="POST",
            payload={
                "email": email,
                "password": password,
                "display_name": display_name,
                "workspace_name": workspace_name,
            },
        )

    login = request(
        base_url,
        "/api/auth/login",
        method="POST",
        payload={"email": email, "password": password},
    )
    token = str(login.get("access_token") or "")
    if not token:
        raise SmokeTestError("Login did not return an access token.")

    leads_result = request(
        base_url,
        "/api/ai/generate-bd-leads",
        method="POST",
        payload={"theme": theme, "company_filters": [], "limit": 10},
        token=token,
    )
    leads = leads_result.get("data") or []
    if not leads:
        raise SmokeTestError("No retained, evidence-grounded lead was available to save.")

    pitch_result = request(
        base_url,
        "/api/ai/build-company-pitch",
        method="POST",
        payload={
            "company": company,
            "theme": theme,
            "case_type": "Company opportunity pitch",
        },
        token=token,
    )
    report = pitch_result.get("data") or {}
    if not report.get("markdown_report"):
        raise SmokeTestError("Pitch generation did not return an exportable report.")

    saved_lead = request(
        base_url,
        "/api/ai/save-lead",
        method="POST",
        payload={"lead": leads[0]},
        token=token,
    )
    saved_report = request(
        base_url,
        "/api/ai/save-report",
        method="POST",
        payload={"report": report},
        token=token,
    )
    exported = request(
        base_url,
        "/api/ai/export-report",
        method="POST",
        payload={"report": report},
        token=token,
    )
    if not isinstance(exported, str) or not exported.strip():
        raise SmokeTestError("Report export returned no Markdown content.")

    return {
        "status": "passed",
        "database_backend": health["database_backend"],
        "schema_version": int(health["schema_version"]),
        "lead_id": leads[0].get("lead_id"),
        "saved_lead_id": (saved_lead.get("data") or {}).get("saved_lead_id"),
        "report_id": report.get("report_id"),
        "saved_report_id": (saved_report.get("data") or {}).get("saved_report_id"),
        "export_bytes": len(exported.encode("utf-8")),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("PHARMADRONE_AI_SMOKE_BASE_URL", "http://localhost:8000"),
    )
    parser.add_argument("--email", default=os.getenv("PHARMADRONE_AI_SMOKE_EMAIL", ""))
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--display-name", default="Production smoke-test user")
    parser.add_argument("--workspace-name", default="Production validation")
    parser.add_argument("--theme", default="Particle properties")
    parser.add_argument("--company", default="Pfizer")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    password = os.getenv("PHARMADRONE_AI_SMOKE_PASSWORD", "")
    if not password:
        password = getpass.getpass("PharmaDrone AI smoke-test password: ")
    try:
        result = run_smoke_test(
            base_url=args.base_url,
            email=args.email,
            password=password,
            register=args.register,
            display_name=args.display_name,
            workspace_name=args.workspace_name,
            theme=args.theme,
            company=args.company,
        )
    except SmokeTestError as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
