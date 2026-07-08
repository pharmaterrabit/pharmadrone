"""End-to-end orchestrator. Callable from the dashboard or the CLI.

    python -m pharmadrone.run --mode test              # 5 real test reports
    python -m pharmadrone.run --mode flagship --n 20   # only after you approve
    python -m pharmadrone.run --mode memo --n 80
"""
from __future__ import annotations
import argparse
import json
from collections import Counter
from . import settings, db, export
from .cost import CostTracker
from .pipeline import queries, retrieve, extract, dedup, score, report, failure_signal


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
    """Run the pipeline. Returns (accepted, rejected, cost, coverage_summary)."""
    profile = settings.load_profile()
    cost = CostTracker(profile.get("pricing_usd_per_million_tokens", {}))
    enabled = settings.enabled_sources(profile)
    say = log or (lambda m: None)

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
            "change LLM_PROVIDER.")
    else:
        say(f"LLM: {st_llm['provider']} · model {st_llm['model']}")
    if "tavily" in enabled and not settings.HAS_TAVILY:
        say("⚠ Tavily is enabled but TAVILY_API_KEY is missing — web discovery "
            "will report a clear error per query.")

    say(f"Building queries ({'LLM' if use_llm_queries else 'basic'})…")
    qs = (queries.build_llm_queries(profile, cost) if use_llm_queries
          else queries.build_basic_queries(profile))
    # Failure Signal Intelligence: add failure/rescue-oriented queries.
    fail_on = profile.get("failure_signal", {}).get("enabled", True)
    if fail_on:
        qs = qs + failure_signal.build_failure_queries(profile)
    if mode == "test":
        seen, trimmed = set(), []
        for q in qs:
            if q["region"] not in seen:
                seen.add(q["region"]); trimmed.append(q)
        qs = trimmed[:5]
    say(f"{len(qs)} queries. Sources enabled: {', '.join(enabled)}")

    evidence, coverage = retrieve.retrieve(qs, enabled, cost, progress=progress, log=say)
    say(f"Retrieved {len(evidence)} raw evidence items. Extracting…")

    candidates = extract.extract(evidence, cost)
    if fail_on:
        fsignals = failure_signal.extract_failure_signals(evidence, cost)
        say(f"Failure Signal layer: {len(fsignals)} raw failure/rescue candidate(s).")
        candidates = candidates + fsignals
    candidates = dedup.dedup(candidates)
    say(f"{len(candidates)} unique candidates after dedup. Scoring (0-100)…")

    min_ev = profile["output"].get("min_evidence_links", 2)
    accepted, rejected = score.score_and_filter(candidates, cost, min_ev)
    # Apply the new scoring dimension (Failure / Rescue Signal Strength) and re-rank.
    if fail_on:
        for opp in accepted:
            if opp.get("failure"):
                failure_signal.apply_failure_scoring(opp)
        accepted.sort(key=lambda x: x.get("score", 0), reverse=True)
    accepted = accepted[:n]
    say(f"{len(accepted)} accepted, {len(rejected)} rejected. Writing reports…")

    n_flag = profile["output"].get("flagship_reports", 20) if mode != "memo" else 0
    for i, opp in enumerate(accepted):
        opp["report_type"] = ("flagship" if (mode == "flagship" or i < n_flag)
                              else "memo")
        if mode == "test":
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

    say("\n── Source coverage summary ──")
    for src, s in cov_summary.items():
        flag = f"  ✗ {len(s['errors'])} error(s)" if s["failed"] else ""
        say(f"  {src:<22} evidence={s['evidence_items']:<4} "
            f"leads_citing={s['accepted_leads_citing']:<3}"
            f" queries={s['queries']}{flag}")
    say(f"\nDone. Est. cost ${cost.total_usd} (${cost.per_report_usd}/report). "
        f"Output in ./reports")
    return accepted, rejected, cost, cov_summary


def _cli():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="test", choices=["test", "flagship", "memo"])
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--basic-queries", action="store_true",
                    help="skip LLM query generation to save tokens")
    args = ap.parse_args()
    generate(args.mode, args.n, use_llm_queries=not args.basic_queries,
             log=print, progress=lambda i, t, m: print(f"  [{i}/{t}] {m}"))


if __name__ == "__main__":
    _cli()
