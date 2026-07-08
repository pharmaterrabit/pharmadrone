"""Candidate discovery: turn raw evidence into opportunity candidates WITHOUT
depending on the LLM succeeding — but ONLY when a valid product/company/asset
target genuinely exists in the evidence.

Hard rules enforced here (quality gates):
  - A candidate MUST have a valid target: a specific product/molecule name, a
    company/sponsor/manufacturer, a trial ID, a regulatory application, or a
    recall/enforcement product. Generic scientific terms (prodrug, treatment,
    review, therapeutic targets, etc.) are NOT valid targets and are blacklisted.
  - A failure EVENT (terminated/withdrawn/recalled/discontinued) is only
    asserted when the evidence structurally proves it (a recall record, a
    trial's stopped-status/whyStopped, or a company/regulatory source stating
    the event) — NEVER because an academic paper merely mentions the word.
  - Clusters with no valid target are classified as generic-literature and are
    NOT turned into reports.

`discover_candidates()` runs first (no LLM). `classify_cluster()` tags each
cluster as one of: valid_bd_opportunity | weak_academic_cluster |
rejected_generic_literature. Only valid_bd_opportunity becomes a report.
"""
from __future__ import annotations
import re
from collections import defaultdict

PROBLEM_KEYWORDS = [
    "poor solubility", "poor dissolution", "dissolution failure",
    "poor bioavailability", "bioavailability", "food effect", "instability",
    "degradation", "precipitation", "aggregation", "polymorphism",
    "crystallinity", "particle size", "manufacturability", "scale-up",
    "cmc deficienc", "reproducibility", "impurit", "sterility",
    "contamination", "excipient incompatibility", "container closure",
    "packaging defect", "leachable", "extractable", "cold-chain",
    "shelf-life", "delivery failure", "bioequivalence", "reformulation",
    "recall", "dose burden", "adherence burden",
]

FAILURE_EVENT_KEYWORDS = [
    "terminated", "withdrawn", "recall", "recalled", "discontinued",
    "rejected", "complete response letter", "suspended", "clinical hold",
    "deprioritised", "deprioritized",
]

# Source types whose evidence can STRUCTURALLY confirm a failure event.
# Academic papers (source_type "paper") can NEVER confirm an event on their own.
EVENT_CONFIRMING_TYPES = {"recall", "trial", "label"}
EVENT_CONFIRMING_CATEGORIES = {"regulatory", "company", "trial"}

# Non-target terms that must never be treated as a product/company entity.
TARGET_BLACKLIST = {
    "prodrug", "prodrugs", "treatment", "treatments", "review",
    "narrative review", "systematic review", "therapeutic target",
    "therapeutic targets", "pharmacological mechanism",
    "pharmacological mechanisms", "emerging approach", "emerging approaches",
    "off-label", "off label", "trial site terminated", "patient", "patients",
    "disease", "diseases", "formulation", "bioavailability", "solubility",
    "dissolution", "mechanism", "mechanisms", "strategy", "strategies",
    "approach", "approaches", "therapy", "therapies", "efficacy", "safety",
    "pharmacokinetics", "pharmacodynamics", "clinical trial", "in vitro",
    "in vivo", "drug delivery", "nanoparticle", "nanoparticles", "background",
    "introduction", "conclusion", "overview", "perspective", "perspectives",
    "advances", "development", "developments", "application", "applications",
    "molecule", "molecules", "compound", "compounds", "drug", "drugs",
    "tablet", "tablets", "capsule", "injectable", "oral", "topical",
}

