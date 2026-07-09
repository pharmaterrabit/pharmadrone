"""Root-Cause & Solution-Fit Intelligence Layer (deterministic, embedded).

This is NOT a new app or product — it is an embedded reasoning layer used by the
existing report writer to turn a confirmed failure signal (recall / trial stop /
regulatory event) into a deeper, evidence-graded BD memo:

  1. Confirmed event
  2. Confirmed stated reason
  3. Confirmed root cause (only if a source proves it)
  4. Ranked root-cause hypotheses (evidence-graded, NOT all equal)
  5. Evidence gaps
  6. Solution / service fit (ranked: most / possibly / low relevance)
  7. Safe, non-accusatory outreach angle
  8. Confidence & readiness scores + validation checklist

Evidence discipline is strict: it never claims a root cause the evidence doesn't
prove. Confidence labels are bounded (Confirmed / Strongly supported / Moderate /
Plausible / Weak / Unknown). Everything is deterministic so it works when the LLM
is rate-limited or the circuit breaker has tripped.
"""
from __future__ import annotations
from . import bd_rules

CONF_LABELS = ["Confirmed", "Strongly supported", "Moderate", "Plausible",
               "Weak", "Unknown / requires validation"]


# --- Root-cause hypothesis sets per problem bucket -------------------------
# Each hypothesis: (name, base_confidence, supporting_signal_keys, bd_relevance)
# base_confidence is the ceiling when ONLY the recall reason is known; it is
# raised only if corroborating evidence is actually found (see grade_hypotheses).
_HYPOTHESES = {
    "dissolution": [
        ("Dissolution method / specification or release-testing issue",
         "Moderate", ["dissolution", "specification", "release", "assay"],
         "Dissolution method review, discriminatory method development, QC support"),
        ("API particle-size / solid-state (polymorph) variability",
         "Plausible", ["particle size", "polymorph", "solid-state", "crystal",
                       "micronization", "monohydrate", "macrocrystal"],
         "Solid-state characterisation, particle engineering, API control"),
        ("Formulation / excipient / capsule-fill performance issue",
         "Plausible", ["excipient", "capsule", "formulation", "wetting",
                       "disintegrant", "fill"],
         "Formulation optimisation, excipient compatibility, wetting/surfactant"),
        ("Manufacturing / granulation / compression variability",
         "Plausible", ["granulation", "compression", "blend", "process",
                       "manufacturing"],
         "Process optimisation, manufacturing troubleshooting"),
        ("Stability-related dissolution change over shelf-life",
         "Plausible", ["stability", "shelf", "aging", "storage"],
         "Stability programme, formulation robustness"),
        ("Isolated batch / QC release variability (lot-specific)",
         "Plausible", ["one lot", "single lot", "batch", "lot"],
         "Batch-variability investigation, QC release review"),
    ],
    "stability": [
        ("Degradation pathway / formulation robustness issue",
         "Moderate", ["degradation", "degradant", "impurity", "formulation"],
         "Degradant ID, formulation robustness, CMC support"),
        ("Packaging / container-closure protection insufficient",
         "Plausible", ["packaging", "moisture", "container", "closure",
                       "desiccant", "oxygen", "light"],
         "Packaging/CCI optimisation, protective packaging"),
        ("Storage-condition / distribution excursion contribution",
         "Plausible", ["storage", "temperature", "cold chain", "distribution"],
         "Stability/logistics review"),
        ("Isolated batch / manufacturing variability",
         "Plausible", ["batch", "lot", "process"],
         "Batch-variability investigation"),
    ],
    "impurity": [
        ("Analytical method / impurity-specification issue",
         "Moderate", ["method", "specification", "analytical", "assay"],
         "Analytical method review, impurity spec setting"),
        ("Process / route-of-synthesis control issue (API)",
         "Plausible", ["process", "synthesis", "route", "api", "drug substance"],
         "Process control, API supplier assessment"),
        ("Degradation-derived impurity (stability-linked)",
         "Plausible", ["degradation", "stability", "storage"],
         "Stability, degradant characterisation"),
        ("Supplier / raw-material variability",
         "Plausible", ["supplier", "raw material", "vendor", "source"],
         "Supplier quality, incoming-material control"),
    ],
    "sterility": [
        ("Aseptic-process / sterility-assurance breakdown",
         "Moderate", ["aseptic", "sterility", "media fill", "process"],
         "Aseptic process support, sterility assurance"),
        ("Filtration / environmental-monitoring / containment issue",
         "Plausible", ["filtration", "environmental", "monitoring", "containment",
                       "cleanroom"],
         "Sterile manufacturing, EM programme"),
        ("Particulate source (components / process / container closure)",
         "Plausible", ["particulate", "component", "container", "closure",
                       "glass", "rubber"],
         "Particulate investigation, component/CCI review"),
        ("Inspection / in-process control gap",
         "Plausible", ["inspection", "visual", "in-process"],
         "Visual inspection, in-process controls"),
    ],
    "packaging": [
        ("Container-closure integrity (CCI) failure",
         "Moderate", ["container", "closure", "integrity", "seal", "cci",
                      "leak"],
         "CCIT programme, closure design"),
        ("Packaging-component / seal-design defect",
         "Plausible", ["component", "seal", "blister", "cap", "design"],
         "Packaging redesign, component qualification"),
        ("Labelling / artwork mix-up",
         "Plausible", ["label", "labeling", "labelling", "artwork", "mix-up",
                       "mix up"],
         "Labelling/artwork controls"),
        ("Handling / line / distribution damage",
         "Plausible", ["handling", "line", "distribution", "transport"],
         "Line/handling review"),
    ],
    "potency": [
        ("Assay / content-uniformity issue",
         "Moderate", ["assay", "content uniformity", "uniformity", "potency"],
         "Assay method review, content-uniformity investigation"),
        ("Blend uniformity / manufacturing-control issue",
         "Plausible", ["blend", "mixing", "process", "manufacturing"],
         "Process control, blend-uniformity work"),
        ("API potency / overage / stability contribution",
         "Plausible", ["api", "overage", "stability", "degradation"],
         "API control, stability assessment"),
        ("Release-testing / sampling issue",
         "Plausible", ["release", "sampling", "specification", "testing"],
         "Release-testing review"),
    ],
    "gmp": [
        ("Quality-system / GMP-compliance gap",
         "Moderate", ["gmp", "quality system", "deviation", "capa", "data integrity"],
         "QMS remediation, GMP consulting"),
        ("Manufacturing process-capability issue",
         "Plausible", ["process", "capability", "manufacturing", "control"],
         "Process optimisation, manufacturing support"),
        ("Analytical / data-integrity issue",
         "Plausible", ["analytical", "data integrity", "method", "laboratory"],
         "Analytical remediation, data-integrity review"),
        ("Specification / release-criteria issue",
         "Plausible", ["specification", "release", "criteria"],
         "CMC/spec remediation"),
    ],
    "solidstate": [
        ("Polymorph / salt / hydrate conversion",
         "Moderate", ["polymorph", "salt", "hydrate", "form", "conversion",
                      "crystal"],
         "Solid-state characterisation, form control"),
        ("Crystallization / precipitation in process or product",
         "Plausible", ["crystallization", "precipitation", "process"],
         "Process control, particle engineering"),
        ("Formulation stabilisation of the solid form",
         "Plausible", ["formulation", "excipient", "stabiliser", "stabilizer"],
         "Formulation optimisation"),
    ],
}

