"""Phase 12 tenant-scoped saved intelligence, alerts and governed exports."""
from __future__ import annotations

import csv
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import io
import json
import uuid
from typing import Any

from pharmadrone import db

WRITERS = {"analyst_reviewer"}
READERS = WRITERS | {"read_only_executive"}
RECORD_TYPES = (
    "opportunity", "organisation", "regulatory", "patent", "research", "commercial_event"
)
ALERT_CADENCES = ("daily", "weekly")
ALERT_SEVERITIES = ("low", "medium", "high", "critical")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:20]}"


def scope(principal: dict[str, Any]) -> dict[str, str]:
    role = str(principal.get("role") or "")
    if role not in READERS:
        raise PermissionError("Customer intelligence access is not assigned to this role.")
    organisation_id = str(principal.get("organisation_id") or "").strip()
    workspace_id = str(principal.get("workspace_id") or "").strip()
    if organisation_id:
        key = f"org:{organisation_id}:ws:{workspace_id or 'default'}"
    else:
        key = "personal:default"
    return {
        "scope_key": key,
        "organisation_id": organisation_id,
        "workspace_id": workspace_id,
        "actor": str(principal.get("display_name") or "Authenticated customer"),
        "role": role,
    }


def capabilities(principal: dict[str, Any], conn=None) -> dict[str, Any]:
    identity = scope(principal)
    policy = "analyst_allowed"
    if identity["organisation_id"]:
        owned = conn is None
        conn = conn or db.connect()
        try:
            row = conn.execute(
                "SELECT export_policy,notification_mode FROM workspace_settings WHERE organisation_id=?",
                (identity["organisation_id"],),
            ).fetchone()
            if row:
                policy = str(row.get("export_policy") or "workspace_admin_approval")
                notification = str(row.get("notification_mode") or "daily_digest")
            else:
                notification = "daily_digest"
        finally:
            if owned:
                conn.close()
    else:
        notification = "daily_digest"
    can_write = identity["role"] in WRITERS
    can_export = can_write and policy != "disabled" and (
        policy == "analyst_allowed" or bool(principal.get("export_allowed"))
    )
    return {
        **identity, "can_write": can_write, "can_export": can_export,
        "export_policy": policy, "notification_mode": notification,
    }


def _require_write(principal: dict[str, Any]) -> dict[str, str]:
    identity = scope(principal)
    if identity["role"] not in WRITERS:
        raise PermissionError("This role has read-only customer access.")
    return identity


def _activity(conn, identity: dict[str, str], event_type: str, summary: str,
              metadata: dict[str, Any] | None = None) -> None:
    conn.execute(
        "INSERT INTO customer_activity_events (scope_key,actor_name,actor_role,event_type,safe_summary,created_at,metadata_json) VALUES (?,?,?,?,?,?,?)",
        (identity["scope_key"], identity["actor"], identity["role"], event_type,
         summary[:500], now_iso(), json.dumps(metadata or {}, sort_keys=True)),
    )


def create_list(principal: dict[str, Any], name: str, description: str = "",
                visibility: str = "workspace") -> str:
    identity = _require_write(principal)
    clean = " ".join(name.split())
    if len(clean) < 2:
        raise ValueError("A saved-list name is required.")
    if visibility not in {"private", "workspace"}:
        raise ValueError("Unsupported list visibility.")
    conn = db.connect()
    try:
        list_id = _id("list")
        with conn.transaction():
            conn.execute(
                "INSERT INTO customer_saved_lists (saved_list_id,scope_key,organisation_id,workspace_id,name,description,visibility,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (list_id, identity["scope_key"], identity["organisation_id"] or None,
                 identity["workspace_id"] or None, clean, description.strip(), visibility,
                 identity["actor"], now_iso(), now_iso()),
            )
            _activity(conn, identity, "SAVED_LIST_CREATED", f"Created saved list {clean}.", {"saved_list_id": list_id})
        return list_id
    finally:
        conn.close()


def saved_lists(principal: dict[str, Any]) -> list[dict[str, Any]]:
    identity = scope(principal)
    conn = db.connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT l.*,COUNT(i.saved_item_id) AS item_count FROM customer_saved_lists l "
            "LEFT JOIN customer_saved_items i ON i.saved_list_id=l.saved_list_id "
            "WHERE l.scope_key=? AND l.archived=0 GROUP BY l.saved_list_id ORDER BY l.updated_at DESC,l.name",
            (identity["scope_key"],),
        ).fetchall()]
    finally:
        conn.close()


