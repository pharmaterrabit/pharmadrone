"""Write the 12-section report for each accepted opportunity.

Flagship reports are longer and more detailed; scouting memos are concise. Both
use the configured LLM provider (see llm.py) and enforce cautious, non-accusatory
language and the mandatory 'Red Flags' and 'Why This May Be Wrong' sections.
"""
from __future__ import annotations
from .. import llm

SECTIONS = [
    "Quick Summary", "Scientific / RWE View", "Business & Commercial View",
    "Opportunity Fit", "Evidence Table", "Patent / IP Signal",
    "Company / Ownership Note", "Who to Contact", "Outreach Angle",
    "Confidence Score", "Red Flags", "Why This May Be Wrong",
]

BASE_RULES = """Writing rules (mandatory):
- Ground every factual claim in the supplied evidence. No invented contacts,
  revenues, deals, market sizes, or patent conclusions.
- Separate confirmed facts from hypotheses. Use cautious language:
  "public evidence suggests", "may indicate", "possible BD angle".
- Patent section = PUBLIC PATENT SIGNAL ONLY. Never assert freedom-to-operate,
  validity, or infringement.
- 'Who to Contact' = buyer ROLES only (e.g. Head of Drug Product Development),
  never named individuals unless a name appears in the evidence.
- Always include Red Flags and a candid 'Why This May Be Wrong'.
- Output GitHub-flavoured Markdown. Build the Evidence Table from the evidence
  list with these exact columns:
  Source Type | Source Language | Title | ID | Link | Supports | Does not prove.
  Put each URL as a real Markdown link. Every report MUST contain: the evidence
  table (with links, source type, source language), a Confidence Score, Red Flags,
  a 'Why This May Be Wrong' section, and an Outreach Angle.
"""

FLAGSHIP_PROMPT = """Write a FLAGSHIP pharma BD opportunity case study.
{rules}
Use all 12 sections as ## headings, in this order:
{sections}

Opportunity data:
{opp}

Evidence:
{evidence}
"""

MEMO_PROMPT = """Write a CONCISE pharma BD scouting memo (~350-500 words).
{rules}
Include these sections as ## headings: Quick Summary, Opportunity Fit,
Evidence Table, Who to Contact (roles), Outreach Angle, Confidence Score,
Red Flags, Why This May Be Wrong.

Opportunity data:
{opp}

Evidence:
{evidence}
"""


def _opp_block(opp: dict) -> str:
    fields = ["company", "parent_company", "product", "generic_name", "brand_name",
              "dev_code", "indication", "therapeutic_area", "region", "stage",
              "problem_signal", "confirmed_fact", "inference", "bd_hypothesis",
              "validation_step", "score", "grade", "confidence"]
    return "\n".join(f"- {f}: {opp.get(f)}" for f in fields if opp.get(f) is not None)


def _evidence_block(opp: dict) -> str:
    return "\n".join(
        f"- [{e.get('source_type')}] {e.get('title','')} | id={e.get('record_id','')} "
        f"| {e.get('url','')} | lang={e.get('language','en')} "
        f"| supports: {e.get('supports','')} | does not prove: {e.get('does_not_prove','')}"
        for e in opp.get("evidence", [])
    ) or "No evidence."


def write_report(opp: dict, cost, report_type: str = "memo") -> str:
    prompt_tpl = FLAGSHIP_PROMPT if report_type == "flagship" else MEMO_PROMPT
    prompt = prompt_tpl.format(
        rules=BASE_RULES,
        sections="\n".join(f"{i+1}. {s}" for i, s in enumerate(SECTIONS)),
        opp=_opp_block(opp),
        evidence=_evidence_block(opp),
    )
    try:
        body = llm.complete(prompt, cost,
                            temperature=0.3 if report_type == "flagship" else 0.2)
    except Exception as e:
        body = f"> Report generation failed: {e}\n"
    header = (f"# {opp.get('company','Unknown')} — {opp.get('product','asset')}\n"
              f"*{report_type.upper()} · {opp.get('region','')} · "
              f"Grade {opp.get('grade','?')} ({opp.get('score','?')}/100) · "
              f"Confidence {opp.get('confidence','?')}*\n\n"
              "> Automated public-source scan. Possible opportunity signal only — "
              "requires human validation before commercial decision-making.\n\n")
    return header + body
