"""Customer / Analyst screens for Checkpoint 6D-A."""
from __future__ import annotations

import math
import json
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


def _official_evidence_url(row: dict[str, Any]) -> str:
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    evidence_rows = details.get("evidence") or []
    if not isinstance(evidence_rows, list):
        evidence_rows = []
    for evidence in evidence_rows:
        if not isinstance(evidence, dict):
            continue
        entities = evidence.get("entities") if isinstance(evidence.get("entities"), dict) else {}
        url = str(evidence.get("url") or entities.get("official_source_url") or "").strip()
        if url.startswith(("https://", "http://")):
            return url
    try:
        links = json.loads(row.get("evidence_links_json") or "[]")
    except Exception:
        links = []
    for link in links:
        url = link.get("url") if isinstance(link, dict) else link
        if str(url or "").startswith(("https://", "http://")):
            return str(url)
    return ""


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
        {"Area":"Patent & lifecycle","Status":"Live","Detail":"FDA Orange Book applications, listed patents, exclusivities and weekly expiry monitoring"},
        {"Area":"Research and deals","Status":"Planned","Detail":"Placeholders until genuine production connectors are available"},
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def explorer(navigate: Callable[[str], None]) -> None:
    theme.page_header("Opportunity Explorer", "Prioritise and qualify live public-source signals. Results are paginated at the database.", "Discover")
    with st.container():
        q1,q2,q3,q4 = st.columns([2.2,1,1,0.8])
        search = q1.text_input("Search", placeholder="Company, product, problem or source ID", key="opp_search")
        facets = data.opportunity_facets()
        source = q2.selectbox("Source", ["All"]+facets["source_type"], key="opp_source")
        region = q3.selectbox("Region", ["All"]+facets["region"], key="opp_region")
        size = q4.selectbox("Rows", [10,25,50], index=1, key="opp_size")
        q5,q6 = st.columns(2)
        priority = q5.selectbox("Qualification priority", ["All"]+facets["priority"], key="opp_priority")
        contact_role = q6.selectbox("Recommended contact function", ["All"]+facets["contact_role"], key="opp_contact_role")
    page = int(st.session_state.get("opp_page",1))
    result = data.opportunity_page(page=page,page_size=size,search=search,source=source,region=region,priority=priority,contact_role=contact_role)
    pages = max(1, math.ceil(result["total"]/size))
    if page > pages: st.session_state["opp_page"] = 1; st.rerun()
    st.caption(f"{result['total']:,} matching records · page {page} of {pages}")
    if not result["rows"]: theme.empty("No matching opportunities", "Try a broader search or remove one of the filters.", "No results"); return
    st.caption("P1–P3 ranks qualification readiness from public-source completeness; it does not claim urgency, budget or buying intent.")
    frame = pd.DataFrame([{ "Priority":r.get("priority_tier"),"Company":r.get("company") or "Not stated by source","Product":r.get("product") or "Not stated by source","Problem":r.get("problem_category"),"Contact function":r.get("recommended_contact_role"),"Source":r.get("source_type"),"Region":r.get("region"),"Score":r.get("score"),"Grade":r.get("grade"),"Official evidence":r.get("official_source_url"),"Lead ID":r.get("stable_lead_id")} for r in result["rows"]])
    event = st.dataframe(
        frame, use_container_width=True, hide_index=True, on_select="rerun",
        selection_mode="single-row", key="opp_table",
        column_config={"Official evidence": st.column_config.LinkColumn("Official evidence", display_text="Open ↗")},
    )
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
    official_url = _official_evidence_url(row)
    st.markdown(theme.badge(evidence,"green")+theme.badge(_safe(row.get("lead_status"),"Requires validation"),"blue")+theme.badge("Human validation required","violet"),unsafe_allow_html=True)
    c1,c2,c3,c4 = st.columns(4)
    brief = (row.get("details") or {}).get("sales_qualification_brief") or {}
    qualification = {k: row.get(k) for k in ("priority_tier","readiness","recommended_contact_role","contact_rationale","missing_requirements","next_action","qualification_basis")}
    qualification.update({k:v for k,v in brief.items() if v not in (None, "", [])})
    c1.metric("Opportunity score", _safe(row.get("score"),"—")); c2.metric("Grade",_safe(row.get("grade"),"—")); c3.metric("Source",_safe(row.get("source_type"))); c4.metric("Qualification brief","Available" if brief else "Pending refresh")
    st.markdown("### Evidence chain")
    a,b,c = st.columns(3)
    with a:
        st.markdown(f'<div class="pt-card pt-evidence"><h4 style="color:#3DBE8B">A · Confirmed source evidence</h4><p><b>{_safe(row.get("source_type"))}</b><br>{_safe(row.get("source_id"))}</p><p>{_safe(row.get("problem_category"),"No direct problem category recorded")}</p><div class="pt-mono">Last checked {_safe(row.get("last_checked_at"))}</div></div>',unsafe_allow_html=True)
        if official_url:
            st.link_button("Open official source ↗", official_url, use_container_width=True)
        else:
            st.caption("Official source URL is not available for this legacy record.")
    with b:
        st.markdown(f'<div class="pt-card pt-evidence"><h4 style="color:#4D8DFF">B · PharmaTune interpretation</h4><p><b>{evidence}</b></p><p>This is a deterministic opportunity signal. It does not establish commercial need, urgency, budget or buying intent.</p></div>',unsafe_allow_html=True)
    with c:
        st.markdown('<div class="pt-card pt-evidence"><h4 style="color:#9180F4">C · Human decision</h4><p><b>Requires validation</b></p><p>Approval is managed in the Human Validation workspace. Internal, external-case-study and outreach approvals remain separate.</p></div>',unsafe_allow_html=True)
    if brief:
        st.markdown("### Sales qualification brief")
        st.markdown(
            f"**Target account:** {_safe(brief.get('target_account'))}  \n"
            f"**Public signal:** {_safe(brief.get('public_signal'))}  \n"
            f"**Evidence basis:** {_safe(brief.get('evidence_basis'))}  \n"
            f"**Next step:** {_safe(brief.get('recommended_next_step'))}"
        )
        st.info(_safe(brief.get("commercial_limit")))
    st.markdown("### Commercial qualification route")
    st.markdown(
        f"**Priority:** {_safe(qualification.get('priority_tier'))}  \n"
        f"**Readiness:** {_safe(qualification.get('readiness'))}  \n"
        f"**Recommended contact function:** {_safe(qualification.get('recommended_contact_role'))}  \n"
        f"**Why this function:** {_safe(qualification.get('contact_rationale'))}  \n"
        f"**Next action:** {_safe(qualification.get('next_action'))}"
    )
    missing = qualification.get("missing_requirements") or []
    if missing:
        st.warning("Missing before qualification: " + "; ".join(str(item) for item in missing))
    st.caption(_safe(qualification.get("qualification_basis")))
    with st.expander("Technical record"):
        fields = {k:v for k,v in row.items() if k not in {"data_json","details"} and v not in (None,"")}
        st.json(fields, expanded=False)