# Solution-fit tiers per bucket: (most_relevant, possibly_relevant, low_relevance)
_SOLUTION_FIT = {
    "dissolution": (
        ["dissolution method review", "discriminatory dissolution method development",
         "analytical/QC testing support", "batch-variability investigation",
         "capsule/tablet formulation optimisation"],
        ["API particle-size control", "solid-state / polymorph characterisation",
         "excipient compatibility review", "wetting/surfactant strategy",
         "process optimisation"],
        ["packaging / container-closure support (only if storage/packaging implicated)"],
    ),
    "stability": (
        ["stability programme review", "degradant identification",
         "formulation robustness assessment"],
        ["packaging / container-closure protection", "storage/distribution review",
         "excipient / antioxidant strategy"],
        ["dissolution method work (only if dissolution drift implicated)"],
    ),
    "impurity": (
        ["analytical method review", "impurity identification / characterisation",
         "impurity-specification setting"],
        ["process / route control", "API supplier assessment",
         "stability-linked degradant review"],
        ["packaging support (only if degradation implicated)"],
    ),
    "sterility": (
        ["aseptic process review", "sterility-assurance / media-fill support",
         "environmental-monitoring programme"],
        ["filtration validation", "particulate investigation",
         "container-closure / component review"],
        ["dissolution/formulation work (low relevance for sterility events)"],
    ),
    "packaging": (
        ["container-closure integrity (CCIT) programme", "closure/seal design review",
         "labelling / artwork controls"],
        ["packaging-component qualification", "line/handling review"],
        ["API/formulation work (only if product-contact implicated)"],
    ),
    "potency": (
        ["assay method review", "content-uniformity investigation",
         "blend-uniformity / process-control support"],
        ["API potency / overage review", "release-testing review",
         "stability contribution assessment"],
        ["packaging support (low relevance for potency events)"],
    ),
    "gmp": (
        ["quality-system (QMS) remediation", "GMP / compliance consulting",
         "CAPA / deviation support"],
        ["manufacturing process support", "analytical / data-integrity remediation",
         "CMC / specification remediation"],
        ["formulation redesign (only if a specific product issue is implicated)"],
    ),
    "solidstate": (
        ["solid-state / polymorph characterisation", "form-control strategy",
         "particle engineering"],
        ["formulation stabilisation", "process/crystallization control",
         "dissolution impact assessment"],
        ["packaging support (low relevance unless storage implicated)"],
    ),
}

