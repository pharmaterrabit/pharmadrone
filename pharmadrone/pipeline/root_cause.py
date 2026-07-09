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

# Text that is drug indication / mechanism / label description — NEVER a recall
# root cause. Used to reject "nitrofurantoin is an antibacterial ... caused by
# bacteria" style false positives.
_INDICATION_MARKERS = [
    "antibacterial", "antibiotic", "indicated for", "indication", "used to treat",
    "used for", "treatment of", "mechanism of action", "pharmacology",
    "urinary tract infection", "is a medication", "is an antibiotic",
    "class of medications", "how it works", "drug information", "patient information",
    "prescribing information", "side effects", "dosage and administration",
    "caused by bacteria", "bacterial infection", "kills bacteria", "inhibits",
]

# Domains/text that are reliable for corroboration vs junk to reject outright.
_REJECT_DOMAINS = [
    "drugs.com", "webmd", "mayoclinic", "drugbank", "wikipedia", "medlineplus",
    "rxlist", "healthline", "goodrx", "patient.info", "everydayhealth",
    "vehicle", "automobile", "car recall", "honda", "toyota", "nhtsa",
]
_RELIABLE_REGULATORY = [
    "fda.gov", "accessdata.fda.gov", "ema.europa.eu", "gov.uk", "mhra",
    "tga.gov.au", "hc-sc.gc.ca", "canada.ca", "pmda.go.jp", "sfda.gov.sa",
]

# Explicit causal-evidence source types allowed to CONFIRM a root cause.
_CAUSAL_SOURCE_MARKERS = [
    "warning letter", "form 483", "483 observation", "inspection", "establishment inspection",
    "recall notice", "recall press release", "recall statement",
]


def _norm(s):
    return " ".join(str(s or "").split()).strip().lower()


# Label / SPL / ANDA / approval pages are regulatory CONTEXT, not recall evidence.
_LABEL_PAGE_MARKERS = [
    "/spl/", "/label/", "drugsatfda", "/scripts/cder", "prescribing information",
    "package insert", "structured product label", "anda ", "nda ", "approval package",
    "label pdf", "/daf/", "drug label",
]
# An official RECALL/enforcement page (distinct from a label page).
_RECALL_PAGE_MARKERS = [
    "enforcement", "/safety/recalls", "recall", "ires/index.cfm", "/recalls/",
]


def _is_label_page(e: dict) -> bool:
    text = _norm(e.get("url", "")) + " " + _norm(e.get("title", "")) + " " \
        + _norm(e.get("english_summary", ""))
    return any(m in text for m in _LABEL_PAGE_MARKERS)


def _is_recall_page(e: dict) -> bool:
    text = _norm(e.get("url", "")) + " " + _norm(e.get("title", ""))
    # A recall page marker AND not obviously just a label page.
    return any(m in text for m in _RECALL_PAGE_MARKERS) and not _is_label_page(e)


def _distinctive_product_tokens(product: str) -> list[str]:
    """Distinctive tokens from the product name (drop generic dosage words) so a
    product match means the SAME product, not just any capsule/tablet."""
    generic = {"capsules", "capsule", "tablets", "tablet", "usp", "mg", "ml",
               "oral", "rx", "only", "per", "bottle", "solution", "injection",
               "100", "50", "500", "the", "and", "of", "for"}
    toks = [t for t in _norm(product).replace(",", " ").split()
            if t not in generic and len(t) >= 4]
    return toks


def _match_fields(rf: dict, opp: dict, e: dict) -> set:
    """Which identifying fields of THIS recall does evidence item `e` match?"""
    text = _norm(e.get("title", "")) + " " + _norm(e.get("english_summary", "")) \
        + " " + _norm(e.get("url", ""))
    matched = set()
    recall_no = _norm(rf.get("recall_number"))
    firm = _norm(rf.get("recalling_firm") or opp.get("company"))
    product = _norm(rf.get("product_description") or opp.get("product"))
    molecule = product.split(",")[0].split()[0] if product else ""
    problem = _norm(opp.get("problem_category") or opp.get("problem_signal"))
    ndc = _norm(rf.get("code_info"))  # code_info sometimes carries lot/NDC

    if recall_no and recall_no in text:
        matched.add("recall_number")
    if firm and firm in text:
        matched.add("recalling_firm")
    # Product match requires the DISTINCTIVE product token(s), not a 30-char
    # prefix (which would let "Nitrofurantoin Capsules" match any capsule).
    prod_tokens = _distinctive_product_tokens(product)
    if prod_tokens and all(t in text for t in prod_tokens[:2]):
        matched.add("product")
    if molecule and len(molecule) >= 4 and molecule in text:
        matched.add("molecule")
    if problem and problem in text:
        matched.add("problem_category")
    if ndc and len(ndc) >= 5 and ndc in text:
        matched.add("ndc")
    # An OFFICIAL RECALL page (enforcement/recall), NOT a label/SPL/ANDA page.
    if _is_recall_page(e):
        matched.add("official_recall_page")
    return matched