def regulatory_signals(navigate: Callable[[str], None]) -> None:
    theme.page_header(
        "Regulatory Intelligence",
        "A dedicated workspace for official shortages, recalls, safety actions and withdrawals.",
        "Intelligence",
    )
    facets = data.regulatory_facets()
    q1, q2, q3, q4 = st.columns([2, 1, 1.2, 0.7])
    search = q1.text_input("Search regulatory events", placeholder="Organisation, medicine, issue or source ID", key="reg_search")
    regulator = q2.selectbox("Regulator", ["All"] + facets["regulator"], key="reg_regulator")
    family = q3.selectbox("Event type", ["All"] + facets["event_family"], key="reg_family")
    size = q4.selectbox("Rows", [10, 25, 50], index=1, key="reg_size")
    q5, q6, q7, q8, q9 = st.columns(5)
    source = q5.selectbox("Official source", ["All"] + facets["source"], key="reg_source")
    region = q6.selectbox("Market / region", ["All"] + facets["region"], key="reg_region")
    account_status = q7.selectbox("Organisation identity", ["All", "Resolved organisation", "Organisation missing"], key="reg_account")
    evidence_status = q8.selectbox("Evidence status", ["All", "Official link present", "Evidence repair required"], key="reg_evidence")
    review_status = q9.selectbox("Review status", ["All", "Current", "Review due", "Stale", "Review date missing"], key="reg_review")
    page = int(st.session_state.get("reg_page", 1))
    result = data.regulatory_page(
        page=page, page_size=size, search=search, regulator=regulator,
        event_family=family, source=source, region=region,
        account_status=account_status, evidence_status=evidence_status, review_status=review_status,
    )
    pages = max(1, math.ceil(result["total"] / size))
    if page > pages:
        st.session_state["reg_page"] = 1
        st.rerun()

    quality = data.regulatory_workspace_quality()
    coverage = result.get("coverage") or []
    a, b, c, d = st.columns(4)
    a.metric("Regulatory events", f"{quality.get('total', 0):,}")
    b.metric("Current matches", f"{result['total']:,}")
    c.metric("Missing organisation", f"{quality.get('missing_company', 0):,}")
    d.metric("Evidence links to repair", f"{quality.get('missing_official_link', 0):,}")
    st.caption("Regulatory events are confirmed public records. Commercial need, urgency and buying intent still require human qualification.")

    if not result["rows"]:
        theme.empty("No matching regulatory events", "Broaden the filters or remove the organisation/evidence restriction.", "No results")
        return
    frame = pd.DataFrame([{
        "Regulator": row.get("regulator"), "Event": row.get("event_family"),
        "Organisation": row.get("company") or "Not stated by official source",
        "Medicine / product": row.get("product") or row.get("molecule") or "Not stated",
        "Issue": row.get("problem_category"), "Market": row.get("region"),
        "Review status": row.get("freshness"), "Responsible function": row.get("responsible_function"),
        "Official evidence": row.get("official_source_url"), "Lead ID": row.get("stable_lead_id"),
    } for row in result["rows"]])
    event = st.dataframe(
        frame, use_container_width=True, hide_index=True, on_select="rerun",
        selection_mode="single-row", key="reg_table",
        column_config={"Official evidence": st.column_config.LinkColumn("Official evidence", display_text="Open source ↗")},
    )
    selected_rows = event.selection.rows if event and hasattr(event, "selection") else []
    controls = st.columns([1, 1, 3])
    if selected_rows and controls[0].button("Open regulatory detail", type="primary"):
        selected_row = result["rows"][selected_rows[0]]
        st.session_state["regulatory_lead_id"] = selected_row["stable_lead_id"]
        navigate("Regulatory Detail")
    controls[1].download_button(
        "Export this page (.csv)", frame.to_csv(index=False).encode("utf-8"),
        "pharmatune_regulatory_intelligence.csv", "text/csv",
    )
    prev, counter, nxt = st.columns([1, 4, 1])
    if prev.button("← Previous", disabled=page <= 1, key="reg_prev"):
        st.session_state["reg_page"] = page - 1; st.rerun()
    counter.markdown(f"<div style='text-align:center;padding:12px'>Page {page} / {pages}</div>", unsafe_allow_html=True)
    if nxt.button("Next →", disabled=page >= pages, key="reg_next"):
        st.session_state["reg_page"] = page + 1; st.rerun()
    with st.expander("Regulator and event coverage"):
        st.dataframe(pd.DataFrame(coverage), use_container_width=True, hide_index=True)
    with st.expander("Data-quality audit"):
        st.dataframe(pd.DataFrame(quality.get("sources") or []), use_container_width=True, hide_index=True)