def list_items(principal: dict[str, Any], saved_list_id: str) -> list[dict[str, Any]]:
    identity = scope(principal)
    conn = db.connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT i.* FROM customer_saved_items i JOIN customer_saved_lists l ON l.saved_list_id=i.saved_list_id "
            "WHERE i.saved_list_id=? AND l.scope_key=? ORDER BY i.added_at DESC",
            (saved_list_id, identity["scope_key"]),
        ).fetchall()]
    finally:
        conn.close()


def add_item(principal: dict[str, Any], saved_list_id: str, *, record_type: str,
             record_id: str, record_label: str, source_url: str = "", note: str = "",
             evidence_status: str = "internal intelligence", snapshot: dict[str, Any] | None = None) -> str:
    identity = _require_write(principal)
    if record_type not in RECORD_TYPES:
        raise ValueError("Unsupported record type.")
    if not record_id.strip() or not record_label.strip():
        raise ValueError("A record ID and label are required.")
    if source_url and not source_url.startswith(("https://", "http://")):
        raise ValueError("Evidence URLs must use HTTP or HTTPS.")
    conn = db.connect()
    try:
        owned = conn.execute(
            "SELECT name FROM customer_saved_lists WHERE saved_list_id=? AND scope_key=? AND archived=0",
            (saved_list_id, identity["scope_key"]),
        ).fetchone()
        if not owned:
            raise PermissionError("The saved list is outside this workspace or archived.")
        item_id = _id("item")
        with conn.transaction():
            conn.execute(
                "INSERT INTO customer_saved_items (saved_item_id,saved_list_id,scope_key,record_type,record_id,record_label,source_url,evidence_status,note,added_by,added_at,snapshot_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(saved_list_id,record_type,record_id) DO UPDATE SET record_label=excluded.record_label,source_url=excluded.source_url,evidence_status=excluded.evidence_status,note=excluded.note,snapshot_json=excluded.snapshot_json",
                (item_id, saved_list_id, identity["scope_key"], record_type, record_id.strip(),
                 record_label.strip(), source_url.strip(), evidence_status, note.strip(), identity["actor"],
                 now_iso(), json.dumps(snapshot or {}, sort_keys=True, default=str)),
            )
            conn.execute("UPDATE customer_saved_lists SET updated_at=? WHERE saved_list_id=?", (now_iso(), saved_list_id))
            _activity(conn, identity, "SAVED_ITEM_ADDED", f"Saved {record_label.strip()} to {owned.get('name')}.",
                      {"saved_list_id": saved_list_id, "record_type": record_type, "record_id": record_id})
        return item_id
    finally:
        conn.close()


def remove_item(principal: dict[str, Any], saved_item_id: str) -> None:
    identity = _require_write(principal)
    conn = db.connect()
    try:
        row = conn.execute("SELECT record_label,saved_list_id FROM customer_saved_items WHERE saved_item_id=? AND scope_key=?",
                           (saved_item_id, identity["scope_key"])).fetchone()
        if not row:
            raise ValueError("Saved item not found in this workspace.")
        with conn.transaction():
            conn.execute("DELETE FROM customer_saved_items WHERE saved_item_id=?", (saved_item_id,))
            conn.execute("UPDATE customer_saved_lists SET updated_at=? WHERE saved_list_id=?", (now_iso(), row.get("saved_list_id")))
            _activity(conn, identity, "SAVED_ITEM_REMOVED", f"Removed {row.get('record_label')} from a saved list.")
    finally:
        conn.close()


