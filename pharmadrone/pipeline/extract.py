"""Extract structured opportunity candidates from raw evidence, then attach
the evidence items that support each one. Enforces the skill's evidence
discipline: confirmed fact vs inference vs BD hypothesis, and 'what the source
does not prove'.
"""
from __future__ import annotations
import json
from .. import llm
from . import discover

EXTRACT_PROMPT = """You are a pharma BD intelligence analyst. From the evidence
snippets below, extract distinct COMPANY + PRODUCT/ASSET opportunities that may
be relevant to a formulation / drug-delivery / CDMO seller (poorly soluble small
molecules, bioavailability, food effect, dose burden, reformulation).

STRICT RULES:
- Only use facts present in the snippets. Do NOT invent companies, contacts,
  revenues, deals, market size, or patent conclusions.
- If a field is not stated in the evidence, use null.
- Exclude biologics, vaccines, diagnostics, devices, and purely academic
  mechanisms with no company link.
- Separate confirmed fact from inference from hypothesis.

For each opportunity return an object with keys:
  company, parent_company, product, generic_name, brand_name, dev_code,
  indication, therapeutic_area, region, stage, problem_signal,
  confirmed_fact, inference, bd_hypothesis, validation_step,
  what_source_does_not_prove,
  evidence_ids  (list of the integer snippet ids that support THIS opportunity)

Return a JSON list. Snippets:
{snippets}
"""


def _format_snippets(evidence: list[dict], limit: int = 40) -> str:
    lines = []
    for idx, e in enumerate(evidence[:limit]):
        lines.append(
            f"[{idx}] ({e['source_type']}/{e['source_name']}) "
            f"{e.get('title','')} :: {e.get('raw_text','')[:600]} "
            f"<url:{e.get('url','')}>"
        )
    return "\n".join(lines)


def extract(evidence: list[dict], cost, batch_size: int = 40) -> tuple[list[dict], dict]:
    """Returns (candidates, debug). debug reports batch outcomes so a fully
    failing LLM is visible instead of silently producing zero candidates."""
    candidates = []
    debug = {"batches_total": 0, "batches_ok": 0, "batches_failed": 0, "errors": []}
    for start in range(0, len(evidence), batch_size):
        batch = evidence[start:start + batch_size]
        debug["batches_total"] += 1
        prompt = EXTRACT_PROMPT.format(snippets=_format_snippets(batch))
        try:
            items = llm.complete_json(prompt, cost)
        except Exception as e:
            debug["batches_failed"] += 1
            debug["errors"].append(f"opportunity extraction batch {start}: {e}")
            continue
        if not isinstance(items, list):
            debug["batches_failed"] += 1
            debug["errors"].append(
                f"opportunity extraction batch {start}: LLM returned non-list JSON")
            continue
        debug["batches_ok"] += 1
        for it in items:
            # Quality gate: reject candidates whose only "entity" is a generic /
            # blacklisted scientific term (prodrug, treatment, review, etc.).
            company = it.get("company")
            product = it.get("product")
            if discover.is_blacklisted_target(company):
                company = None
            if discover.is_blacklisted_target(product):
                product = None
            if not company and not product and not it.get("dev_code"):
                debug.setdefault("rejected_generic", 0)
                debug["rejected_generic"] += 1
                continue
            it["company"], it["product"] = company, product
            ev_ids = it.get("evidence_ids") or []
            attached = []
            for i in ev_ids:
                if isinstance(i, int) and 0 <= i < len(batch):
                    src = batch[i]
                    attached.append({
                        "source_type": src["source_type"],
                        "source_category": src.get("source_category"),
                        "source_name": src["source_name"],
                        "record_id": src["record_id"],
                        "title": src["title"],
                        "url": src["url"],
                        "language": src["language"],
                        "english_summary": src["raw_text"][:400],
                        "date_accessed": src["date_accessed"],
                        "supports": it.get("confirmed_fact"),
                        "does_not_prove": it.get("what_source_does_not_prove"),
                    })
            it["evidence"] = discover.dedup_evidence(attached)
            it["discovery_method"] = "llm-extraction"
            candidates.append(it)
    return candidates, debug
