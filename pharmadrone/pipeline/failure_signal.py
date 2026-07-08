"""Failure Signal Intelligence Layer (embedded in the PharmaDrone Opportunity Engine).

This is NOT a standalone product. It adds, to the existing problem-first workflow:
  - failure/rescue query templates,
  - source-category + signal-status tagging with an evidence-priority order,
  - a 22-field failure/rescue extraction schema,
  - a new scoring dimension (Failure / Rescue Signal Strength) that influences
    ranking but never overrides evidence quality,
  - a "Failure Signal Intelligence" section rendered into each report.

Evidence discipline (hard rules): never invent a failure reason; never claim a
formulation/CMC failure unless a source supports it; separate confirmed fact from
interpretation; prefer regulatory sources over news; academic papers support
mechanism only, not commercial conclusions.

NOTE: this module deliberately does NOT import the connectors package (keeps it
free of network deps so scoring/reporting import cleanly).
"""
from __future__ import annotations
from .. import llm

# --- Failure / rescue vocabulary -------------------------------------------
FAILURE_TERMS = [
    "discontinued", "terminated", "withdrawn", "recall", "recalled", "rejected",
    "Complete Response Letter", "refused marketing authorisation",
    "withdrawn application", "clinical hold", "suspended", "deprioritised",
    "manufacturing issue", "CMC issue", "stability issue", "formulation issue",
    "bioavailability issue", "supply issue", "quality defect", "impurity",
]

PROBLEM_CATEGORIES = [
    "poor solubility", "poor dissolution", "poor bioavailability", "instability",
    "degradation", "precipitation", "aggregation", "polymorphism", "crystallinity",
    "particle size", "poor manufacturability", "scale-up failure", "CMC deficiency",
    "batch reproducibility", "impurities", "degradation products", "sterility",
    "contamination", "excipient incompatibility", "container-closure",
    "packaging defect", "leachables/extractables", "cold-chain/storage",
    "shelf-life", "drug-device delivery failure", "bioequivalence failure",
    "failed reformulation", "recalled dosage form", "quality discontinuation",
]

# --- Source category + evidence priority -----------------------------------
SOURCE_CATEGORY_BY_TYPE = {
    "recall": "regulatory", "enforcement": "regulatory", "label": "regulatory",
    "trial": "trial", "paper": "publication", "web": "news",
    "company": "company", "conference": "conference", "patent": "patent",
}

# regulatory > company/investor > trial registry > publication > conference > news
EVIDENCE_PRIORITY = ["regulatory", "company", "trial", "publication",
                     "conference", "news", "patent"]

SIGNAL_STATUSES = ["confirmed", "indirect", "weak", "needs_verification"]

_REGULATORY_HOSTS = ("fda.gov", "ema.europa.eu", "gov.uk", "mhra", "tga.gov.au",
                     "pmda.go.jp", "sfda.gov.sa", "nmpa.gov.cn", "canada.ca",
                     "who.int", "edqm.eu")
_COMPANY_HOSTS = ("sec.gov", "investor", "press", "prnewswire", "businesswire",
                  "globenewswire")


def source_category(source_type: str, url: str = "") -> str:
    """Map a source_type to a category, upgrading web hits by domain heuristics."""
    cat = SOURCE_CATEGORY_BY_TYPE.get(source_type, "news")
    if source_type == "web" and url:
        u = url.lower()
        if any(h in u for h in _REGULATORY_HOSTS):
            return "regulatory"
        if any(h in u for h in _COMPANY_HOSTS):
            return "company"
    return cat


def priority_rank(category: str) -> int:
    """Lower is stronger. Unknown categories rank last."""
    return EVIDENCE_PRIORITY.index(category) if category in EVIDENCE_PRIORITY else 99


# --- Failure/rescue query templates ----------------------------------------
def build_failure_queries(profile: dict, max_per_region: int = 4) -> list[dict]:
    """Regulatory / trial / company / news failure-oriented queries per region."""
    out = []
    regions = [r for r in profile.get("regions", []) if r.get("active")]
    core = ["drug discontinued formulation", "Complete Response Letter CMC",
            "drug recall manufacturing defect", "withdrawn marketing authorisation",
            "terminated trial formulation bioavailability",
            "reformulation stability issue"]
    for region in regions:
        for term in core[:max_per_region]:
            out.append({"query": f"{term} {region['name']}",
                        "region": region["name"], "lang": "en", "intent": "failure"})
    # de-dup identical query strings
    seen, uniq = set(), []
    for q in out:
        k = (q["query"], q["region"])
        if k not in seen:
            seen.add(k)
            uniq.append(q)
    return uniq


