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

from pharmadrone import settings, db, auth
from pharmadrone.run import generate
from pharmadrone.test_connectors import check_all, DEFAULT_QUERY
from pharmadrone.pipeline.opportunity_matcher import (
    MATCH_SCOPE_LABEL,
    TECH_CERTAINTY_NOTE,
    match_problem_to_solutions,
    match_technology_to_targets,
)

st.set_page_config(page_title="PharmaDrone", layout="wide")

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

st.title("PharmaTune / PharmaDrone — Global Pharma Opportunity Engine")
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

        if not accepted:
            st.error("0 reports generated. Open the Debug panel below — it shows "
                     "exactly where candidates were lost (LLM batch failures, "
                     "rejection reasons, or too little evidence).")

        st.markdown("### Source coverage summary")
        st.caption("Global public-source scouting — not complete global regulator "
                   "coverage.")
        cov_df = pd.DataFrame([
            {"Source": s, "Evidence items": d["evidence_items"],
             "Accepted leads citing": d["accepted_leads_citing"],
             "Queries": d["queries"], "Failed": d["failed"]}
            for s, d in cov.items()])
        st.dataframe(cov_df, use_container_width=True, hide_index=True)

        errors = [e for d in cov.values() for e in d["errors"]]
        if errors:
            st.warning(f"{len(errors)} source failure(s) — shown so nothing is "
                       "hidden:")
            st.code("\n".join(errors[:30]))

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
- **LLM status:** {'🔴 DISABLED — ' + dbg.get('llm_disabled_reason','') if dbg.get('llm_disabled_reason') else '🟢 active (or not needed)'}
- **Final valid-target gate dropped:** {dbg.get('final_gate_dropped', 0)} (no real product/company/trial)
- **Accepted:** {dbg.get('accepted_count', 0)} · **Rejected:** {dbg.get('rejected_count', 0)}
  - rejected (too little evidence): {dbg.get('scoring', {}).get('rejected_low_evidence', 0)}
  - rejected (grade D): {dbg.get('scoring', {}).get('rejected_grade_d', 0)}
