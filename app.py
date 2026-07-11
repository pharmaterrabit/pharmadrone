"""PharmaDrone local dashboard.  Run:  streamlit run app.py
Opens at http://localhost:8501 by default.
"""
from __future__ import annotations
import io
import os
import zipfile
import json as _json
import pandas as pd
import streamlit as st

# Must be the first Streamlit command; settings may inspect st.secrets on import.
st.set_page_config(page_title="PharmaDrone", layout="wide")

from pharmadrone import settings, db, auth, CHECKPOINT
from pharmadrone.run import generate, continue_queue
from pharmadrone.test_connectors import check_all, DEFAULT_QUERY
from pharmadrone.pipeline import opportunity_index, enrichment, seller_target_matcher, pilot_case_study, validation_study, precision_validation
from pharmadrone.pipeline.opportunity_matcher import (
    MATCH_SCOPE_LABEL,
    TECH_CERTAINTY_NOTE,
    match_problem_to_solutions,
    match_technology_to_targets,
)

# --- Password gate (server-side; password never reaches the browser) --------
auth.require_password()

# --- Deploy guardrails (set as env vars on the host) ------------------------
ALLOW_SCALE = settings.env("ALLOW_SCALE_RUNS", "").lower() in ("1", "true", "yes")
MAX_PER_RUN = int(settings.env("MAX_REPORTS_PER_RUN", "5") or "5")

profile = settings.load_profile()


def _zip_reports() -> bytes | None:
    """Zip the ./reports folder in memory for download (disk is ephemeral on
    free cloud hosts, so let the user save outputs during the session)."""
    rdir = settings.REPORTS_DIR
    files = [f for f in rdir.glob("*") if f.is_file() and f.name != ".gitkeep"]
    if not files:
        return None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=f.name)
    return buf.getvalue()


def _load_index_state(include_hidden: bool = False) -> tuple[list[dict], dict]:
    try:
        conn = db.connect(settings.DB_PATH)
        opportunity_index.backfill_generated_opportunities(conn)
        records = db.fetch_index_records(conn, include_hidden=include_hidden)
        stats = db.fetch_index_stats(conn)
        conn.close()
        return records, stats
    except Exception:
        return [], {
            "indexed_total": 0, "full_reports": 0, "waiting_queue": 0,
            "new_count": 0, "updated_count": 0, "seen_count": 0,
            "monitor_only_count": 0, "archived_hidden_count": 0,
        }


def _index_summary_text(stats: dict) -> str:
    return (
        f"{stats.get('indexed_total', 0)} indexed opportunity records · "
        f"{stats.get('full_reports', 0)} full reports · "
        f"{stats.get('waiting_queue', 0)} waiting in queue · "
        f"{stats.get('updated_count', 0)} updated since last indexing"
    )


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "; ".join(str(x) for x in value if x)
    return str(value)


def _status_label(value) -> str:
    cleaned = db.normalize_status_label(value)
    return "" if cleaned is None else str(cleaned)


def _matcher_export_csv(result: dict, mode: str, search_term: str) -> bytes:
    """CSV export for the currently displayed matcher results only."""
    rows = []
    for m in result.get("matches", []) or []:
        problem_category = (
            m.get("matched_problem_category")
            or _as_text(m.get("matched_problem_categories"))
            or _as_text(m.get("relevant_problem_categories"))
            or m.get("technology_category")
            or ""
        )
        rows.append({
            "company": m.get("company") or "",
            "short product": m.get("short_product") or m.get("product") or "",
            "match mode": mode,
            "search term": search_term,
            "match strength": m.get("match_strength") or "",
            "match reason": m.get("match_reason") or "",
            "problem category": problem_category,
            "score": m.get("opportunity_score") or "",
            "grade": m.get("grade") or "",
            "lead status": m.get("lead_status") or "",
            "source type": m.get("source_type") or m.get("evidence_source") or "",
            "first found": m.get("first_seen_at") or "",
            "last checked": m.get("last_checked_at") or "",
            "last updated": m.get("last_updated_at") or "",
            "source freshness": m.get("source_freshness") or "",
            "full report": "yes" if m.get("has_full_report") else "no",
            "corroboration status": m.get("corroboration_status") or "",
            "evidence quality": m.get("evidence_quality") or "",
            "enrichment status": m.get("enrichment_status") or "",
            "last enrichment check": m.get("last_enrichment_check") or "",
            "source coverage count": m.get("source_coverage_count") or "",
            "official follow-up status": m.get("official_followup_status") or "",
            "official follow-up count": m.get("official_followup_count") or "",
            "label context status": m.get("label_context_status") or "",
            "clinical trial context status": _status_label(m.get("clinical_trial_context_status")),
            "literature context status": m.get("literature_context_status") or "",
            "best evidence tier": m.get("best_evidence_tier") or "",
            "official source count": m.get("official_source_count") or "",
            "literature source count": m.get("literature_source_count") or "",
            "safe BD action": m.get("safe_bd_action") or m.get("safe_outreach_angle") or "",
        })
    for row in rows:
        for key in list(row.keys()):
            if "status" in key.lower() or key.lower() in {"corroboration", "enrichment"}:
                row[key] = _status_label(row.get(key))
    return pd.DataFrame(rows).to_csv(index=False).encode("utf-8-sig")


def _matcher_table_rows(result: dict) -> list[dict]:
    rows = []
    for m in result.get("matches", []) or []:
        rows.append({
            "Lead": m.get("display_title") or m.get("matching_product_problem_lead") or m.get("matching_product_company_lead"),
            "Match strength": m.get("match_strength"),
            "Problem category": m.get("matched_problem_category") or _as_text(m.get("matched_problem_categories")),
            "Opportunity Score": m.get("opportunity_score"),
            "Grade": m.get("grade"),
            "Lead status": m.get("lead_status"),
            "Source type": m.get("source_type") or m.get("evidence_source"),
            "Freshness": m.get("source_freshness") or "—",
            "Full report": "yes" if m.get("has_full_report") else "no",
            "Corroboration": m.get("corroboration_status") or "direct source only",
            "Evidence quality": m.get("evidence_quality") or "not checked",
            "Enrichment": m.get("enrichment_status") or "enrichment not checked",
            "Official follow-up": m.get("official_followup_status") or "not checked",
            "Label context": m.get("label_context_status") or "not checked",
            "Trial context": _status_label(m.get("clinical_trial_context_status")) or "not checked",
            "Literature context": m.get("literature_context_status") or "not checked",
            "Best evidence tier": m.get("best_evidence_tier") or m.get("evidence_quality") or "not checked",
            "Source coverage": m.get("source_coverage_count") or 0,
            "Last enrichment": m.get("last_enrichment_check") or "—",
            "Last checked": m.get("last_checked_at") or "—",
            "Match reason": m.get("match_reason"),
        })
    for row in rows:
        for key in list(row.keys()):
            if "status" in key.lower() or key in {"Corroboration", "Enrichment"}:
                row[key] = _status_label(row.get(key))
    return rows


def _render_match_cards(result: dict, mode: str) -> None:
    for i, m in enumerate(result.get("matches", []) or [], start=1):
        title = m.get("display_title") or m.get("matching_product_problem_lead") or m.get("matching_product_company_lead")
        st.markdown(f"#### {i}. {title}")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Match strength", m.get("match_strength") or "—")
        c2.metric("Opportunity Score", str(m.get("opportunity_score") or "—"))
        c3.metric("Grade", str(m.get("grade") or "—"))
        c4.metric("Lead status", m.get("lead_status") or "—")

        meta1, meta2, meta3 = st.columns(3)
        meta1.markdown(f"**Problem category:** {_as_text(m.get('matched_problem_category') or m.get('matched_problem_categories') or m.get('relevant_problem_categories')) or '—'}")
        meta2.markdown(f"**Source type:** {m.get('source_type') or m.get('evidence_source') or '—'}")
        meta3.markdown(f"**Full report:** {'yes' if m.get('has_full_report') else 'no — indexed preview'}")

        fresh1, fresh2, fresh3 = st.columns(3)
        fresh1.markdown(f"**First found:** {m.get('first_seen_at') or '—'}")
        fresh2.markdown(f"**Last checked:** {m.get('last_checked_at') or '—'}")
        fresh3.markdown(f"**Source freshness:** {m.get('source_freshness') or '—'}")
        if m.get('last_updated_at'):
            st.caption(f"Last updated: {m.get('last_updated_at')} · Novelty: {m.get('novelty_status') or '—'} · Queue: {m.get('queue_status') or '—'}")

        enrich1, enrich2, enrich3 = st.columns(3)
        enrich1.markdown(f"**Corroboration:** {m.get('corroboration_status') or 'direct source only'}")
        enrich2.markdown(f"**Evidence quality:** {m.get('evidence_quality') or 'not checked'}")
        enrich3.markdown(f"**Enrichment:** {m.get('enrichment_status') or 'enrichment not checked'}")
        st.caption(f"Source coverage count: {m.get('source_coverage_count') or 0} · Last enrichment check: {m.get('last_enrichment_check') or '—'}")
        context_bits = [
            m.get('official_followup_status'),
            m.get('label_context_status'),
            _status_label(m.get('clinical_trial_context_status')),
            m.get('literature_context_status'),
        ]
        context_bits = [str(x) for x in context_bits if x and str(x) not in {'not checked', 'skipped - not trial lead', 'skipped - no product/molecule', 'skipped - not FDA/regulatory lead'}]
        context_bits.append('no product-specific root cause confirmed')
        st.caption(' · '.join(context_bits))

        st.markdown(f"**Match reason:** {m.get('match_reason') or '—'}")
        if mode == "Problem → Solution Match":
            st.markdown(f"**Safe BD action:** {m.get('safe_bd_action') or 'Validate before outreach.'}")
        else:
            st.markdown(f"**Safe outreach angle:** {m.get('safe_outreach_angle') or 'Potential relevance only; requires validation.'}")


        with st.expander("Product / evidence details"):
            st.markdown("**Long stored product / recall description:**")
            st.write(m.get("long_product_description") or "No long product description stored.")
            st.markdown(f"**Evidence source:** {m.get('evidence_source') or '—'}")
            st.markdown(f"**Confirmed fact:** {m.get('confirmed_fact') or '—'}")
            if mode == "Problem → Solution Match":
                st.markdown(f"**Interpretation / hypothesis:** {m.get('interpretation_hypothesis') or '—'}")
                st.markdown("**Likely solution types:** " + _as_text(m.get("likely_solution_types")))
                st.markdown("**Possible partner categories:** " + _as_text(m.get("possible_partner_categories")))
            else:
                st.markdown(f"**Why this technology may fit:** {m.get('why_this_technology_may_fit') or '—'}")
                st.markdown("**Matched problem categories:** " + _as_text(m.get("matched_problem_categories")))
            if m.get("match_terms"):
                st.caption("Matched terms: " + _as_text(m.get("match_terms")))

        report_md = m.get("stored_report_md") or ""
        if m.get("has_full_report") and report_md.strip():
            with st.expander("Open full stored report"):
                st.caption("Score note: matcher cards show the stored Opportunity Score used for ranking. The report may also include a separate Root-Cause/Solution-Fit overall score.")
                st.markdown(report_md)
        else:
            st.caption("Full report not generated yet. This is an indexed opportunity preview. Use Continue previous queue or Generate/Refresh to create a full report.")
        st.divider()


