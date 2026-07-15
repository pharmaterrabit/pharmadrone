"""Customer / Analyst screens for Checkpoint 6D-A."""
from __future__ import annotations

import math
from typing import Any, Callable

import pandas as pd
import streamlit as st

from pharmadrone.pipeline import human_audit, seller_case_study
from . import data, theme


def _safe(value: Any, fallback: str = "Not recorded") -> str:
    return str(value) if value not in (None, "") else fallback


def _qualitative_evidence(row: dict[str, Any]) -> str:
    source = str(row.get("source_type") or "").lower()
    if source in {"openfda_enforcement", "openfda", "fda enforcement"}: return "Confirmed"
    if source in {"clinicaltrials", "clinicaltrials.gov"}: return "Strongly supported"
    if source in {"europepmc", "openalex", "crossref"}: return "Plausible"
    return "Requires validation"


def overview() -> None:
    theme.page_header("Global intelligence overview", "A live, evidence-governed view of pharmaceutical opportunity signals and validation work.", "Discover")
    info = data.overview(); stats = info["stats"]; audit = info["audit"]; sched = info["scheduler"]
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Indexed opportunities", f"{stats.get('indexed_total',0):,}")
    c2.metric("Awaiting validation", f"{audit.get('pending_audits',0):,}")
    c3.metric("Full reports", f"{stats.get('full_reports',0):,}")
    c4.metric("Connected source jobs", f"{sched.get('enabled_sources',0):,}")
    st.markdown("### Intelligence operations")
    a,b = st.columns([1.6,1])
    with a:
        latest = sched.get("latest_run") or {}
        theme.card("Scheduled intelligence refresh", "The worker runs outside Streamlit and refreshes only sources that are due.", [(sched.get("scheduler_status","Unknown"), "green" if sched.get("failed_sources",0)==0 else "amber")], f"Latest run {latest.get('started_at','Not recorded')} · {sched.get('records_created',0)} created · {sched.get('duplicates_prevented',0)} duplicates prevented")
        theme.card("Human validation", "Confirmed evidence, PharmaTune interpretation, and human decisions remain separate throughout review.", [(f"{audit.get('audit_completion_percentage',0)}% complete","violet")], f"{audit.get('audits_completed',0)} completed · {audit.get('unresolved_company_warnings',0)} unresolved company warnings")
    with b:
        st.markdown('<div class="pt-card"><div class="pt-eyebrow">Evidence governance</div><h3>Signals require human validation</h3><p>PharmaTune never treats a scientific association, trial, shortage, or company context as proof of commercial need.</p><div class="pt-rule"></div>'+theme.badge("Confirmed evidence","green")+theme.badge("Interpretation","blue")+theme.badge("Human decision","violet")+'</div>', unsafe_allow_html=True)
    st.markdown("### Current platform state")
    rows = [
        {"Area":"Opportunity index","Status":"Live","Detail":f"{stats.get('indexed_total',0):,} indexed; {stats.get('waiting_queue',0):,} waiting"},
        {"Area":"Human audit","Status":"Live","Detail":f"{audit.get('total_queue_records',0)} frozen benchmark records"},
        {"Area":"Scheduled refresh","Status":"Healthy" if sched.get('failed_sources',0)==0 else "Attention","Detail":f"{sched.get('enabled_sources',0)} enabled source jobs"},
        {"Area":"Research, deals, patents","Status":"Planned","Detail":"Placeholders until genuine production connectors are available"},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def explorer(navigate: Callable[[str], None]) -> None:
    theme.page_header("Opportunity Explorer", "Search and filter the live opportunity index. Results are paginated at the database.", "Discover")
    with st.container():
        q1,q2,q3,q4 = st.columns([2.2,1,1,0.8])
        search = q1.text_input("Search", placeholder="Company, product, problem or source ID", key="opp_search")
        facets = data.opportunity_facets()
        source = q2.selectbox("Source", ["All"]+facets["source_type"], key="opp_source")
        region = q3.selectbox("Region", ["All"]+facets["region"], key="opp_region")
        size = q4.selectbox("Rows", [10,25,50], index=1, key="opp_size")
    page = int(st.session_state.get("opp_page",1))
    result = data.opportunity_page(page=page,page_size=size,search=search,source=source,region=region)
    pages = max(1, math.ceil(result["total"]/size))
    if page > pages: st.session_state["opp_page"] = 1; st.rerun()
    st.caption(f"{result['total']:,} matching records · page {page} of {pages}")
    if not result["rows"]: theme.empty("No matching opportunities", "Try a broader search or remove one of the filters.", "No results"); return
    frame = pd.DataFrame([{ "Company":r.get("company"),"Product":r.get("product"),"Problem":r.get("problem_category"),"Source":r.get("source_type"),"Region":r.get("region"),"Score":r.get("score"),"Grade":r.get("grade"),"Status":r.get("lead_status"),"Lead ID":r.get("stable_lead_id")} for r in result["rows"]])
    event = st.dataframe(frame, use_container_width=True, hide_index=True, on_select="rerun", selection_mode="single-row", key="opp_table")
    selected = event.selection.rows if event and hasattr(event,"selection") else []
    if selected:
        st.session_state["selected_lead_id"] = result["rows"][selected[0]]["stable_lead_id"]
        if st.button("Open selected opportunity", type="primary"): navigate("Opportunity Detail")
    p1,p2,p3 = st.columns([1,3,1])
    def set_page(target: int) -> None:
        st.session_state["opp_page"] = target
    p1.button("← Previous", disabled=page<=1, on_click=set_page, args=(page-1,))
    p2.markdown(f"<div style='text-align:center;color:#7787A6;padding:8px'>Page {page} / {pages}</div>", unsafe_allow_html=True)
    p3.button("Next →", disabled=page>=pages, on_click=set_page, args=(page+1,))


def opportunity_detail(navigate: Callable[[str], None]) -> None:
    sid = st.session_state.get("selected_lead_id")
    if not sid:
        theme.page_header("Opportunity detail", "Select an opportunity from the Explorer first.", "Discover")
        if st.button("← Opportunity Explorer"): navigate("Opportunity Explorer")
        return
    row = data.opportunity(sid)
    if not row: st.error("This opportunity is no longer available."); return
    if st.button("← Opportunity Explorer"): navigate("Opportunity Explorer")
    theme.page_header(_safe(row.get("product"),"Unnamed product"), f"{_safe(row.get('company'),'Unknown organisation')} · {_safe(row.get('region'),'Region not recorded')}", "Opportunity detail")
    evidence = _qualitative_evidence(row)
    st.markdown(theme.badge(evidence,"green")+theme.badge(_safe(row.get("lead_status"),"Requires validation"),"blue")+theme.badge("Human validation required","violet"),unsafe_allow_html=True)
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("Opportunity score", _safe(row.get("score"),"—")); c2.metric("Grade",_safe(row.get("grade"),"—")); c3.metric("Source",_safe(row.get("source_type"))); c4.metric("Full report","Available" if row.get("has_full_report") else "Not generated")
    st.markdown("### Evidence chain")
    a,b,c = st.columns(3)
    with a:
        st.markdown(f'<div class="pt-card pt-evidence"><h4 style="color:#3DBE8B">A · Confirmed source evidence</h4><p><b>{_safe(row.get("source_type"))}</b><br>{_safe(row.get("source_id"))}</p><p>{_safe(row.get("problem_category"),"No direct problem category recorded")}</p><div class="pt-mono">Last checked {_safe(row.get("last_checked_at"))}</div></div>',unsafe_allow_html=True)
    with b:
        st.markdown(f'<div class="pt-card pt-evidence"><h4 style="color:#4D8DFF">B · PharmaTune interpretation</h4><p><b>{evidence}</b></p><p>This is a deterministic opportunity signal. It does not establish commercial need, urgency, budget or buying intent.</p></div>',unsafe_allow_html=True)
    with c:
        st.markdown('<div class="pt-card pt-evidence"><h4 style="color:#9180F4">C · Human decision</h4><p><b>Requires validation</b></p><p>Approval is managed in the Human Validation workspace. Internal, external-case-study and outreach approvals remain separate.</p></div>',unsafe_allow_html=True)
    with st.expander("Technical record"):
        fields = {k:v for k,v in row.items() if k not in {"data_json","details"} and v not in (None,"")}
        st.json(fields, expanded=False)


def entity_page(title: str, subtitle: str, field: str) -> None:
    theme.page_header(title, subtitle, "Discover")
    rows = data.entity_summary(field)
    if not rows: theme.empty(f"No {title.lower()} available", "Entity profiles will appear when indexed records contain this information.", "Empty")
    else: st.dataframe(pd.DataFrame(rows).rename(columns={"name":title.rstrip("s"),"opportunities":"Linked opportunities","highest_score":"Highest opportunity score","latest_signal":"Latest signal"}),use_container_width=True,hide_index=True)


def technology_profile() -> None:
    theme.page_header("Technology Profile", "Match real service-provider capabilities to indexed pharmaceutical problem signals.", "Discover")
    st.info("Technology fit is expressed qualitatively. It does not imply commercial readiness or buying intent.")
    rows = data.entity_summary("problem_category",50)
    if rows:
        st.markdown("### Indexed problem landscape")
        st.dataframe(pd.DataFrame(rows).rename(columns={"name":"Problem category","opportunities":"Indexed signals","highest_score":"Highest opportunity score","latest_signal":"Latest signal"}),use_container_width=True,hide_index=True)
    theme.empty("Persistent technology catalogue", "A genuine technology-ownership catalogue is planned for the Research & Innovation and Patents checkpoints. Current matching uses the approved service-provider profile.", "Planned")


def validation() -> None:
    theme.page_header("Human Validation", "Review immutable evidence, deterministic interpretation and append-only human decisions.", "Workflow")
    rows, metrics = data.audit_queue()
    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Queue",metrics.get("total_queue_records",0)); m2.metric("Completed",metrics.get("audits_completed",0)); m3.metric("External approved",metrics.get("approved_for_external_use",0)); m4.metric("Warnings unresolved",metrics.get("unresolved_company_warnings",0))
    if not rows: theme.empty("Validation queue is empty", "The frozen benchmark has not been imported into this database.", "No records"); return
    f1,f2,f3 = st.columns([1,1,2])
    status = f1.selectbox("Status",["All"]+list(human_audit.AUDIT_STATUSES))
    tier = f2.selectbox("Signal tier",["All","A","B","C","D"])
    search = f3.text_input("Find a record",placeholder="Source ID, company or product")
    filtered = human_audit.filter_queue(rows,{"audit_status":[] if status=="All" else [status],"signal_tier":[] if tier=="All" else [tier]})
    if search.strip():
        needle=search.strip().lower()
        filtered=[r for r in filtered if any(needle in str(r.get(k) or "").lower() for k in ("source_id","target_company","company","product"))]
    labels = {f"{r.get('source_id','No source ID')} · {r.get('target_company') or r.get('company','Unknown')} · Tier {r.get('signal_tier','?')}":r for r in filtered}
    if not labels: st.warning("No validation records match these filters."); return
    label_options = list(labels)
    requested_key = str(st.session_state.get("validation_audit_key") or "")
    requested_index = next(
        (i for i, label in enumerate(label_options) if str(labels[label].get("audit_key") or "") == requested_key),
        0,
    )
    selected_label = st.selectbox("Validation record", label_options, index=requested_index)
    record = labels[selected_label]
    st.session_state["validation_audit_key"] = record.get("audit_key") or ""
    a,b,c = st.columns(3)
    with a: theme.card("Confirmed source evidence",_safe(record.get("evidence_span") or record.get("specific_problem_category") or record.get("problem_category")),[(record.get("source_type","Source"),"green")],_safe(record.get("source_id")))
    with b: theme.card("PharmaTune interpretation",_safe(record.get("signal_reason") or record.get("signal_tier_reason") or "Deterministic classification"),[(f"Tier {record.get('signal_tier','?')}","blue")],f"External eligibility: {'eligible' if record.get('external_case_study_eligible') else 'not eligible'}")
    with c: theme.card("Latest human decision",_safe(record.get("audit_decision"),"Pending review"),[(record.get("audit_status","pending"),"violet")],f"Version {record.get('audit_version',0)}")
    with st.form("audit_form"):
        st.markdown("### Mandatory audit checklist")
        q1,q2,q3 = st.columns(3)
        evidence_checked=q1.checkbox("Official source checked",value=bool(record.get("evidence_checked")))
        product_checked=q1.checkbox("Product identity checked",value=bool(record.get("product_identity_checked")))
        company_checked=q2.checkbox("Company identity/role checked",value=bool(record.get("company_identity_checked")))
        signal_checked=q2.checkbox("Signal classification checked",value=bool(record.get("problem_signal_checked")))
        supports=q3.checkbox("Evidence supports signal",value=bool(record.get("evidence_supports_problem")))
        warnings=q3.checkbox("Warnings acknowledged",value=bool(record.get("unresolved_warnings_acknowledged")))
        warning_resolved=st.checkbox("Company/distributor warning reviewed and resolved",value=bool(record.get("company_warning_resolved")),disabled=not bool(record.get("company_match_warning") or record.get("company_identity_mismatch") or record.get("target_is_distributor_or_repackager_only")))
        reviewer=st.text_input("Reviewer name",value=str(record.get("reviewer_name") or ""))
        action=st.selectbox("Decision",human_audit.AUDIT_ACTIONS)
        notes=st.text_area("Audit notes",value=str(record.get("audit_notes") or ""))
        external=st.checkbox("Approve for external case study",value=bool(record.get("external_use_approved")),help="Requires every mandatory check and cannot be used for Tier D.")
        outreach=st.checkbox("Approve for outreach",value=bool(record.get("outreach_approved")),disabled=not external,help="Outreach remains locked until external approval is requested and valid.")
        submitted=st.form_submit_button("Save append-only audit version",type="primary")
    if submitted:
        payload={"reviewer_name":reviewer,"action":action,"audit_notes":notes,"evidence_checked":evidence_checked,"product_identity_checked":product_checked,"company_identity_checked":company_checked,"problem_signal_checked":signal_checked,"evidence_supports_problem":supports,"unresolved_warnings_acknowledged":warnings,"company_warning_resolved":warning_resolved,"external_use_approved":external,"outreach_approved":outreach}
        try: saved=data.save_audit(record,payload); st.success(f"Audit version {saved['audit_version']} saved. Previous versions were preserved."); st.rerun()
        except Exception as exc: st.error(str(exc))
    with st.expander("Audit and correction history (loaded on demand)"):
        history, corrections = data.audit_histories(record["audit_key"])
        st.dataframe(pd.DataFrame(history),use_container_width=True,hide_index=True) if history else st.caption("No audit versions yet.")
        if corrections: st.dataframe(pd.DataFrame(corrections),use_container_width=True,hide_index=True)


def case_studies(principal: dict[str, Any], navigate: Callable[[str], None]) -> None:
    theme.page_header("Hovione Case Study", "A real provider profile matched to evidence-backed product problems, with human approval before customer export.", "Workflow")
    st.markdown(theme.badge("1 Verified provider","green")+theme.badge("2 Evidence match","blue")+theme.badge("3 Human validation","violet")+theme.badge("4 Approved shortlist","violet")+theme.badge("5 Customer export","green"),unsafe_allow_html=True)

    provider = seller_case_study.HOVIONE_PROFILE
    st.markdown("### Verified seller / solution-provider profile")
    theme.card(
        provider["provider_name"],
        provider["profile_summary"],
        [("Real provider", "green"), (f"Verified {provider['last_verified_at']}", "blue")],
        provider["provider_type"],
    )
    st.caption("Published capabilities used for matching: " + " · ".join(provider["capabilities"]))
    with st.expander("Provider capability evidence"):
        for source in provider["evidence_sources"]:
            st.markdown(f"[{source['title']}]({source['url']})  ")
            st.caption(source["supports"])

    left, right = st.columns([1, 2])
    limit = left.slider("Maximum shortlist candidates", 5, 20, 12)
    right.info("The build uses the frozen human-validation dataset. No target enters the customer export until a reviewer passes the external-use gate.")
    if st.button("Build and save Hovione case study", type="primary"):
        with st.spinner("Matching the verified provider profile to validated evidence..."):
            try:
                st.session_state["case_result"] = data.build_seller_case_study(limit, principal)
            except Exception as exc:
                st.error("The case study could not be built or saved. No partial customer export was created.")
                st.caption(str(exc))

    result = st.session_state.get("case_result")
    if result:
        metrics = result.get("metrics", {})
        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Validation records", metrics.get("validation_records_reviewed", 0))
        c2.metric("Matched candidates", metrics.get("candidate_count", 0))
        c3.metric("Human reviewed", metrics.get("reviewed_count", 0))
        c4.metric("Customer-ready", metrics.get("approved_count", 0))
        if result.get("approved_rows"):
            st.success(result.get("message"))
        else:
            st.warning(result.get("message"))

        candidates = result.get("candidate_rows", [])
        if candidates:
            st.markdown("### Evidence review and human-validation status")
            display_rows = [{
                "Rank": row.get("pilot_rank"),
                "Company": row.get("target_company"),
                "Product": row.get("product"),
                "Public problem signal": row.get("problem_category"),
                "Potential Hovione fit": row.get("seller_capability_match"),
                "Fit strength": row.get("seller_fit_strength"),
                "Evidence source": f"{row.get('source_type','')} · {row.get('source_id','')}",
                "Validation": row.get("validation_status"),
            } for row in candidates]
            st.dataframe(pd.DataFrame(display_rows), use_container_width=True, hide_index=True)
            review_labels = {
                f"{row.get('target_company') or 'Unknown'} · {row.get('product') or row.get('source_id') or 'Record'}": row
                for row in candidates if row.get("audit_key") and not row.get("external_use_approved")
            }
            if review_labels:
                selected = st.selectbox("Candidate to validate", list(review_labels))
                if st.button("Open selected candidate in Human Validation"):
                    st.session_state["validation_audit_key"] = review_labels[selected]["audit_key"]
                    navigate("Human Validation")
            st.download_button(
                "Download internal evidence-review CSV",
                seller_case_study.export_review_csv(result),
                "pharmatune_hovione_internal_review.csv",
                "text/csv",
            )

        if result.get("approved_rows"):
            st.markdown("### Customer-safe case study exports")
            st.caption("Only human-approved rows are included. Internal reviewer names and unapproved candidates are excluded.")
            e1,e2 = st.columns(2)
            e1.download_button(
                "Download customer case study (.md)",
                seller_case_study.export_customer_markdown(result),
                "pharmatune_hovione_customer_case_study.md",
                "text/markdown",
            )
            e2.download_button(
                "Download customer case study (.html)",
                seller_case_study.export_customer_html(result),
                "pharmatune_hovione_customer_case_study.html",
                "text/html",
            )
        else:
            st.info("Customer exports are locked. Validate a candidate and approve it for external case-study use, then rebuild this case study.")
        with st.expander("Method and limitations"):
            st.write(result.get("method_note"))
            for limitation in result.get("limitations", []):
                st.warning(limitation)

    history = data.seller_case_study_history(principal)
    if history:
        with st.expander("Saved case-study snapshots"):
            st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)