def regulatory_detail(navigate: Callable[[str], None]) -> None:
    if st.button("← Regulatory Intelligence"):
        navigate("Regulatory Signals")
    lead_id = str(st.session_state.get("regulatory_lead_id") or "")
    row = data.opportunity(lead_id) if lead_id else None
    if not row:
        theme.empty("Regulatory event not found", "Return to Regulatory Intelligence and select an event.", "Missing")
        return
    from pharmadrone.pipeline import regulatory_intelligence
    route = regulatory_intelligence.action_route(row)
    official_url = _official_evidence_url(row)
    theme.page_header(
        _safe(row.get("product") or row.get("molecule"), "Regulatory event"),
        f"{route['regulator']} · {route['event_family']} · {_safe(row.get('region'), 'Market not recorded')}",
        "Regulatory Intelligence",
    )
    st.markdown(theme.badge(route["regulator"], "green") + theme.badge(route["event_family"], "blue") + theme.badge("Human validation required", "violet"), unsafe_allow_html=True)
    a, b, c, d = st.columns(4)
    a.metric("Organisation / MAH", _safe(row.get("company"), "Not stated"))
    b.metric("Opportunity score", _safe(row.get("score"), "—"))
    c.metric("Grade", _safe(row.get("grade"), "—"))
    d.metric("Review status", regulatory_intelligence.freshness(row.get("last_checked_at")))
    st.markdown("### Confirmed regulatory evidence")
    left, right = st.columns([2, 1])
    with left:
        theme.card(
            _safe(row.get("source_type")),
            _safe(row.get("problem_category"), "Regulatory event category not recorded"),
            [("Official public record", "green")],
            f"Source ID {_safe(row.get('source_id'))} · Last checked {_safe(row.get('last_checked_at'))}",
        )
    with right:
        if official_url:
            st.link_button("Open official regulator evidence ↗", official_url, use_container_width=True)
        else:
            st.error("Official evidence URL requires repair before external use.")
    st.markdown("### Analyst action route")
    st.write(f"**Responsible function:** {route['responsible_function']}")
    st.write(f"**Required review:** {route['recommended_review']}")
    st.warning(route["commercial_boundary"])
    details = row.get("details") if isinstance(row.get("details"), dict) else {}
    evidence = details.get("evidence") if isinstance(details.get("evidence"), list) else []
    if evidence:
        st.markdown("### Structured source facts")
        for item in evidence:
            if not isinstance(item, dict):
                continue
            entities = item.get("entities") if isinstance(item.get("entities"), dict) else {}
            facts = {key: value for key, value in entities.items() if value not in (None, "", [], {})}
            with st.expander(f"{_safe(item.get('source_name') or item.get('source_type'))} · {_safe(item.get('record_id'))}"):
                st.json(facts, expanded=False)
    with st.expander("Technical record"):
        st.json({key: value for key, value in row.items() if key not in {"details", "data_json"} and value not in (None, "")}, expanded=False)