# Molecule/dosage-form scientific context (deterministic mini knowledge notes).
# Kept factual and general; used only to frame hypotheses, never to assert cause.
_MOLECULE_NOTES = {
    "nitrofurantoin": ("Nitrofurantoin is a BCS-class-dependent antibacterial whose "
        "oral products (e.g. monohydrate/macrocrystals) are known to be sensitive to "
        "particle size and solid-state form, both of which can influence dissolution "
        "and absorption — so a dissolution signal for this molecule is consistent "
        "with (but does not prove) particle-size or solid-state contributions."),
    "itraconazole": ("Itraconazole is a poorly water-soluble, weakly basic azole whose "
        "oral bioavailability is highly formulation- and solid-state-dependent; "
        "amorphous/crystalline form and particle engineering strongly affect "
        "dissolution, so dissolution/potency signals are consistent with solid-state "
        "or formulation contributions (not proof)."),
}


def _text_blob(opp: dict) -> str:
    parts = []
    for e in opp.get("evidence", []):
        parts.append(str(e.get("english_summary", "")))
        parts.append(str(e.get("title", "")))
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        parts.append(" ".join(str(v) for v in rf.values()))
    parts.append(str(opp.get("problem_category") or ""))
    parts.append(str(opp.get("problem_signal") or ""))
    return " ".join(parts).lower()


def _rank_conf(label: str) -> int:
    try:
        return len(CONF_LABELS) - CONF_LABELS.index(label)
    except ValueError:
        return 0


