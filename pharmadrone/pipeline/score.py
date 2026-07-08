"""100-point Opportunity Score.

Aligned with the PharmaDrone spec's scoring dimensions (not the skill's /30).
Positive dimensions sum to 100; red-flag and duplication penalties subtract.

    Scientific evidence strength   /20
    Technology (seller) fit        /20
    Commercial trigger / timing    /15
    Evidence quality / diversity   /15
    Company accessibility          /15
    Regional relevance             /10
    Novelty                        / 5
    -------------------------------------
    Positive subtotal              /100
    - Red-flag penalty             up to -15
    - Duplication penalty          up to -10

Grades:  A >= 70 (actionable) · B 50-69 (validate) · C 30-49 (evidence anchor) ·
         D < 30 (reject).

Guardrails carried over from the /30 skill rubric (so strong science alone can't
inflate a lead):
  - class-level-only evidence with no active trigger  -> capped at 60
  - poor buyer accessibility                          -> capped at 73
"""
from __future__ import annotations
from .. import llm

MAXES = {
    "scientific_evidence_strength": 20,
    "technology_fit": 20,
    "commercial_trigger": 15,
    "evidence_quality": 15,
    "company_accessibility": 15,
    "regional_relevance": 10,
    "novelty": 5,
}

SCORE_PROMPT = """Score this pharma BD opportunity. Be conservative and use ONLY
the evidence provided. Return an integer for each dimension within its maximum:

- scientific_evidence_strength (0-20): strength/directness of product-level science
- technology_fit (0-20): fit with a formulation/solubility/bioavailability seller
- commercial_trigger (0-15): reformulation, dose/food-effect study, CRL, lifecycle,
  patent-cliff, tech-transfer or partnering trigger present and timely
- evidence_quality (0-15): source diversity + primary sources (label/trial/peer-review)
- company_accessibility (0-15): small/mid biotech reachable = high; big pharma in-house = low
- regional_relevance (0-10): relevance/clarity of the region signal
- novelty (0-5): non-obvious, not already widely worked
- red_flag_penalty (0-15): subtract for weak/contradictory/stale/inaccessible signals
- duplication_penalty (0-10): subtract if likely duplicate of a well-known solved case

Also flag:
- is_class_level_only (true/false): evidence is only class-level, not molecule-specific
- has_active_trigger (true/false): a real timing/commercial trigger is present

Opportunity:
{opp}

Evidence ({n_types} distinct source type(s)):
{evidence}

Return JSON with keys: scores (object with the 7 dimensions + the 2 penalties),
is_class_level_only, has_active_trigger, confidence ("high|medium|low"),
red_flags (list), why_this_may_be_wrong (string)."""


def _grade(total: int) -> str:
    if total >= 70:
        return "A"
    if total >= 50:
        return "B"
    if total >= 30:
        return "C"
    return "D"


def deterministic_score(opp: dict) -> dict:
    """Rule-based score for provisional/fallback candidates — no LLM call, so it
    always works even when the configured model is completely unavailable.
    Deliberately capped low (max 60) since it reflects clustering, not full
    evidence synthesis; still shown (never silently dropped)."""
    ev = opp.get("evidence", [])
    cats = {e.get("source_category") for e in ev}
    base = 10
    if "regulatory" in cats:
        base += 20
    if "company" in cats:
        base += 12
    if "trial" in cats:
        base += 10
    if "publication" in cats:
        base += 5
    base += min(10, 3 * len(ev))
    if opp.get("problem_category") or opp.get("problem_signal"):
        base += 8
    total = max(5, min(60, base))
    opp["score"] = total
    opp["grade"] = _grade(total)
    opp.setdefault("confidence", "low")
    opp.setdefault("red_flags", [])
    opp["why_this_may_be_wrong"] = (
        "This is a deterministic/provisional candidate (clustered from evidence, "
        "not fully LLM-synthesised) — validate every field before outreach.")
    return opp


