"""Checkpoint 6C.1 scheduled incremental refresh orchestrator."""
from __future__ import annotations

from dataclasses import asdict
import json
import time
from typing import Any

from .. import db
from ..pipeline import account_intelligence, discover, opportunity_index, patent_lifecycle, score
from .config import guardrails, source_spec, source_names, utc_now
from .errors import SchedulerError, classify_error, safe_summary
from . import repository, sources


def _zero_totals() -> dict[str, Any]:
    return {
        "sources_completed": 0, "sources_failed": 0,
        "records_retrieved": 0, "records_created": 0, "records_updated": 0,
        "records_unchanged": 0, "records_rejected": 0,
        "opportunities_created": 0, "duplicate_records_prevented": 0,
        "estimated_spend": 0.0,
    }


def _add_totals(total: dict[str, Any], result: dict[str, Any], success: bool) -> None:
    total["sources_completed" if success else "sources_failed"] += 1
    for key in (
        "records_retrieved", "records_created", "records_updated", "records_unchanged",
        "records_rejected", "opportunities_created", "duplicate_records_prevented",
    ):
        total[key] += int(result.get(key, 0) or 0)
    total["estimated_spend"] += float(result.get("estimated_spend", 0) or 0)


def _benchmark_snapshot(conn) -> dict[str, int]:
    counts = {}
    for table in ("audit_benchmark_batches", "audit_queue_records", "human_audit_versions", "human_audit_corrections"):
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        counts[table] = int(row["n"] if row else 0)
    golden = conn.execute("SELECT COUNT(*) AS n FROM audit_queue_records q JOIN audit_benchmark_batches b ON b.batch_id=q.batch_id WHERE b.is_golden=1").fetchone()
    counts["golden_queue_records"] = int(golden["n"] if golden else 0)
    return counts


def _monthly_maintenance(conn, run_id: str, fetch_result: dict[str, Any]) -> dict[str, Any]:
    rows = db.fetch_index_records(conn, include_hidden=False)
    reviewed = 0
    for row in rows:
        freshness = opportunity_index.source_freshness(row)
        if freshness not in {"stale", "monitor only"}:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO opportunity_refresh_flags "
            "(stable_lead_id, refresh_run_id, review_status, reason, reviewed_at, metadata_json) VALUES (?,?,?,?,?,?)",
            (
                row.get("stable_lead_id"), run_id, freshness,
                "Monthly deterministic freshness review; human validation remains required.",
                __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(microsecond=0).isoformat(),
                json.dumps({"lead_status": row.get("lead_status"), "last_checked_at": row.get("last_checked_at")}, default=str),
            ),
        )
        reviewed += 1
    url_checks = list((fetch_result.get("metadata") or {}).get("url_checks") or [])
    checked_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).replace(microsecond=0).isoformat()
    for check in url_checks:
        conn.execute(
            "INSERT INTO source_url_checks (source_type, source_id, official_source_url, checked_at, status, http_status, error_summary, refresh_run_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (check.get("source_type"), check.get("source_id"), check.get("official_source_url"), checked_at,
             check.get("status"), check.get("http_status"), check.get("error_summary"), run_id),
        )
    return {
        "records_retrieved": len(rows) + len(url_checks), "records_created": reviewed + len(url_checks),
        "records_updated": 0, "records_unchanged": len(rows) - reviewed,
        "records_rejected": 0, "opportunities_created": 0,
        "duplicate_records_prevented": 0, "watermark_after": "", "cursor_after": "monthly-review",
        "metadata": {
            "records_reviewed": len(rows), "records_flagged": reviewed,
            "url_checks_attempted": len(url_checks),
            "url_checks_available": sum(1 for c in url_checks if c.get("status") == "available"),
            "url_checks_unavailable": sum(1 for c in url_checks if c.get("status") != "available"),
        },
    }


def _account_intelligence_refresh(conn, run_id: str, fetch_result: dict[str, Any]) -> dict[str, Any]:
    projection = account_intelligence.sync_account_intelligence(conn, run_id=run_id)
    return {
        "records_retrieved": int(projection.get("organisations_seen", 0)) + int(projection.get("contacts_seen", 0)),
        "records_created": int(projection.get("organisations_changed", 0)) + int(projection.get("contacts_changed", 0)),
        "records_updated": 0,
        "records_unchanged": max(0, int(projection.get("organisations_seen", 0)) - int(projection.get("organisations_changed", 0))),
        "records_rejected": 0,
        "opportunities_created": 0, "duplicate_records_prevented": 0,
        "watermark_after": "",
        "cursor_after": "weekly-account-projection",
        "metadata": {**(fetch_result.get("metadata") or {}), **projection},
    }