def create_alert_rule(principal: dict[str, Any], *, name: str, record_type: str,
                      search_term: str = "", source_filter: str = "", region_filter: str = "",
                      severity: str = "medium", cadence: str = "daily",
                      saved_list_id: str | None = None) -> str:
    identity = _require_write(principal)
    if record_type not in ("all",) + RECORD_TYPES:
        raise ValueError("Unsupported alert record type.")
    if severity not in ALERT_SEVERITIES or cadence not in ALERT_CADENCES:
        raise ValueError("Unsupported alert severity or cadence.")
    clean = " ".join(name.split())
    if len(clean) < 2:
        raise ValueError("An alert-rule name is required.")
    conn = db.connect()
    try:
        if saved_list_id and not conn.execute(
            "SELECT 1 FROM customer_saved_lists WHERE saved_list_id=? AND scope_key=? AND archived=0",
            (saved_list_id, identity["scope_key"]),
        ).fetchone():
            raise PermissionError("The linked saved list is outside this workspace.")
        rule_id = _id("alert")
        with conn.transaction():
            conn.execute(
                "INSERT INTO customer_alert_rules (alert_rule_id,scope_key,saved_list_id,name,record_type,search_term,source_filter,region_filter,severity,cadence,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (rule_id, identity["scope_key"], saved_list_id, clean, record_type,
                 search_term.strip(), source_filter.strip(), region_filter.strip(), severity,
                 cadence, identity["actor"], now_iso()),
            )
            _activity(conn, identity, "ALERT_RULE_CREATED", f"Created alert rule {clean}.", {"alert_rule_id": rule_id})
        return rule_id
    finally:
        conn.close()


def alert_rules(principal: dict[str, Any]) -> list[dict[str, Any]]:
    identity = scope(principal)
    conn = db.connect()
    try:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM customer_alert_rules WHERE scope_key=? ORDER BY enabled DESC,created_at DESC",
            (identity["scope_key"],),
        ).fetchall()]
    finally:
        conn.close()


def _candidate_queries(record_type: str) -> list[tuple[str, str]]:
    queries = {
        "opportunity": ("opportunity_index", "stable_lead_id", "COALESCE(product,company,source_id)", "COALESCE(company,'') || ' · ' || COALESCE(problem_category,'')", "source_type", "region", "evidence_links_json", "COALESCE(last_updated_at,last_checked_at,created_at)"),
        "organisation": ("account_organisations", "organisation_id", "canonical_name", "COALESCE(organisation_type,'Organisation') || ' · ' || COALESCE(country,'')", "'Account Intelligence'", "country", "official_website_url", "last_verified_at"),
        "regulatory": ("opportunity_index", "stable_lead_id", "COALESCE(product,company,source_id)", "COALESCE(problem_category,'Regulatory signal')", "source_type", "region", "evidence_links_json", "COALESCE(last_updated_at,last_checked_at,created_at)"),
        "patent": ("lifecycle_products", "lifecycle_id", "COALESCE(trade_name,ingredient,application_number)", "COALESCE(application_holder,'') || ' · ' || COALESCE(lifecycle_status,'')", "'FDA Orange Book'", "'United States'", "official_source_url", "last_verified_at"),
        "research": ("research_publications", "research_publication_id", "title", "COALESCE(publication_type,'Publication') || ' · ' || COALESCE(publication_year,'')", "'Scholarly source'", "''", "evidence_urls_json", "last_verified_at"),
        "commercial_event": ("commercial_events", "commercial_event_id", "COALESCE(subject_name,party_a_name,source_id)", "COALESCE(event_type,'Commercial event') || ' · ' || COALESCE(party_a_name,'')", "source_type", "geography", "evidence_url", "last_verified_at"),
    }
    selected = list(queries) if record_type == "all" else [record_type]
    result = []
    for kind in selected:
        if kind not in queries:
            continue
        v = queries[kind]
        query = "SELECT " + ",".join((f"{v[1]} AS record_id", f"{v[2]} AS title", f"{v[3]} AS summary", f"{v[4]} AS source_name", f"{v[5]} AS region", f"{v[6]} AS source_url", f"{v[7]} AS observed_at")) + f" FROM {v[0]}"
        if kind == "regulatory":
            query += " WHERE source_type LIKE 'FDA %' OR source_type LIKE 'EMA %' OR source_type LIKE 'MHRA %'"
        result.append((kind, query))
    return result