def _seller_target_table_rows(result: dict) -> list[dict]:
    rows = []
    for m in result.get("matches", []) or []:
        rows.append({
            "Target": f"{m.get('target_company') or m.get('company') or 'Unknown'} — {m.get('product') or 'Unknown product'}",
            "Seller Fit Strength": m.get("fit_strength") or "—",
            "Risk/readiness": m.get("risk_readiness_label") or "—",
            "Problem category": m.get("problem_category") or "—",
            "Opportunity Score": m.get("opportunity_score") or "—",
            "Grade": m.get("grade") or "—",
            "Lead status": m.get("lead_status") or "—",
            "Queue status": m.get("queue_status") or "—",
            "Full report": "yes" if m.get("has_full_report") else "no",
            "Evidence quality": m.get("evidence_quality") or "not checked",
            "Best evidence tier": m.get("best_evidence_tier") or "not checked",
            "Corroboration": m.get("corroboration_status") or "direct source only",
            "Official follow-up": m.get("official_followup_status") or "not checked",
            "Label context": m.get("label_context_status") or "not checked",
            "Trial context": _status_label(m.get("clinical_trial_context_status")) or "not checked",
            "Literature context": m.get("literature_context_status") or "not checked",
            "Source coverage": m.get("source_coverage_count") or 0,
        })
    for row in rows:
        for key in list(row.keys()):
            if "status" in key.lower() or key in {"Corroboration"}:
                row[key] = _status_label(row.get(key))
    return rows


def _render_seller_target_cards(result: dict) -> None:
    for i, m in enumerate(result.get("matches", []) or [], start=1):
        title = f"{m.get('target_company') or m.get('company') or 'Unknown company'} — {m.get('product') or 'Unknown product'}"
        st.markdown(f"#### {i}. {title}")
        a, b, c, d = st.columns(4)
        a.metric("Seller Fit Strength", m.get("fit_strength") or "—")
        b.metric("Opportunity Score", str(m.get("opportunity_score") or "—"))
        c.metric("Grade", str(m.get("grade") or "—"))
        d.metric("Risk/readiness", m.get("risk_readiness_label") or "—")

        meta1, meta2, meta3, meta4 = st.columns(4)
        meta1.markdown(f"**Problem category:** {m.get('problem_category') or '—'}")
        meta2.markdown(f"**Source type:** {m.get('source_type') or '—'}")
        meta3.markdown(f"**Source ID:** {m.get('source_id') or '—'}")
        meta4.markdown(f"**Region:** {m.get('region') or '—'}")

        lead1, lead2, lead3 = st.columns(3)
        lead1.markdown(f"**Lead status:** {m.get('lead_status') or '—'}")
        lead2.markdown(f"**Queue status:** {m.get('queue_status') or '—'}")
        lead3.markdown(f"**Full report:** {'yes' if m.get('has_full_report') else 'no — indexed preview'}")

        e1, e2, e3 = st.columns(3)
        e1.markdown(f"**Evidence quality:** {m.get('evidence_quality') or 'not checked'}")
        e2.markdown(f"**Best evidence tier:** {m.get('best_evidence_tier') or 'not checked'}")
        e3.markdown(f"**Corroboration:** {m.get('corroboration_status') or 'direct source only'}")
        st.caption(
            f"Official follow-up: {m.get('official_followup_status') or 'not checked'} · "
            f"Label context: {m.get('label_context_status') or 'not checked'} · "
            f"Trial context: {_status_label(m.get('clinical_trial_context_status')) or 'not checked'} · "
            f"Literature context: {m.get('literature_context_status') or 'not checked'} · "
            f"Source coverage: {m.get('source_coverage_count') or 0}"
        )

        st.markdown(f"**Why this seller may fit:** {m.get('why_fit') or 'Possible fit; requires validation.'}")
        st.markdown(f"**What the evidence proves:** {m.get('what_evidence_proves') or 'Public evidence indicates an indexed opportunity signal.'}")
        st.markdown(f"**What the evidence does not prove:** {m.get('what_evidence_does_not_prove') or 'No product-specific root cause confirmed.'}")
        st.markdown(f"**Recommended BD angle:** {m.get('recommended_bd_angle') or m.get('safe_bd_angle') or 'Validation-led discussion only.'}")
        questions = m.get("validation_questions") or []
        if questions:
            st.markdown("**Validation questions:**")
            for q in questions:
                st.markdown(f"- {q}")
        st.markdown(f"**Safe outreach wording:** {m.get('safe_outreach_wording') or 'Use possible-fit language and validate before outreach.'}")

        if m.get("has_full_report") and (m.get("stored_report_md") or "").strip():
            with st.expander("Open full stored report"):
                st.caption("This is the existing stored report. It is not regenerated by seller-to-target matching.")
                st.markdown(m.get("stored_report_md"))
        else:
            st.caption(seller_target_matcher.PREVIEW_MESSAGE)
        st.divider()

st.title("PharmaTune / PharmaDrone — Global Pharma Opportunity Engine")
st.info(f"**{CHECKPOINT}** · both primary Generate modes are wired to bounded expanded official-source discovery.")
st.caption("Find evidence-backed pharma product problems, solution technologies, "
           "service provider categories, research innovation signals, and BD "
           "opportunities. Private dashboard · global public-source scouting "
           "where configured · opportunity signals only, require human validation.")

llm_st = settings.llm_status()
c1, c2, c3, c4 = st.columns(4)
c1.metric("LLM provider", llm_st["provider"])
c2.metric("Provider key", "set" if llm_st["key_present"] else "MISSING")
c3.metric("Tavily key", "set" if settings.HAS_TAVILY else "MISSING")
c4.metric("Budget", f"${profile.get('budget_usd_total', 200)}")
st.caption(f"Model: `{llm_st['model']}` · change with LLM_PROVIDER / LLM_MODEL env vars.")
if not llm_st["valid_provider"]:
    st.error(f"LLM_PROVIDER '{llm_st['provider']}' is not valid — use openrouter, "
             "groq, openai, or gemini.")
elif not llm_st["key_present"]:
    st.error(f"LLM provider is '{llm_st['provider']}' but {llm_st['key_env']} is "
             "not set. Add it (or switch LLM_PROVIDER) — extract/score/write need it.")

tab_gen, tab_matcher, tab_profile, tab_results, tab_conn = st.tabs(
    ["① Generate", "② Opportunity Matcher", "③ Technology Profile", "④ Results & Export", "⑤ Connectors"])

