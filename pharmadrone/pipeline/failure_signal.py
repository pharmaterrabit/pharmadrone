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
from . import discover

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
# Targeted phrases (not generic "poor solubility small molecule oral") so
# openFDA Enforcement, ClinicalTrials.gov, and web discovery actually surface
# failure/rescue signals instead of generic literature.
FAILURE_QUERY_PHRASES = [
    ('"terminated trial" "poor solubility"', "terminated trial poor solubility"),
    ('"withdrawn" "formulation issue" pharmaceutical', "withdrawn formulation issue pharmaceutical"),
    ('"discontinued" "bioavailability" drug company', "discontinued bioavailability drug"),
    ('"complete response letter" "CMC deficiencies"', "complete response letter CMC deficiencies"),
    ('"recall" "dissolution" tablet', "recall dissolution tablet"),
    ('"recall" "stability" drug product', "recall stability drug product"),
    ('"manufacturing issue" "drug supply" clinical trial', "manufacturing issue drug supply clinical trial"),
    ('"formulation change" "bioavailability" clinical trial', "formulation change bioavailability clinical trial"),
    ('"suspended trial" "drug supply issue"', "suspended trial drug supply issue"),
    ('"failed bioequivalence" tablet', "failed bioequivalence tablet"),
    ('"dissolution failure" FDA recall', "dissolution failure FDA recall"),
    ('"impurity" "recall" "tablet"', "impurity recall tablet"),
    ('"container closure" "recall" drug', "container closure recall drug"),
    ('"sterility" "recall" injectable', "sterility recall injectable"),
]