def _looks_like_indication(e: dict) -> bool:
    text = _norm(e.get("title", "")) + " " + _norm(e.get("english_summary", ""))
    return any(m in text for m in _INDICATION_MARKERS)


def _is_reject_domain(e: dict) -> bool:
    text = _norm(e.get("url", "")) + " " + _norm(e.get("title", ""))
    return any(d in text for d in _REJECT_DOMAINS)


def _is_regulatory_domain(e: dict) -> bool:
    text = _norm(e.get("url", ""))
    return any(d in text for d in _RELIABLE_REGULATORY)


def classify_corroboration(e: dict, opp: dict, rf: dict) -> dict:
    """Classify one evidence item's relevance to THIS specific recall.

    Evidence classes:
      direct_recall_evidence        — the recall itself / same product+firm+problem
                                       / explicit recall number; the ONLY class that
                                       can support confirmed event/root-cause.
      same_product_firm             — same product AND firm, different record.
      same_firm_quality_history     — SAME FIRM, DIFFERENT product (context only).
      regulatory_label_context      — FDA label/SPL/ANDA/approval page (context).
      same_molecule_science         — same molecule, scientific/regulatory context.
      formulation_science_background— general molecule/formulation literature.
      reject                        — unrelated / low-quality.
    """
    matched = _match_fields(rf, opp, e)
    is_reg = _is_regulatory_domain(e)
    is_label = _is_label_page(e)
    is_recall_pg = _is_recall_page(e)
    causal_text = any(m in (_norm(e.get("title", "")) + " "
                            + _norm(e.get("english_summary", "")))
                      for m in _CAUSAL_SOURCE_MARKERS)

    # Hard rejects first.
    if _is_reject_domain(e) and "recall_number" not in matched:
        return {"class": "reject", "matched_fields": sorted(matched),
                "accepted": False,
                "reason": "low-quality / unrelated domain (e.g. drug-info, vehicle recall)"}
    if _looks_like_indication(e) and "recall_number" not in matched:
        return {"class": "formulation_science_background"
                if ("dissolution" in _norm(e.get("english_summary", ""))
                    or "particle" in _norm(e.get("english_summary", ""))) else "reject",
                "matched_fields": sorted(matched), "accepted": False,
                "reason": "drug indication / label / mechanism text — not recall-cause evidence"}

    # FDA label / SPL / ANDA / approval page = regulatory CONTEXT, never recall
    # evidence, even though it lives on an FDA domain (issue 3).
    if is_label and "recall_number" not in matched and not is_recall_pg:
        return {"class": "regulatory_label_context", "matched_fields": sorted(matched),
                "accepted": False, "causal_source": False,
                "reason": "FDA label / SPL / ANDA / approval page — regulatory "
                          "context only, not a recall or root-cause source"}

    # Direct recall evidence: strongest tier. Requires an explicit recall-number
    # match, OR this exact product + firm + problem, OR an official RECALL page
    # (not a label page) that names this product.
    is_direct = (
        ("recall_number" in matched)
        or ({"product", "recalling_firm", "problem_category"} <= matched)
        or ({"official_recall_page", "product"} <= matched))
    if is_direct:
        return {"class": "direct_recall_evidence", "matched_fields": sorted(matched),
                "accepted": True,
                "causal_source": bool(is_reg and is_recall_pg and causal_text
                                      or (causal_text and "warning letter" in
                                          _norm(e.get("title", "")))),
                "reason": "matches this recall (recall number, or exact product+firm+problem, "
                          "or official recall page naming this product)"}

    # Same product AND firm (different record) — supporting context, not the recall.
    if {"product", "recalling_firm"} <= matched:
        return {"class": "same_product_firm", "matched_fields": sorted(matched),
                "accepted": True, "causal_source": False,
                "reason": "same product and firm (supporting context, not this recall)"}

    # SAME FIRM, DIFFERENT product (e.g. potassium/ranitidine/valsartan recalls
    # by the same firm) — historical quality context ONLY. Never product-specific
    # evidence, root cause, or a score lift (issue 1).
    if "recalling_firm" in matched and "product" not in matched:
        return {"class": "same_firm_quality_history", "matched_fields": sorted(matched),
                "accepted": True, "causal_source": False,
                "context_only": True,
                "reason": "Same-firm quality history only; not evidence for this "
                          "product or root cause"}

    # Same molecule (not same product) — scientific/regulatory context only.
    if "molecule" in matched:
        cls = "same_molecule_science" if e.get("source_category") in (
            "regulatory", "news") else "formulation_science_background"
        return {"class": cls, "matched_fields": sorted(matched),
                "accepted": False, "causal_source": False,
                "reason": "same molecule scientific/regulatory context — not this "
                          "specific recall or its root cause"}

    return {"class": "reject", "matched_fields": sorted(matched), "accepted": False,
            "reason": "insufficient match to this recall (needs recall number, or "
                      "product+firm, or official recall page naming this product)"}


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
    """Return a root cause ONLY when an authoritative source EXPLICITLY states the
    cause of THIS recall. Allowed sources: FDA warning letter, inspection/Form 483,
    an FDA/official recall page or company recall statement that explicitly explains
    the defect. NEVER from a product label, drug-info page, mechanism/indication
    text, general literature, or an unrelated/same-firm-different-product recall.
    Returns None otherwise (we never fabricate a cause).
    """
    rf = _recall_fields(opp)
    for e in opp.get("evidence", []):
        cls = classify_corroboration(e, opp, rf)
        # Must be direct recall evidence AND flagged as a causal regulatory source.
        if cls["class"] != "direct_recall_evidence" or not cls.get("causal_source"):
            continue
        text = (str(e.get("english_summary", "")) + " " + str(e.get("title", "")))
        low = text.lower()
        # Reject if the matched text is actually indication/mechanism language.
        if any(m in low for m in _INDICATION_MARKERS):
            continue
        # Find an EXPLICIT causal clause and return a tight, sourced snippet.
        for m in ("root cause", "caused by", "attributed to", "resulted from",
                  "determined that", "due to a", "due to the"):
            i = low.find(m)
            if i != -1:
                snippet = " ".join(text[i:i + 200].split())
                src = e.get("source_name") or "regulatory source"
                return f"{snippet} (per {src})"
    return None


