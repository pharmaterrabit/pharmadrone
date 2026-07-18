"""Persistence repository for scheduled incremental refresh."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import threading
import uuid
from typing import Any, Iterable

from .config import CADENCE_DELTAS, SOURCE_SPECS, iso, next_due, parse_time, source_enabled, utc_now
from .errors import safe_summary

_SQLITE_LOCKS: dict[str, threading.Lock] = {}
_SQLITE_LOCKS_GUARD = threading.Lock()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def ensure_source_states(conn) -> None:
    now = iso()
    for spec in SOURCE_SPECS:
        conn.execute(
            "INSERT OR IGNORE INTO source_refresh_state "
            "(source_name, source_type, cadence, next_due_at, last_status, enabled, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (spec.name, spec.source_type, spec.cadence, now, "Never run", int(source_enabled(spec)), now),
        )
        current = conn.execute(
            "SELECT source_type, cadence, enabled FROM source_refresh_state WHERE source_name=?",
            (spec.name,),
        ).fetchone()
        if current and (
            current.get("source_type") != spec.source_type
            or current.get("cadence") != spec.cadence
            or int(current.get("enabled") or 0) != int(source_enabled(spec))
        ):
            conn.execute(
                "UPDATE source_refresh_state SET source_type=?, cadence=?, enabled=?, updated_at=? WHERE source_name=?",
                (spec.source_type, spec.cadence, int(source_enabled(spec)), now, spec.name),
            )
    conn.commit()


def get_refresh_states(conn) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM source_refresh_state ORDER BY source_name").fetchall()
    return [dict(r) for r in rows]


def due_source_names(conn, *, now=None, include_failed_only: bool = False) -> list[str]:
    current = now or utc_now()
    due = []
    for row in get_refresh_states(conn):
        if not int(row.get("enabled") or 0):
            continue
        status = str(row.get("last_status") or "").lower()
        if include_failed_only:
            if status in {"failed", "degraded", "partial", "capped"}:
                due.append(str(row["source_name"]))
            continue
        due_at = parse_time(row.get("next_due_at"))
        if due_at is None or due_at <= current:
            due.append(str(row["source_name"]))
    return due


def source_state(conn, source_name: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM source_refresh_state WHERE source_name=?", (source_name,)).fetchone()
    if not row:
        raise KeyError(source_name)
    return dict(row)


def start_run(conn, trigger_type: str, sources_due: list[str], metadata: dict | None = None) -> str:
    run_id = f"refresh-{uuid.uuid4().hex}"
    conn.execute(
        "INSERT INTO refresh_runs (run_id, trigger_type, started_at, status, sources_due, metadata_json) VALUES (?,?,?,?,?,?)",
        (run_id, trigger_type, iso(), "Running", len(sources_due), _json(metadata or {})),
    )
    conn.commit()
    return run_id


def finish_run(conn, run_id: str, *, status: str, totals: dict[str, Any], error_summary: str = "", metadata: dict | None = None) -> None:
    conn.execute(
        "UPDATE refresh_runs SET completed_at=?, status=?, sources_completed=?, sources_failed=?, "
        "records_retrieved=?, records_created=?, records_updated=?, records_unchanged=?, records_rejected=?, "
        "opportunities_created=?, duplicate_records_prevented=?, estimated_spend=?, error_summary=?, metadata_json=? "
        "WHERE run_id=?",
        (
            iso(), status, int(totals.get("sources_completed", 0)), int(totals.get("sources_failed", 0)),
            int(totals.get("records_retrieved", 0)), int(totals.get("records_created", 0)),
            int(totals.get("records_updated", 0)), int(totals.get("records_unchanged", 0)),
            int(totals.get("records_rejected", 0)), int(totals.get("opportunities_created", 0)),
            int(totals.get("duplicate_records_prevented", 0)), float(totals.get("estimated_spend", 0) or 0),
            safe_summary(error_summary), _json(metadata or {}), run_id,
        ),
    )
    conn.commit()


def start_source_run(conn, run_id: str, source_name: str, state: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO source_refresh_runs (run_id, source_name, started_at, status, cursor_before, watermark_before) "
        "VALUES (?,?,?,?,?,?)",
        (run_id, source_name, iso(), "Running", state.get("last_cursor"), state.get("last_watermark")),
    )
    conn.execute(
        "UPDATE source_refresh_state SET last_attempt_at=?, last_status='Running', updated_at=? WHERE source_name=?",
        (iso(), iso(), source_name),
    )
    conn.commit()


def finish_source_run(conn, run_id: str, source_name: str, *, status: str, result: dict[str, Any], state_update: bool = True) -> None:
    conn.execute(
        "UPDATE source_refresh_runs SET completed_at=?, status=?, cursor_after=?, watermark_after=?, "
        "records_retrieved=?, records_created=?, records_updated=?, records_unchanged=?, records_rejected=?, "
        "opportunities_created=?, duplicate_records_prevented=?, retry_count=?, elapsed_seconds=?, estimated_spend=?, "
        "error_class=?, error_summary=?, metadata_json=? WHERE run_id=? AND source_name=?",
        (
            iso(), status, result.get("cursor_after"), result.get("watermark_after"),
            int(result.get("records_retrieved", 0)), int(result.get("records_created", 0)),
            int(result.get("records_updated", 0)), int(result.get("records_unchanged", 0)),
            int(result.get("records_rejected", 0)), int(result.get("opportunities_created", 0)),
            int(result.get("duplicate_records_prevented", 0)), int(result.get("retry_count", 0)),
            float(result.get("elapsed_seconds", 0) or 0), float(result.get("estimated_spend", 0) or 0),
            result.get("error_class"), safe_summary(result.get("error_summary") or ""), _json(result.get("metadata") or {}),
            run_id, source_name,
        ),
    )
    if state_update:
        state = source_state(conn, source_name)
        failures = 0 if status in {"Healthy", "Partial", "Capped"} else int(state.get("consecutive_failures") or 0) + 1
        success_at = iso() if status in {"Healthy", "Partial", "Capped"} else state.get("last_success_at")
        next_at = next_due(str(state.get("cadence") or "daily")) if status in {"Healthy", "Partial", "Capped"} else state.get("next_due_at")
        conn.execute(
            "UPDATE source_refresh_state SET last_success_at=?, next_due_at=?, last_cursor=?, last_watermark=?, "
            "last_status=?, consecutive_failures=?, last_error_summary=?, updated_at=? WHERE source_name=?",
            (
                success_at, next_at,
                result.get("cursor_after") if status in {"Healthy", "Partial", "Capped"} else state.get("last_cursor"),
                result.get("watermark_after") if status in {"Healthy", "Partial", "Capped"} else state.get("last_watermark"),
                status, failures, safe_summary(result.get("error_summary") or ""), iso(), source_name,
            ),
        )
    conn.commit()


def _canonical_record(record: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(record)
    cleaned.pop("date_accessed", None)
    return cleaned


def checksum_record(record: dict[str, Any]) -> str:
    return hashlib.sha256(_json(_canonical_record(record)).encode("utf-8")).hexdigest()


def record_identity(record: dict[str, Any]) -> tuple[str, str] | None:
    source_type = str(record.get("source_type") or "").strip().lower()
    entities = record.get("entities") or {}
    source_id = str(record.get("record_id") or entities.get("source_event_id") or "").strip()
    if not source_type or not source_id:
        return None
    return source_type, source_id


def source_updated_at(record: dict[str, Any]) -> str:
    e = record.get("entities") or {}
    rf = e.get("recall_fields") or {}
    for key in (
        "last_update_date", "update_date", "report_date", "center_classification_date",
        "recall_initiation_date", "initial_posting_date", "discontinued_date",
    ):
        value = e.get(key) or rf.get(key)
        if value:
            return str(value)
    return ""


def _changed_fields(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    keys = sorted(set(old) | set(new))
    changed = []
    for key in keys:
        if key == "date_accessed":
            continue
        if old.get(key) != new.get(key):
            changed.append(key)
    old_e = old.get("entities") or {}
    new_e = new.get("entities") or {}
    for key in sorted(set(old_e) | set(new_e)):
        if old_e.get(key) != new_e.get(key):
            changed.append(f"entities.{key}")
    return list(dict.fromkeys(changed))


def ingest_source_records(conn, *, run_id: str, source_name: str, records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    counts = {"retrieved": 0, "created": 0, "updated": 0, "unchanged": 0, "rejected": 0, "duplicates_prevented": 0}
    material: list[dict[str, Any]] = []
    max_watermark = ""
    now = iso()
    seen_batch: set[tuple[str, str]] = set()
    for record in records:
        counts["retrieved"] += 1
        identity = record_identity(record)
        if identity is None:
            counts["rejected"] += 1
            continue
        if identity in seen_batch:
            counts["duplicates_prevented"] += 1
            continue
        seen_batch.add(identity)
        source_type, source_id = identity
        checksum = checksum_record(record)
        current = conn.execute(
            "SELECT * FROM source_records WHERE source_type=? AND source_id=?", (source_type, source_id)
        ).fetchone()
        payload = _json(_canonical_record(record))
        updated = source_updated_at(record)
        if updated and updated > max_watermark:
            max_watermark = updated
        if current is None:
            conn.execute(
                "INSERT INTO source_records (source_type, source_id, source_name, official_source_url, source_updated_at, "
                "content_checksum, record_json, first_seen_at, last_seen_at, last_changed_at, last_refresh_run_id, active) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                (source_type, source_id, source_name, record.get("url") or "", updated, checksum, payload, now, now, now, run_id),
            )
            conn.execute(
                "INSERT INTO source_record_changes (source_type, source_id, previous_checksum, new_checksum, fields_changed_json, "
                "source_update_timestamp, ingested_at, refresh_run_id, previous_record_json, new_record_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (source_type, source_id, None, checksum, _json(["created"]), updated, now, run_id, None, payload),
            )
            counts["created"] += 1
            material.append(record)
            continue
        current = dict(current)
        if current.get("content_checksum") == checksum:
            last_seen = parse_time(current.get("last_seen_at"))
            if last_seen is None or last_seen.date() < utc_now().date():
                conn.execute(
                    "UPDATE source_records SET last_seen_at=?, last_refresh_run_id=?, active=1 WHERE source_type=? AND source_id=?",
                    (now, run_id, source_type, source_id),
                )
            counts["unchanged"] += 1
            counts["duplicates_prevented"] += 1
            continue
        try:
            old_json = json.loads(current.get("record_json") or "{}")
        except Exception:
            old_json = {}
        changes = _changed_fields(old_json, _canonical_record(record))
        conn.execute(
            "INSERT INTO source_record_changes (source_type, source_id, previous_checksum, new_checksum, fields_changed_json, "
            "source_update_timestamp, ingested_at, refresh_run_id, previous_record_json, new_record_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (source_type, source_id, current.get("content_checksum"), checksum, _json(changes), updated, now, run_id, current.get("record_json"), payload),
        )
        conn.execute(
            "UPDATE source_records SET source_name=?, official_source_url=?, source_updated_at=?, content_checksum=?, record_json=?, "
            "last_seen_at=?, last_changed_at=?, last_refresh_run_id=?, active=1 WHERE source_type=? AND source_id=?",
            (source_name, record.get("url") or "", updated, checksum, payload, now, now, run_id, source_type, source_id),
        )
        counts["updated"] += 1
        material.append(record)
    return {**counts, "material_records": material, "watermark_after": max_watermark}


def latest_run(conn) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM refresh_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def source_status_rows(conn) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT s.*, r.records_retrieved, r.records_created, r.records_updated, r.records_unchanged, "
        "r.records_rejected, r.retry_count, r.error_summary AS latest_run_error "
        "FROM source_refresh_state s LEFT JOIN source_refresh_runs r ON r.run_id=("
        "SELECT rr.run_id FROM source_refresh_runs rr WHERE rr.source_name=s.source_name ORDER BY rr.started_at DESC LIMIT 1) "
        "AND r.source_name=s.source_name ORDER BY s.source_name"
    ).fetchall()
    return [dict(r) for r in rows]


def scheduler_summary(conn) -> dict[str, Any]:
    states = source_status_rows(conn)
    latest = latest_run(conn)
    from .config import next_orchestrator_run
    current = utc_now()
    latest_started = parse_time((latest or {}).get("started_at")) if latest else None
    stale_scheduler = bool(latest_started and current - latest_started > timedelta(days=2))
    notifications = ([{"event_type": "scheduler_stale", "severity": "warning", "summary": "Scheduler has not executed within the expected window."}] if stale_scheduler else [])
    for row in states:
        if not int(row.get("enabled") or 0) or str(row.get("last_status") or "") == "Running":
            continue
        due_at = parse_time(row.get("next_due_at"))
        cadence = str(row.get("cadence") or "daily")
        grace = CADENCE_DELTAS.get(cadence, timedelta(days=1))
        if due_at and current > due_at + grace:
            notifications.append({
                "event_type": "source_refresh_overdue", "severity": "warning",
                "source_name": row.get("source_name"),
                "summary": f"{row.get('source_name')} has not refreshed within its expected {cadence} window.",
            })
    return {
        "scheduler_type": "GitHub Actions daily orchestrator",
        "latest_run": latest,
        "next_orchestrator_run": next_orchestrator_run(),
        "scheduler_status": "Degraded" if stale_scheduler else (str((latest or {}).get("status") or "Never run")),
        "notification_ready_events": notifications,
        "sources": states,
        "enabled_sources": sum(int(r.get("enabled") or 0) for r in states),
        "failed_sources": sum(1 for r in states if str(r.get("last_status") or "").lower() in {"failed", "degraded"}),
        "records_created": sum(int(r.get("records_created") or 0) for r in states),
        "records_updated": sum(int(r.get("records_updated") or 0) for r in states),
        "duplicates_prevented": sum(int(r.get("records_unchanged") or 0) for r in states),
    }


@contextmanager
def source_lock(conn, source_name: str):
    acquired = False
    lock = None
    if conn.backend == "postgresql":
        row = conn.execute("SELECT pg_try_advisory_lock(hashtext(?)) AS locked", (f"pharmatune:{source_name}",)).fetchone()
        acquired = bool(row and row.get("locked"))
    else:
        with _SQLITE_LOCKS_GUARD:
            lock = _SQLITE_LOCKS.setdefault(source_name, threading.Lock())
        acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if not acquired:
            return
        if conn.backend == "postgresql":
            try:
                conn.execute("SELECT pg_advisory_unlock(hashtext(?))", (f"pharmatune:{source_name}",))
                conn.commit()
            except Exception:
                pass
        elif lock:
            lock.release()


def add_notification(conn, *, run_id: str, source_name: str, severity: str, event_type: str, summary: str) -> None:
    conn.execute(
        "INSERT INTO scheduler_notifications (run_id, source_name, severity, event_type, safe_summary, created_at) VALUES (?,?,?,?,?,?)",
        (run_id, source_name, severity, event_type, safe_summary(summary), iso()),
    )
    conn.commit()