_TITLE_CASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9\-]{2,}(?:\s+[A-Z][a-zA-Z0-9\-]{2,}){0,2})\b")
_STOPWORDS = {"The", "This", "That", "These", "Those", "Study", "Trial", "Drug",
             "Phase", "FDA", "EMA", "Report", "Results", "Effect", "Effects",
             "Some", "Many", "New", "Recent", "Latest", "Current", "Various",
             "Several", "Article", "News", "Update", "Overview", "Analysis",
             "Industry", "Market", "General", "Company", "Companies", "Product",
             "Products", "Data", "Review", "Summary", "Prodrug", "Prodrugs",
             "Treatment", "Treatments", "Therapy", "Therapies", "Approaches",
             "Approach", "Mechanism", "Mechanisms", "Strategies", "Strategy",
             "Emerging", "Narrative", "Systematic", "Therapeutic", "Targets",
             "Target", "Formulation", "Bioavailability", "Solubility",
             "Patients", "Patient", "Disease", "Diseases", "Advances",
             "Development", "Perspective", "Perspectives", "Background",
             "Introduction", "Conclusion", "Pharmacological", "Off-label"}


def is_blacklisted_target(name: str | None) -> bool:
    if not name:
        return True
    n = name.strip().lower()
    if n in TARGET_BLACKLIST:
        return True
    # single generic word that is a blacklisted stem
    if n in {w.lower() for w in _STOPWORDS}:
        return True
    return False


def guess_problem_category(text: str) -> str | None:
    t = (text or "").lower()
    for kw in PROBLEM_KEYWORDS:
        if kw in t:
            return kw
    return None


def event_mentioned(text: str) -> str | None:
    """A failure word merely APPEARS in text. This is NOT confirmation of an
    event — an academic paper discussing terminations will match. Used only for
    background classification, never to assert a real event."""
    t = (text or "").lower()
    for kw in FAILURE_EVENT_KEYWORDS:
        if kw in t:
            return kw
    return None


def confirmed_event(cluster: list[dict]) -> str | None:
    """Return a failure event ONLY when a source structurally proves it:
      - a recall/enforcement record (source_type 'recall'),
      - a trial whose evidence carries a stopped-status entity, or
      - a regulatory/company source that states the event in its text.
    Academic papers ('paper') can never confirm an event on their own.
    """
    for e in cluster:
        stype = e.get("source_type")
        scat = e.get("source_category")
        ent = e.get("entities") or {}
        if stype == "recall":
            return "recall"
        if stype == "trial" and ent.get("event_type") and (
                "terminat" in str(ent.get("event_type")).lower()
                or "withdraw" in str(ent.get("event_type")).lower()
                or "suspend" in str(ent.get("event_type")).lower()):
            return str(ent.get("event_type"))
        if scat in ("regulatory", "company"):
            ev = event_mentioned(e.get("raw_text", "") + " " + e.get("title", ""))
            if ev:
                return ev
    return None


def valid_target(e_or_entities: dict) -> dict | None:
    """Extract a VALID target from an evidence item's structured entities.
    Returns {type, name} or None. Generic/blacklisted names are rejected."""
    ent = e_or_entities.get("entities", e_or_entities) or {}
    company = ent.get("company")
    product = ent.get("product")
    trial_id = ent.get("trial_id")
    if company and not is_blacklisted_target(company):
        return {"type": "company", "name": company}
    if product and not is_blacklisted_target(product):
        return {"type": "product", "name": product}
    if trial_id:
        return {"type": "trial_id", "name": trial_id}
    return None


def _title_entities(title: str, limit: int = 2) -> list[str]:
    """Rough proper-noun guesser for sources without structured entities.
    Blacklisted/generic terms are dropped, so academic titles like
    'Prodrug strategies for treatment' yield nothing."""
    found = []
    for m in _TITLE_CASE_RE.finditer(title or ""):
        cand = m.group(1).strip()
        if (cand not in _STOPWORDS and len(cand) > 3
                and not is_blacklisted_target(cand)):
            found.append(cand)
        if len(found) >= limit:
            break
    return found


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def cluster_key(e: dict) -> str | None:
    """Cluster key ONLY from a valid target. No valid target -> no key ->
    the item is not clustered into an opportunity candidate."""
    tgt = valid_target(e)
    if tgt:
        return f"{tgt['type']}|{_norm(tgt['name'])}"
    # Weak fallback: a non-blacklisted proper noun in the title.
    guesses = _title_entities(e.get("title", ""))
    if guesses:
        return f"guess|{_norm('-'.join(guesses))}"
    return None