def _relevant_text_blob(opp: dict) -> str:
    """Text used for hypothesis grading — the recall record + evidence that was
    ACCEPTED as relevant to this recall (direct/same-product-firm/same-molecule).
    Rejected corroboration is excluded so unrelated pages can't sway grading."""
    rf = _recall_fields(opp)
    parts = [str(rf.get("reason_for_recall", "")), str(rf.get("product_description", "")),
             str(opp.get("problem_category") or ""), str(opp.get("product") or "")]
    for e in opp.get("evidence", []):
        # recall record itself
        if e.get("source_type") == "recall":
            parts.append(str(e.get("english_summary", "")))
            parts.append(str(e.get("title", "")))
        # accepted corroboration only
        elif e.get("corroboration") and e.get("evidence_class") in (
                "direct_recall_evidence", "same_product_firm", "same_molecule_science"):
            parts.append(str(e.get("english_summary", "")))
            parts.append(str(e.get("title", "")))
    return " ".join(parts).lower()


def grade_hypotheses(opp: dict) -> list[dict]:
    """Rank root-cause hypotheses for the problem bucket. Grading uses only the
    recall record and evidence accepted as relevant (see _relevant_text_blob).

    Evidence phrasing is explicit (req 6): no vague "mentions:" — hypotheses tied
    to product composition or the stated reason are described in full, and the
    per-hypothesis gap ("no source links this to THIS recalled lot") is always
    stated. Confidence is only raised above the base level by a RELEVANT,
    accepted corroboration source — never by keyword coincidence."""
    bucket = bd_rules.bucket_for(opp.get("problem_category")
                                 or opp.get("problem_signal"))
    hyps = _HYPOTHESES.get(bucket)
    if not hyps:
        return []
    rf = _recall_fields(opp)
    reason = _norm(rf.get("reason_for_recall") or opp.get("problem_category"))
    product = _norm(rf.get("product_description") or opp.get("product"))
    blob = _relevant_text_blob(opp)

    # Was there any ACCEPTED, relevant corroboration that actually discusses cause?
    accepted_corro = [e for e in opp.get("evidence", [])
                      if e.get("corroboration") and e.get("evidence_class") in (
                          "direct_recall_evidence", "same_product_firm")]

    lot_specific = any(k in blob for k in ("one lot", "single lot", "lot #",
                                           "lot number", "one batch", "repackaged lot"))
    graded = []
    for name, base_conf, keys, bd_rel in hyps:
        # A hit only counts if the term appears in the RECALL/relevant text AND
        # is a product-composition or stated-reason fact — not a coincidental word.
        hits = [k for k in keys if k in blob]
        conf = base_conf
        supporting = None
        against = None

        # Product-composition-based support (e.g. product literally contains
        # "monohydrate/macrocrystals" -> particle-size/solid-state hypothesis).
        composition_hit = [k for k in keys if k in product]
        stated_reason_hit = [k for k in keys if k in reason]

        if stated_reason_hit:
            supporting = (f"The FDA-stated reason references "
                          f"'{', '.join(sorted(set(stated_reason_hit))[:3])}'.")
        elif composition_hit:
            supporting = (f"Product contains "
                          f"'{', '.join(sorted(set(composition_hit))[:3])}'; "
                          f"{bucket} performance may be sensitive to this based on "
                          "scientific literature.")
        elif hits:
            supporting = ("Related technical factor appears in the relevant "
                          "evidence for this product/firm.")
        else:
            supporting = "Not indicated by the recall record."

        # Confidence escalation ONLY from accepted, relevant corroboration whose
        # text actually supports this specific hypothesis.
        escalated = False
        for e in accepted_corro:
            etext = _norm(e.get("english_summary", "")) + " " + _norm(e.get("title", ""))
            if any(k in etext for k in keys) and any(
                    m in etext for m in ("root cause", "caused by", "due to",
                                         "attributed to", "investigation", "finding")):
                idx = max(0, CONF_LABELS.index(base_conf) - 1)
                conf = CONF_LABELS[idx]
                supporting += (f" Corroborated by {e.get('source_name','a relevant source')} "
                               "discussing this factor for this recall.")
                escalated = True
                break

        # lot-specific recalls make the isolated-batch hypothesis more plausible.
        if ("batch" in name.lower() or "lot-specific" in name.lower()) and lot_specific:
            conf = "Moderate"
            supporting = ("The recall record indicates it is lot/batch-specific "
                          "(supports an isolated-batch explanation).")

        # The mandatory gap statement (req 6): nothing ties this to THIS lot unless
        # an accepted causal source did.
        if not escalated:
            against = ("No source links this factor to this specific recalled "
                       "lot; scientific/plausibility only — requires validation.")
        else:
            against = "Still requires independent validation of scope and recurrence."

        graded.append({
            "hypothesis": name,
            "confidence": conf,
            "supporting": supporting,
            "against": against,
            "bd_relevance": bd_rel,
        })
    graded.sort(key=lambda h: _rank_conf(h["confidence"]), reverse=True)
    return graded