def sources() -> None:
    theme.page_header("Data Sources", "Connected source jobs, incremental refresh state and evidence roles.", "Platform")
    info=data.source_health(); summary=info["summary"]
    a,b,c = st.columns(3); a.metric("Enabled source jobs",summary.get("enabled_sources",0)); b.metric("Failed sources",summary.get("failed_sources",0)); c.metric("Scheduler status",summary.get("scheduler_status","Unknown"))
    rows=[]
    for r in info["sources"]:
        rows.append({"Source job":r.get("source_name"),"Cadence":r.get("cadence"),"Status":r.get("last_status") or "Not run","Last success":r.get("last_success_at"),"Next due":r.get("next_due_at"),"Created":r.get("records_created") or 0,"Updated":r.get("records_updated") or 0,"Unchanged":r.get("records_unchanged") or 0})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    st.caption("Connected does not mean a source automatically establishes a pharmaceutical problem or commercial need.")
    ema = data.ema_coverage()
    st.markdown("### European Medicines Agency")
    e1,e2,e3 = st.columns(3)
    e1.metric("EMA medicines retained", f"{ema.get('total', 0):,}")
    e2.metric("Medicine categories", len(ema.get("categories") or {}))
    e3.metric("Latest source update", ema.get("latest_update") or "Not ingested yet")
    if ema.get("categories"):
        st.dataframe(pd.DataFrame([
            {"Category": category, "Medicines": count}
            for category, count in sorted(ema["categories"].items())
        ]), use_container_width=True, hide_index=True)
    st.caption("EMA catalogue records confirm published regulatory facts only. They do not prove product failure, customer need or solution fit.")
    mhra = data.mhra_coverage()
    st.markdown("### UK Medicines and Healthcare products Regulatory Agency")
    m1,m2,m3 = st.columns(3)
    m1.metric("MHRA medicine alerts retained", f"{mhra.get('total', 0):,}")
    m2.metric("Explicit problem descriptions", f"{mhra.get('direct', 0):,}")
    m3.metric("Latest source update", mhra.get("latest_update") or "Not ingested yet")
    if mhra.get("classes"):
        st.dataframe(pd.DataFrame([
            {"Alert class": alert_class, "Records": count}
            for alert_class, count in sorted(mhra["classes"].items())
        ]), use_container_width=True, hide_index=True)
    st.caption("Only explicit MHRA medicine recall or defect descriptions can support a problem signal. General safety and device alerts are not converted into medicine opportunities.")
    st.markdown("### Planned source families")
    for name in ("PMDA and further global regulators","Company news, deals and funding","Patent families and ownership","University technology transfer"):
        theme.card(name,"Not connected. This module will remain a placeholder until a genuine evidence-aware connector is implemented.",[("Planned","muted")])