def build_failure_queries(profile: dict, max_per_region: int = 4) -> list[dict]:
    """Regulatory / trial / company / news failure-oriented queries per region.
    Tavily gets the quoted phrase (real web search supports it); structured
    connectors (ClinicalTrials.gov, openFDA) get the plain unquoted terms."""
    out = []
    regions = [r for r in profile.get("regions", []) if r.get("active")]
    for region in regions:
        for tavily_q, plain_q in FAILURE_QUERY_PHRASES[:max_per_region]:
            out.append({"query": f"{tavily_q} {region['name']}",
                        "plain_query": plain_q,
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


def extract_failure_signals(evidence: list[dict], cost, batch_size: int = 40,
                            max_batches: int = 3) -> tuple[list[dict], dict]:
    """Return (failure-signal candidates, debug). Enrichment only, capped at
    `max_batches` LLM calls, and stops if the circuit breaker trips."""
    signals = []
    debug = {"batches_total": 0, "batches_ok": 0, "batches_failed": 0,
             "errors": [], "llm_disabled": False}
    for bi, start in enumerate(range(0, len(evidence), batch_size)):
        if bi >= max_batches:
            break
        batch = evidence[start:start + batch_size]
        debug["batches_total"] += 1
        try:
            items = llm.complete_json(EXTRACT_PROMPT.format(
                snippets=_format_snippets(batch)), cost)
        except llm.LLMDisabled as e:
            debug["llm_disabled"] = True
            debug["errors"].append(f"LLM disabled: {e}")
            break
        except Exception as e:
            debug["batches_failed"] += 1
            debug["errors"].append(f"failure-signal extraction batch {start}: {e}")
            if llm.BREAKER.tripped:
                debug["llm_disabled"] = True
                break
            continue
        if not isinstance(items, list):
            debug["batches_failed"] += 1
            debug["errors"].append(
                f"failure-signal extraction batch {start}: LLM returned non-list JSON")
            continue
        debug["batches_ok"] += 1
        for it in items:
            # Quality gate: a valid target (product/company/molecule/trial) that
            # is NOT a generic/blacklisted scientific term. Otherwise discard.
            company = it.get("company")
            product = it.get("product") or it.get("molecule")
            if discover.is_blacklisted_target(company):
                company = None
            if discover.is_blacklisted_target(product):
                product = None
            if not company and not product:
                debug.setdefault("rejected_generic", 0)
                debug["rejected_generic"] += 1
                continue
            it["company"], it["product"] = company, product
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
            it["evidence"] = discover.dedup_evidence(attached)
            it["failure"] = True
            it["discovery_method"] = "llm-extraction"
            # Strict: the failure section will only assert an event if the
            # evidence structurally confirms it (see render_failure_section).
            # Do NOT trust a bare LLM-provided event_type as confirmation.
            it["failure_event_confirmed"] = bool(_event_from_evidence(it["evidence"]))
            signals.append(it)
    return signals, debug


def _event_from_evidence(evidence: list[dict]) -> str | None:
    """Lightweight structural check used at extraction time (mirrors the strict
    render-time gate): only recall records or BD-grade sources whose text states
    an event count. Academic papers never count."""
    words = ("terminat", "withdraw", "recall", "discontinu", "suspend",
             "complete response letter", "clinical hold", "refused")
    for e in evidence:
        if e.get("source_type") == "recall":
            return "recall"
        cat = e.get("source_category")
        text = (str(e.get("english_summary", "")) + " " + str(e.get("title", ""))).lower()
        if cat in ("regulatory", "company", "trial") and any(w in text for w in words):
            return next((w for w in words if w in text), "event")
    return None


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
def has_bd_grade_evidence(opp: dict) -> bool:
    """True if at least one evidence item is regulatory, company, trial, or a
    recall/enforcement record — the minimum quality gate for a real failure
    signal. Academic-only evidence does NOT qualify."""
    for e in opp.get("evidence", []):
        cat = e.get("source_category") or source_category(
            e.get("source_type", ""), e.get("url", ""))
        if cat in ("regulatory", "company", "trial"):
            return True
        if e.get("source_type") in ("recall", "trial", "label"):
            return True
    return False


def _event_structurally_confirmed(opp: dict) -> str | None:
    """Return a failure event string ONLY when the evidence structurally proves
    it — a recall/enforcement record, or a trial/regulatory/company evidence item
    whose own text/entities state the event. A bare `event_type` field (which the
    LLM path may set speculatively) is NOT sufficient and is ignored here.
    Academic papers can never confirm an event on their own.
    """
    event_words = ("terminat", "withdraw", "recall", "discontinu", "suspend",
                   "complete response letter", "clinical hold", "refused")
    for e in opp.get("evidence", []):
        stype = e.get("source_type")
        cat = e.get("source_category") or source_category(stype or "", e.get("url", ""))
        if stype == "recall":
            return "recall"
        text = (str(e.get("english_summary", "")) + " " + str(e.get("title", ""))).lower()
        # Trial-status confirmation: a trial source whose text states a stop.
        if stype == "trial" and any(w in text for w in event_words):
            return next((w for w in ("terminated", "withdrawn", "suspended")
                        if w in text), "trial stopped")
        # Regulatory/company source that explicitly states the event.
        if cat in ("regulatory", "company") and any(w in text for w in event_words):
            return next((w for w in ("recall", "withdrawn", "discontinued",
                                     "terminated", "complete response letter",
                                     "refused") if w in text), "event stated")
    return None


def render_failure_section(opp: dict) -> str:
    """Render the 'Failure Signal Intelligence' section from extracted fields.
    Deterministic (no fresh LLM) so it faithfully reflects the evidence.

    Quality gate: a real failure signal requires BOTH a STRUCTURALLY CONFIRMED
    failure event (from the evidence itself, not a speculative field) AND at
    least one BD-grade source (regulatory/company/trial/recall). If only academic
    literature exists, we render a 'No confirmed failure event' notice (technical
    background), never an invented failure.
    """
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
    ev_block = "\n".join(ev_lines) if ev_lines else "- (no evidence attached)"

    # Strict: confirm the event from the evidence, ignoring any speculative
    # event_type field. This closes the loophole where an LLM-guessed
    # event_type on academic-only evidence rendered a fake "terminated" signal.
    confirmed_event = _event_structurally_confirmed(opp)
    bd_grade = has_bd_grade_evidence(opp)
    event_confirmed = bool(confirmed_event)

    # No confirmed event OR no BD-grade source -> NOT a failure signal.
    if not (event_confirmed and bd_grade):
        reason = ("only academic/mechanistic literature is available"
                  if not bd_grade else
                  "no explicit failure event (terminated/withdrawn/recalled/"
                  "discontinued) is stated in the evidence")
        return f"""

## Failure Signal Intelligence

**No confirmed failure event found.** {reason.capitalize()}.

**Assessment:** Mechanistic / academic relevance only — **not a confirmed rescue
opportunity.** The sources below are shown as technical background; they do not
establish that any specific product or programme was terminated, withdrawn,
recalled, or discontinued.

**Background evidence:**
{ev_block}

**Problem classification (from literature only):** {opp.get('problem_category') or 'not established'}

**What must be verified before this becomes a BD signal:** a regulatory record
(recall/CRL/withdrawal), a clinical-trial stopped-status with reason, a company/
investor disclosure, or reputable pharma news confirming a specific failure event
for a named product or company.

> Signal status: **needs verification**. This is technical background, not a
failure/rescue signal.
"""

    # Confirmed event + BD-grade source -> real failure signal.
    from . import bd_rules
    # Pull the structured openFDA recall record if this is a recall.
    rf = None
    for e in opp.get("evidence", []):
        ent = e.get("entities") or {}
        if ent.get("recall_fields"):
            rf = ent["recall_fields"]
            break
    src_link = next((e.get("url") for e in opp.get("evidence", []) if e.get("url")), "—")
    problem = opp.get("problem_category") or opp.get("problem_signal")

    if rf:
        def g(k):
            return rf.get(k) or "not stated"
        what_block = f"""**What happened?** Confirmed FDA drug recall.

| Field | Value |
|---|---|
| Recall number | {g('recall_number')} |
| Recall classification | {g('classification')} |
| Recall status | {g('status')} |
| Recall initiation date | {g('recall_initiation_date')} |
| FDA report date | {g('report_date')} |
| Center classification date | {g('center_classification_date')} |
| Product description | {g('product_description')} |
| Recalling firm | {g('recalling_firm')} |
| Reason for recall | {g('reason_for_recall')} |
| Distribution pattern | {g('distribution_pattern')} |
| Product quantity | {g('product_quantity')} |
| Code information | {g('code_info')} |
| Voluntary / mandated | {g('voluntary_mandated')} |
| Firm location | {', '.join([x for x in (rf.get('city'), rf.get('state'), rf.get('country')) if x]) or 'not stated'} |
| Source | [openFDA / FDA enforcement record]({src_link}) |"""
        interp = bd_rules.interpretation(problem)
        rescue_list = bd_rules.rescue_steps(problem)
        partner_list = bd_rules.partners(problem)
    else:
        what_block = (f"**What happened?** {confirmed_event} · "
                      f"{opp.get('product','—')} · {opp.get('company','—')} · "
                      f"{opp.get('region','—')} · "
                      f"{opp.get('event_date','date not stated')}\n\n"
                      f"**Source:** [{src_link}]({src_link})")
        interp = opp.get("interpretation") or bd_rules.interpretation(problem)
        rescue_list = (opp.get("rescue_strategy") and [opp["rescue_strategy"]]) \
            or bd_rules.rescue_steps(problem)
        partner_list = opp.get("potential_partners") or bd_rules.partners(problem)

    rescue_block = "\n".join(f"- {s}" for s in rescue_list)
    partners_block = "\n".join(f"- {p}" for p in partner_list)

    return f"""

## Failure Signal Intelligence

**Signal summary:** Confirmed {confirmed_event} event affecting \
{opp.get('product') or opp.get('company') or 'the identified asset'}.

{what_block}

**Problem classification:** {problem or 'not established (needs verification)'}

**Evidence strength:** {opp.get('failure_rescue_strength','—')} \
({opp.get('failure_rescue_rationale','')})

**Confirmed vs interpretation (evidence discipline):**
- Confirmed (FDA fact): {(opp.get('confirmed_fact') or 'the recall event and its stated reason above').rstrip('.')}.
- PharmaDrone interpretation (not an FDA finding): the reason {interp}. This
  does not establish root cause beyond the stated recall reason and requires
  validation.

**Possible rescue strategy (rule-based; validate first):**
{rescue_block}

**Possible partner categories:**
{partners_block}

**Recommended next action:** {opp.get('next_action','validate')} — confirm scope \
(isolated lot vs recurring), site, and supplier before any outreach.

**Red flags / missing evidence:** {'; '.join(opp.get('red_flags', []) or []) or 'root cause and scope not established from the recall record alone — validate before outreach'}

> Signal status: **{opp.get('signal_status','needs_verification')}**. Confirmed FDA \
recall fact is separated from PharmaDrone interpretation above; validate before any \
commercial decision.
"""