# --- Extraction: the 22-field failure/rescue schema ------------------------
EXTRACT_PROMPT = """You are a pharma failure-signal analyst. From the evidence
snippets below, extract distinct FAILURE / RESCUE signals: products, molecules,
dosage forms, delivery systems, drug-device products, or manufacturing programmes
that were failed, rejected, recalled, delayed, withdrawn, discontinued,
deprioritised, reformulated, or placed on hold for reasons that may relate to
FORMULATION, CMC, physical form, packaging, delivery, quality, stability, or
manufacturing.

STRICT RULES:
- Use ONLY facts present in the snippets. NEVER invent a failure reason.
- Do NOT claim a formulation/CMC failure unless a snippet supports it. If the
  reason is unclear or unrelated to formulation/CMC/delivery/quality, set
  problem_category to null and signal_status to "needs_verification".
- Separate confirmed fact from interpretation.
- Academic papers support mechanism/plausibility only — never a business conclusion.
- Exclude biologics/vaccines/devices only if clearly out of formulation scope.

For each signal return an object with keys:
  product, molecule, dosage_form, route, company, region, regulatory_body, stage,
  event_type, event_date, failure_reason, problem_category,
  signal_status ("confirmed"|"indirect"|"weak"|"needs_verification"),
  confirmed_fact, interpretation,
  why_scientific, why_commercial, rescue_strategy, bd_opportunity,
  potential_partners (list from: "formulation technology","CDMO","excipient supplier",
    "packaging/container-closure","drug-delivery","analytical/CMC consultancy",
    "university lab","research group","patent holder"),
  next_action (one of: "monitor","validate","contact BD/licensing",
    "contact CMC/formulation team","contact external innovation",
    "identify solution providers","prepare outreach memo"),
  red_flags,
  evidence_ids (list of integer snippet ids supporting THIS signal)

Return a JSON list. Snippets:
{snippets}
"""


def _format_snippets(evidence: list[dict], limit: int = 40) -> str:
    lines = []
    for idx, e in enumerate(evidence[:limit]):
        cat = source_category(e.get("source_type", ""), e.get("url", ""))
        lines.append(
            f"[{idx}] ({cat}/{e.get('source_name','')}) {e.get('title','')} :: "
            f"{e.get('raw_text','')[:600]} <url:{e.get('url','')}>")
    return "\n".join(lines)


def extract_failure_signals(evidence: list[dict], cost, batch_size: int = 40) -> list[dict]:
    """Return failure-signal candidates in the same shape as normal opportunity
    candidates (company/product + evidence), plus the failure schema fields and
    a `failure` flag so the report writer knows to render the section."""
    signals = []
    for start in range(0, len(evidence), batch_size):
        batch = evidence[start:start + batch_size]
        try:
            items = llm.complete_json(EXTRACT_PROMPT.format(
                snippets=_format_snippets(batch)), cost)
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        for it in items:
            if not it.get("company") and not it.get("product") and not it.get("molecule"):
                continue
            attached = []
            for i in it.get("evidence_ids") or []:
                if isinstance(i, int) and 0 <= i < len(batch):
                    src = batch[i]
                    cat = source_category(src.get("source_type", ""), src.get("url", ""))
                    attached.append({
                        "source_type": src["source_type"],
                        "source_category": cat,
                        "source_name": src["source_name"],
                        "record_id": src["record_id"],
                        "title": src["title"],
                        "url": src["url"],
                        "language": src["language"],
                        "english_summary": src["raw_text"][:400],
                        "date_accessed": src["date_accessed"],
                        "supports": it.get("confirmed_fact"),
                        "does_not_prove": it.get("red_flags"),
                    })
            it["evidence"] = attached
            it["failure"] = True
            it.setdefault("product", it.get("molecule"))
            signals.append(it)
    return signals