def pharmaceutical_memory() -> None:
    theme.page_header(
        "Pharmaceutical Memory",
        "Durable, evidence-derived relationships between companies, products, molecules and public problem signals.",
        "Phase 7",
    )
    search = st.text_input("Find a company", placeholder="Search the remembered company index")
    result = data.pharmaceutical_memory(search)
    metrics = result["metrics"]
    a,b,c,d = st.columns(4)
    a.metric("Companies", metrics.get("company", 0))
    b.metric("Products", metrics.get("product", 0))
    c.metric("Relationships", metrics.get("relationships", 0))
    d.metric("Historical observations", metrics.get("observations", 0))
    st.info("Memory is derived only from stored PharmaTune evidence. A remembered relationship is not proof of current commercial need or confirmed root cause.")
    companies = result["companies"]
    if not companies:
        theme.empty("No company memory found", "Try a broader company search or refresh the opportunity index.", "No results")
        return
    labels = {f"{row['display_name']} · {row['evidence_records']} records": row for row in companies}
    selected = labels[st.selectbox("Company memory", list(labels))]
    relationships = data.pharmaceutical_memory_relationships(selected["entity_id"])
    st.caption(f"{selected['evidence_records']} evidence-linked memory records · last seen {selected.get('last_seen_at') or 'Not recorded'}")
    if relationships:
        frame = pd.DataFrame([{
            "Relationship": row.get("relationship_type", "").replace("_", " ").title(),
            "Entity type": row.get("entity_type", "").title(),
            "Remembered entity": row.get("display_name"),
            "Evidence source": row.get("source_type"),
            "Source ID": row.get("source_id"),
            "Status": row.get("evidence_status"),
            "Lead ID": row.get("stable_lead_id"),
        } for row in relationships])
        st.dataframe(frame, use_container_width=True, hide_index=True)


