"""Merge duplicate opportunity candidates and pool their evidence."""
from __future__ import annotations
import re


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _key(c: dict) -> str:
    # Prefer the most specific stable identifier available.
    for f in ("dev_code", "brand_name", "generic_name", "product"):
        if c.get(f):
            return f"{_norm(c.get('company'))}|{_norm(c.get(f))}"
    return _norm(c.get("company")) or _norm(c.get("product")) or id(c)


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