def confirmed_root_cause(opp: dict) -> str | None:
    """Return a root cause ONLY if the evidence text explicitly states one
    (e.g. a warning letter or company statement naming the cause). Otherwise
    None — we never fabricate it."""
    blob = _text_blob(opp)
    # Look for explicit causal statements from authoritative sources.
    cause_markers = ["root cause", "caused by", "attributed to", "due to",
                     "determined that", "found that the"]
    has_authoritative = any(
        (e.get("source_category") in ("regulatory", "company"))
        for e in opp.get("evidence", []))
    if has_authoritative:
        for m in cause_markers:
            i = blob.find(m)
            if i != -1:
                snippet = blob[i:i + 160].strip()
                # Only treat as confirmed if it's more than the recall reason echo.
                return snippet
    return None


def grade_hypotheses(opp: dict) -> list[dict]:
    """Rank root-cause hypotheses for the problem bucket, grading each by whether
    corroborating evidence is actually present in the collected text. With only
    the recall reason, confidences stay at their (bounded) base level."""
    bucket = bd_rules.bucket_for(opp.get("problem_category")
                                 or opp.get("problem_signal"))
    hyps = _HYPOTHESES.get(bucket)
    if not hyps:
        return []
    blob = _text_blob(opp)
    n_sources = len({e.get("source_name") for e in opp.get("evidence", [])})
    lot_specific = any(k in blob for k in ("one lot", "single lot", "lot #",
                                           "lot number", "one batch"))
    graded = []
    for name, base_conf, keys, bd_rel in hyps:
        # corroboration = how many distinct supporting signal keys appear
        hits = [k for k in keys if k in blob]
        conf = base_conf
        supporting = []
        if hits:
            supporting.append("mentions: " + ", ".join(sorted(set(hits))[:4]))
            # raise confidence a step if corroborated by >1 signal AND >1 source
            if len(hits) >= 2 and n_sources >= 2:
                idx = max(0, CONF_LABELS.index(base_conf) - 1)
                conf = CONF_LABELS[idx]
        # lot-specific recalls boost the "isolated batch" hypothesis
        if "batch" in name.lower() or "lot-specific" in name.lower():
            if lot_specific:
                conf = "Moderate"
                supporting.append("recall appears lot-specific")
        against = []
        if not hits:
            against.append("no corroborating evidence found beyond the stated reason")
        graded.append({
            "hypothesis": name,
            "confidence": conf,
            "supporting": "; ".join(supporting) or "stated reason only",
            "against": "; ".join(against) or "none identified (still requires validation)",
            "bd_relevance": bd_rel,
        })
    # Rank by confidence (desc), stable within same level by original order.
    graded.sort(key=lambda h: _rank_conf(h["confidence"]), reverse=True)
    return graded


def evidence_gaps(opp: dict) -> list[str]:
    """Enumerate the authoritative sources we did NOT find, so the gap is explicit."""
    have = set()
    blob = _text_blob(opp)
    for e in opp.get("evidence", []):
        cat = e.get("source_category")
        stype = e.get("source_type")
        if stype == "recall":
            have.add("recall")
        if cat == "regulatory":
            have.add("regulatory")
        if cat == "company":
            have.add("company")
        if cat == "trial":
            have.add("trial")
        if cat in ("publication",):
            have.add("publication")
    gaps = []
    if "warning letter" not in blob:
        gaps.append("no FDA warning letter found")
    if "483" not in blob and "inspection" not in blob:
        gaps.append("no FDA inspection / Form 483 finding found")
    if "company" not in have:
        gaps.append("no company statement / press release found")
    if "publication" not in have:
        gaps.append("no supporting scientific literature retrieved")
    if "trial" not in have:
        gaps.append("no related clinical-trial record found")
    gaps.append("root cause not independently confirmed by a second source")
    return gaps


def solution_fit(opp: dict) -> dict:
    bucket = bd_rules.bucket_for(opp.get("problem_category")
                                 or opp.get("problem_signal"))
    most, possible, low = _SOLUTION_FIT.get(bucket, (
        ["formulation / analytical / CMC assessment"],
        ["manufacturing or process support"],
        ["packaging support (only if implicated)"]))
    return {"most": most, "possible": possible, "low": low,
            "partners": bd_rules.partners(opp.get("problem_category")
                                          or opp.get("problem_signal"))}