# ==========================================================================
# TAB 1 — GENERATE
# ==========================================================================
with tab_gen:
    st.subheader("Milestone 1 — generate 5 real test reports")
    st.caption("Review these 5 before scaling. The 20 + 80 buttons stay locked "
               "in your workflow until you approve the test batch.")

    regions = [r["name"] for r in profile["regions"]]
    active = [r["name"] for r in profile["regions"] if r.get("active")]
    sel_regions = st.multiselect("Regions", regions, default=active)

    all_sources = list(profile["sources"].keys())
    sel_sources = st.multiselect("Sources", all_sources,
                                 default=settings.enabled_sources(profile))
    sel_signals = st.multiselect("Problem signals", profile["problem_signals"],
                                 default=profile["problem_signals"][:6])
    use_llm_q = st.checkbox("Use LLM to craft multilingual queries "
                            "(sharper; costs a few tokens)", value=True)

    st.divider()
    st.caption(f"Budget guardrail: each run is capped at **{MAX_PER_RUN} reports** "
               "(server-side, cannot be exceeded from the UI).")
    run_mode = None
    bcol1, bcol2 = st.columns(2)
    if bcol1.button("Generate 5 Test Reports", type="primary"):
        run_mode = ("test", 5)
    if bcol2.button("Generate 5 Failure/Rescue Opportunity Reports"):
        run_mode = ("failure", 5)
    st.caption("The Failure/Rescue mode biases every query toward recalls, "
               "terminations, withdrawals, CRLs, CMC/formulation/quality/delivery "
               "problems — see `FAILURE_SIGNAL_LAYER.md`.")

    index_records_now, index_stats_now = _load_index_state(include_hidden=True)
    if index_stats_now.get("indexed_total", 0):
        st.info(_index_summary_text(index_stats_now))
        qcols = st.columns(5)
        qcols[0].metric("New leads", index_stats_now.get("new_count", 0))
        qcols[1].metric("Updated leads", index_stats_now.get("updated_count", 0))
        qcols[2].metric("Already seen", index_stats_now.get("seen_count", 0))
        qcols[3].metric("Monitor only", index_stats_now.get("monitor_only_count", 0))
        qcols[4].metric("Archived / hidden", index_stats_now.get("archived_hidden_count", 0))
    continue_requested = False
    if index_stats_now.get("waiting_queue", 0):
        continue_requested = st.button(
            f"Continue previous queue — generate next {MAX_PER_RUN} reports",
            help=f"Generate up to {MAX_PER_RUN} reports from waiting indexed opportunity records only. No new source search is run. {index_stats_now.get('waiting_queue', 0)} waiting.",
        )

    if ALLOW_SCALE:
        st.info("Scale runs are unlocked (ALLOW_SCALE_RUNS is on).")
        b2, b3 = st.columns(2)
        if b2.button("Generate 20 Flagship Reports"):
            run_mode = ("flagship", 20)
        if b3.button("Generate 80 Scouting Memos"):
            run_mode = ("memo", 80)
    else:
        st.caption("🔒 The 20 / 80 / 100 runs are hidden until you approve the "
                   "5-report test. Unlock later by setting `ALLOW_SCALE_RUNS=true` "
                   "in the host environment.")

    if continue_requested:
        prog = st.progress(0.0)
        logbox = st.empty()
        logs = []

        def qlog(m):
            logs.append(m)
            logbox.code("\n".join(logs[-16:]))

        with st.spinner("Continuing previous queue…"):
            accepted, rejected, cost, cov, dbg = continue_queue(
                5,
                progress=lambda i, t, msg: prog.progress(min(1.0, i / max(t, 1))),
                log=qlog,
            )
        st.success(f"Generated {len(accepted)} queued report(s) · {len(rejected)} rejected · "
                   f"est. ${cost.total_usd} (${cost.per_report_usd}/report)")
        if dbg.get("llm_disabled_reason"):
            st.warning(
                "LLM unavailable/rate-limited; deterministic evidence mode used. "
                "Queued reports remain usable but require validation."
            )
        idx = dbg.get("opportunity_index_stats") or {}
        if idx:
            st.info(_index_summary_text(idx))
        st.info("Open the ④ Results & Export tab to read reports and export files.")

    if run_mode:
        for r in profile["regions"]:
            r["active"] = r["name"] in sel_regions
        for s in profile["sources"]:
            profile["sources"][s]["enabled"] = s in sel_sources
        if sel_signals:
            profile["problem_signals"] = sel_signals
        settings.save_profile(profile)

        mode, n = run_mode
        prog = st.progress(0.0)
        logbox = st.empty()
        logs = []

        def log(m):
            logs.append(m)
            logbox.code("\n".join(logs[-16:]))

        with st.spinner(f"Generating ({mode})…"):
            accepted, rejected, cost, cov, dbg = generate(
                mode, n, use_llm_queries=use_llm_q,
                progress=lambda i, t, msg: prog.progress(min(1.0, i / max(t, 1))),
                log=log)

        st.success(f"Generated {len(accepted)} reports · {len(rejected)} rejected · "
                   f"est. ${cost.total_usd} (${cost.per_report_usd}/report)")

        if dbg.get("llm_disabled_reason"):
            st.warning(
                "LLM unavailable/rate-limited; deterministic evidence mode used. "
                "Candidate discovery, scoring fallback, and stored reports remain usable but require validation."
            )

        if dbg.get("web_enrichment_unavailable"):
            st.warning(
                "Web enrichment unavailable for this run because Tavily/API failed. "
                "The run continued using structured sources and deterministic evidence; web corroboration was not available."
            )

        idx = dbg.get("opportunity_index_stats") or dbg.get("opportunity_index_stats_pre_report") or {}
        if idx:
            st.info(_index_summary_text(idx))

        if not accepted:
            st.error("0 reports generated. Open the Debug panel below — it shows "
                     "exactly where candidates were lost (LLM batch failures, "
                     "rejection reasons, or too little evidence).")

        st.markdown("### Source coverage summary")
        st.caption("Global public-source scouting — not complete global regulator "
                   "coverage.")
        cov_df = pd.DataFrame([
            {"Source": s, "Status": d.get("status", "—"),
             "Raw source results": d.get("raw_results", d.get("evidence_items", 0)),
             "Taxonomy raw": d.get("taxonomy_raw_results", 0),
             "Fallback/sweep raw": d.get("fallback_sweep_raw_results") or d.get("newest_sweep_raw_results", 0),
             "Taxonomy configured": d.get("taxonomy_query_specs_configured", 0),
             "Taxonomy attempted": d.get("taxonomy_query_calls", 0),
             "Fallback calls": d.get("fallback_sweep_query_calls", 0),
             "Expansion diagnostic": d.get("expansion_diagnostic", "—"),
             "Evidence items": d.get("evidence_items", 0),
             "Candidates created": d.get("candidate_records_created", 0),
             "Candidates rejected": d.get("candidate_records_rejected", 0),
             "Indexed leads": d.get("indexed_leads", 0),
             "Full reports citing": d.get("full_reports_citing", d.get("accepted_leads_citing", 0)),
             "Queries": d.get("queries", 0), "Succeeded": d.get("succeeded", 0),
             "Failed": d.get("failed", 0)}
            for s, d in cov.items()])
        st.dataframe(cov_df, use_container_width=True, hide_index=True)

        errors = [e for d in cov.values() for e in d.get("errors", [])]
        warnings = [w for d in cov.values() for w in d.get("warnings", [])]
        if errors or warnings:
            st.caption(
                "Some source/API diagnostics are available in developer/debug mode and debug exports. "
                "Normal reports show evidence gaps rather than raw API error text."
            )
            with st.expander("Developer/debug: source/API diagnostics", expanded=False):
                if errors:
                    st.markdown(f"**Source failure details ({len(errors)}):**")
                    st.code("\n".join(errors[:30]))
                if warnings:
                    st.markdown(f"**Source warnings ({len(warnings)}), including sanitised fallback queries if used:**")
                    st.code("\n".join(warnings[:20]))

        with st.expander("🔍 Debug: candidate pipeline (raw evidence → reports)",
                         expanded=not accepted):
            disc = dbg.get('discovery_breakdown', {})
            fbinfo = dbg.get('fallback_info', {})
            st.markdown(f"""
- **Raw evidence items:** {dbg.get('raw_evidence_count', 0)}
- **Deterministic cluster classification:**
  - valid BD opportunity: **{disc.get('valid_bd_opportunity', 0)}**
  - weak academic cluster (discarded): {disc.get('weak_academic_cluster', 0)}
  - rejected generic literature (discarded): {disc.get('rejected_generic_literature', 0)}
- **LLM opportunity extraction:** {dbg.get('llm_extraction', {}).get('batches_ok', 0)}/{dbg.get('llm_extraction', {}).get('batches_total', 0)} batch(es) ok · {dbg.get('llm_extraction', {}).get('rejected_generic', 0)} generic entity(ies) rejected
- **LLM failure-signal extraction:** {dbg.get('failure_llm_extraction', {}).get('batches_ok', 0)}/{dbg.get('failure_llm_extraction', {}).get('batches_total', 0)} batch(es) ok · {dbg.get('failure_llm_extraction', {}).get('rejected_generic', 0)} generic entity(ies) rejected
- **Candidates after dedup:** {dbg.get('candidates_after_dedup', 0)}
- **Fallback:** generated {fbinfo.get('generated', 0)} · valid targets available {fbinfo.get('valid_available', 0)} · _{fbinfo.get('reason','n/a')}_
- **Pre-scoring cap:** kept **{dbg.get('candidates_kept_for_scoring', 0)}** of {dbg.get('candidates_total_pre_cap', 0)} for scoring · {dbg.get('candidates_skipped_by_cap', 0)} skipped (deterministic ranking)
- **Root-cause corroboration:** {dbg.get('corroboration', {}).get('searched_leads', dbg.get('corroboration', {}).get('searched', 0))} lead(s) · {dbg.get('corroboration', {}).get('queries_run', 0)} query(ies) · {dbg.get('corroboration', {}).get('attached', dbg.get('corroboration', {}).get('hits', 0))} attached · {dbg.get('corroboration', {}).get('hits_retrieved', 0)} hit(s) retrieved · {dbg.get('corroboration', {}).get('rejected', 0)} rejected · {dbg.get('corroboration', {}).get('no_hits', 0)} no-hit query(ies) · {dbg.get('corroboration', {}).get('api_failed', 0)} API-failed query(ies)
- **LLM status:** {'🔴 unavailable/rate-limited — deterministic evidence mode used · ' + dbg.get('llm_disabled_reason','') if dbg.get('llm_disabled_reason') else '🟢 active (or not needed)'}
- **Final valid-target gate dropped:** {dbg.get('final_gate_dropped', 0)} (no real product/company/trial)
- **Accepted:** {dbg.get('accepted_count', 0)} · **Rejected:** {dbg.get('rejected_count', 0)}
  - rejected (too little evidence): {dbg.get('scoring', {}).get('rejected_low_evidence', 0)}
  - rejected (grade D): {dbg.get('scoring', {}).get('rejected_grade_d', 0)}
""")
            st.markdown(f"**Runtime checkpoint:** `{dbg.get('checkpoint_version') or CHECKPOINT}`")
            st.markdown(
                "**Official discovery path:** "
                + ("expanded for this Generate mode" if dbg.get("expanded_official_discovery") else "not run / not applicable")
            )
            effective_settings = dbg.get("effective_discovery_settings", {}) or {}
            if effective_settings:
                st.markdown("**Effective discovery settings used in this run:**")
                setting_rows = []
                for source_name, source_settings in effective_settings.items():
                    for setting_name, setting_value in (source_settings or {}).items():
                        setting_rows.append({
                            "Source": source_name,
                            "Setting": setting_name,
                            "Effective value": setting_value,
                        })
                st.dataframe(pd.DataFrame(setting_rows), use_container_width=True, hide_index=True)

            source_pipeline = dbg.get("source_candidate_pipeline", {}) or {}
            if source_pipeline:
                st.markdown("**Source → candidate → index diagnostics:**")
                source_rows = []
                for source_name, values in source_pipeline.items():
                    source_rows.append({
                        "Source": source_name,
                        "Raw source results": values.get("raw_source_results", values.get("raw_evidence", 0)),
                        "Taxonomy raw": values.get("taxonomy_raw_results", 0),
                        "Fallback/sweep raw": values.get("fallback_sweep_raw_results") or values.get("newest_sweep_raw_results", 0),
                        "Taxonomy accepted": values.get("taxonomy_accepted_unique", 0),
                        "Fallback/sweep accepted": values.get("fallback_sweep_accepted_unique") or values.get("newest_sweep_accepted_unique", 0),
                        "Taxonomy rejected": values.get("taxonomy_records_rejected", 0),
                        "Fallback/sweep rejected": values.get("fallback_sweep_records_rejected", 0),
                        "Taxonomy configured": values.get("taxonomy_query_specs_configured", 0),
                        "Taxonomy attempted": values.get("taxonomy_query_calls", 0),
                        "Fallback calls": values.get("fallback_sweep_query_calls", 0),
                        "Expansion diagnostic": values.get("expansion_diagnostic", "—"),
                        "API total available": values.get("api_total_available") if values.get("api_total_available") is not None else "—",
                        "Evidence after source gates": values.get("raw_evidence", 0),
                        "Rejected at source gate": values.get("source_records_rejected", 0),
                        "Candidates created": values.get("candidate_records_created", 0),
                        "Candidates rejected": values.get("candidate_records_rejected", 0),
                        "Indexed leads": values.get("indexed_leads", 0),
                    })
                st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)
                st.markdown("#### Candidate rejection reasons by source")
                rejection_rows = []
                for source_name, values in source_pipeline.items():
                    for reason, count in (values.get("source_rejection_reasons") or {}).items():
                        rejection_rows.append({
                            "Source": source_name, "Stage": "source gate",
                            "Reason": reason, "Count": count,
                        })
                    for reason, count in (values.get("candidate_rejection_reasons") or {}).items():
                        rejection_rows.append({
                            "Source": source_name, "Stage": "candidate construction",
                            "Reason": reason, "Count": count,
                        })
                if rejection_rows:
                    st.dataframe(pd.DataFrame(rejection_rows), use_container_width=True, hide_index=True)
                else:
                    st.caption("No candidate-construction rejections recorded for this run.")

            examples = disc.get('discarded_examples', [])
            if examples:
                st.markdown("**Discarded clusters (why they are NOT reports):**")
                st.dataframe(pd.DataFrame(examples), use_container_width=True,
                             hide_index=True)

            if accepted:
                rows = []
                for o in accepted:
                    cats = sorted({e.get("source_category") for e in o.get("evidence", [])
                                  if e.get("source_category")})
                    rows.append({
                        "target": o.get("company") or o.get("product"),
                        "valid_target_type": o.get("valid_target_type", "—"),
                        "event_confirmed": bool(o.get("failure_event_confirmed")),
                        "event": o.get("event_type") or "—",
                        "source_categories": ", ".join(cats) or "—",
                        "has_reg/company/trial": any(c in cats for c in
                            ("regulatory", "company", "trial")),
                        "why_accepted": o.get("discovery_reason")
                            or ("LLM-extracted opportunity"
                                if o.get("discovery_method") == "llm-extraction"
                                else "scored opportunity"),
                    })
                st.markdown("**Accepted candidates — why each is a valid target "
                            "(not a generic cluster):**")
                st.dataframe(pd.DataFrame(rows), use_container_width=True,
                             hide_index=True)

            llm_errs = (dbg.get('llm_extraction', {}).get('errors', [])
                       + dbg.get('failure_llm_extraction', {}).get('errors', [])
                       + dbg.get('scoring', {}).get('score_errors', []))
            if llm_errs:
                st.markdown("**LLM errors during this run:**")
                st.code("\n".join(llm_errs[:20]))
            top10 = dbg.get('top_entities', [])
            if top10:
                st.markdown("**Top valid product/company names extracted before scoring:**")
                st.dataframe(pd.DataFrame(top10), use_container_width=True, hide_index=True)

        with st.expander("Cost breakdown"):
            st.json(cost.summary())
        st.info("Open the ④ Results & Export tab to read reports and export files.")