def evidence_gaps(opp: dict) -> list[str]:
    """Enumerate the authoritative sources we did NOT find, so the gap is explicit.
    Only ACCEPTED, relevant evidence counts — rejected corroboration (unrelated
    pages, indication text) cannot close a gap."""
    have = set()
    # Relevant text = recall record + accepted direct/product-firm corroboration.
    rel_parts = []
    for e in opp.get("evidence", []):
        cat = e.get("source_category")
        stype = e.get("source_type")
        is_corro = e.get("corroboration")
        accepted_relevant = (not is_corro) or e.get("evidence_class") in (
            "direct_recall_evidence", "same_product_firm", "same_firm_quality_history")
        if not accepted_relevant:
            continue
        rel_parts.append(_norm(e.get("title", "")) + " " + _norm(e.get("english_summary", "")))
        if stype == "recall":
            have.add("recall")
        if cat == "regulatory":
            have.add("regulatory")
        if cat == "company" and (e.get("entities") or {}).get("event_type"):
            have.add("company")
        if cat == "trial":
            have.add("trial")
        # Only count a warning letter / inspection if it's an ACCEPTED relevant source.
        title_sum = _norm(e.get("title", "")) + " " + _norm(e.get("english_summary", ""))
        if "warning letter" in title_sum and (not is_corro or e.get("evidence_class")
                                              == "direct_recall_evidence"):
            have.add("warning_letter")
        if ("483" in title_sum or "inspection" in title_sum) and (
                not is_corro or e.get("evidence_class") == "direct_recall_evidence"):
            have.add("inspection")
        if cat == "publication":
            have.add("publication")
        # Track whether ANY general molecule/formulation science context exists
        # (either retrieved literature, or the built-in molecule note).
        if e.get("evidence_class") in ("same_molecule_science",
                                       "formulation_science_background"):
            have.add("molecule_science_context")
    # The built-in molecule note also counts as general scientific context.
    if molecule_note(opp):
        have.add("molecule_science_context")
    gaps = []
    if "warning_letter" not in have:
        gaps.append("no FDA warning letter found")
    if "inspection" not in have:
        gaps.append("no FDA inspection / Form 483 finding found")
    if "company" not in have:
        gaps.append("no company statement / press release found")
    # Issue 2: don't say "no scientific literature" when we DO use molecule/
    # dosage-form context. Distinguish general context from lot-specific linkage.
    if "molecule_science_context" in have:
        gaps.append("No source was found linking the scientific mechanism to this "
                    "specific recalled lot. General molecule/formulation context "
                    "was retrieved.")
    else:
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
    # "Support" for commercial scoring means RELEVANT support only (req 9): a
    # genuine trial/company event source, OR corroboration that was ACCEPTED as
    # direct/same-product-firm. Rejected web hits and generic publications do NOT
    # count and must not lift the score.
    has_support = any(
        (e.get("source_category") == "company" and (e.get("entities") or {}).get("event_type"))
        or (e.get("source_type") == "trial" and (e.get("entities") or {}).get("event_type"))
        or (e.get("corroboration") and e.get("evidence_class") in (
            "direct_recall_evidence", "same_product_firm"))
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

    # Direct supporting source (req 10): name the openFDA enforcement record.
    direct_src = None
    for e in opp.get("evidence", []):
        if e.get("source_type") == "recall":
            rid = (e.get("entities") or {}).get("recall_fields", {}).get("recall_number") \
                or e.get("record_id")
            direct_src = f"{e.get('source_name','openFDA Enforcement/Recalls')} " \
                         f"{('record ' + rid) if rid else ''}".strip()
            break

    # Corroboration filtering debug (req 8): show accepted/rejected with reasons.
    corro_dbg = opp.get("corroboration_debug") or []
    if corro_dbg:
        rows = "\n".join(
            f"| {d['title'] or '—'} | {'✅ accepted' if d['accepted'] else '❌ rejected'} "
            f"| {d['class']} | {', '.join(d['matched_fields']) or 'none'} | {d['reason']} |"
            for d in corro_dbg[:20])
        corro_block = (
            "\n### Corroboration Filtering Debug\n"
            "Every corroboration hit and why it was accepted or rejected "
            "(only *direct recall evidence* can support the confirmed event/cause):\n\n"
            "| Source | Verdict | Evidence class | Matched fields | Reason |\n"
            "|---|---|---|---|---|\n" + rows + "\n")
    else:
        corro_block = (
            "\n### Corroboration Filtering Debug\n"
            "No external corroboration was attached (either none was searched, "
            "Tavily was disabled, or all hits were filtered out as irrelevant). "
            "The confirmed event/reason rests on the openFDA recall record only.\n")

    return f"""

## Root-Cause & Solution-Fit Intelligence

*Deeper deterministic investigation of the confirmed signal. Confidence-graded;
no root cause is asserted beyond what the evidence proves.*

- **Confirmed event:** {event}
- **Confirmed stated reason:** {stated_reason}
- {cause_line}
- **Direct supporting source:** {direct_src or 'openFDA Enforcement/Recalls record'}
- **Source link:** [regulatory / evidence record]({src_link})

{('**Molecule / dosage-form context:** ' + mol) if mol else ''}

### Root-Cause Evidence Matrix
Ranked by evidence strength — hypotheses are **not** equal. With only the recall
reason available, most remain *Plausible* pending validation.

| Hypothesis | Evidence supporting | Evidence against / missing | Confidence | BD relevance |
|---|---|---|---|---|
{hyp_rows}

{('> Underlying root cause is not publicly confirmed. The FDA record confirms the recall reason only.' if not confirmed_cause else '')}
{corro_block}
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
