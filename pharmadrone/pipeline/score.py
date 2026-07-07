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
    except Exception as e:
        opp.update(score=0, grade="D", confidence="low",
                   red_flags=[f"scoring failed: {e}"],
                   why_this_may_be_wrong="Automated scoring could not run.")
    return opp


def score_and_filter(candidates, cost, min_evidence: int = 2):
    """Returns (accepted, rejected). Rejects < min_evidence links or grade D."""
    accepted, rejected = [], []
    for c in candidates:
        n_ev = len(c.get("evidence", []))
        if n_ev < min_evidence:
            c["reject_reason"] = f"only {n_ev} evidence link(s); need {min_evidence}"
            rejected.append(c)
            continue
        scored = score_one(c, cost)
        if scored["grade"] == "D":
            scored["reject_reason"] = f"grade D (score {scored['score']}/100)"
            rejected.append(scored)
        else:
            accepted.append(scored)
    accepted.sort(key=lambda x: x.get("score", 0), reverse=True)
    return accepted, rejected
