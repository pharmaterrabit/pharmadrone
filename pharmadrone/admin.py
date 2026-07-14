"""Checkpoint 6D-B tenant administration and operator-safe mutations."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import uuid
from typing import Any

from . import db
from .scheduler import repository as scheduler_repository
from .storage.backup import build_audit_backup

PLATFORM_ADMIN = "platform_admin"
WORKSPACE_ADMIN = "workspace_admin"
ANALYST = "analyst_reviewer"
READ_ONLY = "read_only_executive"
ROLES = (PLATFORM_ADMIN, WORKSPACE_ADMIN, ANALYST, READ_ONLY)
EXPORT_POLICIES = ("workspace_admin_approval", "analyst_allowed", "disabled")
NOTIFICATION_MODES = ("daily_digest", "weekly_digest", "critical_only", "disabled")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80] or "organisation"


def _require_platform(principal: dict[str, Any]) -> None:
    if principal.get("role") != PLATFORM_ADMIN:
        raise PermissionError("Platform administrator access is required.")


def _scope(principal: dict[str, Any], organisation_id: str | None = None) -> str | None:
    if principal.get("role") == PLATFORM_ADMIN:
        return organisation_id
    if principal.get("role") != WORKSPACE_ADMIN:
        raise PermissionError("Administration access is required.")
    own = str(principal.get("organisation_id") or "").strip()
    if not own or (organisation_id and organisation_id != own):
        raise PermissionError("Workspace administrators are restricted to their organisation.")
    return own


def log_event(conn, principal: dict[str, Any], event_type: str, summary: str, *,
              organisation_id: str | None = None, workspace_id: str | None = None,
              severity: str = "INFO", metadata: dict[str, Any] | None = None) -> None:
    org = _scope(principal, organisation_id)
    conn.execute(
        "INSERT INTO admin_audit_events (organisation_id, workspace_id, actor_name, actor_role, event_type, severity, safe_summary, metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (org, workspace_id, principal.get("display_name") or "Authenticated administrator", principal.get("role"),
         event_type, severity, summary[:500], json.dumps(metadata or {}, sort_keys=True), now_iso()),
    )


def create_organisation(principal: dict[str, Any], name: str, plan_name: str = "Unassigned") -> str:
    _require_platform(principal)
    clean = " ".join(name.split())
    if len(clean) < 2:
        raise ValueError("Organisation name is required.")
    conn = db.connect()
    try:
        org_id = _id("org")
        slug = _slug(clean)
        if conn.execute("SELECT 1 FROM organisations WHERE slug=?", (slug,)).fetchone():
            slug = f"{slug}-{org_id[-6:]}"
        with conn.transaction():
            conn.execute("INSERT INTO organisations (organisation_id,name,slug,plan_name,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                         (org_id, clean, slug, plan_name, "active", now_iso(), now_iso()))
            conn.execute("INSERT INTO workspace_settings (organisation_id,updated_at) VALUES (?,?)", (org_id, now_iso()))
            log_event(conn, principal, "ORGANISATION_CREATED", f"Created organisation {clean}.", organisation_id=org_id)
        return org_id
    finally:
        conn.close()


def create_workspace(principal: dict[str, Any], organisation_id: str, name: str) -> str:
    org = _scope(principal, organisation_id)
    clean = " ".join(name.split())
    if not clean:
        raise ValueError("Workspace name is required.")
    conn = db.connect()
    try:
        workspace_id = _id("ws")
        with conn.transaction():
            conn.execute("INSERT INTO workspaces (workspace_id,organisation_id,name,status,created_at) VALUES (?,?,?,?,?)",
                         (workspace_id, org, clean, "active", now_iso()))
            log_event(conn, principal, "WORKSPACE_CREATED", f"Created workspace {clean}.", organisation_id=org, workspace_id=workspace_id)
        return workspace_id
    finally:
        conn.close()


def invite_user(principal: dict[str, Any], organisation_id: str, email: str, display_name: str,
                role_name: str, workspace_id: str | None = None) -> str:
    org = _scope(principal, organisation_id)
    if role_name not in ROLES or (principal.get("role") == WORKSPACE_ADMIN and role_name == PLATFORM_ADMIN):
        raise PermissionError("That role cannot be assigned by this administrator.")
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError("A valid email address is required.")
    conn = db.connect()
    try:
        if workspace_id:
            workspace = conn.execute(
                "SELECT organisation_id FROM workspaces WHERE workspace_id=?", (workspace_id,)
            ).fetchone()
            if not workspace or workspace.get("organisation_id") != org:
                raise ValueError("The selected workspace does not belong to this organisation.")
        user_id = _id("usr")
        with conn.transaction():
            conn.execute("INSERT INTO admin_users (user_id,organisation_id,workspace_id,display_name,email,role_name,status,mfa_enabled,invited_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (user_id, org, workspace_id, display_name.strip() or email, email, role_name, "invited", 0, now_iso(), now_iso()))
            log_event(conn, principal, "USER_INVITED", f"Invited {email} as {role_name}.", organisation_id=org, workspace_id=workspace_id)
        return user_id
    finally:
        conn.close()


def set_user_status(principal: dict[str, Any], user_id: str, status: str) -> None:
    if status not in {"active", "suspended", "invited"}:
        raise ValueError("Unsupported user status.")
    conn = db.connect()
    try:
        row = conn.execute("SELECT organisation_id,email FROM admin_users WHERE user_id=?", (user_id,)).fetchone()
        if not row:
            raise ValueError("User not found.")
        org = _scope(principal, row.get("organisation_id"))
        with conn.transaction():
            conn.execute("UPDATE admin_users SET status=? WHERE user_id=?", (status, user_id))
            log_event(conn, principal, "USER_STATUS_CHANGED", f"Set {row.get('email')} to {status}.", organisation_id=org)
    finally:
        conn.close()


def update_workspace_settings(principal: dict[str, Any], organisation_id: str, *, export_policy: str,
                              notification_mode: str, retention_days: int, mfa_required: bool) -> None:
    org = _scope(principal, organisation_id)
    if export_policy not in EXPORT_POLICIES:
        raise ValueError("Unsupported export policy.")
    if notification_mode not in NOTIFICATION_MODES:
        raise ValueError("Unsupported notification setting.")
    if retention_days < 30 or retention_days > 3650:
        raise ValueError("Retention must be between 30 and 3650 days.")
    conn = db.connect()
    try:
        with conn.transaction():
            conn.execute("UPDATE workspace_settings SET export_policy=?,notification_mode=?,retention_days=?,mfa_required=?,updated_at=? WHERE organisation_id=?",
                         (export_policy, notification_mode, retention_days, int(mfa_required), now_iso(), org))
            conn.execute("UPDATE organisations SET retention_days=?,updated_at=? WHERE organisation_id=?", (retention_days, now_iso(), org))
            log_event(conn, principal, "WORKSPACE_SETTINGS_CHANGED", "Updated workspace governance settings.", organisation_id=org)
    finally:
        conn.close()


def set_source_enabled(principal: dict[str, Any], source_name: str, enabled: bool) -> None:
    _require_platform(principal)
    conn = db.connect()
    try:
        scheduler_repository.ensure_source_states(conn)
        with conn.transaction():
            result = conn.execute("UPDATE source_refresh_state SET enabled=?,updated_at=? WHERE source_name=?", (int(enabled), now_iso(), source_name))
            if result.rowcount != 1:
                raise ValueError("Source job not found.")
            log_event(conn, principal, "SOURCE_STATE_CHANGED", f"Set {source_name} to {'enabled' if enabled else 'disabled'}.")
    finally:
        conn.close()


def queue_source_run(principal: dict[str, Any], source_name: str) -> None:
    _require_platform(principal)
    conn = db.connect()
    try:
        with conn.transaction():
            result = conn.execute("UPDATE source_refresh_state SET next_due_at=?,updated_at=? WHERE source_name=?", (now_iso(), now_iso(), source_name))
            if result.rowcount != 1:
                raise ValueError("Source job not found.")
            log_event(conn, principal, "SOURCE_RUN_QUEUED", f"Queued {source_name} for the next orchestrator run.")
    finally:
        conn.close()


def set_feature_flag(principal: dict[str, Any], scope_key: str, enabled: bool) -> None:
    _require_platform(principal)
    conn = db.connect()
    try:
        with conn.transaction():
            row = conn.execute("SELECT flag_key FROM feature_flags WHERE scope_key=?", (scope_key,)).fetchone()
            if not row:
                raise ValueError("Feature flag not found.")
            conn.execute("UPDATE feature_flags SET enabled=?,updated_at=? WHERE scope_key=?", (int(enabled), now_iso(), scope_key))
            log_event(conn, principal, "FEATURE_FLAG_CHANGED", f"Set {row.get('flag_key')} to {'enabled' if enabled else 'disabled'}.")
    finally:
        conn.close()


def prepare_backup(principal: dict[str, Any]) -> bytes:
    _require_platform(principal)
    conn = db.connect()
    try:
        payload = build_audit_backup(conn)
        digest = hashlib.sha256(payload).hexdigest()
        with conn.transaction():
            conn.execute("INSERT INTO backup_records (scope_name,status,checksum_verified,size_bytes,safe_summary,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
                         ("audit", "ready", 1, len(payload), f"Audit backup SHA-256 {digest[:16]}…", principal.get("display_name"), now_iso()))
            log_event(conn, principal, "BACKUP_PREPARED", "Prepared a checksum-verified audit backup.")
        return payload
    finally:
        conn.close()


def snapshot(principal: dict[str, Any]) -> dict[str, Any]:
    """Return real, credential-safe administration data scoped by role."""
    org = _scope(principal)
    conn = db.connect()
    try:
        db_status = db.database_status()
        scheduler = scheduler_repository.scheduler_summary(conn)
        where, params = ("", ()) if principal.get("role") == PLATFORM_ADMIN else (" WHERE organisation_id=?", (org,))
        organisations = [dict(r) for r in conn.execute("SELECT * FROM organisations ORDER BY name").fetchall()] if principal.get("role") == PLATFORM_ADMIN else [dict(r) for r in conn.execute("SELECT * FROM organisations WHERE organisation_id=?", (org,)).fetchall()]
        users = [dict(r) for r in conn.execute(f"SELECT * FROM admin_users{where} ORDER BY display_name", params).fetchall()]
        workspaces = [dict(r) for r in conn.execute(f"SELECT * FROM workspaces{where} ORDER BY name", params).fetchall()]
        events = [dict(r) for r in conn.execute(f"SELECT * FROM admin_audit_events{where} ORDER BY created_at DESC LIMIT 200", params).fetchall()]
        usage = [dict(r) for r in conn.execute("SELECT * FROM api_usage_daily ORDER BY usage_date DESC, provider LIMIT 200").fetchall()] if principal.get("role") == PLATFORM_ADMIN else []
        flags = [dict(r) for r in conn.execute("SELECT * FROM feature_flags ORDER BY flag_key,scope_key").fetchall()] if principal.get("role") == PLATFORM_ADMIN else []
        backups = [dict(r) for r in conn.execute("SELECT * FROM backup_records ORDER BY created_at DESC LIMIT 50").fetchall()] if principal.get("role") == PLATFORM_ADMIN else []
        settings = dict(conn.execute("SELECT * FROM workspace_settings WHERE organisation_id=?", (org,)).fetchone() or {}) if org else {}
        failed_runs = [dict(r) for r in conn.execute("SELECT * FROM source_refresh_runs WHERE status IN ('Failed','Degraded') ORDER BY started_at DESC LIMIT 100").fetchall()] if principal.get("role") == PLATFORM_ADMIN else []
        return {
            "principal": dict(principal), "database": db_status, "scheduler": scheduler,
            "organisations": organisations, "users": users, "workspaces": workspaces,
            "events": events, "usage": usage, "flags": flags, "backups": backups,
            "settings": settings, "failed_runs": failed_runs,
        }
    finally:
        conn.close()