def cluster_evidence(evidence: list[dict]) -> dict[str, list[dict]]:
    clusters = defaultdict(list)
    for e in evidence:
        k = cluster_key(e)
        if k:
            clusters[k].append(e)
    return dict(clusters)


def classify_signal_status(cluster: list[dict]) -> str:
    cats = {e.get("source_category") for e in cluster}
    has_problem = any(guess_problem_category(e.get("raw_text", "") + " "
                      + e.get("title", "")) for e in cluster)
    if confirmed_event(cluster) and ("regulatory" in cats or "company" in cats
                                     or "trial" in cats):
        return "confirmed" if has_problem else "indirect"
    if ("regulatory" in cats or "company" in cats or "trial" in cats) and has_problem:
        return "indirect"
    if has_problem:
        return "weak"
    return "needs_verification"


def dedup_evidence(cluster: list[dict]) -> list[dict]:
    """Deduplicate evidence by URL, then DOI/PMID/PMCID/record_id, then title,
    so the same paper/link can never appear multiple times in one report."""
    seen: set = set()
    out = []
    for e in cluster:
        url = (e.get("url") or "").strip().lower().rstrip("/")
        rid = (e.get("record_id") or "").strip().lower()
        title = re.sub(r"\s+", " ", (e.get("title") or "").strip().lower())
        keys = [k for k in (f"url:{url}" if url else None,
                            f"id:{rid}" if rid else None,
                            f"title:{title}" if title else None) if k]
        if any(k in seen for k in keys):
            continue
        for k in keys:
            seen.add(k)
        out.append(e)
    return out


def classify_cluster(cluster: list[dict]) -> dict:
    """Classify a cluster into one of three classes and explain why.

    Returns {class, valid_target, event, event_confirmed, has_bd_source,
             source_categories, reason}. Only 'valid_bd_opportunity' should
    become a report.
    """
    cluster = dedup_evidence(cluster)
    cats = sorted({e.get("source_category") for e in cluster if e.get("source_category")})
    tgt = None
    for e in cluster:
        tgt = valid_target(e)
        if tgt:
            break
    event = confirmed_event(cluster)
    has_bd_source = bool(EVENT_CONFIRMING_CATEGORIES & set(cats))
    only_academic = set(cats) <= {"publication"} or all(
        e.get("source_type") == "paper" for e in cluster)

    if not tgt:
        cls, reason = ("rejected_generic_literature",
                       "no valid product/company/trial/recall target found — "
                       "generic literature or non-target terms only")
    elif only_academic and not event:
        cls, reason = ("weak_academic_cluster",
                       "has a target but only academic/mechanistic evidence and "
                       "no confirmed failure event — technical background, not a "
                       "BD opportunity")
    else:
        cls, reason = ("valid_bd_opportunity",
                       f"valid {tgt['type']} target"
                       + (f"; confirmed event: {event}" if event
                          else "; problem signal present")
                       + f"; sources: {', '.join(cats) or 'none'}")

    return {"class": cls, "valid_target": tgt, "event": event,
            "event_confirmed": bool(event), "has_bd_source": has_bd_source,
            "source_categories": cats, "only_academic": only_academic,
            "reason": reason, "deduped_evidence": cluster}


def _to_evidence_entry(e: dict) -> dict:
    return {
        "source_type": e.get("source_type"),
        "source_category": e.get("source_category"),
        "source_name": e.get("source_name"),
        "record_id": e.get("record_id"),
        "title": e.get("title"),
        "url": e.get("url"),
        "language": e.get("language", "en"),
        "english_summary": (e.get("raw_text") or "")[:400],
        "date_accessed": e.get("date_accessed"),
        "supports": None,
        "does_not_prove": None,
    }