def molecule_note(opp: dict) -> str | None:
    prod = (opp.get("product") or "").lower()
    for mol, note in _MOLECULE_NOTES.items():
        if mol in prod:
            return note
    return None


# --- Safe outreach variants (non-accusatory) -------------------------------
SAFE_OUTREACH = {
    "soft_collaboration": (
        "We work with teams addressing oral solid-dose performance, dissolution "
        "robustness, and CMC/formulation optimisation. I'd be interested in "
        "exploring whether our capabilities could support any ongoing lifecycle, "
        "quality, or formulation-improvement priorities."),
    "problem_aware": (
        "We noticed publicly available regulatory information indicating a "
        "historical product-quality signal in this product area. We do not assume "
        "the underlying cause or current status, but our team supports formulation, "
        "dissolution, and CMC troubleshooting and would be happy to explore whether "
        "there is any relevant fit."),
    "technology_provider": (
        "Our technology may be relevant where oral product performance, dissolution "
        "robustness, batch variability, or formulation optimisation are strategic "
        "priorities. Would it be useful to explore a confidential technical "
        "discussion?"),
}


def _recall_fields(opp: dict) -> dict:
    for e in opp.get("evidence", []):
        rf = (e.get("entities") or {}).get("recall_fields")
        if rf:
            return rf
    return {}


def confidence_and_readiness(opp: dict) -> dict:
    """Multi-dimensional confidence + a bounded overall readiness classification."""
    rf = _recall_fields(opp)
    hyps = grade_hypotheses(opp)
    top_conf = hyps[0]["confidence"] if hyps else "Unknown / requires validation"
    root_confirmed = confirmed_root_cause(opp) is not None
    n_sources = len({e.get("source_name") for e in opp.get("evidence", [])})
    has_support = any(e.get("source_category") in ("company", "trial", "publication")
                      for e in opp.get("evidence", []))

    blob = _text_blob(opp)
    status = (rf.get("status") or "").lower()
    classification = (rf.get("classification") or "").lower()
    terminated = "terminated" in status or "completed" in status
    lot_specific = any(k in blob for k in ("one lot", "single lot", "lot #",
                                           "one batch"))

    event_conf = "High" if rf or any(e.get("source_type") == "recall"
                                     for e in opp.get("evidence", [])) else "Medium"
    reason_conf = "High" if (rf.get("reason_for_recall")
                             or opp.get("problem_category")) else "Medium"
    root_conf = "Moderate" if root_confirmed else "Low / not publicly confirmed"
    tech_fit = "Medium"
    if hyps and _rank_conf(hyps[0]["confidence"]) >= _rank_conf("Moderate"):
        tech_fit = "Medium-High"
    # Commercial priority is limited by the caveats the spec lists.
    commercial = "Low-Medium"
    if terminated or lot_specific or "class iii" in classification or not has_support:
        commercial = "Low"
    if has_support and not terminated and not lot_specific:
        commercial = "Medium"

    readiness = "Needs validation"
    return {
        "event": event_conf,
        "stated_reason": reason_conf,
        "root_cause": root_conf,
        "technical_fit": tech_fit,
        "commercial": commercial,
        "outreach_readiness": readiness,
        "_meta": {"terminated": terminated, "lot_specific": lot_specific,
                  "classification": classification, "has_support": has_support,
                  "n_sources": n_sources},
    }