# ===========================================================================
# TAB 2 — OPPORTUNITY MATCHER
# ===========================================================================
with tab_matcher:
    st.subheader("Opportunity Matcher — indexed evidence")
    st.caption(
        "Matched against currently indexed PharmaTune evidence. Use Generate/Refresh to add new signals. "
        "This tab matches stored product/problem signals to solution types, partner categories, "
        "or technology-target hypotheses."
    )
    st.caption("Potential relevance only · Requires validation · Not proof that the company needs this technology.")

    matcher_opps, matcher_stats = _load_index_state(include_hidden=False)
    matcher_ev = []

    if not matcher_opps:
        st.info("Run Generate first to create evidence-backed opportunities, then use the matcher.")
        st.caption(
            "The matcher is intentionally read-only. It searches indexed PharmaTune evidence only, "
            "and will not invent leads or pretend to search the whole world when no stored evidence exists."
        )
    else:
        st.success(_index_summary_text(matcher_stats))
        mcols = st.columns(4)
        mcols[0].metric("New", matcher_stats.get("new_count", 0))
        mcols[1].metric("Updated", matcher_stats.get("updated_count", 0))
        mcols[2].metric("Waiting", matcher_stats.get("waiting_queue", 0))
        mcols[3].metric("Monitor only", matcher_stats.get("monitor_only_count", 0))
        mode = st.radio(
            "Matcher mode",
            ["Problem → Solution Match", "Technology → Target Match"],
            horizontal=True,
        )

        default_query = (
            "dissolution failure"
            if mode == "Problem → Solution Match"
            else "particle engineering technology"
        )
        query = st.text_input("Search term", value=default_query)

        with st.expander("Search examples"):
            colp, colt = st.columns(2)
            with colp:
                st.markdown("**Problem searches**")
                st.markdown(
                    "- dissolution failure\n"
                    "- stability issue\n"
                    "- impurity issue\n"
                    "- sterility issue\n"
                    "- bioavailability issue"
                )
            with colt:
                st.markdown("**Technology searches**")
                st.markdown(
                    "- particle engineering technology\n"
                    "- solubility enhancement technology\n"
                    "- analytical/QC service\n"
                    "- formulation CDMO\n"
                    "- solid-state characterisation"
                )

        max_matches = st.slider("Maximum matches to show", 3, 20, 10)
        include_weak = st.checkbox(
            "Include related/weak matches",
            value=False,
            help=(
                "Default shows high-specificity matches only. Descriptor-only "
                "phrases such as extended release or immediate release are treated as "
                "background, not evidence of dissolution failure."
            ),
        )

        if st.button("Run Opportunity Match", type="primary"):
            if mode == "Problem → Solution Match":
                result = match_problem_to_solutions(
                    query, matcher_opps, matcher_ev, limit=max_matches, include_weak=include_weak
                )
            else:
                result = match_technology_to_targets(
                    query, matcher_opps, matcher_ev, limit=max_matches, include_weak=include_weak
                )
            st.session_state["opportunity_matcher_result"] = {
                "result": result,
                "mode": mode,
                "query": query,
                "include_weak": include_weak,
            }

        cached = st.session_state.get("opportunity_matcher_result")
        if cached:
            result = cached["result"]
            result_mode = cached["mode"]
            result_query = cached["query"]

            if result.get("status") == "ok":
                st.markdown(f"**{MATCH_SCOPE_LABEL}**")
                if result_mode == "Problem → Solution Match":
                    st.markdown(f"**Searched problem:** {result.get('searched_problem', result_query)}")
                    st.markdown(f"**Matched problem category:** {result.get('matched_problem_category', '—')}")
                    st.markdown("**Likely solution types:** " + _as_text(result.get("likely_solution_types", [])))
                    st.markdown("**Possible partner categories:** " + _as_text(result.get("possible_partner_categories", [])))
                    st.info(result.get("safe_bd_action", "Validate the evidence before outreach."))
                else:
                    st.markdown(f"**Searched technology:** {result.get('searched_technology', result_query)}")
                    st.markdown(f"**Technology category:** {result.get('technology_category', '—')}")
                    st.markdown("**Relevant problem categories:** " + _as_text(result.get("relevant_problem_categories", [])))
                    st.info(result.get("why_this_technology_may_fit", "Potential relevance only; requires validation."))

                table_rows = _matcher_table_rows(result)
                if table_rows:
                    st.caption("Score shown here is the stored Opportunity Score used for app ranking. Root-Cause/Solution-Fit report sections may show a separate overall score.")
                    st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
                    st.download_button(
                        "⬇ Download current matcher results (.csv)",
                        _matcher_export_csv(result, result_mode, result_query),
                        file_name="pharmatune_opportunity_matcher_results.csv",
                        mime="text/csv",
                    )
                if result.get("hidden_weak_count"):
                    st.caption(
                        f"{result.get('hidden_weak_count')} related/weak/background match(es) hidden. "
                        "Tick 'Include related/weak matches' to review them."
                    )
                _render_match_cards(result, result_mode)

            else:
                st.warning(result.get("message"))
                if result_mode == "Problem → Solution Match":
                    if result.get("likely_solution_types"):
                        st.markdown("**Relevant solution types for this problem category:** " + _as_text(result.get("likely_solution_types", [])))
                else:
                    if result.get("technology_category"):
                        st.markdown(f"**Technology category:** {result.get('technology_category')}")
                        st.markdown("**Relevant problem categories:** " + _as_text(result.get("relevant_problem_categories", [])))
                if result.get("hidden_weak_count"):
                    st.caption(
                        f"{result.get('hidden_weak_count')} related/weak/background match(es) hidden. "
                        "Tick 'Include related/weak matches' to review them."
                    )