def _patent_lifecycle_refresh(conn, run_id: str, fetch_result: dict[str, Any]) -> dict[str, Any]:
    projection = patent_lifecycle.sync(conn, run_id=run_id)
    return {
        "records_retrieved": int(projection.get("products_seen", 0)),
        "records_created": int(projection.get("products_changed", 0)),
        "records_updated": 0,
        "records_unchanged": max(0, int(projection.get("products_seen", 0)) - int(projection.get("products_changed", 0))),
        "records_rejected": 0, "opportunities_created": 0, "duplicate_records_prevented": 0,
        "watermark_after": "", "cursor_after": "weekly-patent-lifecycle-projection",
        "metadata": {**(fetch_result.get("metadata") or {}), **projection},
    }


def _generate_opportunities(conn, material_records: list[dict[str, Any]]) -> dict[str, int]:
    eligible = [r for r in material_records if str(r.get("source_type") or "").lower() in {"recall", "trial", "shortage"}]
    if not eligible:
        return {"opportunities_created": 0, "opportunities_updated": 0, "candidates": 0}
    candidates, _breakdown = discover.discover_candidates(eligible, min_cluster_evidence=1)
    if not candidates:
        return {"opportunities_created": 0, "opportunities_updated": 0, "candidates": 0}
    opportunity_index.enrich_ema_companies(conn, candidates)
    # Every indexed preview must have a useful deterministic score and grade.
    # Full LLM enrichment/report generation remains a separate, optional step.
    for candidate in candidates:
        if candidate.get("score") is None or not candidate.get("grade"):
            score.deterministic_score(candidate)
    rank_row = conn.execute("SELECT COALESCE(MAX(queue_rank),0) AS n FROM opportunity_index").fetchone()
    counts = opportunity_index.upsert_index_records(
        conn, candidates, queue_status="waiting", has_full_report=False,
        starting_rank=int(rank_row["n"] if rank_row else 0) + 1,
    )
    return {
        "opportunities_created": int(counts.get("new", 0)),
        "opportunities_updated": int(counts.get("updated", 0)),
        "candidates": len(candidates),
    }


def run_one_source(conn, *, run_id: str, source_name: str, force: bool = False,
                   dry_run: bool = False, lookback_days: int | None = None) -> dict[str, Any]:
    spec = source_spec(source_name)
    state = repository.source_state(conn, source_name)
    guards = guardrails(lookback_days=lookback_days)
    if dry_run:
        return {
            "source_name": source_name, "status": "Dry run", "due": True,
            "state": state, "guardrails": asdict(guards),
        }

    with repository.source_lock(conn, source_name) as acquired:
        if not acquired:
            result = {"error_class": "database failure", "error_summary": "source refresh already running", "records_retrieved": 0}
            return {"source_name": source_name, "status": "Skipped", **result}

        repository.start_source_run(conn, run_id, source_name, state)
        before = _benchmark_snapshot(conn)
        started = time.monotonic()
        attempt = 0
        fetch_result = None
        last_error: Exception | None = None
        while attempt < guards.retry_attempts:
            attempt += 1
            try:
                fetch_result = sources.fetch_source(source_name, conn, state, guards, force=force)
                break
            except SchedulerError as exc:
                last_error = exc
                if not exc.retryable or attempt >= guards.retry_attempts:
                    break
                time.sleep(min(2 ** (attempt - 1), 8))
            except Exception as exc:
                error_class, retryable = classify_error(str(exc))
                last_error = SchedulerError(str(exc), error_class, retryable=retryable)
                if not retryable or attempt >= guards.retry_attempts:
                    break
                time.sleep(min(2 ** (attempt - 1), 8))

        if fetch_result is None:
            exc = last_error or SchedulerError("source failed", "unknown failure")
            result = {
                "retry_count": max(0, attempt - 1), "elapsed_seconds": time.monotonic() - started,
                "error_class": getattr(exc, "error_class", "unknown failure"),
                "error_summary": safe_summary(str(exc)),
            }
            failure_count = int(state.get("consecutive_failures") or 0) + 1
            source_status = "Degraded" if failure_count >= 2 else "Failed"
            repository.finish_source_run(conn, run_id, source_name, status=source_status, result=result)
            if failure_count >= 3 or result.get("error_class") in {"source schema change", "database failure", "authentication failure"}:
                repository.add_notification(
                    conn, run_id=run_id, source_name=source_name, severity="error",
                    event_type=("source_repeated_failure" if failure_count >= 3 else str(result.get("error_class") or "source_failure").replace(" ", "_")),
                    summary=str(exc),
                )
            return {"source_name": source_name, "status": source_status, **result}

        try:
            with conn.transaction():
                if source_name == "monthly_maintenance":
                    result = _monthly_maintenance(conn, run_id, fetch_result)
                elif source_name == "account_intelligence":
                    result = _account_intelligence_refresh(conn, run_id, fetch_result)
                elif source_name == "patent_lifecycle":
                    result = _patent_lifecycle_refresh(conn, run_id, fetch_result)
                else:
                    ingest = repository.ingest_source_records(
                        conn, run_id=run_id, source_name=source_name, records=fetch_result.get("records") or []
                    )
                    generated = _generate_opportunities(conn, ingest.pop("material_records")) if spec.creates_opportunities else {
                        "opportunities_created": 0, "opportunities_updated": 0, "candidates": 0
                    }
                    repaired_labels = opportunity_index.repair_regulator_source_labels(conn)
                    entity_repairs = opportunity_index.repair_regulator_entities(conn)
                    evidence_url_repairs = opportunity_index.repair_evidence_urls(conn)
                    scoring_backfill = opportunity_index.backfill_missing_scores(conn)
                    qualification_briefs = opportunity_index.build_sales_qualification_briefs(conn)
                    result = {
                        "records_retrieved": ingest["retrieved"],
                        "records_created": ingest["created"],
                        "records_updated": ingest["updated"],
                        "records_unchanged": ingest["unchanged"],
                        "records_rejected": ingest["rejected"],
                        "duplicate_records_prevented": ingest["duplicates_prevented"],
                        "opportunities_created": generated["opportunities_created"],
                        "cursor_after": fetch_result.get("cursor_after") or state.get("last_cursor"),
                        "watermark_after": fetch_result.get("watermark_after") or ingest.get("watermark_after") or state.get("last_watermark"),
                        "estimated_spend": float(fetch_result.get("estimated_spend", 0) or 0),
                        "metadata": {**(fetch_result.get("metadata") or {}), **generated,
                                     "regulator_source_labels_repaired": repaired_labels,
                                     "regulator_entities_repaired": entity_repairs,
                                     "evidence_urls_repaired": evidence_url_repairs,
                                     "missing_scores_backfilled": scoring_backfill,
                                     "qualification_briefs_built": qualification_briefs},
                    }
                after = _benchmark_snapshot(conn)
                if before != after:
                    raise SchedulerError("frozen benchmark isolation check failed", "validation failure", retryable=False)
            partial = bool(fetch_result.get("partial"))
            status = "Partial" if partial else "Healthy"
            if partial:
                result["error_class"] = "budget limit"
                result["error_summary"] = "Source run stopped at configured cost/volume guardrail; continuation state preserved."
                repository.add_notification(
                    conn, run_id=run_id, source_name=source_name, severity="warning",
                    event_type="budget_limit", summary=result["error_summary"],
                )
            result["retry_count"] = max(0, attempt - 1)
            result["elapsed_seconds"] = time.monotonic() - started
            result.setdefault("metadata", {})["frozen_benchmark_integrity"] = "unchanged"
            repository.finish_source_run(conn, run_id, source_name, status=status, result=result)
            return {"source_name": source_name, "status": status, **result}
        except Exception as exc:
            error_class, _retryable = classify_error(str(exc))
            result = {
                "retry_count": max(0, attempt - 1), "elapsed_seconds": time.monotonic() - started,
                "error_class": getattr(exc, "error_class", error_class),
                "error_summary": safe_summary(str(exc)),
            }
            repository.finish_source_run(conn, run_id, source_name, status="Failed", result=result)
            return {"source_name": source_name, "status": "Failed", **result}