def entity_page(title: str, subtitle: str, field: str, navigate: Callable[[str], None] | None = None) -> None:
    if field == "company" and navigate is not None:
        companies(navigate)
        return
    theme.page_header(title, subtitle, "Discover")
    rows = data.entity_summary(field)
    if not rows: theme.empty(f"No {title.lower()} available", "Entity profiles will appear when indexed records contain this information.", "Empty")
    else: st.dataframe(pd.DataFrame(rows).rename(columns={"name":title.rstrip("s"),"opportunities":"Linked opportunities","highest_score":"Highest opportunity score","latest_signal":"Latest signal"}),use_container_width=True,hide_index=True)


def companies(navigate: Callable[[str], None]) -> None:
    theme.page_header(
        "Account Intelligence",
        "Evidence-governed organisations, linked products and signals, and weekly-reviewed contact routes.",
        "Discover",
    )
    search = st.text_input("Find an organisation", placeholder="Company, university, hospital, agency or known alias")
    result = data.account_directory(search)
    metrics = result["metrics"]
    a, b, c, d = st.columns(4)
    a.metric("Organisations", f"{metrics.get('organisations', 0):,}")
    b.metric("Evidence links", f"{metrics.get('relationships', 0):,}")
    c.metric("Function routes", f"{metrics.get('contact_routes', 0):,}")
    d.metric("Public named contacts", f"{metrics.get('named_contacts', 0):,}")
    monitor = metrics.get("latest_monitor") or {}
    st.caption(
        "Named contacts appear only when a public source contains a person and an evidence URL. "
        "A function route is not a verified person; every contact must be reconfirmed before outreach."
    )
    if monitor:
        st.info(
            f"Latest weekly monitor: {_safe(monitor.get('completed_at'))} · "
            f"{int(monitor.get('organisations_changed') or 0):,} organisation changes · "
            f"{int(monitor.get('contacts_due_review') or 0):,} contacts due revalidation"
        )
    rows = result["organisations"]
    if not rows:
        theme.empty("No organisations found", "Run the weekly Account Intelligence source job after source evidence is loaded.", "No matches")
        return
    labels = {
        f"{row['canonical_name']} · {row.get('organisation_type') or 'organisation'} · {row.get('relationship_count', 0)} links": row
        for row in rows
    }
    selected = st.selectbox("Organisation profile", list(labels))
    table = pd.DataFrame(rows)[[
        "canonical_name", "organisation_type", "country", "relationship_count",
        "source_count", "named_contacts", "contact_routes", "last_verified_at",
    ]].rename(columns={
        "canonical_name": "Organisation", "organisation_type": "Type", "country": "Country",
        "relationship_count": "Linked evidence", "source_count": "Sources",
        "named_contacts": "Named contacts", "contact_routes": "Function routes",
        "last_verified_at": "Last reviewed",
    })
    st.dataframe(table, use_container_width=True, hide_index=True)
    if st.button("Open organisation profile", type="primary"):
        st.session_state["account_organisation_id"] = labels[selected]["organisation_id"]
        navigate("Company Detail")