# ==========================================================================
# TAB 3 — TECHNOLOGY PROFILE
# ==========================================================================
with tab_profile:
    st.subheader("Seller / Technology Profile")
    sp = profile["seller_profile"]
    sp["name"] = st.text_input("Seller name", sp.get("name", ""))
    sp["description"] = st.text_area("Description", sp.get("description", ""), height=90)
    st.markdown("**Problem signals** (one per line)")
    sigs = st.text_area("signals", "\n".join(profile["problem_signals"]),
                        label_visibility="collapsed", height=180)
    st.markdown("**Regions** — tick to activate")
    cols = st.columns(3)
    for i, r in enumerate(profile["regions"]):
        with cols[i % 3]:
            r["active"] = st.checkbox(f"{r['name']} ({r['lang']})",
                                      r.get("active"), key=f"reg_{r['code']}")
    colf, colm = st.columns(2)
    profile["output"]["flagship_reports"] = colf.number_input(
        "Flagship reports", 1, 100, profile["output"]["flagship_reports"])
    profile["output"]["scouting_memos"] = colm.number_input(
        "Scouting memos", 1, 500, profile["output"]["scouting_memos"])
    profile["output"]["min_evidence_links"] = st.slider(
        "Min evidence links to accept a lead", 1, 5,
        profile["output"].get("min_evidence_links", 2))
    if st.button("Save profile"):
        profile["problem_signals"] = [s.strip() for s in sigs.splitlines() if s.strip()]
        settings.save_profile(profile)
        st.success("Saved to config/technology_profile.yaml")

    st.divider()
    st.markdown("### Seller-to-Target Opportunity Workflow")
    st.caption(
        "Match a seller/company capability profile against existing indexed PharmaTune evidence only. "
        "This does not call APIs, rerun discovery, regenerate reports, or change Opportunity Score."
    )
    st.info(seller_target_matcher.FIT_NOTE)

    seller_name_for_match = st.text_input(
        "Seller/company name for target matching",
        value=sp.get("name", ""),
        key="seller_target_name",
    )
    seller_desc_for_match = st.text_area(
        "Seller/company capability description",
        value=sp.get("description", ""),
        height=90,
        key="seller_target_description",
    )
    cap_default = [
        "particle engineering",
        "solubility enhancement",
        "formulation CDMO",
    ]
    capability_categories = st.multiselect(
        "Technology/service categories",
        seller_target_matcher.DISPLAY_CATEGORIES,
        default=[c for c in cap_default if c in seller_target_matcher.DISPLAY_CATEGORIES],
        help="These categories are mapped deterministically to indexed product/problem signals.",
    )
    problem_interest_text = st.text_area(
        "Problem signals of interest",
        "dissolution failure\npoor solubility\nlow bioavailability\nformulation challenge",
        height=110,
        help="Optional. One per line. These refine the deterministic fit label; they do not create new evidence.",
    )
    dosage_options = ["Any", "oral solid", "injectable", "topical", "inhalation", "drug-device"]
    region_options = ["Any"] + [r["name"] for r in profile.get("regions", [])]
    f1, f2, f3 = st.columns(3)
    dosage_focus = f1.multiselect("Dosage-form / modality focus", dosage_options, default=["Any"])
    region_pref = f2.multiselect("Region preference", region_options, default=["Any"])
    min_quality = f3.selectbox(
        "Minimum evidence quality",
        ["Any", "Tier 1 / high", "Tier 2 / moderate or better", "Tier 3 / limited or better", "Tier 4 / weak or better"],
        index=0,
    )
    cmon, cweak, cmax = st.columns(3)
    include_monitor_only = cmon.checkbox(
        "Include monitor-only leads",
        value=True,
        help="Old/terminated/one-lot recall leads remain monitor only unless stronger current evidence exists.",
    )
    include_weak_fit = cweak.checkbox(
        "Include weak/background fits",
        value=False,
        help="Default focuses on Strong/Moderate seller-fit matches.",
    )
    max_targets = cmax.number_input("Maximum targets to show", min_value=1, max_value=50, value=10, step=1)

    if st.button("Find target opportunities", type="primary"):
        fresh_records, fresh_stats = _load_index_state(include_hidden=False)
        result = seller_target_matcher.match_seller_to_targets(
            seller_name_for_match,
            seller_desc_for_match,
            capability_categories,
            fresh_records,
            problem_signals=problem_interest_text,
            dosage_focus=dosage_focus,
            region_preference=region_pref,
            min_evidence_quality=min_quality,
            include_monitor_only=include_monitor_only,
            max_targets=int(max_targets),
            include_weak=include_weak_fit,
        )
        st.session_state["seller_target_result"] = {
            "result": result,
            "stats": fresh_stats,
        }

    cached_seller_result = st.session_state.get("seller_target_result")
    if cached_seller_result:
        result = cached_seller_result.get("result") or {}
        stats = cached_seller_result.get("stats") or {}
        if stats:
            st.caption(_index_summary_text(stats))
        if result.get("status") == "ok":
            st.success(result.get("message"))
            st.markdown(f"**{seller_target_matcher.MATCH_SCOPE_LABEL}**")
            st.caption(result.get("fit_note") or seller_target_matcher.FIT_NOTE)
            cols = st.columns(4)
            cols[0].metric("Targets shown", len(result.get("matches", []) or []))
            cols[1].metric("Weak/background hidden", result.get("hidden_weak_count", 0))
            cols[2].metric("Monitor-only hidden", result.get("hidden_monitor_count", 0))
            cols[3].metric("Quality/region hidden", (result.get("hidden_quality_count", 0) or 0) + (result.get("hidden_region_count", 0) or 0))
            rows = _seller_target_table_rows(result)
            if rows:
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.download_button(
                    "⬇ Download seller target matches (.csv)",
                    seller_target_matcher.export_seller_target_matches_csv(result),
                    file_name="seller_target_matches.csv",
                    mime="text/csv",
                )
            _render_seller_target_cards(result)
        elif result.get("status") == "empty":
            st.info(result.get("message"))
        else:
            st.warning(result.get("message") or seller_target_matcher.NO_TARGETS_MESSAGE)
            if result.get("hidden_monitor_count"):
                st.caption("Some potential historical/monitor-only signals were hidden by your filters.")
            if result.get("hidden_weak_count"):
                st.caption("Some weak/background fits were hidden by your filters.")

