"""End-to-end orchestrator. Callable from the dashboard or the CLI.

    python -m pharmadrone.run --mode test              # 5 real test reports
    python -m pharmadrone.run --mode failure --n 5      # failure/rescue biased
    python -m pharmadrone.run --mode flagship --n 20    # only after you approve
    python -m pharmadrone.run --mode memo --n 80

Candidate generation no longer depends solely on the LLM succeeding:
  1. Deterministic candidate discovery (pipeline/discover.py) always runs on
     the structured entities the connectors already extracted (trial sponsor,
     recall firm, label brand/generic) — no LLM call needed.
  2. LLM-based extraction (pipeline/extract.py, pipeline/failure_signal.py)
     runs on top as an ENRICHMENT step. If it fails or is rate-limited, its
     errors are captured (never silently swallowed) and reported in `debug`.
  3. If the combined candidate count is still below a floor and raw evidence
     is substantial, `discover.build_fallback_candidates()` forces 3-5
     provisional candidates, clearly labelled, deterministically scored, and
     ALWAYS included — so a healthy evidence haul can never silently end in
     zero reports.
"""
from __future__ import annotations
import argparse
import json
from collections import Counter
import uuid
from . import settings, db, export, llm
from .cost import CostTracker
from .pipeline import (queries, retrieve, extract, dedup, score, report,
                       failure_signal, discover, event_discovery, opportunity_index, source_health)

FALLBACK_MIN_TOTAL = 3
FALLBACK_MAX_TOTAL = 5
FALLBACK_MIN_RAW_EVIDENCE = 20
# Hard cap on how many candidates are scored (LLM or deterministic) per run.
# Prevents scoring 100+ candidates — we rank deterministically and keep the best.
MAX_CANDIDATES_TO_SCORE = 12
# Cap evidence batches sent to the LLM extraction step (each batch = 1 LLM call).
MAX_LLM_EXTRACTION_BATCHES = 3


def _source_status(cov: dict) -> str:
    """Human-readable source state for UI/source coverage."""
    queries = int(cov.get("queries", 0) or 0)
    ok = int(cov.get("ok", 0) or 0)
    failed = int(cov.get("failed", 0) or 0)
    evidence = int(cov.get("evidence", 0) or 0)
    if queries == 0:
        return "search skipped"
    if failed >= queries and ok == 0:
        return "API failed / unavailable"
    if evidence == 0 and ok > 0:
        return "no hits found"
    if failed > 0 and evidence > 0:
        return "partial — some API failures"
    return "available"


def _candidate_counts_by_source(candidates: list[dict]) -> Counter:
    counts = Counter()
    for opp in candidates or []:
        cited = {e.get("source_name") for e in opp.get("evidence", []) if e.get("source_name")}
        for source in cited:
            counts[source] += 1
    return counts


def _coverage_summary(coverage: dict, accepted: list[dict], *,
                      indexed_candidates: list[dict] | None = None,
                      discovery_by_source: dict | None = None) -> dict:
    """Per-source retrieval, candidate, index, and full-report diagnostics.

    ``accepted_leads_citing`` is retained for backward compatibility but now has
    the clearer alias ``full_reports_citing``. Indexed leads are counted before
    the report/scoring cap, which prevents trial/shortage sources from appearing
    to have produced zero leads merely because the top five reports were recalls.
    """
    full_reports_by_source = _candidate_counts_by_source(accepted)
    indexed_by_source = _candidate_counts_by_source(indexed_candidates or [])
    discovery_by_source = discovery_by_source or {}
    summary = {}
    all_sources = set(coverage) | set(discovery_by_source) | set(indexed_by_source)
    for source in sorted(all_sources):
        cov = coverage.get(source, {})
        disc = discovery_by_source.get(source, {}) or {}
        connector_reasons = dict(cov.get("rejection_reasons", {}) or {})
        candidate_reasons = dict(disc.get("rejection_reasons", {}) or {})
        summary[source] = {
            "status": _source_status(cov),
            "queries": cov.get("queries", 0),
            "succeeded": cov.get("ok", 0),
            "failed": cov.get("failed", 0),
            "raw_results": cov.get("raw_results", cov.get("evidence", 0)),
            "evidence_items": cov.get("evidence", 0),
            "source_records_rejected": cov.get("source_rejected", 0),
            "candidate_records_created": disc.get("candidate_records_created", 0),
            "candidate_records_rejected": disc.get("candidate_records_rejected", 0),
            "indexed_leads": indexed_by_source.get(source, 0),
            "full_reports_citing": full_reports_by_source.get(source, 0),
            "accepted_leads_citing": full_reports_by_source.get(source, 0),
            "source_rejection_reasons": connector_reasons,
            "candidate_rejection_reasons": candidate_reasons,
            "settings": cov.get("settings", {}),
            "connector_stats": cov.get("connector_stats", []),
            "errors": cov.get("errors", []),
            "warnings": cov.get("warnings", []),
        }
    return summary