def validation_checklist(opp: dict) -> list[tuple[str, str]]:
    """(question, deterministic best-effort answer from available fields)."""
    rf = _recall_fields(opp)
    blob = _text_blob(opp)
    status = (rf.get("status") or "").strip() or "not stated"
    classification = (rf.get("classification") or "").strip() or "not stated"
    lot_specific = any(k in blob for k in ("one lot", "single lot", "lot #",
                                           "one batch"))
    dist = (rf.get("distribution_pattern") or "").strip() or "not stated"
    qty = (rf.get("product_quantity") or "").strip() or "not stated"
    return [
        ("Is the recall active or terminated?", status),
        ("Class I, II, or III?", classification),
        ("One lot or repeated?",
         "appears lot-specific" if lot_specific else "not established from this record"),
        ("Broad distribution or small quantity?", f"{dist} · quantity: {qty}"),
        ("Repeated recalls for same firm/product/problem?",
         "not established — check recall history for the firm/product"),
        ("Any warning letter or inspection finding?",
         "found" if ("warning letter" in blob or "483" in blob) else "none found"),
        ("Any company statement?",
         "found" if any(e.get("source_category") == "company"
                        for e in opp.get("evidence", [])) else "none found"),
        ("Any current lifecycle/manufacturing relevance?",
         "requires validation (is the product still marketed / made here?)"),
        ("Clear solution-provider fit?",
         "yes — see Solution-Fit Mapping" if grade_hypotheses(opp) else "unclear"),
        ("Safe contact route?", "requires validation (identify the right role/site)"),
    ]


def lead_classification(cr: dict) -> str:
    """Classify the lead using the confidence/readiness meta (spec step 7)."""
    meta = cr.get("_meta", {})
    if meta.get("terminated") and meta.get("lot_specific") and not meta.get("has_support"):
        return "Monitor only"
    if meta.get("terminated") and not meta.get("has_support"):
        return "Low priority / archive"
    if meta.get("has_support") and cr.get("commercial") in ("Medium", "Low-Medium"):
        return "Needs validation"
    return "Needs validation"


def dimension_scores(opp: dict, cr: dict | None = None) -> dict:
    """Separate 0-100 sub-scores + a capped overall (spec step 8)."""
    cr = cr or confidence_and_readiness(opp)
    meta = cr["_meta"]

    def band(label):
        return {"High": 90, "Medium-High": 75, "Medium": 60, "Low-Medium": 45,
                "Moderate": 60, "Low": 30, "Low / not publicly confirmed": 25,
                "Needs validation": 40}.get(label, 40)

    event = band(cr["event"])
    reason = band(cr["stated_reason"])
    root = band(cr["root_cause"])
    tech = band(cr["technical_fit"])
    commercial = band(cr["commercial"])
    contactability = 55 if meta.get("has_support") else 40
    # Overall is limited by the spec's caveats.
    overall = int(0.15 * event + 0.15 * reason + 0.15 * root
                  + 0.25 * tech + 0.20 * commercial + 0.10 * contactability)
    caps = []
    if cr["root_cause"].startswith("Low"):
        overall = min(overall, 65); caps.append("root cause not publicly confirmed")
    if meta.get("terminated"):
        overall = min(overall, 55); caps.append("event terminated/old")
    if meta.get("lot_specific"):
        overall = min(overall, 55); caps.append("single-lot only")
    if not meta.get("has_support"):
        overall = min(overall, 60); caps.append("no company/trial/news/literature support")
    return {
        "event_confidence": event, "stated_reason_confidence": reason,
        "root_cause_confidence": root, "technical_solution_fit": tech,
        "commercial_priority": commercial, "contactability": contactability,
        "outreach_readiness": band(cr["outreach_readiness"]),
        "overall": max(5, overall), "caps_applied": caps,
    }


