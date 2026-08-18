"""Workspace-scoped persistence for the standalone PharmaDrone AI app."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any
from uuid import uuid4

from pharmadrone_ai import security


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _workspace_slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:40] or "workspace"
    return f"{base}-{uuid4().hex[:8]}"


def register(conn, email: str, password: str, display_name: str, workspace_name: str) -> dict[str, Any]:
    normalized = security.normalize_email(email)
    security.validate_password(password)
    name = _text(display_name, 120)
    workspace = _text(workspace_name, 160)
    if not name or not workspace:
        raise ValueError("Display name and workspace name are required.")
    if conn.execute(
        "SELECT user_id FROM saas_users WHERE normalized_email=? LIMIT 1", (normalized,),
    ).fetchone():
        raise ValueError("An account already exists for this email address.")
    user_id = f"user-{uuid4().hex}"
    workspace_id = f"workspace-{uuid4().hex}"
    timestamp = _now()
    with conn.transaction():
        conn.execute(
            """INSERT INTO saas_users
            (user_id,email,normalized_email,display_name,password_hash,password_algorithm,
             active,created_at)
            VALUES (?,?,?,?,?,?,1,?)""",
            (
                user_id, normalized, normalized, name,
                security.hash_password(password), security.PASSWORD_ALGORITHM, timestamp,
            ),
        )
        conn.execute(
            """INSERT INTO saas_workspaces
            (workspace_id,workspace_name,workspace_slug,plan_code,active,created_at)
            VALUES (?,?,?,'free-trial',1,?)""",
            (workspace_id, workspace, _workspace_slug(workspace), timestamp),
        )
        conn.execute(
            """INSERT INTO saas_workspace_memberships
            (membership_id,workspace_id,user_id,membership_role,active,created_at)
            VALUES (?,?,?,'owner',1,?)""",
            (f"membership-{uuid4().hex}", workspace_id, user_id, timestamp),
        )
        conn.execute(
            """INSERT INTO saas_subscriptions
            (subscription_id,workspace_id,provider,plan_code,subscription_status,
             created_at,updated_at)
            VALUES (?,?,'not-configured','free-trial','development',?,?)""",
            (f"subscription-{uuid4().hex}", workspace_id, timestamp, timestamp),
        )
    return current_user(conn, user_id, workspace_id)


def authenticate(conn, email: str, password: str) -> dict[str, Any]:
    normalized = security.normalize_email(email)
    row = conn.execute(
        """SELECT u.user_id,u.password_hash,m.workspace_id
        FROM saas_users u
        JOIN saas_workspace_memberships m ON m.user_id=u.user_id
        WHERE u.normalized_email=? AND u.active=1 AND m.active=1
        ORDER BY CASE WHEN m.membership_role='owner' THEN 0 ELSE 1 END
        LIMIT 1""",
        (normalized,),
    ).fetchone()
    if not row or not security.verify_password(password, row["password_hash"]):
        raise security.AuthenticationError("Email or password is incorrect.")
    conn.execute(
        "UPDATE saas_users SET last_login_at=? WHERE user_id=?",
        (_now(), row["user_id"]),
    )
    conn.commit()
    return current_user(conn, row["user_id"], row["workspace_id"])


def current_user(conn, user_id: str, workspace_id: str) -> dict[str, Any]:
    row = conn.execute(
        """SELECT u.user_id,u.email,u.display_name,w.workspace_id,w.workspace_name,
        w.plan_code,m.membership_role
        FROM saas_users u
        JOIN saas_workspace_memberships m ON m.user_id=u.user_id
        JOIN saas_workspaces w ON w.workspace_id=m.workspace_id
        WHERE u.user_id=? AND w.workspace_id=? AND u.active=1 AND w.active=1
          AND m.active=1 LIMIT 1""",
        (user_id, workspace_id),
    ).fetchone()
    if not row:
        raise security.AuthenticationError("Authenticated workspace access is unavailable.")
    return dict(row)


def create_conversation(conn, user_id: str, workspace_id: str, title: str) -> dict[str, Any]:
    current_user(conn, user_id, workspace_id)
    conversation_id = f"conversation-{uuid4().hex}"
    timestamp = _now()
    conn.execute(
        """INSERT INTO saas_conversations
        (conversation_id,workspace_id,user_id,title,created_at,updated_at)
        VALUES (?,?,?,?,?,?)""",
        (conversation_id, workspace_id, user_id, _text(title, 160) or "New conversation", timestamp, timestamp),
    )
    conn.commit()
    return {"conversation_id": conversation_id, "title": _text(title, 160) or "New conversation", "created_at": timestamp}


def list_conversations(conn, user_id: str, workspace_id: str, limit: int = 30) -> list[dict[str, Any]]:
    current_user(conn, user_id, workspace_id)
    bounded = max(1, min(int(limit), 50))
    return [
        dict(row)
        for row in conn.execute(
            """SELECT conversation_id,title,created_at,updated_at
            FROM saas_conversations WHERE user_id=? AND workspace_id=?
            ORDER BY updated_at DESC LIMIT ?""",
            (user_id, workspace_id, bounded),
        ).fetchall()
    ]


def save_message(
    conn,
    user_id: str,
    workspace_id: str,
    conversation_id: str,
    role: str,
    content: dict[str, Any],
) -> dict[str, Any]:
    if role not in {"user", "assistant"}:
        raise ValueError("Message role is invalid.")
    owner = conn.execute(
        """SELECT conversation_id FROM saas_conversations
        WHERE conversation_id=? AND user_id=? AND workspace_id=? LIMIT 1""",
        (conversation_id, user_id, workspace_id),
    ).fetchone()
    if not owner:
        raise PermissionError("Conversation does not belong to the authenticated workspace.")
    encoded = json.dumps(content, ensure_ascii=False)
    if len(encoded) > 250_000:
        raise ValueError("Message payload exceeds the bounded storage limit.")
    message_id = f"message-{uuid4().hex}"
    timestamp = _now()
    with conn.transaction():
        conn.execute(
            """INSERT INTO saas_messages
            (message_id,conversation_id,workspace_id,user_id,message_role,content_json,created_at)
            VALUES (?,?,?,?,?,?,?)""",
            (message_id, conversation_id, workspace_id, user_id, role, encoded, timestamp),
        )
        conn.execute(
            "UPDATE saas_conversations SET updated_at=? WHERE conversation_id=?",
            (timestamp, conversation_id),
        )
    return {"message_id": message_id, "created_at": timestamp}


def list_messages(conn, user_id: str, workspace_id: str, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
    bounded = max(1, min(int(limit), 100))
    owner = conn.execute(
        """SELECT conversation_id FROM saas_conversations
        WHERE conversation_id=? AND user_id=? AND workspace_id=? LIMIT 1""",
        (conversation_id, user_id, workspace_id),
    ).fetchone()
    if not owner:
        raise PermissionError("Conversation does not belong to the authenticated workspace.")
    rows = conn.execute(
        """SELECT message_id,message_role,content_json,created_at
        FROM saas_messages WHERE conversation_id=? AND user_id=? AND workspace_id=?
        ORDER BY created_at ASC LIMIT ?""",
        (conversation_id, user_id, workspace_id, bounded),
    ).fetchall()
    return [
        {
            "message_id": row["message_id"],
            "role": row["message_role"],
            "content": json.loads(row["content_json"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _decode_saved(rows, json_column: str) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        value = json.loads(row[json_column])
        value["saved_id"] = row[0]
        value["saved_at"] = row["created_at"]
        output.append(value)
    return output


def list_saved_leads(conn, user_id: str, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
    current_user(conn, user_id, workspace_id)
    rows = conn.execute(
        """SELECT saved_lead_id,lead_json,created_at FROM saas_saved_leads
        WHERE user_id=? AND workspace_id=? ORDER BY created_at DESC LIMIT ?""",
        (user_id, workspace_id, max(1, min(int(limit), 100))),
    ).fetchall()
    return _decode_saved(rows, "lead_json")


def list_saved_reports(conn, user_id: str, workspace_id: str, limit: int = 50) -> list[dict[str, Any]]:
    current_user(conn, user_id, workspace_id)
    rows = conn.execute(
        """SELECT saved_report_id,report_json,created_at FROM saas_saved_reports
        WHERE user_id=? AND workspace_id=? ORDER BY created_at DESC LIMIT ?""",
        (user_id, workspace_id, max(1, min(int(limit), 100))),
    ).fetchall()
    return _decode_saved(rows, "report_json")


def delete_saved(conn, table: str, id_column: str, saved_id: str, user_id: str, workspace_id: str) -> bool:
    allowed = {
        ("saas_saved_leads", "saved_lead_id"),
        ("saas_saved_reports", "saved_report_id"),
    }
    if (table, id_column) not in allowed:
        raise ValueError("Saved record type is invalid.")
    result = conn.execute(
        f"DELETE FROM {table} WHERE {id_column}=? AND user_id=? AND workspace_id=?",
        (saved_id, user_id, workspace_id),
    )
    conn.commit()
    return result.rowcount == 1


def record_usage(conn, user_id: str, workspace_id: str, event_type: str, metadata: dict[str, Any] | None = None) -> None:
    if event_type not in {"lead-generation", "pitch-report", "chat-message", "export"}:
        raise ValueError("Usage event type is invalid.")
    current_user(conn, user_id, workspace_id)
    conn.execute(
        """INSERT INTO saas_usage_events
        (usage_event_id,workspace_id,user_id,event_type,quantity,event_metadata_json,created_at)
        VALUES (?,?,?,?,1,?,?)""",
        (
            f"usage-{uuid4().hex}", workspace_id, user_id, event_type,
            json.dumps(metadata or {}, ensure_ascii=False), _now(),
        ),
    )
    conn.commit()


def billing_status(conn, user_id: str, workspace_id: str) -> dict[str, Any]:
    principal = current_user(conn, user_id, workspace_id)
    counts = {
        row["event_type"]: int(row["total"] or 0)
        for row in conn.execute(
            """SELECT event_type,SUM(quantity) AS total FROM saas_usage_events
            WHERE user_id=? AND workspace_id=? GROUP BY event_type LIMIT 10""",
            (user_id, workspace_id),
        ).fetchall()
    }
    subscription = conn.execute(
        """SELECT provider,plan_code,subscription_status,current_period_end
        FROM saas_subscriptions WHERE workspace_id=? LIMIT 1""",
        (workspace_id,),
    ).fetchone()
    return {
        "plan": principal["plan_code"],
        "subscription": dict(subscription or {}),
        "usage": {
            "lead_generation_count": counts.get("lead-generation", 0),
            "pitch_report_count": counts.get("pitch-report", 0),
            "chat_message_count": counts.get("chat-message", 0),
            "export_count": counts.get("export", 0),
        },
        "stripe_configured": False,
        "billing_status": "billing-ready abstraction; Stripe checkout is not implemented",
    }
