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
from .pipeline import queries, retrieve, extract, dedup, score, report, failure_signal, discover

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
        say("Failure/Rescue mode: using only failure-oriented queries.")
        qs = failure_signal.build_failure_queries(profile)
        seen, trimmed = set(), []
        for q in qs:
            if q["region"] not in seen:
                seen.add(q["region"]); trimmed.append(q)
        qs = trimmed[:6]
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
    say(f"{len(qs)} queries. Sources enabled: {', '.join(enabled)}")

    # --- Retrieve --------------------------------------------------------
    evidence, coverage = retrieve.retrieve(qs, enabled, cost, progress=progress, log=say)
    say(f"Retrieved {len(evidence)} raw evidence items.")
    debug["raw_evidence_count"] = len(evidence)
    debug["top_entities"] = discover.top_entities(evidence, n=10)

    # --- Step 1: deterministic candidate discovery (always runs, no LLM) --
    discovered = discover.discover_candidates(evidence)
    say(f"Candidate discovery (deterministic): {len(discovered)} candidate(s) "
        f"from structured entities.")
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

    # --- Step 3: guaranteed fallback if still short ------------------------
    fallback = discover.build_fallback_candidates(
        evidence, existing_count=len(candidates),
        min_total=FALLBACK_MIN_TOTAL, max_total=FALLBACK_MAX_TOTAL,
        min_raw_evidence=FALLBACK_MIN_RAW_EVIDENCE)
    if fallback:
        say(f"⚠ Only {len(candidates)} candidate(s) after normal extraction with "
            f"{len(evidence)} evidence items — generating {len(fallback)} "
            "provisional candidate(s) from the strongest evidence clusters "
            "(clearly labelled; verify before outreach).")
        candidates = dedup.dedup(candidates + fallback)
    debug["fallback_generated"] = len(fallback)

    say("Scoring (0-100)…")
    min_ev = profile["output"].get("min_evidence_links", 2)
    accepted, rejected, score_debug = score.score_and_filter(candidates, cost, min_ev)
    debug["scoring"] = score_debug

    # Apply the Failure / Rescue Signal Strength dimension and re-rank.
    if fail_on:
        for opp in accepted:
            if opp.get("failure"):
                failure_signal.apply_failure_scoring(opp)
        if mode == "failure":
            # Bias ranking toward rescue strength in this mode.
            rank = {"High": 3, "Medium": 2, "Low": 1, "Reject/flag": 0}
            accepted.sort(key=lambda x: (
                rank.get(x.get("failure_rescue_strength"), 0), x.get("score", 0)),
                reverse=True)
        else:
            accepted.sort(key=lambda x: x.get("score", 0), reverse=True)
    accepted = accepted[:n]
    debug["accepted_count"] = len(accepted)
    debug["rejected_count"] = len(rejected)
    say(f"{len(accepted)} accepted, {len(rejected)} rejected. Writing reports…")

    if not accepted:
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