def _candidate_from_cluster(key: str, cluster: list[dict], provisional: bool,
                            classification: dict | None = None) -> dict | None:
    """Build a candidate ONLY from a valid_bd_opportunity cluster. Returns None
    for generic/academic clusters so they never become reports."""
    cls = classification or classify_cluster(cluster)
    if cls["class"] != "valid_bd_opportunity":
        return None
    cluster = cls["deduped_evidence"]
    tgt = cls["valid_target"]
    event = cls["event"]  # only a CONFIRMED event, or None

    ent = next((e.get("entities") or {} for e in cluster if valid_target(e)), {})
    combined_text = " ".join((e.get("raw_text", "") + " " + e.get("title", ""))
                             for e in cluster)
    problem = guess_problem_category(combined_text)
    status = classify_signal_status(cluster)
    region = next((e.get("region_hint") for e in cluster if e.get("region_hint")), None)
    sources = sorted({e.get("source_name", "") for e in cluster})
    n_ev = len(cluster)

    company = ent.get("company") if not is_blacklisted_target(ent.get("company")) else None
    product = ent.get("product") if not is_blacklisted_target(ent.get("product")) else None
    if not company and not product:
        # target came from trial_id
        product = tgt["name"] if tgt["type"] != "company" else None
        company = tgt["name"] if tgt["type"] == "company" else company

    is_failure = bool(event)  # ONLY a structurally-confirmed event counts

    opp = {
        "company": company,
        "product": product or tgt["name"],
        "region": region,
        "stage": None,
        "problem_signal": problem,
        "problem_category": problem,
        "event_type": event,  # None unless confirmed
        "signal_status": status,
        "confidence": "medium" if status == "confirmed" else "low",
        "valid_target_type": tgt["type"],
        "confirmed_fact": (f"{n_ev} distinct evidence item(s) from "
                          f"{', '.join(sources)} reference this {tgt['type']}"
                          + (f"; a failure event ({event}) is confirmed by a "
                             f"{'recall' if event=='recall' else 'regulatory/company/trial'} "
                             "source" if event else "")
                          + (f"; problem signal: '{problem}'" if problem else "") + "."),
        "interpretation": "Deterministic clustering (no LLM) — requires human validation.",
        "why_scientific": (f"Sources reference '{problem}'." if problem
                          else "Mechanism not established from these snippets alone."),
        "why_commercial": ("Possible BD signal pending validation." if (problem or event)
                          else "Relevance unclear — verify before any outreach."),
        "rescue_strategy": "To be defined after validation." if (problem or event) else None,
        "next_action": "validate",
        "red_flags": (["provisional candidate — generated by deterministic clustering, "
                       "not full LLM synthesis; verify manually before any outreach"]
                      if provisional else []),
        "failure": is_failure,
        "failure_reason": event,
        "failure_event_confirmed": bool(event),
        "discovery_method": "deterministic-cluster",
        "discovery_reason": cls["reason"],
        "provisional": provisional,
        "evidence": [_to_evidence_entry(e) for e in cluster],
    }
    return opp