def run_sources(*, selected: list[str] | None = None, force: bool = False, dry_run: bool = False,
                trigger_type: str = "manual", lookback_days: int | None = None,
                failed_only: bool = False) -> dict[str, Any]:
    conn = db.connect()
    repository.ensure_source_states(conn)
    if selected is None:
        selected = repository.due_source_names(conn, include_failed_only=failed_only)
    else:
        unknown = sorted(set(selected) - set(source_names()))
        if unknown:
            conn.close()
            raise ValueError(f"Unknown scheduled source(s): {', '.join(unknown)}")
        if not force and not failed_only:
            due = set(repository.due_source_names(conn))
            selected = [s for s in selected if s in due]

    if dry_run:
        rows = [run_one_source(conn, run_id="dry-run", source_name=s, force=force, dry_run=True, lookback_days=lookback_days) for s in selected]
        snapshot = repository.scheduler_summary(conn)
        conn.close()
        return {"status": "Dry run", "sources_due": selected, "results": rows, "scheduler": snapshot}

    run_id = repository.start_run(conn, trigger_type, selected, metadata={"force": force, "lookback_days": lookback_days})
    totals = _zero_totals()
    results = []
    errors = []
    for source_name in selected:
        result = run_one_source(conn, run_id=run_id, source_name=source_name, force=force, lookback_days=lookback_days)
        results.append(result)
        success = result.get("status") in {"Healthy", "Partial", "Capped"}
        _add_totals(totals, result, success)
        if not success:
            errors.append(f"{source_name}: {result.get('error_summary') or result.get('status')}")
    any_partial = any(r.get("status") in {"Partial", "Capped"} for r in results)
    status = ("Failed" if totals["sources_completed"] == 0 and errors else
              "Partial" if errors or any_partial else "Healthy")
    repository.finish_run(conn, run_id, status=status, totals=totals, error_summary="; ".join(errors), metadata={"source_results": results})
    summary = repository.scheduler_summary(conn)
    conn.close()
    return {"run_id": run_id, "status": status, "sources_due": selected, "totals": totals, "results": results, "scheduler": summary}


def status() -> dict[str, Any]:
    conn = db.connect()
    summary = repository.scheduler_summary(conn)
    conn.close()
    return summary
