"""Write the 12-section report for each accepted opportunity.

Flagship reports are longer and more detailed; scouting memos are concise. Both
use the configured LLM provider (see llm.py) and enforce cautious, non-accusatory
language and the mandatory 'Red Flags' and 'Why This May Be Wrong' sections.
"""
from __future__ import annotations
from .. import llm
from . import failure_signal, bd_rules, root_cause

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


def _recall_record(opp: dict) -> dict | None:
    """Return the structured openFDA recall_fields from the strongest recall
    evidence item, if any (entities.recall_fields set by the enforcement
    connector). None if this candidate isn't recall-backed."""
    for e in opp.get("evidence", []):
        ent = e.get("entities") or {}
        if ent.get("recall_fields"):
            return ent["recall_fields"]
    return None


def _fmt(v) -> str:
    return v if v else "not stated"


def _short_product_name(opp: dict) -> str:
    """Short product label for the report title (issue 6): keep the molecule +
    dosage form, drop the long ", 100 Capsules per bottle, Rx only" tail. The
    full description stays in the recall details table."""
    prod = (opp.get("product") or "asset").strip()
    if not prod or prod == "asset":
        return "asset"
    # Take the part before the first comma (usually "Nitrofurantoin Capsules USP"
    # or "Nitrofurantoin Capsules"), and cap length as a safety net.
    head = prod.split(",")[0].strip()
    # Keep an immediately-following dosage-form/strength token group if short.
    if len(head) < 12 and "," in prod:
        # e.g. head was just a molecule; add the next chunk (dosage form)
        head = ", ".join(p.strip() for p in prod.split(",")[:2])
    return (head[:70].rsplit(" ", 1)[0] + "…") if len(head) > 70 else head


def _evidence_table(opp: dict) -> str:
    """Evidence table that shows the actual recall reason (req 3), not just the
    title. 'Supports' carries the recall reason / event; 'Does not prove' is a
    cautious disclaimer."""
    rows = []
    for e in opp.get("evidence", []):
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        reason = (rf.get("reason_for_recall") or ent.get("event_reason")
                  or e.get("supports") or "")
        reason = (reason[:160] + "…") if len(reason) > 160 else reason
        supports = reason or "see source"
        does_not = ("confirms an FDA recall event; does not establish root cause "
                    "beyond the stated reason" if rf else
                    "does not prove causation or scope — validate")
        title = (e.get("title") or "")[:90]
        url = e.get("url") or ""
        rows.append(f"| {e.get('source_type','')} | {e.get('language','en')} | "
                    f"{title} | {e.get('record_id','')} | "
                    f"[{'link' if url else '—'}]({url}) | {supports} | {does_not} |")
    body = "\n".join(rows) or "| — | — | (no evidence) | — | — | — | — |"
    return ("| Source Type | Source Language | Title | ID | Link | Supports | "
            "Does not prove |\n|---|---|---|---|---|---|---|\n" + body)