def score_one(opp: dict, cost) -> dict:
    ev = opp.get("evidence", [])
    n_types = len({e.get("source_type") for e in ev}) or 0
    ev_text = "\n".join(
        f"- ({e.get('source_type')}/{e.get('language','en')}) {e.get('title','')} "
        f"[{e.get('url','')}]" for e in ev) or "No evidence."
    opp_text = (f"Company: {opp.get('company')}; Product: {opp.get('product')}; "
                f"Signal: {opp.get('problem_signal')}; Stage: {opp.get('stage')}; "
                f"Region: {opp.get('region')}")
    try:
        res = llm.complete_json(
            SCORE_PROMPT.format(opp=opp_text, evidence=ev_text, n_types=n_types), cost)
        s = res.get("scores", {})
        positive = sum(min(MAXES[k], max(0, int(s.get(k, 0)))) for k in MAXES)
        red_pen = min(15, max(0, int(s.get("red_flag_penalty", 0))))
        dup_pen = min(10, max(0, int(s.get("duplication_penalty", 0))))
        total = positive - red_pen - dup_pen

        # Guardrails
        if res.get("is_class_level_only") and not res.get("has_active_trigger"):
            total = min(total, 60)
        if int(s.get("company_accessibility", 0)) <= 5:
            total = min(total, 73)
        if n_types <= 1:
            total = min(total, 60)

        total = max(0, min(100, total))
        opp["scores"] = s
        opp["score"] = total
        opp["grade"] = _grade(total)
        opp["confidence"] = res.get("confidence", "low")
        opp["red_flags"] = res.get("red_flags", []) or []
        opp["why_this_may_be_wrong"] = res.get("why_this_may_be_wrong", "")
        opp["score_error"] = None
    except Exception as e:
        # LLM scoring failed (e.g. 429 / bad JSON). Don't silently kill a
        # legitimate candidate: if it has a valid target or authoritative
        # evidence, score it deterministically instead of assigning grade D.
        ev = opp.get("evidence", [])
        authoritative = any(
            x.get("source_type") == "recall"
            or x.get("source_category") in ("regulatory", "company", "trial")
            for x in ev)
        if opp.get("valid_target_type") or opp.get("failure_event_confirmed") or authoritative:
            deterministic_score(opp)
            opp["score_error"] = f"LLM scoring failed ({e}); used deterministic score"
        else:
            opp.update(score=0, grade="D", confidence="low",
                       red_flags=[f"scoring failed: {e}"],
                       why_this_may_be_wrong="Automated scoring could not run.",
                       score_error=str(e))
    return opp


def score_and_filter(candidates, cost, min_evidence: int = 2):
    """Returns (accepted, rejected, debug).

    Provisional candidates (opp['provisional'] is True) are scored
    deterministically (no LLM dependency) and ALWAYS included in `accepted` —
    they exist precisely to guarantee visible, clearly-labelled output when the
    LLM path is degraded. Non-provisional candidates keep the full LLM-scored,
    evidence-gated path unchanged.
    """
    accepted, rejected = [], []
    debug = {"rejected_low_evidence": 0, "rejected_grade_d": 0,
             "score_errors": [], "provisional_included": 0}
    for c in candidates:
        if c.get("provisional"):
            scored = deterministic_score(c)
            accepted.append(scored)
            debug["provisional_included"] += 1
            continue
        ev = c.get("evidence", [])
        n_ev = len(ev)
        # A single HIGH-AUTHORITY source (regulatory record, recall/enforcement,
        # or a trial with a confirmed stopped-status) is sufficient on its own —
        # one FDA recall outweighs two vague web snippets. Weaker sources still
        # need >= min_evidence links.
        event_confirmed = bool(c.get("failure_event_confirmed"))

        def _is_authoritative(e):
            ent = e.get("entities") or {}
            if e.get("source_type") == "recall":
                return True
            if e.get("source_category") == "regulatory":
                return True
            # company/trial single sources qualify only when a concrete event
            # (discontinuation / stopped trial) is attached — either on the
            # evidence item or confirmed at the candidate level.
            if e.get("source_category") == "company" and (
                    ent.get("event_type") or event_confirmed):
                return True
            if e.get("source_type") == "trial" and (
                    event_confirmed or ent.get("event_type")):
                return True
            return False
        authoritative = any(_is_authoritative(e) for e in ev)
        min_needed = 1 if authoritative else min_evidence
        if n_ev < min_needed:
            c["reject_reason"] = (f"only {n_ev} evidence link(s); need {min_needed}"
                                  + (" (authoritative source path)" if authoritative else ""))
            rejected.append(c)
            debug["rejected_low_evidence"] += 1
            continue
        scored = score_one(c, cost)
        if scored.get("score_error"):
            debug["score_errors"].append(
                f"{scored.get('company') or scored.get('product')}: {scored['score_error']}")
        if scored["grade"] == "D":
            scored["reject_reason"] = f"grade D (score {scored['score']}/100)"
            rejected.append(scored)
            debug["rejected_grade_d"] += 1
        else:
            accepted.append(scored)
    accepted.sort(key=lambda x: x.get("score", 0), reverse=True)
    return accepted, rejected, debug