# ==========================================================================
# TAB 4 — RESULTS & EXPORT
# ==========================================================================
with tab_results:
    st.subheader("Generated opportunities")
    try:
        conn = db.connect(settings.DB_PATH)
        opportunity_index.backfill_generated_opportunities(conn)
        opps = db.fetch_all(conn, "opportunities")
        ev = db.fetch_all(conn, "evidence")
        rej = db.fetch_all(conn, "rejected")
        idx_records = db.fetch_index_records(conn, include_hidden=True)
        idx_stats = db.fetch_index_stats(conn)
        conn.close()
    except Exception:
        opps, ev, rej, idx_records, idx_stats = [], [], [], [], {}

    st.markdown("### Opportunity index")
    if idx_stats.get("indexed_total", 0):
        st.info(_index_summary_text(idx_stats))
        st.caption("SQLite persistence is suitable for this local/Streamlit MVP, but it is not production SaaS persistence.")
        precision_records = [
            precision_validation.annotate_record(
                r,
                seller_profile="Specialist formulation / drug-product technology provider particle engineering solubility enhancement formulation CDMO dissolution testing analytical/QC testing stability troubleshooting impurity investigation",
                official_source_url=precision_validation.extract_stored_official_url(r),
            )
            for r in idx_records
        ]
        preview = pd.DataFrame(precision_records)
        if "problem_category" in preview.columns:
            preview["problem_category"] = preview["problem_category"].apply(opportunity_index.clean_problem_category)
        for _status_col in [c for c in preview.columns if c.endswith("_status") or c in {"enrichment_status", "corroboration_status"}]:
            preview[_status_col] = preview[_status_col].apply(_status_label)
        show_cols = [c for c in [
            "stable_lead_id", "company", "product", "problem_category", "source_type",
            "source_id", "region", "score", "grade", "lead_status", "novelty_status",
            "queue_status", "has_full_report", "corroboration_status", "evidence_quality",
            "enrichment_status", "source_coverage_count", "last_enrichment_check",
            "official_followup_status", "label_context_status", "clinical_trial_context_status",
            "literature_context_status", "best_evidence_tier", "official_source_count",
            "literature_source_count", "signal_tier", "signal_type", "specific_problem_subcategory",
            "source_id_verification_status", "source_record_present",
            "source_id_verified_by_structured_source", "manual_audit_status",
            "company_match_warning", "company_identity_mismatch",
            "company_role_difference", "technical_manufacturer_differs",
            "target_is_product_owner_or_sponsor", "target_is_distributor_or_repackager_only",
            "product_type_warning", "clinical_trial_signal_code",
            "clinical_trial_signal_reason", "clinical_trial_evidence_field",
            "clinical_trial_evidence_text", "audit_correction_note",
            "external_case_study_eligible", "exclusion_reason",
            "first_seen_at", "last_checked_at", "last_updated_at"
        ] if c in preview.columns]
        if show_cols:
            st.dataframe(preview[show_cols], use_container_width=True, hide_index=True)

        st.markdown("#### Enrichment queue")
        st.caption("Capped enrichment checks indexed leads only. It does not rerun discovery and does not change Opportunity Score. Phase 3B adds official follow-up, label, trial, and literature context where available.")
        ecol1, ecol2 = st.columns([1, 3])
        enrich_clicked = ecol1.button("Enrich indexed leads — next 5")
        use_web_enrich = ecol2.checkbox(
            "Use Tavily/web if available for enrichment",
            value=True,
            help="If Tavily is unavailable, enrichment still records evidence quality from indexed evidence only.",
        )
        if enrich_clicked:
            with st.spinner("Enriching indexed leads…"):
                conn_e = db.connect(settings.DB_PATH)
                logs_e = []
                result_e = enrichment.enrich_indexed_leads(
                    conn_e, limit=5, use_web=use_web_enrich, log=lambda m: logs_e.append(m)
                )
                opportunity_index.export_index_csv(conn_e, settings.REPORTS_DIR)
                conn_e.close()
            st.success(result_e.get("message", "Enrichment completed."))
            # Avoid stale enrichment labels in cached matcher/seller cards after the enrichment queue updates SQLite.
            st.session_state.pop("opportunity_matcher_result", None)
            st.session_state.pop("seller_target_result", None)
            st.session_state.pop("pilot_case_study_result", None)
            st.session_state.pop("validation_study_result", None)
            if logs_e:
                with st.expander("Developer/debug: enrichment log", expanded=False):
                    st.code("\n".join(logs_e[-30:]))
    else:
        st.caption("No indexed opportunity records yet.")

    st.divider()
    st.markdown("### 20-company pilot case study")
    st.caption(
        "Build a configurable, deterministic pilot from currently indexed/enriched PharmaTune evidence only. "
        "This workflow does not call APIs or an LLM and does not change Opportunity Scores or stable lead IDs."
    )

    pilot_region_options = sorted({
        str(r.get("region") or "").strip()
        for r in idx_records
        if str(r.get("region") or "").strip()
    })
    pilot_problem_options = list(dict.fromkeys(
        list(pilot_case_study.DEFAULT_PROBLEM_SIGNALS)
        + list(profile.get("problem_signals", []) or [])
    ))
    pilot_capability_options = list(seller_target_matcher.DISPLAY_CATEGORIES)

    with st.form("pilot_case_study_form"):
        st.markdown("#### Case-study lens / seller profile")
        pilot_title = st.text_input(
            "Case study title",
            value=pilot_case_study.DEFAULT_CASE_STUDY_TITLE,
        )
        pilot_objective = st.text_area(
            "Case study objective",
            value=pilot_case_study.DEFAULT_CASE_STUDY_OBJECTIVE,
            height=110,
        )
        pilot_seller_profile = st.text_input(
            "Seller / technology / service profile",
            value=pilot_case_study.DEFAULT_SELLER_SERVICE_PROFILE,
        )
        pilot_capabilities = st.multiselect(
            "Capability categories",
            pilot_capability_options,
            default=[x for x in pilot_case_study.DEFAULT_CAPABILITIES if x in pilot_capability_options],
        )
        pilot_problem_signals = st.multiselect(
            "Problem signals of interest",
            pilot_problem_options,
            default=[x for x in pilot_case_study.DEFAULT_PROBLEM_SIGNALS if x in pilot_problem_options],
        )
        pf1, pf2 = st.columns(2)
        pilot_regions = pf1.multiselect(
            "Region filter",
            pilot_region_options,
            default=[],
            help="Leave blank to use all indexed regions.",
        )
        pilot_min_quality = pf2.selectbox(
            "Minimum evidence quality",
            ["Any", "Tier 1 / high", "Tier 2 / moderate", "Tier 3 / limited", "Tier 4 / weak"],
            index=0,
        )
        pf3, pf4, pf5 = st.columns(3)
        pilot_include_monitor = pf3.checkbox("Include monitor-only leads", value=True)
        pilot_include_previews = pf4.checkbox("Include preview-only records", value=True)
        pilot_max_targets = pf5.number_input(
            "Maximum targets",
            min_value=1,
            max_value=20,
            value=20,
            step=1,
        )
        build_pilot_clicked = st.form_submit_button("Build 20-company pilot set", type="primary")

    if build_pilot_clicked:
        fresh_pilot_records, _ = _load_index_state(include_hidden=False)
        with st.spinner("Building the pilot set from indexed evidence…"):
            st.session_state["pilot_case_study_result"] = pilot_case_study.build_pilot_case_study(
                fresh_pilot_records,
                limit=int(pilot_max_targets),
                case_study_title=pilot_title,
                case_study_objective=pilot_objective,
                seller_service_profile=pilot_seller_profile,
                capability_categories=pilot_capabilities,
                problem_signals=pilot_problem_signals,
                region_filter=pilot_regions,
                include_monitor_only=pilot_include_monitor,
                include_preview_only=pilot_include_previews,
                minimum_evidence_quality=pilot_min_quality,
            )

    pilot_result = st.session_state.get("pilot_case_study_result")
    if pilot_result:
        if pilot_result.get("status") == "ok":
            st.success(pilot_result.get("message") or "Pilot set built.")
            pilot_profile = pilot_result.get("case_study_profile", {}) or {}
            st.markdown(f"#### {pilot_profile.get('case_study_title') or pilot_case_study.DEFAULT_CASE_STUDY_TITLE}")
            st.caption(pilot_profile.get("case_study_objective") or pilot_case_study.DEFAULT_CASE_STUDY_OBJECTIVE)
            with st.expander("Selected case-study profile and filters", expanded=False):
                st.markdown(f"**Seller/service profile:** {pilot_profile.get('seller_service_profile') or '—'}")
                st.markdown(f"**Capabilities:** {_as_text(pilot_profile.get('capability_categories')) or '—'}")
                st.markdown(f"**Problem signals:** {_as_text(pilot_profile.get('problem_signals')) or '—'}")
                st.markdown(f"**Regions:** {_as_text(pilot_profile.get('region_filter')) or 'All regions'}")
                st.markdown(
                    f"**Filters:** include monitor-only = {'yes' if pilot_profile.get('include_monitor_only') else 'no'} · "
                    f"include previews = {'yes' if pilot_profile.get('include_preview_only') else 'no'} · "
                    f"minimum evidence = {pilot_profile.get('minimum_evidence_quality') or 'Any'} · "
                    f"maximum targets = {pilot_profile.get('maximum_targets') or 20}"
                )
                st.caption("Targets were selected from indexed public evidence and are possible BD targets requiring validation, not confirmed customer needs.")
            pm = pilot_result.get("metrics", {}) or {}
            p1, p2, p3, p4, p5 = st.columns(5)
            p1.metric("Indexed reviewed", pm.get("total_indexed_records_reviewed", 0))
            p2.metric("Selected targets", pm.get("target_opportunities_selected", 0))
            p3.metric("Full reports", pm.get("full_reports_count", 0))
            p4.metric("Preview-only", pm.get("preview_only_count", 0))
            p5.metric("Enriched", pm.get("enriched_count", 0))

            p6, p7, p8, p9, p10 = st.columns(5)
            p6.metric("Tier 1 / high", pm.get("tier1_high_count", 0))
            p7.metric("Tier 2", pm.get("tier2_count", 0))
            p8.metric("Monitor only", pm.get("monitor_only_count", 0))
            p9.metric("Strong fit", pm.get("strong_fit_count", 0))
            p10.metric("Average score", pm.get("average_opportunity_score") if pm.get("average_opportunity_score") is not None else "—")

            with st.expander("Pilot distributions and method", expanded=False):
                st.markdown(f"**Evidence strength:** {_as_text(pm.get('evidence_strength_distribution'))}")
                st.markdown(f"**Readiness:** {_as_text(pm.get('readiness_distribution'))}")
                st.markdown(f"**Seller fit:** {_as_text(pm.get('seller_fit_distribution'))}")
                st.markdown(f"**Source types:** {_as_text(pm.get('source_type_breakdown'))}")
                st.markdown(f"**Problem categories:** {_as_text(pm.get('problem_category_breakdown'))}")
                st.caption(pilot_result.get("method_note") or "")

            pilot_rows = pilot_result.get("rows", []) or []
            pilot_preview = pd.DataFrame(pilot_rows)
            pilot_cols = [c for c in [
                "pilot_rank", "target_company", "product", "molecule", "problem_category",
                "source_type", "source_id", "region", "opportunity_score", "grade",
                "lead_status", "queue_status", "evidence_quality", "best_evidence_tier",
                "direct_source_evidence_status", "seller_fit_strength", "has_full_report", "source_coverage_count",
                "safe_bd_angle", "validation_questions"
            ] if c in pilot_preview.columns]
            if pilot_cols:
                st.dataframe(pilot_preview[pilot_cols], use_container_width=True, hide_index=True)

            limitations = pilot_result.get("limitations", []) or []
            if limitations:
                st.warning("Pilot limitations:\n- " + "\n- ".join(str(x) for x in limitations))

            pd1, pd2 = st.columns(2)
            pd1.download_button(
                "⬇ Download pilot_20_company_case_study.csv",
                pilot_case_study.export_pilot_csv(pilot_result),
                file_name="pilot_20_company_case_study.csv",
                mime="text/csv",
            )
            pd2.download_button(
                "⬇ Download pilot_20_company_case_study_summary.md",
                pilot_case_study.export_pilot_markdown(pilot_result),
                file_name="pilot_20_company_case_study_summary.md",
                mime="text/markdown",
            )
        else:
            st.warning(pilot_result.get("message") or "No eligible pilot opportunities were found.")
            for limitation in pilot_result.get("limitations", []) or []:
                st.caption(str(limitation))
    elif not idx_stats.get("indexed_total", 0):
        st.caption("Run Generate first to create indexed PharmaTune evidence before building the pilot case study.")

    st.divider()
    st.markdown("### 100-target validation study")
    st.caption(
        "Build an internal, audit-ready validation set from existing indexed/enriched PharmaTune evidence only. "
        "This workflow does not call APIs or an LLM and does not change Opportunity Scores, stable lead IDs, queues, enrichment, or reports."
    )

    validation_region_options = sorted({
        str(r.get("region") or "").strip()
        for r in idx_records
        if str(r.get("region") or "").strip()
    })
    validation_problem_options = list(dict.fromkeys(
        list(validation_study.DEFAULT_PROBLEM_SIGNALS)
        + list(profile.get("problem_signals", []) or [])
    ))
    validation_capability_options = list(seller_target_matcher.DISPLAY_CATEGORIES)

    with st.form("validation_study_form"):
        validation_title = st.text_input(
            "Validation study title",
            value=validation_study.DEFAULT_VALIDATION_TITLE,
        )
        validation_seller_profile = st.text_area(
            "Seller / service profile",
            value=validation_study.DEFAULT_SELLER_SERVICE_PROFILE,
            height=80,
        )
        validation_capabilities = st.multiselect(
            "Capability categories",
            validation_capability_options,
            default=[x for x in validation_study.DEFAULT_CAPABILITIES if x in validation_capability_options],
        )
        validation_problem_signals = st.multiselect(
            "Problem signals of interest",
            validation_problem_options,
            default=[x for x in validation_study.DEFAULT_PROBLEM_SIGNALS if x in validation_problem_options],
        )
        validation_regions = st.multiselect(
            "Region filter",
            validation_region_options,
            default=[],
            help="Leave blank to include all indexed regions.",
        )
        vf1, vf2, vf3 = st.columns(3)
        validation_include_monitor = vf1.checkbox("Include monitor-only leads", value=True)
        validation_include_previews = vf2.checkbox("Include preview-only records", value=True)
        validation_include_low_priority = vf3.checkbox(
            "Include low priority / archive leads",
            value=True,
            help="Recommended for internal validation so weak/background classifications can be audited. Truly hidden/rejected workflow records remain excluded.",
        )
        vf4, vf5, vf6 = st.columns(3)
        validation_min_quality = vf4.selectbox(
            "Minimum evidence quality",
            validation_study.MIN_EVIDENCE_OPTIONS,
            index=0,
        )
        validation_prefer_unique = vf5.checkbox("Prefer unique companies", value=True)
        validation_require_report = vf6.checkbox("Require full report", value=False)
        vf7, vf8 = st.columns(2)
        validation_require_enrichment = vf7.checkbox("Require enrichment", value=False)
        validation_max_targets = vf8.number_input(
            "Maximum targets",
            min_value=1,
            max_value=100,
            value=100,
            step=1,
        )
        build_validation_clicked = st.form_submit_button("Build 100-target validation set", type="primary")

    if build_validation_clicked:
        with st.spinner("Building deterministic validation set from indexed evidence…"):
            try:
                conn_v = db.connect(settings.DB_PATH)
                opportunity_index.backfill_generated_opportunities(conn_v)
                fresh_validation_records = db.fetch_index_records(conn_v, include_hidden=False)
                conn_v.close()
            except Exception:
                fresh_validation_records = []
            st.session_state["validation_study_result"] = validation_study.build_validation_study(
                fresh_validation_records,
                validation_title=validation_title,
                seller_service_profile=validation_seller_profile,
                capability_categories=validation_capabilities,
                problem_signals=validation_problem_signals,
                region_filter=validation_regions,
                include_monitor_only=validation_include_monitor,
                include_preview_only=validation_include_previews,
                include_low_priority_archive=validation_include_low_priority,
                minimum_evidence_quality=validation_min_quality,
                maximum_targets=int(validation_max_targets),
                prefer_unique_companies=validation_prefer_unique,
                require_full_report=validation_require_report,
                require_enrichment=validation_require_enrichment,
            )

    validation_result = st.session_state.get("validation_study_result")
    if validation_result:
        validation_profile = validation_result.get("validation_profile", {}) or {}
        if validation_result.get("status") == "ok":
            st.success(validation_result.get("message") or "Validation set built.")
        elif validation_result.get("status") == "empty":
            st.info(validation_result.get("message") or "No indexed evidence is available.")
        else:
            st.warning(validation_result.get("message") or "No eligible validation records were found.")

        validation_warning = validation_result.get("warning") or ""
        if validation_warning:
            st.warning(validation_warning)

        vm = validation_result.get("metrics", {}) or {}
        v1, v2, v3, v4, v5 = st.columns(5)
        v1.metric("Indexed reviewed", vm.get("total_indexed_records_reviewed", 0))
        v2.metric("Eligible records", vm.get("eligible_records_available", 0))
        v3.metric("Selected", vm.get("total_selected", 0))
        v4.metric("Unique companies", vm.get("unique_companies_selected", 0))
        v5.metric("Manual audits required", vm.get("number_requiring_manual_audit", 0))

        v6, v7, v8, v9, v10, v11 = st.columns(6)
        v6.metric("Full reports", vm.get("full_reports_count", 0))
        v7.metric("Preview-only", vm.get("preview_only_count", 0))
        v8.metric("Enriched", vm.get("enriched_count", 0))
        v9.metric("Tier 1 / high", vm.get("tier1_high_count", 0))
        v10.metric("Official direct source", vm.get("official_direct_source_records_available", 0))
        v11.metric("Official URLs", vm.get("number_with_official_source_urls_available", 0))
        v12, v13, v14 = st.columns(3)
        v12.metric("External-case-study eligible", vm.get("external_case_study_eligible_count", 0))
        v13.metric("Structured source-ID matches", vm.get("source_id_verified_by_structured_source_count", vm.get("source_id_verified_count", 0)))
        v14.metric("Company warnings", vm.get("company_match_warning_count", 0))
        st.caption(
            f"Structured source records present: {vm.get('source_record_present_count', 0)} · "
            f"Manual audits completed: {vm.get('manual_audit_completed_count', 0)}. "
            "Structured source-ID matching is deterministic and does not imply human verification."
        )
        if vm.get("not_checked_count", 0):
            st.caption(
                f"{vm.get('not_checked_count', 0)} selected record(s) have enrichment/evidence quality not checked. "
                "This is separate from official direct-source availability."
            )

        with st.expander("Validation profile, filters, and distributions", expanded=False):
            st.markdown(f"**Title:** {validation_profile.get('validation_title') or validation_study.DEFAULT_VALIDATION_TITLE}")
            st.markdown(f"**Seller/service profile:** {validation_profile.get('seller_service_profile') or '—'}")
            st.markdown(f"**Capabilities:** {_as_text(validation_profile.get('capability_categories')) or '—'}")
            st.markdown(f"**Problem signals:** {_as_text(validation_profile.get('problem_signals')) or '—'}")
            st.markdown(f"**Regions:** {_as_text(validation_profile.get('region_filter')) or 'All regions'}")
            st.markdown(
                f"**Filters:** monitor-only = {'included' if validation_profile.get('include_monitor_only') else 'excluded'} · "
                f"previews = {'included' if validation_profile.get('include_preview_only') else 'excluded'} · "
                f"low priority/archive = {'included' if validation_profile.get('include_low_priority_archive') else 'excluded'} · "
                f"minimum evidence = {validation_profile.get('minimum_evidence_quality') or 'Any'} · "
                f"prefer unique companies = {'yes' if validation_profile.get('prefer_unique_companies') else 'no'} · "
                f"require full report = {'yes' if validation_profile.get('require_full_report') else 'no'} · "
                f"require enrichment = {'yes' if validation_profile.get('require_enrichment') else 'no'}"
            )
            st.markdown(f"**Evidence quality:** {_as_text(vm.get('evidence_strength_distribution'))}")
            st.markdown(f"**Readiness:** {_as_text(vm.get('readiness_distribution'))}")
            st.markdown(f"**Seller fit:** {_as_text(vm.get('seller_fit_distribution'))}")
            st.markdown(f"**Signal tiers:** {_as_text(vm.get('signal_tier_distribution'))}")
            st.markdown(f"**Source types:** {_as_text(vm.get('source_type_breakdown'))}")
            st.markdown(f"**Problem categories:** {_as_text(vm.get('problem_category_breakdown'))}")
            st.caption(validation_result.get("method_note") or "")

        validation_rows = validation_result.get("rows", []) or []
        if validation_rows:
            validation_preview = pd.DataFrame(validation_rows)
            validation_cols = [c for c in [
                "validation_rank", "target_company", "product", "molecule", "problem_category",
                "source_type", "source_id", "region", "opportunity_score", "grade",
                "lead_status", "queue_status", "signal_tier", "signal_type",
                "broad_problem_category", "specific_problem_subcategory",
                "source_company", "company_match_warning", "company_identity_mismatch",
                "company_role_difference", "technical_manufacturer_differs",
                "target_is_product_owner_or_sponsor", "target_is_distributor_or_repackager_only",
                "company_match_warning_note", "product_type_warning",
                "clinical_trial_signal_code", "clinical_trial_signal_reason",
                "clinical_trial_evidence_field", "clinical_trial_evidence_text",
                "audit_correction_note", "source_id_verification_status",
                "external_case_study_eligible", "exclusion_reason",
                "evidence_quality", "best_evidence_tier", "seller_fit_strength",
                "has_full_report", "source_coverage_count", "official_source_url",
                "safe_bd_angle", "validation_questions"
            ] if c in validation_preview.columns]
            st.dataframe(validation_preview[validation_cols], use_container_width=True, hide_index=True)

            vd1, vd2 = st.columns(2)
            vd1.download_button(
                "⬇ Download pharmatune_100_target_validation_study.csv",
                validation_study.export_validation_csv(validation_result),
                file_name="pharmatune_100_target_validation_study.csv",
                mime="text/csv",
            )
            vd2.download_button(
                "⬇ Download pharmatune_100_target_validation_study_summary.md",
                validation_study.export_validation_markdown(validation_result),
                file_name="pharmatune_100_target_validation_study_summary.md",
                mime="text/markdown",
            )

        validation_limitations = validation_result.get("limitations", []) or []
        if validation_limitations:
            st.warning("Validation limitations:\n- " + "\n- ".join(str(x) for x in validation_limitations))
    elif not idx_stats.get("indexed_total", 0):
        st.caption("Run Generate first to create indexed PharmaTune evidence before building the validation study.")
    else:
        st.caption(f"{idx_stats.get('indexed_total', 0)} indexed opportunity records are currently available for validation filtering.")

    st.divider()
    st.markdown("### Generated full reports")
    if opps:
        st.dataframe(pd.DataFrame(opps)[
            ["company", "product", "region", "problem_signal", "grade",
             "score", "confidence", "evidence_count", "report_type",
             "signal_status", "provisional"]],
            use_container_width=True, hide_index=True)
        st.caption("Score is the 0–100 Opportunity Score. Grades: A≥70, B 50–69, "
                   "C 30–49, D<30 (rejected). `provisional`=1 means the candidate "
                   "came from deterministic clustering/fallback, not full LLM "
                   "synthesis — verify before outreach.")

        st.markdown("**Evidence links** (source type + language)")
        if ev:
            st.dataframe(pd.DataFrame(ev)[
                ["opportunity_id", "source_type", "source_name", "language",
                 "title", "url"]], use_container_width=True, hide_index=True)

        st.markdown("**Read a report**")
        labels = [f"{o['company']} — {o['product']}" for o in opps]
        pick = st.selectbox("Opportunity", labels)
        data = _json.loads(opps[labels.index(pick)]["data_json"])
        st.markdown(data.get("report_md", "_No report body stored._"))
    else:
        st.info("No opportunities yet — run the generator in tab ①.")

    st.divider()
    st.subheader("Rejected leads")
    if rej:
        st.dataframe(pd.DataFrame(rej)[
            ["company", "product", "reason", "evidence_count"]],
            use_container_width=True, hide_index=True)
    else:
        st.caption("None yet.")

    st.divider()
    st.subheader("Export / download")
    st.caption("On free cloud hosts the disk is wiped on restart — download your "
               "outputs here to keep them.")
    zbytes = _zip_reports()
    if zbytes:
        st.download_button("⬇ Download all outputs (.zip)", zbytes,
                           file_name="pharmadrone_reports.zip", type="primary")
        colx, coly, colz, coli = st.columns(4)
        for col, fname, label in (
            (colx, "opportunities.csv", "opportunities.csv"),
            (coly, "evidence.json", "evidence.json"),
            (colz, "rejected_leads.csv", "rejected_leads.csv"),
            (coli, "opportunity_index.csv", "opportunity_index.csv")):
            fpath = settings.REPORTS_DIR / fname
            if fpath.exists():
                col.download_button(label, fpath.read_bytes(), file_name=fname)
    else:
        st.caption("No exports yet — generate the 5 test reports first.")