def company_detail(navigate: Callable[[str], None]) -> None:
    if st.button("← Account Intelligence"):
        navigate("Companies")
    organisation_id = str(st.session_state.get("account_organisation_id") or "")
    profile = data.account_profile(organisation_id) if organisation_id else None
    if not profile:
        theme.empty("Organisation not found", "Return to Account Intelligence and select a profile.", "Missing")
        return
    theme.page_header(profile["canonical_name"], "Organisation identity, product/signal links and contact evidence.", "Account Intelligence")
    a, b, c, d = st.columns(4)
    a.metric("Identity", _safe(profile.get("identity_status")))
    b.metric("Type", _safe(profile.get("organisation_type")))
    c.metric("Evidence sources", int(profile.get("source_count") or 0))
    d.metric("Weekly review", _safe(profile.get("next_review_at")))
    if profile.get("official_website_url"):
        st.link_button("Open official organisation website ↗", profile["official_website_url"])

    st.markdown("### Linked products, programmes and signals")
    relationships = profile.get("relationships") or []
    if relationships:
        frame = pd.DataFrame(relationships)[[
            "relationship_type", "object_name", "source_type", "source_id",
            "evidence_status", "evidence_url", "last_seen_at",
        ]].rename(columns={
            "relationship_type": "Relationship", "object_name": "Product / programme",
            "source_type": "Source", "source_id": "Source ID", "evidence_status": "Evidence status",
            "evidence_url": "Official evidence", "last_seen_at": "Last seen",
        })
        st.dataframe(frame, use_container_width=True, hide_index=True, column_config={
            "Official evidence": st.column_config.LinkColumn("Official evidence", display_text="Open source ↗")
        })
    else:
        st.caption("No current product or signal relationships.")

    st.markdown("### Publicly listed named contacts")
    st.warning("A public listing is evidence that the person was listed—not a guarantee they still own the responsibility. Reconfirm before outreach.")
    contacts = profile.get("contacts") or []
    if contacts:
        frame = pd.DataFrame(contacts)[[
            "person_name", "job_title", "contact_function", "email", "phone", "product_scope",
            "verification_status", "evidence_url", "last_verified_at", "next_review_at",
        ]].rename(columns={
            "person_name": "Person", "job_title": "Published role", "contact_function": "Function",
            "email": "Public email", "phone": "Public phone", "product_scope": "Scope",
            "verification_status": "Status", "evidence_url": "Evidence", "last_verified_at": "Last checked",
            "next_review_at": "Next review",
        })
        st.dataframe(frame, use_container_width=True, hide_index=True, column_config={
            "Evidence": st.column_config.LinkColumn("Evidence", display_text="Open source ↗")
        })
    else:
        st.info("No named person is supported by current public evidence. Use the verified function routes below.")

    st.markdown("### Responsible function routes")
    routes = profile.get("routes") or []
    if routes:
        st.dataframe(pd.DataFrame(routes)[[
            "contact_function", "product_scope", "signal_scope", "rationale", "route_status",
            "evidence_url", "last_verified_at", "next_review_at",
        ]].rename(columns={
            "contact_function": "Function", "product_scope": "Product", "signal_scope": "Signal",
            "rationale": "Why this function", "route_status": "Status", "evidence_url": "Evidence",
            "last_verified_at": "Last checked", "next_review_at": "Next review",
        }), use_container_width=True, hide_index=True, column_config={
            "Evidence": st.column_config.LinkColumn("Evidence", display_text="Open source ↗")
        })
    st.markdown("### Identity aliases and change history")
    left, right = st.columns(2)
    with left:
        aliases = profile.get("aliases") or []
        if aliases:
            st.dataframe(pd.DataFrame(aliases)[["alias_name", "source_type", "source_id", "last_seen_at"]], hide_index=True, use_container_width=True)
    with right:
        changes = profile.get("changes") or []
        if changes:
            st.dataframe(pd.DataFrame(changes)[["observed_at", "snapshot_json"]], hide_index=True, use_container_width=True)