def generate(mode="test", n=5, use_llm_queries=True, progress=None, log=None):
    """Run the pipeline. Returns (accepted, rejected, cost, coverage_summary, debug)."""
    profile = settings.load_profile()
    cost = CostTracker(profile.get("pricing_usd_per_million_tokens", {}))
    enabled = settings.enabled_sources(profile)
    say = log or (lambda m: None)
    debug = {}

    # Fresh circuit breaker for this run: after 2 consecutive 429s the LLM is
    # disabled and the run continues deterministically (no more OpenRouter calls).
    llm.reset_breaker(threshold=2)

    # Hard budget guardrail: one click can never exceed MAX_REPORTS_PER_RUN.
    hard_cap = int(settings.env("MAX_REPORTS_PER_RUN", "5") or "5")
    if n > hard_cap:
        say(f"Run cap active: requested {n}, capped to {hard_cap} "
            "(set MAX_REPORTS_PER_RUN to change).")
        n = hard_cap

    st_llm = settings.llm_status()
    if not st_llm["valid_provider"]:
        say(f"✗ LLM_PROVIDER '{st_llm['provider']}' is not valid. Use one of: "
            "openrouter, groq, openai, gemini.")
    elif not st_llm["key_present"]:
        say(f"✗ LLM provider is '{st_llm['provider']}' but {st_llm['key_env']} "
            "is not set — extraction/scoring/writing will fail. Add the key or "
            "change LLM_PROVIDER. Candidate discovery will still run without it.")
    else:
        say(f"LLM: {st_llm['provider']} · model {st_llm['model']}")
    if "tavily" in enabled and not settings.HAS_TAVILY:
        say("⚠ Tavily is enabled but TAVILY_API_KEY is missing — web discovery "
            "will report a clear error per query.")

    fail_on = profile.get("failure_signal", {}).get("enabled", True)

    # --- Build queries -------------------------------------------------
    if mode == "failure":
        # Bias entirely toward failure/rescue signals, as requested.
        say("Failure/Rescue mode: EVENT-FIRST discovery (recalls, stopped trials, "
            "targeted regulator/company web) — academic literature only as support.")
        qs = []  # event-first mode does not use generic query phrases
    else:
        say(f"Building queries ({'LLM' if use_llm_queries else 'basic'})…")
        qs = (queries.build_llm_queries(profile, cost) if use_llm_queries
              else queries.build_basic_queries(profile))
        if fail_on:
            qs = qs + failure_signal.build_failure_queries(profile)
        if mode == "test":
            seen, trimmed = set(), []
            for q in qs:
                if q["region"] not in seen:
                    seen.add(q["region"]); trimmed.append(q)
            qs = trimmed[:5]
    say(f"{len(qs)} generic queries. Sources enabled: {', '.join(enabled)}")

    # --- Retrieve --------------------------------------------------------
    # Event-first discovery runs in failure mode (exclusively) and in test mode
    # (in addition to the generic queries), so structured event records lead.
    evidence, coverage = [], {}
    if mode in ("failure", "test") and fail_on:
        ev_events, cov_events = event_discovery.discover_events(
            profile, cost, per_source=8 if mode == "failure" else 4, log=say,
            expanded=(mode == "failure"))
        say(f"Event-first discovery: {len(ev_events)} event record(s) "
            "(recalls / stopped trials / targeted web).")
        evidence.extend(ev_events)
        coverage.update(cov_events)
        debug["event_first_count"] = len(ev_events)
        debug["has_event_source"] = event_discovery.has_event_source(ev_events)

    if qs:
        ev_generic, cov_generic = retrieve.retrieve(
            qs, enabled, cost, progress=progress, log=say)
        evidence.extend(ev_generic)
        # merge coverage (event-first + generic) per source
        for src, c in cov_generic.items():
            if src in coverage:
                for k in ("queries", "ok", "failed", "evidence"):
                    coverage[src][k] += c[k]
                coverage[src]["errors"].extend(c.get("errors", []))
                coverage[src].setdefault("warnings", []).extend(c.get("warnings", []))
            else:
                coverage[src] = c
    say(f"Retrieved {len(evidence)} raw evidence items total.")
    debug["raw_evidence_count"] = len(evidence)
    debug["top_entities"] = discover.top_entities(evidence, n=10)

    # --- Step 1: deterministic candidate discovery (always runs, no LLM) --
    discovered, disc_breakdown = discover.discover_candidates(evidence)
    debug["discovery_breakdown"] = disc_breakdown
    say(f"Candidate discovery (deterministic): {len(discovered)} valid BD "
        f"candidate(s); {disc_breakdown['weak_academic_cluster']} weak-academic "
        f"and {disc_breakdown['rejected_generic_literature']} generic-literature "
        "cluster(s) discarded (no valid target).")
    debug["discovered_deterministic"] = len(discovered)

    # --- Step 2: LLM-based extraction (best-effort enrichment, capped) ----
    # Enrichment only. Capped to a few batches, and skipped entirely once the
    # circuit breaker trips, so a rate-limited LLM never stalls the run.
    llm_candidates, extract_debug = extract.extract(
        evidence, cost, max_batches=MAX_LLM_EXTRACTION_BATCHES)
    debug["llm_extraction"] = extract_debug
    say(f"LLM opportunity extraction: {len(llm_candidates)} candidate(s) "
        f"({extract_debug['batches_ok']}/{extract_debug['batches_total']} batch(es) ok"
        + (", LLM disabled (429 circuit breaker)" if extract_debug.get("llm_disabled")
           else "") + ").")
    for err in extract_debug["errors"][:3]:
        say(f"  ⚠ {err}")

    fsignals, fsig_debug = ([], {"batches_total": 0, "batches_ok": 0,
                                 "batches_failed": 0, "errors": []})
    if fail_on:
        fsignals, fsig_debug = failure_signal.extract_failure_signals(
            evidence, cost, max_batches=MAX_LLM_EXTRACTION_BATCHES)
        debug["failure_llm_extraction"] = fsig_debug
        say(f"Failure Signal layer (LLM): {len(fsignals)} candidate(s) "
            f"({fsig_debug['batches_ok']}/{fsig_debug['batches_total']} batch(es) ok"
            + (", LLM disabled (429)" if fsig_debug.get("llm_disabled") else "") + ").")
        for err in fsig_debug["errors"][:3]:
            say(f"  ⚠ {err}")

    candidates = dedup.dedup(discovered + llm_candidates + fsignals)
    debug["candidates_after_dedup"] = len(candidates)
    say(f"{len(candidates)} unique candidates after dedup.")

    # Minimum event-source requirement (req 7) for failure mode: at least one
    # candidate must rest on a regulatory recall, a stopped trial, or a
    # company/news event source — not academic literature.
    if mode == "failure":
        event_backed = [c for c in candidates
                        if event_discovery.has_event_source(c.get("evidence", []))]
        debug["event_backed_candidates"] = len(event_backed)
        if not event_backed:
            say("⚠ Failure/Rescue mode: no candidate is backed by a regulatory "
                "recall, stopped trial, or company/news event source. Not "
                "generating literature-only reports. Broaden regions or check "
                "that openFDA Enforcement / ClinicalTrials.gov returned records.")

    # --- Step 3: conservative fallback (valid targets only) ---------------
    fallback, fb_info = discover.build_fallback_candidates(
        evidence, existing_count=len(candidates),
        min_total=FALLBACK_MIN_TOTAL, max_total=FALLBACK_MAX_TOTAL,
        min_raw_evidence=FALLBACK_MIN_RAW_EVIDENCE)
    debug["fallback_info"] = fb_info
    if fallback:
        say(f"Fallback: {len(fallback)} provisional candidate(s) from valid-target "
            "clusters only (clearly labelled; verify before outreach).")
        candidates = dedup.dedup(candidates + fallback)
    elif fb_info["triggered"]:
        say(f"⚠ Fallback found no valid product/company/trial/recall target — "
            f"{fb_info['reason']}. No misleading reports generated.")
    debug["fallback_generated"] = len(fallback)

    # --- Phase 2: index all valid deduplicated candidates before scoring -------
    # The index is additive/read-only for the discovery pipeline: it stores lead
    # previews and queue metadata without generating extra reports.
    total_candidates = len(candidates)
    ranked_all = sorted(candidates, key=discover.prerank_score, reverse=True)
    discovery_by_source = (disc_breakdown.get("by_source") or {})
    indexed_by_source = _candidate_counts_by_source(ranked_all)
    raw_by_source = Counter(
        e.get("source_name") or "unknown source" for e in evidence
    )
    debug["source_candidate_pipeline"] = {
        source: {
            "raw_source_results": int((coverage.get(source) or {}).get("raw_results", raw_by_source.get(source, 0))),
            "raw_evidence": int(raw_by_source.get(source, 0)),
            "source_records_rejected": int((coverage.get(source) or {}).get("source_rejected", 0)),
            "source_rejection_reasons": dict((coverage.get(source) or {}).get("rejection_reasons", {}) or {}),
            "candidate_records_created": int((discovery_by_source.get(source) or {}).get("candidate_records_created", 0)),
            "candidate_records_rejected": int((discovery_by_source.get(source) or {}).get("candidate_records_rejected", 0)),
            "candidate_rejection_reasons": dict((discovery_by_source.get(source) or {}).get("rejection_reasons", {}) or {}),
            "indexed_leads": int(indexed_by_source.get(source, 0)),
        }
        for source in sorted(set(raw_by_source) | set(discovery_by_source) | set(indexed_by_source) | set(coverage))
    }
    try:
        conn_index = db.connect(settings.DB_PATH)
        opportunity_index.upsert_index_records(
            conn_index, ranked_all, queue_status="waiting", has_full_report=False, starting_rank=1)
        idx_stats = db.fetch_index_stats(conn_index)
        conn_index.close()
        debug["opportunity_index_stats_pre_report"] = idx_stats
        say(f"Opportunity index: {idx_stats['indexed_total']} indexed opportunity record(s) · "
            f"{idx_stats['waiting_queue']} waiting in queue before report generation.")
    except Exception as e:
        debug["opportunity_index_error"] = str(e)
        say(f"  ⚠ opportunity index update skipped: {e}")

    # --- Pre-scoring cap: rank deterministically, keep only the strongest -----
    # This is the fix for the "spinning at Scoring on 104 candidates" hang. We
    # never send more than MAX_CANDIDATES_TO_SCORE candidates to scoring.
    keep_n = min(MAX_CANDIDATES_TO_SCORE, max(n * 2, MAX_CANDIDATES_TO_SCORE))
    kept, skipped = ranked_all[:keep_n], ranked_all[keep_n:]
    debug["candidates_discovered"] = debug.get("discovered_deterministic", 0)
    debug["candidates_total_pre_cap"] = total_candidates
    debug["candidates_kept_for_scoring"] = len(kept)
    debug["candidates_skipped_by_cap"] = len(skipped)
    say(f"Candidates discovered: {debug['candidates_discovered']} · "
        f"after dedup+fallback: {total_candidates}")
    if skipped:
        say(f"Pre-scoring cap: ranked deterministically, keeping top {len(kept)} "
            f"for scoring; {len(skipped)} lower-priority candidate(s) stored in "
            "the opportunity index queue (not scored — prevents scoring 100+ items).")
    else:
        say(f"Candidates kept for scoring: {len(kept)} (no cap needed).")
    candidates = kept

    # --- Deeper per-candidate corroboration (Root-Cause layer) ---------------
    # Runs only on the capped set (<=12), reliable sources only, to feed the
    # Root-Cause Evidence Matrix (warning letters, company statements, molecule
    # literature). Bounded and network-guarded; safe to no-op if Tavily is off.
    if mode in ("failure", "test") and fail_on:
        try:
            corro = event_discovery.corroborate_candidates(
                candidates, cost, enabled, log=say, max_candidates=keep_n)
            debug["corroboration"] = corro
            if corro.get("web_enrichment_unavailable"):
                debug["web_enrichment_unavailable"] = True
        except Exception as e:
            debug["corroboration_error"] = str(e)
            say(f"  ⚠ corroboration step skipped: {e}")

    llm_up = not llm.BREAKER.tripped
    say(f"Scoring (0-100) — {'LLM+deterministic' if llm_up else 'DETERMINISTIC ONLY (LLM disabled by 429 breaker)'}…")
    min_ev = profile["output"].get("min_evidence_links", 2)
    accepted, rejected, score_debug = score.score_and_filter(candidates, cost, min_ev)
    debug["scoring"] = score_debug
    if llm.BREAKER.tripped:
        debug["llm_disabled_reason"] = llm.BREAKER.trip_reason
        debug["llm_mode"] = "deterministic evidence mode"
        say(f"  ℹ {llm.BREAKER.trip_reason}. LLM unavailable/rate-limited; deterministic evidence mode used for the rest.")

    # Source-priority rule (req 5): regulatory recall/enforcement > trial
    # status/whyStopped > company/investor > pharma news > academic.
    def _source_priority(o: dict) -> int:
        cats = {e.get("source_category") for e in o.get("evidence", [])}
        stypes = {e.get("source_type") for e in o.get("evidence", [])}
        if "recall" in stypes:
            return 5
        if "trial" in stypes and o.get("failure_event_confirmed"):
            return 4
        if "regulatory" in cats:
            return 4
        if "company" in cats:
            return 3
        if "news" in cats:
            return 2
        return 1  # academic/other

    # Apply the Failure / Rescue Signal Strength dimension and re-rank.
    if fail_on:
        for opp in accepted:
            if opp.get("failure"):
                failure_signal.apply_failure_scoring(opp)
        if mode == "failure":
            # Bias ranking toward source priority, then rescue strength, then score.
            rank = {"High": 3, "Medium": 2, "Low": 1, "Reject/flag": 0}
            accepted.sort(key=lambda x: (
                _source_priority(x),
                rank.get(x.get("failure_rescue_strength"), 0),
                x.get("score", 0)),
                reverse=True)
        else:
            accepted.sort(key=lambda x: (_source_priority(x), x.get("score", 0)),
                          reverse=True)
    accepted = accepted[:n]
    # FINAL QUALITY GATE: every report must name a real product/company/trial
    # target. Drop anything that still lacks one (belt-and-braces against any
    # path that slipped a generic/blacklisted entity through) so we never emit a
    # "None — prodrug" / "Unknown company" report.
    def _has_valid_target(o: dict) -> bool:
        company = None if discover.is_blacklisted_target(o.get("company")) else o.get("company")
        product = None if discover.is_blacklisted_target(o.get("product")) else o.get("product")
        has_trial = any(e.get("source_type") == "trial" or (e.get("record_id") or "").upper().startswith("NCT")
                        for e in o.get("evidence", []))
        return bool(company or product or o.get("dev_code") or has_trial)

    kept, dropped = [], []
    for o in accepted:
        (kept if _has_valid_target(o) else dropped).append(o)
    if dropped:
        say(f"⚠ Dropped {len(dropped)} candidate(s) at final gate: no valid "
            "product/company/trial target (generic literature — not suitable for "
            "a BD report).")
    accepted = kept
    debug["final_gate_dropped"] = len(dropped)
    # Deduplicate evidence within every accepted candidate (no repeated papers).
    for opp in accepted:
        opp["evidence"] = discover.dedup_evidence(opp.get("evidence", []))
    debug["accepted_count"] = len(accepted)
    debug["rejected_count"] = len(rejected)
    say(f"{len(accepted)} accepted, {len(rejected)} rejected. Writing reports…")

    if not accepted:
        db_ = debug.get("discovery_breakdown", {})
        if (db_.get("valid_bd_opportunity", 0) == 0
                and (db_.get("weak_academic_cluster", 0)
                     or db_.get("rejected_generic_literature", 0))):
            say("Generic literature cluster(s) found, no BD opportunity generated. "
                "No valid product/company/asset/trial/recall target was present in "
                "the retrieved evidence — this is the correct, honest result, not "
                "a hidden failure. Try the Failure/Rescue mode or broaden regions.")
        else:
            say("✗ 0 reports generated. See the Debug panel: check LLM errors above "
                "and confirm at least one source (esp. openFDA/ClinicalTrials.gov) "
                "returned evidence with recognisable company/product names.")

    n_flag = profile["output"].get("flagship_reports", 20) if mode not in ("memo",) else 0
    for i, opp in enumerate(accepted):
        opp["report_type"] = ("flagship" if (mode == "flagship" or i < n_flag)
                              else "memo")
        if mode in ("test", "failure"):
            opp["report_type"] = "memo"
        opp["report_md"] = report.write_report(opp, cost, opp["report_type"])
        cost.reports_done += 1
        if progress:
            progress(i + 1, len(accepted), f"report: {opp.get('company')}")

    # Stable IDs for generated opportunities keep report records aligned with
    # the Phase 2 opportunity index while preserving the existing report flow.
    for opp in accepted:
        opp["stable_lead_id"] = opportunity_index.stable_lead_id(opp)
        opp["id"] = opp["stable_lead_id"]

    index = export.write_reports(accepted, settings.REPORTS_DIR)
    export.write_datasets(accepted, rejected, settings.REPORTS_DIR)
    export.write_static_site(index, settings.REPORTS_DIR)

    report_paths = {}
    for opp, idx_row in zip(accepted, index):
        if opp.get("stable_lead_id") and idx_row.get("file"):
            report_paths[opp["stable_lead_id"]] = idx_row["file"]

    conn = db.connect(settings.DB_PATH)
    for opp in accepted:
        db.save_opportunity(conn, opp, opp.get("evidence", []))
    for r in rejected:
        db.save_rejected(conn, r.get("company"), r.get("product"),
                         r.get("reject_reason"), len(r.get("evidence", [])), r)
    if accepted:
        opportunity_index.upsert_index_records(
            conn, accepted, queue_status="report_generated", has_full_report=True,
            starting_rank=1, report_paths=report_paths)
    if rejected:
        opportunity_index.upsert_index_records(
            conn, rejected, queue_status="rejected", has_full_report=False,
            starting_rank=1)
    idx_stats_final = db.fetch_index_stats(conn)
    debug["opportunity_index_stats"] = idx_stats_final
    run_summary = {
        "run_id": str(uuid.uuid4()),
        "started_at": opportunity_index.utc_now_iso(),
        "mode": mode,
        "indexed_total": idx_stats_final.get("indexed_total", 0),
        "new_count": idx_stats_final.get("new_count", 0),
        "updated_count": idx_stats_final.get("updated_count", 0),
        "seen_count": idx_stats_final.get("seen_count", 0),
        "reports_generated": len(accepted),
        "waiting_count": idx_stats_final.get("waiting_queue", 0),
        "monitor_only_count": idx_stats_final.get("monitor_only_count", 0),
        "llm_mode": debug.get("llm_mode", "active or not needed"),
        "web_enrichment_status": "unavailable" if debug.get("web_enrichment_unavailable") else "available or not needed",
    }
    db.save_run_summary(conn, run_summary)
    opportunity_index.export_index_csv(conn, settings.REPORTS_DIR)
    conn.close()

    say(f"Opportunity index summary: {idx_stats_final['indexed_total']} indexed opportunity record(s) · "
        f"{idx_stats_final['full_reports']} full report(s) · "
        f"{idx_stats_final['waiting_queue']} waiting in queue · "
        f"{idx_stats_final['updated_count']} updated since last indexing.")

    cov_summary = _coverage_summary(
        coverage, accepted,
        indexed_candidates=ranked_all,
        discovery_by_source=disc_breakdown.get("by_source", {}),
    )
    try:
        conn_sh = db.connect(settings.DB_PATH)
        db.save_source_health_events(
            conn_sh,
            source_health.events_from_coverage(cov_summary, run_id=run_summary["run_id"]),
        )
        conn_sh.close()
    except Exception as e:
        debug["source_health_error"] = str(e)
    (settings.REPORTS_DIR / "source_coverage.json").write_text(
        json.dumps(cov_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (settings.REPORTS_DIR / "debug_report.json").write_text(
        json.dumps(debug, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    say("\n── Source coverage summary ──")
    for src, s in cov_summary.items():
        flag = f"  ✗ {len(s['errors'])} error(s)" if s["failed"] else ""
        say(f"  {src:<30} status={s.get('status','n/a'):<28} evidence={s['evidence_items']:<4} "
            f"candidates={s.get('candidate_records_created', 0):<4} "
            f"indexed={s.get('indexed_leads', 0):<4} "
            f"full_reports={s.get('full_reports_citing', 0):<3} "
            f"queries={s['queries']}{flag}")
    say(f"\nDone. Est. cost ${cost.total_usd} (${cost.per_report_usd}/report). "
        f"Output in ./reports")
    return accepted, rejected, cost, cov_summary, debug


def continue_queue(n=5, progress=None, log=None):
    """Generate reports for the next waiting indexed opportunity records.

    This does not run new discovery, Tavily, or LLM extraction. It only reads the
    local Phase 2 opportunity_index queue and keeps the same server-side report
    cap as Generate.
    """
    profile = settings.load_profile()
    cost = CostTracker(profile.get("pricing_usd_per_million_tokens", {}))
    say = log or (lambda m: None)
    debug = {"continue_queue": True}

    llm.reset_breaker(threshold=2)
    hard_cap = int(settings.env("MAX_REPORTS_PER_RUN", "5") or "5")
    if n > hard_cap:
        say(f"Queue cap active: requested {n}, capped to {hard_cap}.")
        n = hard_cap
    n = min(int(n), MAX_CANDIDATES_TO_SCORE, hard_cap)

    conn = db.connect(settings.DB_PATH)
    opportunity_index.backfill_generated_opportunities(conn)
    queued_rows = db.fetch_waiting_index_records(conn, limit=n)
    conn.close()

    if not queued_rows:
        say("No waiting indexed opportunity records found. Run Generate/Refresh to add new signals.")
        return [], [], cost, {}, {**debug, "queue_empty": True}

    candidates = []
    for row in queued_rows:
        try:
            cand = json.loads(row.get("data_json") or "{}")
        except Exception:
            cand = {}
        if not cand:
            continue
        cand.setdefault("stable_lead_id", row.get("stable_lead_id"))
        cand.setdefault("company", row.get("company"))
        cand.setdefault("product", row.get("product"))
        cand.setdefault("problem_category", row.get("problem_category"))
        candidates.append(cand)

    say(f"Continue previous queue: selected {len(candidates)} waiting indexed lead(s) "
        f"for report generation (cap {hard_cap}).")

    min_ev = profile["output"].get("min_evidence_links", 2)
    accepted, rejected, score_debug = score.score_and_filter(candidates, cost, min_ev)
    debug["scoring"] = score_debug
    debug["accepted_count"] = len(accepted)
    debug["rejected_count"] = len(rejected)
    if llm.BREAKER.tripped:
        debug["llm_disabled_reason"] = llm.BREAKER.trip_reason
        debug["llm_mode"] = "deterministic evidence mode"
        say(f"  ℹ {llm.BREAKER.trip_reason}. LLM unavailable/rate-limited; deterministic evidence mode used for the rest.")

    accepted = accepted[:n]
    for i, opp in enumerate(accepted):
        opp["report_type"] = "memo"
        opp["stable_lead_id"] = opp.get("stable_lead_id") or opportunity_index.stable_lead_id(opp)
        opp["id"] = opp["stable_lead_id"]
        opp["report_md"] = report.write_report(opp, cost, opp["report_type"])
        cost.reports_done += 1
        if progress:
            progress(i + 1, len(accepted), f"queue report: {opp.get('company')}")

    index = export.write_reports(accepted, settings.REPORTS_DIR)
    export.write_datasets(accepted, rejected, settings.REPORTS_DIR)
    export.write_static_site(index, settings.REPORTS_DIR)

    report_paths = {}
    for opp, idx_row in zip(accepted, index):
        if opp.get("stable_lead_id") and idx_row.get("file"):
            report_paths[opp["stable_lead_id"]] = idx_row["file"]

    conn = db.connect(settings.DB_PATH)
    for opp in accepted:
        db.save_opportunity(conn, opp, opp.get("evidence", []))
    for r in rejected:
        db.save_rejected(conn, r.get("company"), r.get("product"),
                         r.get("reject_reason"), len(r.get("evidence", [])), r)
    if accepted:
        opportunity_index.upsert_index_records(
            conn, accepted, queue_status="report_generated", has_full_report=True,
            starting_rank=1, report_paths=report_paths)
    if rejected:
        opportunity_index.upsert_index_records(
            conn, rejected, queue_status="rejected", has_full_report=False,
            starting_rank=1)
    idx_stats = db.fetch_index_stats(conn)
    debug["opportunity_index_stats"] = idx_stats
    db.save_run_summary(conn, {
        "run_id": str(uuid.uuid4()),
        "started_at": opportunity_index.utc_now_iso(),
        "mode": "continue_queue",
        "indexed_total": idx_stats.get("indexed_total", 0),
        "new_count": idx_stats.get("new_count", 0),
        "updated_count": idx_stats.get("updated_count", 0),
        "seen_count": idx_stats.get("seen_count", 0),
        "reports_generated": len(accepted),
        "waiting_count": idx_stats.get("waiting_queue", 0),
        "monitor_only_count": idx_stats.get("monitor_only_count", 0),
        "llm_mode": debug.get("llm_mode", "active or not needed"),
        "web_enrichment_status": "not used for continue queue",
    })
    opportunity_index.export_index_csv(conn, settings.REPORTS_DIR)
    conn.close()

    cov_summary = {"opportunity_index": {
        "status": "local indexed evidence",
        "queries": 0, "succeeded": 0, "failed": 0,
        "evidence_items": len(candidates),
        "accepted_leads_citing": len(accepted),
        "errors": [], "warnings": [],
    }}
    try:
        conn_sh = db.connect(settings.DB_PATH)
        db.save_source_health_events(
            conn_sh,
            source_health.events_from_coverage(cov_summary, run_id=debug.get("run_id", "continue_queue")),
        )
        conn_sh.close()
    except Exception as e:
        debug["source_health_error"] = str(e)
    (settings.REPORTS_DIR / "debug_report.json").write_text(
        json.dumps(debug, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (settings.REPORTS_DIR / "source_coverage.json").write_text(
        json.dumps(cov_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    say(f"Queue done. Generated {len(accepted)} report(s); "
        f"{idx_stats.get('waiting_queue', 0)} waiting indexed lead(s) remain.")
    return accepted, rejected, cost, cov_summary, debug


def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="test",
                    choices=["test", "failure", "flagship", "memo"])
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--basic-queries", action="store_true",
                    help="skip LLM query generation to save tokens")
    args = ap.parse_args()
    generate(args.mode, args.n, use_llm_queries=not args.basic_queries,
             log=print, progress=lambda i, t, m: print(f"  [{i}/{t}] {m}"))


if __name__ == "__main__":
    _cli()