""")
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
    st.subheader("Phase 1 — Opportunity Matcher")
    st.caption(
        "Matched from existing evidence only — this is not a live worldwide search. "
        "Run Generate first, then use this tab to match stored product/problem signals "
        "to solution types, partner categories, or technology-target hypotheses."
    )

    try:
        conn = db.connect(settings.DB_PATH)
        matcher_opps = db.fetch_all(conn, "opportunities")
        matcher_ev = db.fetch_all(conn, "evidence")
        conn.close()
    except Exception:
        matcher_opps, matcher_ev = [], []

    if not matcher_opps:
        st.info("Run Generate first to create evidence-backed opportunities, then use the matcher.")
        st.caption(
            "The matcher is intentionally read-only. It will not invent leads or pretend "
            "to search the whole world when no stored evidence exists."
        )
    else:
        st.success(f"{MATCH_SCOPE_LABEL}: {len(matcher_opps)} stored opportunity record(s) available.")
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
        max_matches = st.slider("Maximum matches to show", 3, 20, 10)
        include_weak = st.checkbox(
            "Include related/weak matches",
            value=False,
            help=(
                "Default shows Direct and Strong related matches only. Descriptor-only "
                "phrases such as extended release or immediate release are treated as "
                "background, not evidence of dissolution failure."
            ),
        )

        if st.button("Run Opportunity Match", type="primary"):
            if mode == "Problem → Solution Match":
                result = match_problem_to_solutions(
                    query, matcher_opps, matcher_ev, limit=max_matches, include_weak=include_weak
                )
                if result.get("status") == "ok":
                    st.markdown(f"**{MATCH_SCOPE_LABEL}**")
                    st.markdown(f"**Searched problem:** {result.get('searched_problem', query)}")
                    st.markdown(f"**Matched problem category:** {result.get('matched_problem_category', '—')}")
                    st.markdown("**Likely solution types:** " + "; ".join(result.get("likely_solution_types", [])))
                    st.markdown("**Possible partner categories:** " + "; ".join(result.get("possible_partner_categories", [])))
                    st.info(result.get("safe_bd_action", "Validate the evidence before outreach."))

                    rows = []
                    for m in result.get("matches", []):
                        rows.append({
                            "Lead": m.get("matching_product_problem_lead"),
                            "Evidence source": m.get("evidence_source"),
                            "Match strength": m.get("match_strength"),
                            "Match reason": m.get("match_reason"),
                            "Confidence": m.get("confidence"),
                            "Lead status": m.get("lead_status"),
                            "Evidence count": m.get("evidence_count"),
                            "Grade": m.get("grade"),
                            "Opportunity score": m.get("opportunity_score"),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    if result.get("hidden_weak_count"):
                        st.caption(f"{result.get('hidden_weak_count')} related/weak/background match(es) hidden. Tick 'Include related/weak matches' to review them.")

                    for i, m in enumerate(result.get("matches", []), start=1):
                        with st.expander(f"{i}. {m.get('matching_product_problem_lead')}"):
                            st.markdown(f"**Match scope:** {m.get('match_scope')}")
                            st.markdown(f"**Match strength:** {m.get('match_strength')}")
                            st.markdown(f"**Match reason:** {m.get('match_reason')}")
                            st.markdown(f"**Confirmed fact:** {m.get('confirmed_fact')}")
                            st.markdown(f"**Interpretation / hypothesis:** {m.get('interpretation_hypothesis')}")
                            st.markdown("**Likely solution types:** " + "; ".join(m.get("likely_solution_types", [])))
                            st.markdown("**Possible partner categories:** " + "; ".join(m.get("possible_partner_categories", [])))
                            st.markdown(f"**Safe BD action:** {m.get('safe_bd_action')}")
                            st.caption("Matched terms: " + ", ".join(m.get("match_terms", [])))
                else:
                    st.warning(result.get("message"))
                    if result.get("likely_solution_types"):
                        st.markdown("**Relevant solution types for this problem category:** " + "; ".join(result.get("likely_solution_types", [])))
                    if result.get("hidden_weak_count"):
                        st.caption(f"{result.get('hidden_weak_count')} related/weak/background match(es) hidden. Tick 'Include related/weak matches' to review them.")

            else:
                result = match_technology_to_targets(
                    query, matcher_opps, matcher_ev, limit=max_matches, include_weak=include_weak
                )
                st.caption(TECH_CERTAINTY_NOTE)
                if result.get("status") == "ok":
                    st.markdown(f"**{MATCH_SCOPE_LABEL}**")
                    st.markdown(f"**Searched technology:** {result.get('searched_technology', query)}")
                    st.markdown(f"**Technology category:** {result.get('technology_category', '—')}")
                    st.markdown("**Relevant problem categories:** " + "; ".join(result.get("relevant_problem_categories", [])))
                    st.info(result.get("why_this_technology_may_fit", "Potential relevance only; requires validation."))

                    rows = []
                    for m in result.get("matches", []):
                        rows.append({
                            "Lead": m.get("matching_product_company_lead"),
                            "Evidence source": m.get("evidence_source"),
                            "Match strength": m.get("match_strength"),
                            "Match reason": m.get("match_reason"),
                            "Evidence strength": m.get("evidence_strength"),
                            "Confidence": m.get("confidence"),
                            "Lead status": m.get("lead_status"),
                            "Evidence count": m.get("evidence_count"),
                            "Grade": m.get("grade"),
                        })
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    if result.get("hidden_weak_count"):
                        st.caption(f"{result.get('hidden_weak_count')} related/weak/background target match(es) hidden. Tick 'Include related/weak matches' to review them.")

                    for i, m in enumerate(result.get("matches", []), start=1):
                        with st.expander(f"{i}. {m.get('matching_product_company_lead')}"):
                            st.markdown(f"**Match scope:** {m.get('match_scope')}")
                            st.markdown(f"**Match strength:** {m.get('match_strength')}")
                            st.markdown(f"**Match reason:** {m.get('match_reason')}")
                            st.markdown(f"**Confirmed fact:** {m.get('confirmed_fact')}")
                            st.markdown(f"**Why this technology may fit:** {m.get('why_this_technology_may_fit')}")
                            st.markdown("**Matched problem categories:** " + "; ".join(m.get("matched_problem_categories", [])))
                            st.markdown(f"**Safe outreach angle:** {m.get('safe_outreach_angle')}")
                            st.caption("Matched terms: " + ", ".join(m.get("match_terms", [])))
                else:
                    st.warning(result.get("message"))
                    if result.get("technology_category"):
                        st.markdown(f"**Technology category:** {result.get('technology_category')}")
                        st.markdown("**Relevant problem categories:** " + "; ".join(result.get("relevant_problem_categories", [])))
                        st.caption(TECH_CERTAINTY_NOTE)
                    if result.get("hidden_weak_count"):
                        st.caption(f"{result.get('hidden_weak_count')} related/weak/background target match(es) hidden. Tick 'Include related/weak matches' to review them.")

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

# ==========================================================================
# TAB 4 — RESULTS & EXPORT
# ==========================================================================
with tab_results:
    st.subheader("Generated opportunities")
    try:
        conn = db.connect(settings.DB_PATH)
        opps = db.fetch_all(conn, "opportunities")
        ev = db.fetch_all(conn, "evidence")
        rej = db.fetch_all(conn, "rejected")
        conn.close()
    except Exception:
        opps, ev, rej = [], [], []

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
        colx, coly, colz = st.columns(3)
        for col, fname, label in (
            (colx, "opportunities.csv", "opportunities.csv"),
            (coly, "evidence.json", "evidence.json"),
            (colz, "rejected_leads.csv", "rejected_leads.csv")):
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
             "Error / sample": r["error"] or r["sample"]}
            for r in results]), use_container_width=True, hide_index=True)
        ok = sum(1 for r in results if r["status"] == "OK")
        (st.success if ok == len(results) else st.warning)(
            f"{ok}/{len(results)} sources OK")
    st.caption("CLI equivalent:  python -m pharmadrone.test_connectors \"your query\"")