def _matches(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    haystack = " ".join(str(row.get(k) or "") for k in ("title", "summary", "source_name", "region")).lower()
    return (not rule.get("search_term") or str(rule["search_term"]).lower() in haystack) and \
        (not rule.get("source_filter") or str(rule["source_filter"]).lower() in str(row.get("source_name") or "").lower()) and \
        (not rule.get("region_filter") or str(rule["region_filter"]).lower() in str(row.get("region") or "").lower())


def evaluate_alerts(principal: dict[str, Any] | None = None, *, conn=None,
                    transactional: bool = True) -> dict[str, int]:
    """Evaluate stored rules against stored intelligence; no external API calls."""
    owned = conn is None
    conn = conn or db.connect()
    try:
        if principal:
            identity = _require_write(principal)
            rules = [dict(r) for r in conn.execute(
                "SELECT * FROM customer_alert_rules WHERE scope_key=? AND enabled=1", (identity["scope_key"],)
            ).fetchall()]
        else:
            identity = None
            rules = [dict(r) for r in conn.execute("SELECT * FROM customer_alert_rules WHERE enabled=1").fetchall()]
        created = evaluated = 0
        with (conn.transaction() if transactional else nullcontext()):
            for rule in rules:
                evaluated += 1
                for kind, query in _candidate_queries(str(rule.get("record_type") or "all")):
                    try:
                        rows = conn.execute(query + " ORDER BY observed_at DESC LIMIT 250").fetchall()
                    except Exception:
                        continue
                    for raw in rows:
                        row = dict(raw)
                        if not _matches(row, rule):
                            continue
                        fingerprint = hashlib.sha256(
                            f"{kind}|{row.get('record_id')}|{row.get('observed_at')}|{rule['alert_rule_id']}".encode()
                        ).hexdigest()
                        event_id = _id("event")
                        result = conn.execute(
                            "INSERT INTO customer_alert_events (alert_event_id,scope_key,alert_rule_id,record_type,record_id,title,summary,severity,source_url,evidence_status,event_fingerprint,detected_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(alert_rule_id,event_fingerprint) DO NOTHING",
                            (event_id, rule["scope_key"], rule["alert_rule_id"], kind, str(row.get("record_id")),
                             str(row.get("title") or "Intelligence update"), str(row.get("summary") or "Stored intelligence matched this rule."),
                             rule["severity"], str(row.get("source_url") or ""), "requires human review", fingerprint, now_iso()),
                        )
                        created += max(0, int(result.rowcount or 0))
                conn.execute("UPDATE customer_alert_rules SET last_evaluated_at=? WHERE alert_rule_id=?", (now_iso(), rule["alert_rule_id"]))
            if identity:
                _activity(conn, identity, "ALERTS_EVALUATED", f"Evaluated {evaluated} rules; created {created} alerts.")
        return {"rules_evaluated": evaluated, "alerts_created": created}
    finally:
        if owned:
            conn.close()


def alert_inbox(principal: dict[str, Any], include_dismissed: bool = False) -> list[dict[str, Any]]:
    identity = scope(principal)
    conn = db.connect()
    try:
        dismissed = "" if include_dismissed else " AND e.dismissed_at IS NULL"
        return [dict(row) for row in conn.execute(
            "SELECT e.*,r.name AS rule_name FROM customer_alert_events e JOIN customer_alert_rules r ON r.alert_rule_id=e.alert_rule_id "
            f"WHERE e.scope_key=?{dismissed} ORDER BY CASE e.severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END,e.detected_at DESC LIMIT 500",
            (identity["scope_key"],),
        ).fetchall()]
    finally:
        conn.close()


def mark_alert(principal: dict[str, Any], alert_event_id: str, action: str) -> None:
    identity = _require_write(principal)
    if action not in {"read", "dismiss"}:
        raise ValueError("Unsupported alert action.")
    column = "read_at" if action == "read" else "dismissed_at"
    conn = db.connect()
    try:
        with conn.transaction():
            result = conn.execute(f"UPDATE customer_alert_events SET {column}=? WHERE alert_event_id=? AND scope_key=?",
                                  (now_iso(), alert_event_id, identity["scope_key"]))
            if result.rowcount != 1:
                raise ValueError("Alert not found in this workspace.")
            _activity(conn, identity, f"ALERT_{action.upper()}", f"Marked an alert as {action}.")
    finally:
        conn.close()


def _externally_approved(conn, item: dict[str, Any]) -> bool:
    if item.get("record_type") != "opportunity":
        return False
    row = conn.execute(
        "SELECT external_use_approved FROM human_audit_versions WHERE stable_lead_id=? ORDER BY audit_version DESC LIMIT 1",
        (item.get("record_id"),),
    ).fetchone()
    return bool(row and row.get("external_use_approved"))


def export_saved_list(principal: dict[str, Any], saved_list_id: str, export_kind: str = "internal") -> tuple[bytes, dict[str, Any]]:
    if export_kind not in {"internal", "external"}:
        raise ValueError("Unsupported export type.")
    identity = _require_write(principal)
    conn = db.connect()
    try:
        permission = capabilities(principal, conn)
        if not permission["can_export"]:
            raise PermissionError(f"Exports are blocked by workspace policy: {permission['export_policy']}.")
        listing = conn.execute(
            "SELECT * FROM customer_saved_lists WHERE saved_list_id=? AND scope_key=? AND archived=0",
            (saved_list_id, identity["scope_key"]),
        ).fetchone()
        if not listing:
            raise PermissionError("The saved list is outside this workspace or archived.")
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM customer_saved_items WHERE saved_list_id=? ORDER BY added_at", (saved_list_id,)
        ).fetchall()]
        included = items if export_kind == "internal" else [item for item in items if _externally_approved(conn, item)]
        excluded = len(items) - len(included)
        output = io.StringIO()
        fields = ["record_type", "record_id", "record_label", "source_url", "evidence_status", "note", "added_at"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: item.get(key) for key in fields} for item in included)
        payload = output.getvalue().encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        export_id = _id("export")
        policy = f"{permission['export_policy']} · {export_kind}"
        with conn.transaction():
            conn.execute(
                "INSERT INTO customer_exports (export_id,scope_key,saved_list_id,export_kind,export_format,status,requested_by,requested_role,record_count,excluded_count,policy_snapshot,checksum,created_at,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (export_id, identity["scope_key"], saved_list_id, export_kind, "csv", "completed",
                 identity["actor"], identity["role"], len(included), excluded, policy, digest,
                 now_iso(), json.dumps({"list_name": listing.get("name")}, sort_keys=True)),
            )
            _activity(conn, identity, "SAVED_LIST_EXPORTED", f"Created {export_kind} export for {listing.get('name')}.",
                      {"export_id": export_id, "record_count": len(included), "excluded_count": excluded})
        return payload, {"export_id": export_id, "record_count": len(included), "excluded_count": excluded, "checksum": digest}
    finally:
        conn.close()