def technology_profile() -> None:
    theme.page_header("Technology Profile", "Match real service-provider capabilities to indexed pharmaceutical problem signals.", "Discover")
    st.info("Technology fit is expressed qualitatively. It does not imply commercial readiness or buying intent.")
    rows = data.entity_summary("problem_category",50)
    if rows:
        st.markdown("### Indexed problem landscape")
        st.dataframe(pd.DataFrame(rows).rename(columns={"name":"Problem category","opportunities":"Indexed signals","highest_score":"Highest opportunity score","latest_signal":"Latest signal"}),use_container_width=True,hide_index=True)
    theme.empty("Persistent technology catalogue", "A genuine technology-ownership catalogue is planned for the Research & Innovation and Patents checkpoints. Current matching uses the approved service-provider profile.", "Planned")


def patents(navigate: Callable[[str], None]) -> None:
    theme.page_header(
        "Patent & Lifecycle Intelligence",
        "FDA Orange Book applications, listed patents, regulatory exclusivities and evidence-gated expiry monitoring.",
        "Phase 9",
    )
    st.warning(
        "An Orange Book application holder is the FDA listing organisation—not proof of patent ownership. "
        "Listings and expiry dates are regulatory lifecycle context, not a validity or freedom-to-operate opinion."
    )
    initial = data.patent_lifecycle_directory()
    facets = initial["facets"]
    f1, f2, f3 = st.columns([2, 1, 1])
    search = f1.text_input("Search lifecycle records", placeholder="Product, ingredient or application number")
    status = f2.selectbox("Lifecycle state", ["All"] + list(facets.get("status") or []))
    holder = f3.selectbox("Application holder", ["All"] + list(facets.get("holder") or []))
    result = data.patent_lifecycle_directory(search, status, holder)
    metrics = result["metrics"]
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Products", f"{metrics.get('products', 0):,}")
    m2.metric("Listed patents", f"{metrics.get('patents', 0):,}")
    m3.metric("Exclusivities", f"{metrics.get('exclusivities', 0):,}")
    m4.metric("Expiry ≤24 months", f"{metrics.get('approaching_expiry', 0):,}")
    m5.metric("Family resolution queue", f"{metrics.get('family_resolution_required', 0):,}")
    monitor = metrics.get("latest_monitor") or {}
    if monitor:
        st.info(
            f"Latest weekly lifecycle monitor: {_safe(monitor.get('completed_at'))} · "
            f"{int(monitor.get('products_changed') or 0):,} changed products · "
            f"{int(monitor.get('family_resolution_required') or 0):,} patents awaiting official family evidence"
        )
    rows = result["products"]
    if not rows:
        theme.empty("No lifecycle records found", "Run FDA Orange Book, then the patent_lifecycle projection, or broaden the filters.", "No matches")
        return
    labels = {
        f"{row['trade_name']} · {row['application_number']}-{row['product_number']} · {row.get('lifecycle_status')}": row
        for row in rows
    }
    selected = st.selectbox("Lifecycle profile", list(labels))
    columns = [
        "trade_name", "ingredient", "application_number", "product_number", "application_holder",
        "application_type", "reference_listed_drug", "reference_standard", "patent_count",
        "exclusivity_count", "lifecycle_status", "next_expiry_date", "evidence_status", "last_verified_at",
    ]
    frame = pd.DataFrame(rows)[columns].rename(columns={
        "trade_name": "Product", "ingredient": "Ingredient", "application_number": "Application",
        "product_number": "Product no.", "application_holder": "FDA application holder",
        "application_type": "Application type", "reference_listed_drug": "RLD",
        "reference_standard": "RS", "patent_count": "Listed patents",
        "exclusivity_count": "Exclusivities", "lifecycle_status": "Lifecycle state",
        "next_expiry_date": "Next listed expiry", "evidence_status": "Evidence status",
        "last_verified_at": "Last verified",
    })
    st.dataframe(frame, use_container_width=True, hide_index=True)
    left, right = st.columns([1, 4])
    if left.button("Open lifecycle detail", type="primary"):
        st.session_state["patent_lifecycle_id"] = labels[selected]["lifecycle_id"]
        navigate("Patent Detail")
    right.download_button(
        "Export filtered lifecycle records (.csv)", frame.to_csv(index=False).encode("utf-8"),
        "pharmatune_patent_lifecycle.csv", "text/csv",
    )