# --- Rendering -------------------------------------------------------------
def render_root_cause_section(opp: dict) -> str:
    """The full Root-Cause & Solution-Fit Intelligence section (Markdown).
    Deterministic; safe when the LLM is disabled."""
    if not (opp.get("problem_category") or opp.get("problem_signal")):
        return ""
    rf = _recall_fields(opp)
    stated_reason = (rf.get("reason_for_recall")
                     or opp.get("problem_category") or "not stated")
    event = "FDA recall" if (rf or any(e.get("source_type") == "recall"
                             for e in opp.get("evidence", []))) else \
        (opp.get("event_type") or "failure event")
    confirmed_cause = confirmed_root_cause(opp)
    hyps = grade_hypotheses(opp)
    gaps = evidence_gaps(opp)
    fit = solution_fit(opp)
    cr = confidence_and_readiness(opp)
    scores = dimension_scores(opp, cr)
    checklist = validation_checklist(opp)
    mol = molecule_note(opp)

    # Hypotheses table
    hyp_rows = "\n".join(
        f"| {i+1}. {h['hypothesis']} | {h['supporting']} | {h['against']} | "
        f"{h['confidence']} | {h['bd_relevance']} |"
        for i, h in enumerate(hyps)) or "| — | — | — | Unknown | — |"

    fit_most = "\n".join(f"- {x}" for x in fit["most"])
    fit_possible = "\n".join(f"- {x}" for x in fit["possible"])
    fit_low = "\n".join(f"- {x}" for x in fit["low"])
    partners = "\n".join(f"- {p}" for p in fit["partners"])
    gap_lines = "\n".join(f"- {g}" for g in gaps)
    check_lines = "\n".join(f"- {q} **{a}**" for q, a in checklist)
    lead = lead_classification(cr)

    cause_line = (f"Confirmed root cause: **{confirmed_cause}**"
                  if confirmed_cause else
                  "Confirmed root cause: **not publicly confirmed** — the FDA "
                  "record confirms the recall reason only.")

    src_link = next((e.get("url") for e in opp.get("evidence", []) if e.get("url")), "—")

    return f"""

## Root-Cause & Solution-Fit Intelligence

*Deeper deterministic investigation of the confirmed signal. Confidence-graded;
no root cause is asserted beyond what the evidence proves.*

- **Confirmed event:** {event}
- **Confirmed stated reason:** {stated_reason}
- {cause_line}
- **Source:** [regulatory / evidence record]({src_link})

{('**Molecule / dosage-form context:** ' + mol) if mol else ''}

### Root-Cause Evidence Matrix
Ranked by evidence strength — hypotheses are **not** equal. With only the recall
reason available, most remain *Plausible* pending validation.

| Hypothesis | Evidence supporting | Evidence against / missing | Confidence | BD relevance |
|---|---|---|---|---|
{hyp_rows}

{('> Underlying root cause is not publicly confirmed. The FDA record confirms the recall reason only.' if not confirmed_cause else '')}

### Evidence Gaps
{gap_lines}

### Solution-Fit Mapping
**Most relevant:**
{fit_most}

**Possibly relevant:**
{fit_possible}

**Low relevance:**
{fit_low}

**Partner categories:**
{partners}

### Safe Outreach Angle (non-accusatory)
Choose one; none names a "faulty product", assumes root cause, or offers to "fix a recall":

**A — Soft collaboration:** {SAFE_OUTREACH['soft_collaboration']}

**B — Problem-aware (non-accusatory):** {SAFE_OUTREACH['problem_aware']}

**C — Technology-provider:** {SAFE_OUTREACH['technology_provider']}

### Confidence & Readiness
| Dimension | Assessment |
|---|---|
| Confirmed event | {cr['event']} |
| Stated reason | {cr['stated_reason']} |
| Root cause | {cr['root_cause']} |
| Technical solution fit | {cr['technical_fit']} |
| Commercial opportunity | {cr['commercial']} |
| Outreach readiness | {cr['outreach_readiness']} |

**Sub-scores (0-100):** event {scores['event_confidence']} · stated-reason \
{scores['stated_reason_confidence']} · root-cause {scores['root_cause_confidence']} \
· technical-fit {scores['technical_solution_fit']} · commercial \
{scores['commercial_priority']} · contactability {scores['contactability']} → \
**overall {scores['overall']}/100**{(' (capped: ' + '; '.join(scores['caps_applied']) + ')') if scores['caps_applied'] else ''}

### Validation Checklist (before any BD action)
{check_lines}

**Lead classification:** **{lead}** — validate recurrence and current relevance
before outreach.
"""