def workspace_snapshot(principal: dict[str, Any]) -> dict[str, Any]:
    identity = scope(principal)
    permission = capabilities(principal)
    conn = db.connect()
    try:
        lists = [dict(r) for r in conn.execute(
            "SELECT l.*,COUNT(i.saved_item_id) AS item_count FROM customer_saved_lists l LEFT JOIN customer_saved_items i ON i.saved_list_id=l.saved_list_id WHERE l.scope_key=? AND l.archived=0 GROUP BY l.saved_list_id ORDER BY l.updated_at DESC",
            (identity["scope_key"],),
        ).fetchall()]
        rules = [dict(r) for r in conn.execute("SELECT * FROM customer_alert_rules WHERE scope_key=? ORDER BY created_at DESC", (identity["scope_key"],)).fetchall()]
        alerts = [dict(r) for r in conn.execute("SELECT * FROM customer_alert_events WHERE scope_key=? AND dismissed_at IS NULL ORDER BY detected_at DESC LIMIT 500", (identity["scope_key"],)).fetchall()]
        exports = [dict(r) for r in conn.execute("SELECT * FROM customer_exports WHERE scope_key=? ORDER BY created_at DESC LIMIT 100", (identity["scope_key"],)).fetchall()]
        activity = [dict(r) for r in conn.execute("SELECT * FROM customer_activity_events WHERE scope_key=? ORDER BY created_at DESC LIMIT 100", (identity["scope_key"],)).fetchall()]
        return {
            "identity": identity, "capabilities": permission, "lists": lists, "rules": rules,
            "alerts": alerts, "exports": exports, "activity": activity,
            "metrics": {"lists": len(lists), "saved_items": sum(int(r.get("item_count") or 0) for r in lists),
                        "active_rules": sum(1 for r in rules if r.get("enabled")),
                        "unread_alerts": sum(1 for r in alerts if not r.get("read_at")), "exports": len(exports)},
        }
    finally:
        conn.close()