def _deterministic_body(opp: dict) -> str:
    """No-LLM report body — a useful rule-based BD screening memo (not a
    placeholder). For recall-backed candidates it renders the full openFDA field
    set and rule-based BD interpretation; strict evidence discipline throughout.
    """
    problem = opp.get("problem_category") or opp.get("problem_signal")
    rf = _recall_record(opp)
    company = opp.get("company") or (rf.get("recalling_firm") if rf else None) or "Unknown company"
    product = opp.get("product") or (rf.get("product_description") if rf else None) or "product"

    # Will the richer Root-Cause & Solution-Fit section render after this body?
    # (It renders for confirmed failure events.) If so, defer the BD-analysis
    # sections to it instead of repeating weaker generic text here (issue 4).
    rc_will_render = bool(opp.get("failure")
                          and failure_signal._event_structurally_confirmed(opp))

    interp = bd_rules.interpretation(problem)
    partners = bd_rules.partners(problem)
    rescue = bd_rules.rescue_steps(problem)
    outreach = bd_rules.outreach_angle(problem) if rf else (
        "Do not initiate outreach from this candidate alone — validate the signal "
        "first, then assess whether a formulation/CMC/analytical opportunity exists.")

    # Confirmed-fact block (FDA facts only) vs interpretation — kept separate.
    if rf:
        confirmed = (
            f"**Confirmed (FDA recall record — facts only):**\n"
            f"- Recalling firm: {_fmt(rf.get('recalling_firm'))}\n"
            f"- Product: {_fmt(rf.get('product_description'))}\n"
            f"- Reason for recall: {_fmt(rf.get('reason_for_recall'))}\n"
            f"- Recall number: {_fmt(rf.get('recall_number'))}\n"
            f"- Classification: {_fmt(rf.get('classification'))}\n"
            f"- Status: {_fmt(rf.get('status'))}\n"
            f"- Recall initiation date: {_fmt(rf.get('recall_initiation_date'))}\n"
            f"- FDA report date: {_fmt(rf.get('report_date'))}")
    else:
        confirmed = (f"**Confirmed:** {opp.get('confirmed_fact','A public-source signal '
                     'was found for this target.')}")

    who = "\n".join(f"- {r}" for r in bd_rules.CONTACT_ROLES)
    partner_lines = "\n".join(f"- {p}" for p in partners)
    rescue_lines = "\n".join(f"- {s}" for s in rescue)

    if rc_will_render:
        # Defer Opportunity Fit / Who to Contact / Outreach / Partners / Rescue to
        # the Root-Cause & Solution-Fit Intelligence section below (issue 4).
        analysis_block = (
            "## BD Analysis\n"
            "Root-cause hypotheses (evidence-graded), solution-fit mapping, safe "
            "outreach wording, contact roles, confidence/readiness, and the "
            "validation checklist are in the **Root-Cause & Solution-Fit "
            "Intelligence** section below — they are specific to this recall's "
            "confirmed reason, not generic problem-category text.")
    else:
        analysis_block = f"""## Opportunity Fit
The recall reason {interp}. This is **PharmaDrone interpretation, not an FDA
finding** — the FDA record confirms only the recall and its stated reason, not
the underlying root cause. Requires validation before any commercial conclusion.

## Who to Contact
Buyer roles to consider (validate relevance to this specific firm/product first):
{who}

## Outreach Angle
{outreach}

## Possible Partner Categories
Opportunity types that *may* fit this signal (rule-based; validate before use):
{partner_lines}

## Possible Rescue Strategy
Rule-based validation/rescue steps for a {_fmt(problem)} signal:
{rescue_lines}"""

    return f"""## Quick Summary
Deterministic BD screening memo (generated without LLM synthesis — the LLM was
unavailable or rate-limited for this run). {('This is a **confirmed FDA recall** '
'signal.' if rf else 'Signal status: ' + str(opp.get('signal_status','needs_verification')) + '.')}
Target: **{company} — {product}**. Problem signal: **{_fmt(problem)}**.

{confirmed}

## Evidence Table
{_evidence_table(opp)}

{analysis_block}

## Confidence Score
{opp.get('confidence','low')} — signal status: **{opp.get('signal_status','needs_verification')}**.
{'Confirmed FDA recall event; commercial relevance still requires validation.' if rf else ''}

## Red Flags
{chr(10).join('- ' + r for r in (opp.get('red_flags') or [
    'Root cause beyond the stated recall reason is unknown.',
    'Scope (one lot vs recurring) not established from this record alone.',
    'Not yet validated — do not treat as an outreach-ready lead.']))}

## Why This May Be Wrong
The recall reason is a confirmed FDA fact, but the BD interpretation, partner
categories, and rescue steps are rule-based inferences from the problem
category — they may not reflect the firm's actual situation. The issue could be
an isolated lot, a resolved supplier problem, or already remediated. Verify the
recall record, its scope, and the firm's current status before any BD action.
"""


def write_report(opp: dict, cost, report_type: str = "memo") -> str:
    is_provisional = bool(opp.get("provisional"))
    llm_disabled = getattr(llm, "BREAKER", None) is not None and llm.BREAKER.tripped
    if is_provisional or llm_disabled:
        body = _deterministic_body(opp)
        if llm_disabled and not is_provisional:
            body = ("> ℹ LLM was rate-limited this run (429); this report uses the "
                    "deterministic evidence view.\n\n" + body)
    else:
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
            # Even on total LLM failure, fall back to the deterministic body so
            # the report is never empty — just clearly marked as degraded.
            body = (f"> ⚠ LLM report writing failed ({e}). Showing a "
                    "deterministic fallback view instead.\n\n" + _deterministic_body(opp))

    banner = ""
    if is_provisional:
        banner = ("\n> ⚠ **PROVISIONAL / LOW-EVIDENCE CANDIDATE.** Generated by "
                  "deterministic evidence clustering (candidate-discovery + "
                  "fallback step), not full LLM synthesis. Signal status: "
                  f"**{opp.get('signal_status','needs_verification')}**. "
                  "Verify every field before any BD action.\n")

    header = (f"# {opp.get('company') or 'Unknown company'} — "
              f"{_short_product_name(opp)}\n"
              f"*{report_type.upper()} · {opp.get('region') or 'region not stated'} · "
              f"Grade {opp.get('grade','?')} ({opp.get('score','?')}/100) · "
              f"Confidence {opp.get('confidence','?')}*\n"
              f"{banner}\n"
              "> Automated public-source scan. Possible opportunity signal only — "
              "requires human validation before commercial decision-making.\n\n")
    # Embed the Failure Signal Intelligence section (deterministic, evidence-based).
    failure_section = failure_signal.render_failure_section(opp)
    # Embed the Root-Cause & Solution-Fit Intelligence layer (deterministic).
    # Only when there's a confirmed failure event to investigate — the renderer
    # itself returns "" when there's no problem category, so this is safe.
    root_cause_section = ""
    if opp.get("failure") and failure_signal._event_structurally_confirmed(opp):
        root_cause_section = root_cause.render_root_cause_section(opp)
    return header + body + failure_section + root_cause_section
