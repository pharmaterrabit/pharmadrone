"""Checkpoint 7B production-readiness gates with no credential disclosure."""
from __future__ import annotations

from typing import Any


def evaluate(
    database: dict[str, Any],
    scheduler: dict[str, Any],
    audit: dict[str, Any],
    memory: dict[str, Any],
) -> dict[str, Any]:
    """Return deterministic production gates from existing operational telemetry."""
    latest = scheduler.get("latest_run") or {}
    checks = [
        {
            "gate": "Durable database",
            "passed": str(database.get("connection_status") or "").lower() == "healthy",
            "detail": f"Connection {database.get('connection_status') or 'unknown'}",
        },
        {
            "gate": "Production backend",
            "passed": str(database.get("backend") or "").lower() == "postgresql",
            "detail": str(database.get("backend") or "unknown").upper(),
        },
        {
            "gate": "Ordered schema",
            "passed": int(database.get("schema_version") or 0) >= 8 and int(database.get("migration_count") or 0) >= 8,
            "detail": f"Schema v{database.get('schema_version', 0)} · {database.get('migration_count', 0)} migrations",
        },
        {
            "gate": "Scheduled refresh",
            "passed": str(scheduler.get("scheduler_status") or "").lower() == "healthy"
            and int(scheduler.get("failed_sources") or 0) == 0,
            "detail": f"{scheduler.get('scheduler_status') or 'unknown'} · {scheduler.get('failed_sources', 0)} failed sources",
        },
        {
            "gate": "Completed refresh run",
            "passed": bool(latest.get("started_at")),
            "detail": str(latest.get("started_at") or "No completed run recorded"),
        },
        {
            "gate": "Governed validation queue",
            "passed": int(audit.get("total_queue_records") or 0) > 0,
            "detail": f"{audit.get('total_queue_records', 0)} immutable validation records",
        },
        {
            "gate": "Pharmaceutical memory",
            "passed": int(memory.get("entities") or 0) > 0 and int(memory.get("relationships") or 0) > 0,
            "detail": f"{memory.get('entities', 0)} entities · {memory.get('relationships', 0)} relationships",
        },
    ]
    passed = sum(1 for check in checks if check["passed"])
    return {
        "ready": passed == len(checks),
        "passed": passed,
        "total": len(checks),
        "checks": checks,
        "status": "Production ready" if passed == len(checks) else "Attention required",
    }
