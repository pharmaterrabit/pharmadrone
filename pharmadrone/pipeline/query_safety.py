"""Source-specific query safety helpers for Phase 3A.

These helpers are deterministic. They do not call APIs. They prepare safer,
shorter queries for web enrichment and preserve the original query in source
health logs so rejected API patterns can be audited in developer/debug mode.
"""
from __future__ import annotations

import re
from typing import Any


def normalise_query(text: Any) -> str:
    q = str(text or "").strip()
    q = q.replace("\n", " ")
    q = re.sub(r"\s+", " ", q)
    return q.strip()


def sanitize_tavily_query(query: str, *, max_chars: int = 180) -> str:
    """Remove search-engine syntax that Tavily may reject.

    Tavily is useful for web enrichment but can reject some `site:` patterns and
    long Boolean-heavy queries. This function keeps the meaning while stripping
    unsupported syntax. It is intentionally conservative and deterministic.
    """
    q = normalise_query(query)
    q = re.sub(r"\bsite:\s*", "", q, flags=re.I)
    q = q.replace('"', " ").replace("'", " ")
    q = re.sub(r"[(){}\[\]]", " ", q)
    q = re.sub(r"\b(OR|AND|NOT)\b", " ", q, flags=re.I)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:max_chars].strip()


def compact_terms(*values: Any, max_terms: int = 10, max_chars: int = 180) -> str:
    terms: list[str] = []
    for value in values:
        for token in re.split(r"[|;,/]+", str(value or "")):
            token = normalise_query(token)
            if not token:
                continue
            if token.lower() not in {t.lower() for t in terms}:
                terms.append(token)
            if len(terms) >= max_terms:
                break
        if len(terms) >= max_terms:
            break
    return normalise_query(" ".join(terms))[:max_chars]


def lead_web_enrichment_queries(lead: dict[str, Any], *, max_queries: int = 4) -> list[str]:
    """Build a small set of safe web-enrichment queries for one indexed lead.

    These queries enrich an existing lead. They do not discover new leads and do
    not imply that corroboration exists unless retrieved evidence supports it.
    """
    company = normalise_query(lead.get("company"))
    product = normalise_query(lead.get("product") or lead.get("molecule"))
    molecule = normalise_query(lead.get("molecule"))
    problem = normalise_query(lead.get("problem_category"))
    source_id = normalise_query(lead.get("source_id"))

    queries: list[str] = []
    if source_id and source_id != "unknown-source":
        queries.append(compact_terms(source_id, company, product, "recall", max_chars=150))
    if company or product:
        queries.append(compact_terms(company, product, problem, "official statement recall", max_chars=170))
    if company:
        queries.append(compact_terms(company, "FDA warning letter inspection finding", problem, max_chars=170))
    if molecule or product:
        queries.append(compact_terms(molecule or product, problem, "PubMed literature", max_chars=170))

    out: list[str] = []
    for q in queries:
        q = sanitize_tavily_query(q)
        if q and q.lower() not in {x.lower() for x in out}:
            out.append(q)
        if len(out) >= max_queries:
            break
    return out

# --- Phase 3B source-specific enrichment templates ------------------------

def fda_official_followup_queries(lead: dict[str, Any], *, max_queries: int = 2) -> list[str]:
    company = normalise_query(lead.get("company"))
    product = normalise_query(lead.get("product") or lead.get("molecule"))
    molecule = normalise_query(lead.get("molecule"))
    problem = normalise_query(lead.get("problem_category") or lead.get("problem_signal"))
    source_id = normalise_query(lead.get("source_id"))
    queries: list[str] = []
    if source_id and source_id != "unknown-source":
        queries.append(compact_terms("FDA warning letter inspection", source_id, company, product, max_chars=170))
    if company:
        queries.append(compact_terms("FDA warning letter inspection quality", company, product or molecule, problem, max_chars=170))
    if product or molecule:
        queries.append(compact_terms("FDA official recall follow-up", product or molecule, problem, max_chars=170))
    out: list[str] = []
    for q in queries:
        q = sanitize_tavily_query(q, max_chars=170)
        if q and q.lower() not in {x.lower() for x in out}:
            out.append(q)
        if len(out) >= max_queries:
            break
    return out


def label_context_query(lead: dict[str, Any]) -> str:
    molecule = normalise_query(lead.get("molecule") or lead.get("generic_name"))
    product = normalise_query(lead.get("product"))
    # Prefer the shorter generic/molecule if available; otherwise use product.
    q = molecule or product
    if not q:
        return ""
    # Avoid huge NDC/package strings in label queries.
    q = re.sub(r"\b\d{4,}(?:-\d+)*\b", " ", q)
    q = re.sub(r"\b(capsules?|tablets?|injection|solution|bottle|carton|package|ndc)\b", " ", q, flags=re.I)
    return normalise_query(q)[:120]


def trial_context_query(lead: dict[str, Any]) -> str:
    source_id = normalise_query(lead.get("source_id"))
    if re.search(r"\bNCT\d{8}\b", source_id, re.I):
        return source_id.upper()
    product = normalise_query(lead.get("product") or lead.get("molecule"))
    company = normalise_query(lead.get("company"))
    return compact_terms(product, company, max_chars=140)


def literature_context_query(lead: dict[str, Any]) -> str:
    molecule = normalise_query(lead.get("molecule") or lead.get("generic_name"))
    product = normalise_query(lead.get("product"))
    problem = normalise_query(lead.get("problem_category") or lead.get("problem_signal"))
    term = molecule or product
    if not term:
        return ""
    # Keep query narrow and scientific; literature is context only.
    if not problem:
        problem = "formulation pharmaceutical"
    return compact_terms(term, problem, "formulation", max_chars=160)
