"""Checkpoint 8.4 deterministic regulatory-event presentation helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any


REGULATORS = ("FDA", "EMA", "MHRA")
EVENT_FAMILIES = (
    "Recall / quality defect",
    "Medicine shortage",
    "Safety communication",
    "Safety review / referral",
    "Post-authorisation withdrawal",
    "Other regulatory event",
)


def regulator(source_type: Any) -> str:
    source = str(source_type or "").upper()
    return next((name for name in REGULATORS if source.startswith(name)), "Other")


def event_family(source_type: Any, problem: Any = "") -> str:
    text = f"{source_type or ''} {problem or ''}".lower()
    if "recall" in text or any(term in text for term in ("quality defect", "impurity", "contamination")):
        return "Recall / quality defect"
    if "shortage" in text or "availability" in text:
        return "Medicine shortage"
    if "communication" in text or "dhpc" in text:
        return "Safety communication"
    if any(term in text for term in ("referral", "safety outcome", "safety assessment", "psusa")):
        return "Safety review / referral"
    if "withdraw" in text or "post-authorisation" in text:
        return "Post-authorisation withdrawal"
    return "Other regulatory event"


def evidence_urls(row: dict[str, Any]) -> list[str]:
    values = row.get("evidence_links")
    if not isinstance(values, list):
        try:
            values = json.loads(row.get("evidence_links_json") or "[]")
        except (TypeError, ValueError):
            values = []
    urls: list[str] = []
    for value in values:
        candidate = value.get("url") if isinstance(value, dict) else value
        candidate = str(candidate or "").strip()
        if candidate.startswith(("https://", "http://")) and candidate not in urls:
            urls.append(candidate)
    return urls


def freshness(last_checked_at: Any, now: datetime | None = None) -> str:
    if not last_checked_at:
        return "Review date missing"
    try:
        checked = datetime.fromisoformat(str(last_checked_at).replace("Z", "+00:00"))
        checked = checked if checked.tzinfo else checked.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return "Review date missing"
    days = ((now or datetime.now(timezone.utc)) - checked.astimezone(timezone.utc)).days
    if days <= 7:
        return "Current"
    if days <= 30:
        return "Review due"
    return "Stale"


def action_route(row: dict[str, Any]) -> dict[str, str]:
    family = event_family(row.get("source_type"), row.get("problem_category"))
    if family == "Recall / quality defect":
        function = "Quality / CMC"
        action = "Verify affected product, responsible organisation, recall scope and current status."
    elif family == "Medicine shortage":
        function = "Supply Chain / Procurement"
        action = "Verify shortage status, affected market, stated reason and supply responsibility."
    else:
        function = "Pharmacovigilance / Regulatory Affairs"
        action = "Verify the regulatory outcome, affected medicine and responsible authorisation holder."
    return {
        "event_family": family,
        "regulator": regulator(row.get("source_type")),
        "responsible_function": function,
        "recommended_review": action,
        "commercial_boundary": "A public regulatory event does not prove commercial need, budget or buying intent.",
    }