# ==========================================================================
# TAB 5 — CONNECTORS (self-test)
# ==========================================================================
with tab_conn:
    st.subheader("Connector self-test")
    st.caption("Test each source in isolation. Failures show the exact reason "
               "(bad key, timeout, endpoint change) — nothing is hidden.")
    q = st.text_input("Test query", DEFAULT_QUERY)
    if st.button("Run connector test", type="primary"):
        with st.spinner("Testing each source…"):
            results = check_all(q)
        st.dataframe(pd.DataFrame([
            {"Source": r["source"], "Status": r["status"], "Records": r["count"],
             "Needs key": "yes" if r["needs_key"] else "no",
             "Warning": r.get("warning", ""),
             "Error / sample": r["error"] or r["sample"]}
            for r in results]), use_container_width=True, hide_index=True)
        ok = sum(1 for r in results if r["status"] == "OK")
        (st.success if ok == len(results) else st.warning)(
            f"{ok}/{len(results)} sources OK")
    st.divider()
    st.subheader("Source Health / API Reliability")
    st.caption("Developer/debug view. Normal user reports show evidence gaps, not raw API errors.")
    try:
        conn_h = db.connect(settings.DB_PATH)
        health_summary = db.fetch_source_health_summary(conn_h)
        health_events = db.fetch_source_health_events(conn_h, limit=100)
        conn_h.close()
    except Exception:
        health_summary, health_events = [], []
    if health_summary:
        st.dataframe(pd.DataFrame(health_summary), use_container_width=True, hide_index=True)
        with st.expander("Raw source health events", expanded=False):
            st.dataframe(pd.DataFrame(health_events), use_container_width=True, hide_index=True)
    else:
        st.caption("No source health events recorded yet. Run Generate or Enrich indexed leads first.")

    st.caption("CLI equivalent:  python -m pharmadrone.test_connectors \"your query\". Tavily site: queries are retried once with a sanitised query if the API rejects search-engine operators.")