def health() -> None:
    theme.page_header("System Health", "Live persistence and scheduler telemetry. No credentials or connection strings are displayed.", "Platform")
    info=data.source_health(); dbs=info["database"]; sched=info["summary"]; latest=sched.get("latest_run") or {}
    a,b,c,d=st.columns(4); a.metric("Database",str(dbs.get("connection_status","unknown")).title()); b.metric("Backend",str(dbs.get("backend","unknown")).upper()); c.metric("Schema",f"v{dbs.get('schema_version',0)}"); d.metric("Migrations",dbs.get("migration_count",0))
    st.markdown("### Scheduled refresh")
    theme.card("GitHub Actions orchestrator",f"Latest run: {_safe(latest.get('started_at'))}",[(sched.get("scheduler_status","Unknown"),"green" if sched.get("failed_sources",0)==0 else "amber")],f"Next run {sched.get('next_orchestrator_run')} · {sched.get('failed_sources',0)} failed sources")
    st.success("Checkpoint 6C.1 — stable. Automatic scheduled refresh validated on 13 July 2026; frozen 100-record benchmark remained unchanged.")
    st.caption("Cold-start performance is measured separately from warm page navigation on Streamlit Community Cloud.")
    st.markdown("### Checkpoint 7B production readiness")
    readiness = data.readiness()
    if readiness["ready"]:
        st.success(f"Production ready · {readiness['passed']} of {readiness['total']} operational gates passed.")
    else:
        st.warning(f"Attention required · {readiness['passed']} of {readiness['total']} operational gates passed.")
    st.dataframe(pd.DataFrame([{
        "Gate": check["gate"],
        "Status": "Passed" if check["passed"] else "Attention",
        "Live evidence": check["detail"],
    } for check in readiness["checks"]]), use_container_width=True, hide_index=True)
    st.caption("This verdict uses live operational telemetry and never displays credentials or connection strings.")


def placeholder(title: str, description: str) -> None:
    theme.page_header(title, description, "Intelligence")
    theme.empty(title,"This production module is intentionally not connected yet. PharmaTune will not display fabricated records, metrics or scores.","Coming soon")
