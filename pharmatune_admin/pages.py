"""Live, role-scoped administration pages for Checkpoint 6D-B."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from pharmadrone import admin
from pharmatune_ui import theme


def _table(rows, empty: str, columns: list[str] | None = None):
    if not rows:
        theme.empty(empty, "No durable records have been created for this area yet.", "No records")
        return
    frame = pd.DataFrame(rows)
    if columns:
        frame = frame[[c for c in columns if c in frame.columns]]
    st.dataframe(frame, use_container_width=True, hide_index=True)


def _safe_index(options: list[str], value: str, fallback: str) -> int:
    return options.index(value) if value in options else options.index(fallback)


def platform_overview(principal, state):
    theme.page_header("Platform Overview", "Live multi-tenant, persistence and source-operation status.", "Platform Admin")
    sched, dbs = state["scheduler"], state["database"]
    a,b,c,d = st.columns(4)
    a.metric("Organisations", len(state["organisations"])); b.metric("Users", len(state["users"]))
    c.metric("Enabled source jobs", sched.get("enabled_sources",0)); d.metric("Failed sources", sched.get("failed_sources",0))
    st.markdown("### Production infrastructure")
    rows = [
        {"Area":"Database","Status":str(dbs.get("connection_status","unknown")).title(),"Detail":f"{str(dbs.get('backend','')).upper()} · schema v{dbs.get('schema_version')} · {dbs.get('migration_count')} migrations"},
        {"Area":"Scheduler","Status":sched.get("scheduler_status"),"Detail":f"Next orchestrator {sched.get('next_orchestrator_run')}"},
        {"Area":"Administration audit","Status":"Append-only","Detail":f"{len(state['events'])} recent events loaded"},
    ]
    _table(rows,"No infrastructure status")


def organisations(principal, state):
    theme.page_header("Organisations", "Provision and inspect tenant organisations without exposing record-level intelligence.", "Platform Admin")
    with st.form("create_org"):
        name=st.text_input("Organisation name"); plan=st.selectbox("Plan",["Unassigned","Pilot","Enterprise"])
        if st.form_submit_button("Create organisation",type="primary"):
            try: admin.create_organisation(principal,name,plan); st.success("Organisation created."); st.rerun()
            except Exception as exc: st.error(str(exc))
    _table(state["organisations"],"No organisations provisioned",["organisation_id","name","slug","plan_name","status","retention_days","created_at"])


def workspaces(principal, state):
    theme.page_header("Workspaces", "Provision workspaces inside an existing organisation.", "Platform Admin")
    orgs={f"{r['name']} · {r['organisation_id']}":r["organisation_id"] for r in state["organisations"]}
    if orgs:
        with st.form("create_ws"):
            label=st.selectbox("Organisation",list(orgs)); name=st.text_input("Workspace name")
            if st.form_submit_button("Create workspace",type="primary"):
                try: admin.create_workspace(principal,orgs[label],name); st.success("Workspace created."); st.rerun()
                except Exception as exc: st.error(str(exc))
    _table(state["workspaces"],"No workspaces provisioned",["workspace_id","organisation_id","name","status","created_at"])


def users(principal, state):
    theme.page_header("Users", "Invite users and assign explicit tenant roles.", "Platform Admin")
    orgs={f"{r['name']} · {r['organisation_id']}":r["organisation_id"] for r in state["organisations"]}
    if orgs:
        with st.form("invite_platform_user"):
            c1,c2=st.columns(2); name=c1.text_input("Display name"); email=c2.text_input("Email")
            org_label=st.selectbox("Organisation",list(orgs)); role=st.selectbox("Role",admin.ROLES)
            if st.form_submit_button("Invite user",type="primary"):
                try: admin.invite_user(principal,orgs[org_label],email,name,role); st.success("Invitation recorded."); st.rerun()
                except Exception as exc: st.error(str(exc))
    _table(state["users"],"No users provisioned",["user_id","display_name","email","organisation_id","role_name","status","mfa_enabled","last_login_at"])


def roles(principal, state):
    theme.page_header("Roles & Permissions", "The enforced server-side capability boundary.", "Platform Admin")
    rows=[
        {"Role":"Platform Admin","Tenant scope":"All organisations","Customer intelligence":"No direct record access","Operations":"Connectors, jobs, backups, flags, logs"},
        {"Role":"Workspace Admin","Tenant scope":"Assigned organisation only","Customer intelligence":"Workspace governance only","Operations":"Members, roles, exports, notifications, retention"},
        {"Role":"Analyst / Reviewer","Tenant scope":"Assigned workspace","Customer intelligence":"Customer platform","Operations":"Evidence review and permitted exports"},
        {"Role":"Read-only Executive","Tenant scope":"Assigned workspace","Customer intelligence":"Approved read-only views","Operations":"No mutations"},
    ]; _table(rows,"No role policy")


def connectors(principal, state):
    theme.page_header("Source Connectors", "Live source-state controls. Secret values and connection strings remain hidden.", "Operations")
    sources=state["scheduler"].get("sources",[]); _table(sources,"No source jobs",["source_name","cadence","enabled","last_status","last_success_at","next_due_at","consecutive_failures","last_error_summary"])
    if sources:
        names=[r["source_name"] for r in sources]; selected=st.selectbox("Source operation",names)
        row=next(r for r in sources if r["source_name"]==selected); c1,c2=st.columns(2)
        if c1.button("Disable" if int(row.get("enabled") or 0) else "Enable",use_container_width=True):
            admin.set_source_enabled(principal,selected,not bool(int(row.get("enabled") or 0))); st.rerun()
        if c2.button("Queue for next orchestrator run",use_container_width=True): admin.queue_source_run(principal,selected); st.rerun()


def jobs(principal, state):
    theme.page_header("Scheduled Jobs", "Real scheduler cadence, cursor state and next-run timing.", "Operations")
    _table(state["scheduler"].get("sources",[]),"No scheduled jobs",["source_name","cadence","last_status","last_attempt_at","last_success_at","next_due_at","consecutive_failures"])


def ingestion(principal, state):
    theme.page_header("Data Ingestion", "Latest durable refresh totals and per-source outcomes.", "Operations")
    latest=state["scheduler"].get("latest_run") or {}
    a,b,c,d=st.columns(4); a.metric("Retrieved",latest.get("records_retrieved",0)); b.metric("Created",latest.get("records_created",0)); c.metric("Updated",latest.get("records_updated",0)); d.metric("Duplicates prevented",latest.get("duplicate_records_prevented",0))
    _table(state["scheduler"].get("sources",[]),"No ingestion state",["source_name","records_retrieved","records_created","records_updated","records_unchanged","records_rejected","retry_count"])


def failed_jobs(principal, state):
    theme.page_header("Failed Jobs & Retries", "Credential-safe failure summaries from durable scheduler history.", "Operations")
    _table(state["failed_runs"],"No failed or degraded source runs",["run_id","source_name","started_at","status","retry_count","error_class","error_summary"])


def usage(principal, state):
    theme.page_header("API Usage & Costs", "Only durable provider usage records are shown; missing billing telemetry is not estimated.", "Platform Admin")
    _table(state["usage"],"No durable API billing telemetry",["usage_date","provider","api_family","call_count","success_count","failure_count","estimated_cost_usd"])
    st.info("Provider secrets and raw billing credentials are never displayed. Configure a production usage importer before treating this page as a billing statement.")


def database_backups(principal, state):
    theme.page_header("Database & Backups", "Credential-safe persistence health and checksum-verified audit exports.", "Platform Admin")
    dbs=state["database"]; a,b,c=st.columns(3); a.metric("Backend",str(dbs.get("backend")).upper()); b.metric("Schema",f"v{dbs.get('schema_version')}"); c.metric("Migrations",dbs.get("migration_count"))
    if st.button("Prepare checksum-verified audit backup",type="primary"):
        try: st.session_state["admin_backup"]=admin.prepare_backup(principal); st.success("Backup prepared and recorded.")
        except Exception as exc: st.error(str(exc))
    if st.session_state.get("admin_backup"):
        st.download_button("Download audit backup",st.session_state["admin_backup"],"pharmatune_audit_backup.zip","application/zip")
    _table(state["backups"],"No administration backup records",["created_at","scope_name","status","checksum_verified","size_bytes","safe_summary","restore_tested_at"])


def audit_logs(principal, state):
    theme.page_header("Audit & Security Logs", "Append-only administration events with credential-safe summaries.", "Platform Admin")
    _table(state["events"],"No administration events",["created_at","severity","actor_name","actor_role","organisation_id","event_type","safe_summary"])


def feature_flags(principal, state):
    theme.page_header("Feature Flags", "Durable flags only. No illustrative flags are created automatically.", "Platform Admin")
    _table(state["flags"],"No feature flags configured",["scope_key","flag_key","description","scope_type","organisation_id","enabled","status","updated_at"])
    if state["flags"]:
        keys=[r["scope_key"] for r in state["flags"]]; key=st.selectbox("Flag operation",keys); row=next(r for r in state["flags"] if r["scope_key"]==key)
        if st.button("Disable flag" if int(row.get("enabled") or 0) else "Enable flag"):
            admin.set_feature_flag(principal,key,not bool(int(row.get("enabled") or 0))); st.rerun()


def system_configuration(principal, state):
    theme.page_header("System Configuration", "Reserved for validated, secret-safe configuration controls.", "Platform Admin")
    theme.empty("System Configuration","This remains intentionally unavailable until each setting has a durable schema, validation rule and audit event.","Coming soon")


def workspace_administration(principal, state):
    theme.page_header("Workspace Administration", "Organisation-scoped members, governance, usage and audit activity.", "Workspace Admin")
    st.info("Scoped view: other organisations, global connectors, API secrets, database controls and platform-wide security logs are hidden server-side.")
    org=(state["organisations"] or [{}])[0]; org_id=principal.get("organisation_id")
    if not org:
        theme.empty("Organisation not provisioned",f"No organisation record exists for {org_id}. A Platform Admin must provision it first.","Setup required"); return
    a,b,c=st.columns(3); a.metric("Organisation",org.get("name")); b.metric("Workspaces",len(state["workspaces"])); c.metric("Members",len(state["users"]))
    active_members=sum(1 for user in state["users"] if user.get("status") == "active")
    pending_invites=sum(1 for user in state["users"] if user.get("status") == "invited")
    recent_activity=len(state["events"])
    st.markdown("### Workspace usage")
    u1,u2,u3=st.columns(3); u1.metric("Active members",active_members); u2.metric("Pending invitations",pending_invites); u3.metric("Recent admin events",recent_activity)
    st.caption("These are live workspace-governance counts. API provider billing remains restricted to Platform Administration.")
    st.markdown("### Team members"); _table(state["users"],"No members",["display_name","email","role_name","status","mfa_enabled","export_allowed","outreach_allowed","last_login_at"])
    with st.form("ws_invite"):
        c1,c2=st.columns(2); name=c1.text_input("Display name"); email=c2.text_input("Email")
        role=st.selectbox("Workspace role",[admin.WORKSPACE_ADMIN,admin.ANALYST,admin.READ_ONLY])
        if st.form_submit_button("Invite member",type="primary"):
            try: admin.invite_user(principal,org_id,email,name,role); st.success("Invitation recorded."); st.rerun()
            except Exception as exc: st.error(str(exc))
    settings=state.get("settings") or {}
    with st.form("ws_settings"):
        export_options=list(admin.EXPORT_POLICIES); notification_options=list(admin.NOTIFICATION_MODES)
        export=st.selectbox("Case-study export policy",export_options,index=_safe_index(export_options,settings.get("export_policy","workspace_admin_approval"),"workspace_admin_approval"))
        notify=st.selectbox("Notifications",notification_options,index=_safe_index(notification_options,settings.get("notification_mode","daily_digest"),"daily_digest"))
        retention=st.number_input("Data retention days",30,3650,int(settings.get("retention_days",2555))); mfa=st.checkbox("Require MFA",value=bool(settings.get("mfa_required",1)))
        if st.form_submit_button("Save workspace settings"):
            try: admin.update_workspace_settings(principal,org_id,export_policy=export,notification_mode=notify,retention_days=int(retention),mfa_required=mfa); st.success("Settings saved."); st.rerun()
            except Exception as exc: st.error(str(exc))
    st.markdown("### Workspace audit activity"); _table(state["events"],"No workspace administration events",["created_at","actor_name","event_type","severity","safe_summary"])
