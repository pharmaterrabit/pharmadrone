"""Merge duplicate opportunity candidates and pool their evidence."""
from __future__ import annotations
import re


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _event_identity(c: dict) -> str | None:
    """Prefer official event identity so separate events never collapse."""
    for e in c.get("evidence", []) or []:
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        stype = str(e.get("source_type") or "").lower()
        checks = (
            ("recall", rf.get("recall_number") or ent.get("recall_number")),
            ("trial", ent.get("trial_id") or ent.get("nct_id")),
            ("shortage", ent.get("package_ndc") or ent.get("shortage_key")),
            ("event", ent.get("source_event_id")),
        )
        for prefix, value in checks:
            if value:
                return f"{prefix}|{_norm(value)}"
        if stype in {"recall", "trial", "shortage", "enforcement"} and e.get("record_id"):
            return f"{stype}|{_norm(e.get('record_id'))}"
    if c.get("event_source_id"):
        return f"event|{_norm(c.get('event_source_id'))}"
    return None


def _key(c: dict) -> str:
    event_key = _event_identity(c)
    if event_key:
        return event_key
    for f in ("dev_code", "brand_name", "generic_name", "product"):
        if c.get(f):
            return f"{_norm(c.get('company'))}|{_norm(c.get(f))}"
    return _norm(c.get("company")) or _norm(c.get("product")) or str(id(c))


def dedup(candidates: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for c in candidates:
        k = _key(c)
        if k not in merged:
            merged[k] = c
            merged[k].setdefault("evidence", [])
            continue
        base = merged[k]
        # Pool evidence, de-duplicating by URL/record_id
        seen = {(e.get("url"), e.get("record_id")) for e in base["evidence"]}
        for e in c.get("evidence", []):
            sig = (e.get("url"), e.get("record_id"))
            if sig not in seen:
                base["evidence"].append(e)
                seen.add(sig)
        # Fill any empty fields from the duplicate
        for f, v in c.items():
            if f != "evidence" and not base.get(f) and v:
                base[f] = v
    return list(merged.values())