# --- Failure / Rescue Signal Strength (new scoring dimension) ---------------
def rescue_strength(opp: dict) -> tuple[str, int, str]:
    """Return (rating, score_bonus, rationale).

    High   : regulatory OR company source confirms a formulation/CMC/mfg/quality/
             packaging/delivery/stability issue.
    Medium : trial / company / investor / multiple indirect sources suggest it.
    Low    : only academic or news suggests a possible issue, no direct confirmation.
    Reject : reason unclear, unrelated to formulation/CMC/delivery/quality, or
             speculation only  -> bonus 0 and a red flag.

    Bonus is capped so it influences ranking but cannot override evidence quality.
    """
    cats = {e.get("source_category") or source_category(e.get("source_type", ""),
            e.get("url", "")) for e in opp.get("evidence", [])}
    status = (opp.get("signal_status") or "").lower()
    problem = opp.get("problem_category")
    has_reg = "regulatory" in cats
    has_company = "company" in cats
    has_trial = "trial" in cats
    only_soft = cats.issubset({"publication", "news", "conference", "patent"})

    if not problem:
        # No formulation/CMC/delivery/quality category established -> not a rescue
        # signal (reason unclear or unrelated). Per spec: reject/flag.
        return "Reject/flag", 0, "reason unclear or not formulation/CMC-related"
    if (has_reg or has_company) and status == "confirmed" and problem:
        return "High", 12, "regulatory/company source confirms a technical issue"
    if (has_trial or has_company or status in ("indirect", "confirmed")) and problem:
        return "Medium", 7, "registry/company/indirect sources suggest a technical issue"
    if only_soft:
        return "Low", 3, "academic/news suggests a possible issue; unconfirmed"
    return "Low", 3, "weak/unconfirmed signal"


def apply_failure_scoring(opp: dict) -> dict:
    """Fold the rescue bonus into the 0-100 score (re-clamped) and tag the opp."""
    rating, bonus, rationale = rescue_strength(opp)
    opp["failure_rescue_strength"] = rating
    opp["failure_rescue_rationale"] = rationale
    if bonus:
        base = int(opp.get("score", 0))
        opp["score"] = max(0, min(100, base + bonus))
    if rating == "Reject/flag":
        opp.setdefault("red_flags", [])
        if "failure reason not formulation/CMC-confirmed" not in opp["red_flags"]:
            opp["red_flags"].append("failure reason not formulation/CMC-confirmed")
    return opp


# --- Report section (deterministic; rendered from stored fields) -----------
def render_failure_section(opp: dict) -> str:
    """Render the 'Failure Signal Intelligence' section from extracted fields.
    Deterministic (no fresh LLM) so it faithfully reflects the evidence."""
    if not opp.get("failure"):
        return ""
    ev_lines = []
    for e in sorted(opp.get("evidence", []),
                    key=lambda x: priority_rank(x.get("source_category")
                    or source_category(x.get("source_type", ""), x.get("url", "")))):
        cat = e.get("source_category") or source_category(
            e.get("source_type", ""), e.get("url", ""))
        ev_lines.append(f"- **[{cat}]** {e.get('title','')} "
                        f"({e.get('language','en')}) — [{e.get('url','')}]"
                        f"({e.get('url','')})")
    partners = ", ".join(opp.get("potential_partners", []) or []) or "—"
    return f"""

## Failure Signal Intelligence

**Signal summary:** {opp.get('failure_reason') or 'Possible failure/rescue signal — see below.'}

**What happened?** {opp.get('event_type','—')} · {opp.get('product','—')} · \
{opp.get('company','—')} · {opp.get('region','—')} · {opp.get('event_date','date not stated')}

**Evidence:**
{chr(10).join(ev_lines) if ev_lines else '- (no evidence attached)'}

**Problem classification:** {opp.get('problem_category') or 'not established (needs verification)'}

**Evidence strength:** {opp.get('failure_rescue_strength','—')} \
({opp.get('failure_rescue_rationale','')})

**Confirmed vs interpretation:**
- Confirmed: {opp.get('confirmed_fact') or '—'}
- PharmaDrone interpretation: {opp.get('interpretation') or opp.get('why_commercial') or '—'}

**Scientific relevance:** {opp.get('why_scientific','—')}

**Commercial relevance:** {opp.get('why_commercial','—')}

**Possible rescue strategy:** {opp.get('rescue_strategy','—')}

**Potential partners:** {partners}

**Recommended next action:** {opp.get('next_action','validate')}

**Red flags / missing evidence:** {'; '.join(opp.get('red_flags', []) or []) or 'none stated — verify before outreach'}

> Signal status: **{opp.get('signal_status','needs_verification')}**. Prioritise \
official regulatory sources; validate before any commercial decision.
"""