def patent_detail(navigate: Callable[[str], None]) -> None:
    if st.button("← Patent & Lifecycle Intelligence"):
        navigate("Patents")
    lifecycle_id = str(st.session_state.get("patent_lifecycle_id") or "")
    profile = data.patent_lifecycle_profile(lifecycle_id) if lifecycle_id else None
    if not profile:
        theme.empty("Lifecycle record not found", "Return to Patent & Lifecycle Intelligence and select a product.", "Missing")
        return
    theme.page_header(
        profile["trade_name"],
        f"{_safe(profile.get('ingredient'))} · {profile['application_number']}-{profile['product_number']}",
        "Patent & Lifecycle",
    )
    a, b, c, d = st.columns(4)
    a.metric("Lifecycle state", _safe(profile.get("lifecycle_status")))
    b.metric("Next listed expiry", _safe(profile.get("next_expiry_date"), "None listed"))
    c.metric("Listed patents", len(profile.get("patents") or []))
    d.metric("Exclusivities", len(profile.get("exclusivities") or []))
    st.markdown("### FDA application and product")
    facts = pd.DataFrame([{
        "FDA application holder": profile.get("application_holder"),
        "Application type": profile.get("application_type"),
        "Dosage form / route": profile.get("dosage_form_route"),
        "Strength": profile.get("strength"),
        "Approval date": profile.get("approval_date"),
        "RLD": profile.get("reference_listed_drug"),
        "RS": profile.get("reference_standard"),
        "TE code": profile.get("therapeutic_equivalence_code"),
    }])
    st.dataframe(facts, use_container_width=True, hide_index=True)
    if str(profile.get("official_source_url") or "").startswith("http"):
        st.link_button("Open official FDA Orange Book evidence ↗", profile["official_source_url"])
    st.caption("The application holder submitted or holds the FDA application. This field is not presented as the patent owner.")

    timeline = []
    if profile.get("approval_date"):
        timeline.append({"Date": profile["approval_date"], "Event": "FDA approval", "Identifier": profile["application_number"], "Evidence": profile["official_source_url"]})
    for patent in profile.get("patents") or []:
        timeline.append({"Date": patent.get("expiry_date"), "Event": "Orange Book listed patent expiry", "Identifier": patent.get("patent_number"), "Evidence": patent.get("official_source_url")})
    for exclusivity in profile.get("exclusivities") or []:
        timeline.append({"Date": exclusivity.get("expiry_date"), "Event": "FDA exclusivity expiry", "Identifier": exclusivity.get("exclusivity_code"), "Evidence": exclusivity.get("official_source_url")})
    st.markdown("### Lifecycle timeline")
    if timeline:
        timeline.sort(key=lambda item: str(item.get("Date") or "9999"))
        st.dataframe(pd.DataFrame(timeline), use_container_width=True, hide_index=True, column_config={"Evidence": st.column_config.LinkColumn("Evidence", display_text="FDA source ↗")})

    st.markdown("### Listed patents")
    patents = profile.get("patents") or []
    if patents:
        patent_frame = pd.DataFrame(patents)[[
            "patent_number", "expiry_date", "drug_substance_flag", "drug_product_flag", "use_code",
            "delist_requested", "application_holder_context", "ownership_status", "family_status", "family_id",
            "family_lookup_url", "official_source_url", "last_verified_at",
        ]].rename(columns={
            "patent_number": "Patent number", "expiry_date": "Listed expiry", "drug_substance_flag": "Drug substance",
            "drug_product_flag": "Drug product", "use_code": "Use code", "delist_requested": "Delist requested",
            "application_holder_context": "FDA application holder context", "ownership_status": "Ownership evidence",
            "family_status": "Family evidence", "family_id": "Verified family ID",
            "family_lookup_url": "Espacenet investigation", "official_source_url": "FDA evidence",
            "last_verified_at": "Last verified",
        })
        st.dataframe(patent_frame, use_container_width=True, hide_index=True, column_config={
            "Espacenet investigation": st.column_config.LinkColumn("Espacenet investigation", display_text="Investigate ↗"),
            "FDA evidence": st.column_config.LinkColumn("FDA evidence", display_text="FDA source ↗"),
        })
        st.warning("Patent ownership and family identifiers remain unresolved until supported by official patent-office evidence. An Espacenet search link is an investigation route, not verification.")
    else:
        st.info("No listed patent evidence is present in the retained FDA dataset for this product.")

    st.markdown("### FDA regulatory exclusivities")
    exclusivities = profile.get("exclusivities") or []
    if exclusivities:
        exclusivity_frame = pd.DataFrame(exclusivities)[[
            "exclusivity_code", "expiry_date", "official_source_url", "last_verified_at",
        ]].rename(columns={"exclusivity_code": "Code", "expiry_date": "Expiry", "official_source_url": "FDA evidence", "last_verified_at": "Last verified"})
        st.dataframe(exclusivity_frame, use_container_width=True, hide_index=True, column_config={"FDA evidence": st.column_config.LinkColumn("FDA evidence", display_text="FDA source ↗")})
    else:
        st.caption("No regulatory exclusivity entry is present in the retained FDA dataset.")
    with st.expander("Append-only lifecycle history"):
        history = profile.get("history") or []
        st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True) if history else st.caption("No observations yet.")


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
    quality = data.regulator_quality()
    st.markdown("### Checkpoint 8.1 · sales-data quality")
    q1,q2,q3,q4 = st.columns(4)
    q1.metric("Official signals", f"{quality.get('total', 0):,}")
    q2.metric("Company not stated", f"{quality.get('missing_company', 0):,}")
    q3.metric("Missing official link", f"{quality.get('missing_official_link', 0):,}")
    q4.metric("Missing score / grade", f"{quality.get('missing_score_or_grade', 0):,}")
    quality_rows = quality.get("sources") or []
    if quality_rows:
        st.dataframe(pd.DataFrame([{
            "Source": row.get("source_type"), "Signals": row.get("total"),
            "Required fields complete": f"{row.get('required_field_completeness', 0):.1f}%",
            "Company not stated": row.get("missing_company"), "Product missing": row.get("missing_product"),
            "Region missing": row.get("missing_region"), "Official link missing": row.get("missing_official_link"),
            "Problem missing": row.get("missing_problem"), "Score / grade missing": row.get("missing_score_or_grade"),
        } for row in quality_rows]), use_container_width=True, hide_index=True)
    st.caption("A blank company means the official source did not state a manufacturer or authorisation holder. PharmaTune does not copy a product name into the Company field.")
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
    fda = data.fda_orange_book_coverage()
    st.markdown("### US Food and Drug Administration — Orange Book")
    f1,f2,f3,f4 = st.columns(4)
    f1.metric("FDA products retained", f"{fda.get('total', 0):,}")
    f2.metric("Listed patents", f"{fda.get('patents', 0):,}")
    f3.metric("Exclusivity entries", f"{fda.get('exclusivities', 0):,}")
    f4.metric("Latest source update", fda.get("latest_update") or "Not ingested yet")
    if fda.get("fallback"):
        st.info("FDA is currently serving product records through the official daily Drugs@FDA fallback. Patent and exclusivity fields remain empty until FDA restores the Orange Book archive.")
    st.caption("Orange Book product, patent and exclusivity records are regulatory lifecycle context. Patent listings are not legal advice, proof of freedom to operate, product failure or commercial demand.")
    st.markdown("### Planned source families")
    for name in ("PMDA and further global regulators","Company news, deals and funding","Official patent-office family and ownership enrichment","University technology transfer"):
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