def discover_candidates(evidence: list[dict], min_cluster_evidence: int = 1
                        ) -> tuple[list[dict], dict]:
    """First-pass deterministic candidates (no LLM). Returns (candidates, breakdown).

    Only clusters classified `valid_bd_opportunity` — i.e. with a valid
    product/company/trial target — become candidates. Weak-academic and
    generic-literature clusters are counted in the breakdown but NEVER turned
    into reports.
    """
    clusters = cluster_evidence(evidence)
    out = []
    breakdown = {"valid_bd_opportunity": 0, "weak_academic_cluster": 0,
                 "rejected_generic_literature": 0, "unclustered_generic": 0,
                 "discarded_examples": []}

    # Evidence that produced NO cluster key at all (no valid target and no
    # non-blacklisted proper noun) is generic literature — count it so it is
    # visible in the debug breakdown rather than silently dropped.
    clustered_urls = {e.get("url") for c in clusters.values() for e in c}
    for e in evidence:
        if e.get("url") not in clustered_urls:
            breakdown["unclustered_generic"] += 1
            breakdown["rejected_generic_literature"] += 1
            if len(breakdown["discarded_examples"]) < 8:
                breakdown["discarded_examples"].append({
                    "class": "rejected_generic_literature",
                    "title": (e.get("title") or "")[:80],
                    "sources": [e.get("source_category")],
                    "reason": "no valid product/company/trial target and no "
                              "identifiable proper noun — generic literature",
                })

    for key, cluster in clusters.items():
        if len(cluster) < min_cluster_evidence:
            continue
        cls = classify_cluster(cluster)
        breakdown[cls["class"]] += 1
        if cls["class"] == "valid_bd_opportunity":
            cand = _candidate_from_cluster(key, cluster, provisional=False,
                                           classification=cls)
            if cand:
                out.append(cand)
        elif len(breakdown["discarded_examples"]) < 8:
            breakdown["discarded_examples"].append({
                "class": cls["class"],
                "title": (cluster[0].get("title") or "")[:80],
                "sources": cls["source_categories"],
                "reason": cls["reason"],
            })
    return out, breakdown


def build_fallback_candidates(evidence: list[dict], existing_count: int,
                              min_total: int = 3, max_total: int = 5,
                              min_raw_evidence: int = 20) -> tuple[list[dict], dict]:
    """Conservative fallback. Returns (candidates, info).

    CRITICAL: a fallback candidate is created ONLY from a `valid_bd_opportunity`
    cluster (valid target + real signal). If no such cluster exists, NOTHING is
    fabricated — the run honestly ends with fewer (or zero) reports, and `info`
    explains why. This replaces the old behaviour that invented
    "Unknown company — prodrug" style candidates from generic literature.
    """
    info = {"triggered": False, "reason": "", "valid_available": 0,
            "generated": 0}
    if len(evidence) < min_raw_evidence:
        info["reason"] = (f"raw evidence {len(evidence)} < {min_raw_evidence} "
                          "threshold — no fallback attempted")
        return [], info
    if existing_count >= min_total:
        info["reason"] = "enough candidates already; fallback not needed"
        return [], info

    info["triggered"] = True
    target = min_total - existing_count

    clusters = cluster_evidence(evidence)
    # Only clusters that pass the valid-target gate are eligible.
    valid = []
    for key, cluster in clusters.items():
        cls = classify_cluster(cluster)
        if cls["class"] == "valid_bd_opportunity":
            valid.append((key, cluster, cls))
    info["valid_available"] = len(valid)

    def _rank(item):
        _, cluster, cls = item
        return (1 if cls["event_confirmed"] else 0,
                1 if "regulatory" in cls["source_categories"] else 0,
                1 if "company" in cls["source_categories"] else 0,
                len(cls["deduped_evidence"]))

    valid.sort(key=_rank, reverse=True)
    out = []
    for key, cluster, cls in valid[:min(target, max_total)]:
        cand = _candidate_from_cluster(key, cluster, provisional=True,
                                       classification=cls)
        if cand:
            out.append(cand)

    info["generated"] = len(out)
    if not out:
        info["reason"] = ("no valid product/company/trial/recall target in any "
                          "cluster — generic literature only; no BD reports "
                          "generated (this is correct, not a failure)")
    else:
        info["reason"] = (f"{len(out)} provisional candidate(s) from valid-target "
                          "clusters only")
    return out, info


def top_entities(evidence: list[dict], n: int = 10) -> list[dict]:
    """For the debug panel: most frequently mentioned VALID product/company
    names across all evidence (blacklisted/generic terms excluded)."""
    counts: dict[str, int] = defaultdict(int)
    for e in evidence:
        ent = e.get("entities") or {}
        for val in (ent.get("company"), ent.get("product")):
            if val and not is_blacklisted_target(val):
                counts[val] += 1
        if not ent.get("company") and not ent.get("product"):
            for g in _title_entities(e.get("title", "")):
                counts[g] += 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [{"name": name, "mentions": c} for name, c in ranked]
