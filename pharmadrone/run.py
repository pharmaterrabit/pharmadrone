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
from . import settings, db, export
from .cost import CostTracker
from .pipeline import (queries, retrieve, extract, dedup, score, report,
                       failure_signal, discover, event_discovery)

FALLBACK_MIN_TOTAL = 3
FALLBACK_MAX_TOTAL = 5
FALLBACK_MIN_RAW_EVIDENCE = 20


def _coverage_summary(coverage: dict, accepted: list[dict]) -> dict:
    """Per-source: raw evidence retrieved + how many ACCEPTED leads cite it."""
    leads_by_source = Counter()
    for opp in accepted:
        cited = {e.get("source_name") for e in opp.get("evidence", [])}
        for s in cited:
            leads_by_source[s] += 1
    summary = {}
    for source, cov in coverage.items():
        summary[source] = {
            "queries": cov["queries"], "succeeded": cov["ok"], "failed": cov["failed"],
            "evidence_items": cov["evidence"],
            "accepted_leads_citing": leads_by_source.get(source, 0),
            "errors": cov["errors"],
        }
    return summary


def generate(mode="test", n=5, use_llm_queries=True, progress=None, log=None):
    """Run the pipeline. Returns (accepted, rejected, cost, coverage_summary, debug)."""
    profile = settings.load_profile()
    cost = CostTracker(profile.get("pricing_usd_per_million_tokens", {}))
    enabled = settings.enabled_sources(profile)
    say = log or (lambda m: None)
    debug = {}

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
            profile, cost, per_source=8 if mode == "failure" else 4, log=say)
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
                coverage[src]["errors"].extend(c["errors"])
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

    # --- Step 2: LLM-based extraction (best-effort enrichment) ------------
    llm_candidates, extract_debug = extract.extract(evidence, cost)
    debug["llm_extraction"] = extract_debug
    say(f"LLM opportunity extraction: {len(llm_candidates)} candidate(s) "
        f"({extract_debug['batches_ok']}/{extract_debug['batches_total']} batch(es) ok).")
    for err in extract_debug["errors"]:
        say(f"  ⚠ {err}")

    fsignals, fsig_debug = ([], {"batches_total": 0, "batches_ok": 0,
                                 "batches_failed": 0, "errors": []})
    if fail_on:
        fsignals, fsig_debug = failure_signal.extract_failure_signals(evidence, cost)
        debug["failure_llm_extraction"] = fsig_debug
        say(f"Failure Signal layer (LLM): {len(fsignals)} candidate(s) "
            f"({fsig_debug['batches_ok']}/{fsig_debug['batches_total']} batch(es) ok).")
        for err in fsig_debug["errors"]:
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

    say("Scoring (0-100)…")
    min_ev = profile["output"].get("min_evidence_links", 2)
    accepted, rejected, score_debug = score.score_and_filter(candidates, cost, min_ev)
    debug["scoring"] = score_debug

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

    conn = db.connect(settings.DB_PATH)
    for opp in accepted:
        db.save_opportunity(conn,
            {**opp, "id": f"{opp.get('company','x')}-{opp.get('product','y')}"},
            opp.get("evidence", []))
    for r in rejected:
        db.save_rejected(conn, r.get("company"), r.get("product"),
                         r.get("reject_reason"), len(r.get("evidence", [])), r)
    conn.close()

    index = export.write_reports(accepted, settings.REPORTS_DIR)
    export.write_datasets(accepted, rejected, settings.REPORTS_DIR)
    export.write_static_site(index, settings.REPORTS_DIR)

    cov_summary = _coverage_summary(coverage, accepted)
    (settings.REPORTS_DIR / "source_coverage.json").write_text(
        json.dumps(cov_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (settings.REPORTS_DIR / "debug_report.json").write_text(
        json.dumps(debug, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    say("\n── Source coverage summary ──")
    for src, s in cov_summary.items():
        flag = f"  ✗ {len(s['errors'])} error(s)" if s["failed"] else ""
        say(f"  {src:<22} evidence={s['evidence_items']:<4} "
            f"leads_citing={s['accepted_leads_citing']:<3}"
            f" queries={s['queries']}{flag}")
    say(f"\nDone. Est. cost ${cost.total_usd} (${cost.per_report_usd}/report). "
        f"Output in ./reports")
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
