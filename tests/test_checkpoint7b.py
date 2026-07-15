from pharmadrone import production_readiness


def _inputs():
    return (
        {"connection_status": "healthy", "backend": "postgresql", "schema_version": 8, "migration_count": 8},
        {"scheduler_status": "Healthy", "failed_sources": 0, "latest_run": {"started_at": "2026-07-15T10:00:00Z"}},
        {"total_queue_records": 100},
        {"entities": 200, "relationships": 350},
    )


def test_all_live_gates_produce_ready_verdict():
    result = production_readiness.evaluate(*_inputs())
    assert result["ready"] is True
    assert result["status"] == "Production ready"
    assert result["passed"] == result["total"] == 7


def test_failure_is_visible_and_never_silently_accepted():
    database, scheduler, audit, memory = _inputs()
    scheduler["failed_sources"] = 2
    result = production_readiness.evaluate(database, scheduler, audit, memory)
    assert result["ready"] is False
    failed = [check for check in result["checks"] if not check["passed"]]
    assert [check["gate"] for check in failed] == ["Scheduled refresh"]
    assert "2 failed sources" in failed[0]["detail"]


def test_sqlite_cannot_be_labelled_production_ready():
    database, scheduler, audit, memory = _inputs()
    database["backend"] = "sqlite"
    result = production_readiness.evaluate(database, scheduler, audit, memory)
    assert result["ready"] is False
    assert any(check["gate"] == "Production backend" and not check["passed"] for check in result["checks"])
