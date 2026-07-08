"""Candidate discovery: turn raw evidence into opportunity candidates WITHOUT
depending on the LLM succeeding.

Why this exists: the LLM-based extraction step (pipeline/extract.py,
pipeline/failure_signal.py) can legitimately return zero candidates if the
configured model is flaky, rate-limited, or bad at strict JSON (common with
free OpenRouter models) — and previously that failure was swallowed silently,
producing 0 reports from 84 evidence items with no explanation.

This module fixes that at the architecture level:
  1. `discover_candidates()` — a deterministic first pass that clusters
     evidence by the structured `entities` connectors already provide
     (trial sponsor, recall firm, label brand/generic) or, failing that, by
     simple keyword heuristics on the title/text. No LLM call required.
  2. `build_fallback_candidates()` — if, after LLM extraction, the total
     candidate count is still below a floor and raw evidence is substantial,
     force 3-5 provisional candidates from the strongest evidence clusters,
     clearly labelled confirmed/indirect/weak/needs_verification.

Every candidate produced here carries `discovery_method` so the debug panel
and reports can show exactly how it was found.
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
    "deprioritised", "deprioritized", "delayed",
]

_TITLE_CASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9\-]{2,}(?:\s+[A-Z][a-zA-Z0-9\-]{2,}){0,2})\b")
_STOPWORDS = {"The", "This", "That", "These", "Those", "Study", "Trial", "Drug",
             "Phase", "FDA", "EMA", "Report", "Results", "Effect", "Effects",
             "Some", "Many", "New", "Recent", "Latest", "Current", "Various",
             "Several", "Article", "News", "Update", "Overview", "Analysis",
             "Industry", "Market", "General", "Company", "Companies", "Product",
             "Products", "Data", "Review", "Summary"}


def guess_problem_category(text: str) -> str | None:
    t = (text or "").lower()
    for kw in PROBLEM_KEYWORDS:
        if kw in t:
            return kw
    return None


def guess_event_type(text: str) -> str | None:
    t = (text or "").lower()
    for kw in FAILURE_EVENT_KEYWORDS:
        if kw in t:
            return kw
    return None


def _title_entities(title: str, limit: int = 2) -> list[str]:
    """Very rough proper-noun guesser for sources without structured entities
    (Tavily/Europe PMC/OpenAlex/Crossref). Not a substitute for the LLM — just
    enough to give a fallback cluster key when nothing else is available."""
    found = []
    for m in _TITLE_CASE_RE.finditer(title or ""):
        cand = m.group(1).strip()
        if cand not in _STOPWORDS and len(cand) > 3:
            found.append(cand)
        if len(found) >= limit:
            break
    return found


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def cluster_key(e: dict) -> str | None:
    ent = e.get("entities") or {}
    if ent.get("company") or ent.get("product"):
        return f"{_norm(ent.get('company'))}|{_norm(ent.get('product'))}"
    if ent.get("trial_id"):
        return f"trial|{ent['trial_id']}"
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
    if "regulatory" in cats and len(cluster) >= 2 and has_problem:
        return "confirmed"
    if ("regulatory" in cats or "company" in cats or "trial" in cats) and has_problem:
        return "indirect"
    if has_problem:
        return "weak"
    return "needs_verification"


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


def _candidate_from_cluster(key: str, cluster: list[dict], provisional: bool) -> dict:
    ent = next((e.get("entities") or {} for e in cluster if e.get("entities")), {})
    combined_text = " ".join((e.get("raw_text", "") + " " + e.get("title", ""))
                             for e in cluster)
    problem = guess_problem_category(combined_text)
    event = guess_event_type(combined_text)
    status = classify_signal_status(cluster)
    region = next((e.get("region_hint") for e in cluster if e.get("region_hint")), None)
    sources = sorted({e.get("source_name", "") for e in cluster})
    n_ev = len(cluster)

    is_failure = bool(event) or any(e.get("source_type") == "recall" for e in cluster)

    opp = {
        "company": ent.get("company"),
        "product": ent.get("product") or (key.split("|", 1)[-1] if "|" in key else key),
        "region": region,
        "stage": None,
        "problem_signal": problem,
        "problem_category": problem,
        "event_type": event,
        "signal_status": status,
        "confidence": "medium" if status == "confirmed" else "low",
        "confirmed_fact": (f"{n_ev} evidence item(s) from {', '.join(sources)} "
                          f"reference this entity"
                          + (f" in relation to '{problem}'" if problem else "") + "."),
        "interpretation": "Deterministic clustering (no LLM) — requires human validation.",
        "why_scientific": (f"Multiple sources mention '{problem}'." if problem
                          else "Mechanism not established from these snippets alone."),
        "why_commercial": ("Possible BD signal pending validation." if problem
                          else "Relevance unclear — verify before any outreach."),
        "rescue_strategy": "To be defined after validation." if problem else None,
        "next_action": "validate",
        "red_flags": (["provisional candidate — generated by deterministic clustering, "
                       "not full LLM synthesis; verify manually before any outreach"]
                      if provisional else []),
        "failure": is_failure,
        "failure_reason": event,
        "discovery_method": "deterministic-cluster",
        "provisional": provisional,
        "evidence": [_to_evidence_entry(e) for e in cluster],
    }
    if not opp["company"] and not opp["product"]:
        opp["product"] = key
    return opp


def discover_candidates(evidence: list[dict], min_cluster_evidence: int = 1) -> list[dict]:
    """First-pass deterministic candidates from ALL evidence (not a fallback —
    this always runs, using whatever structured entities the connectors gave us).
    Only clusters with a company/product/trial_id (i.e. NOT a bare title guess)
    are included here, to keep the main candidate pool precise; pure title
    guesses are reserved for the fallback path below.
    """
    clusters = cluster_evidence(evidence)
    out = []
    for key, cluster in clusters.items():
        if len(cluster) < min_cluster_evidence:
            continue
        if key.startswith("guess|"):
            continue  # too weak for the primary pool; may be used as fallback
        out.append(_candidate_from_cluster(key, cluster, provisional=False))
    return out


def build_fallback_candidates(evidence: list[dict], existing_count: int,
                              min_total: int = 3, max_total: int = 5,
                              min_raw_evidence: int = 20) -> list[dict]:
    """If normal extraction + primary discovery still leave us short, force a
    handful of clearly-labelled provisional candidates from the strongest
    evidence clusters (including title-guess clusters this time). If entity/
    title clustering still can't produce enough (e.g. generic titles with no
    proper nouns), fall back further to grouping the remaining unclustered
    evidence by source + region, so the 3-5 floor holds even in the worst case."""
    if len(evidence) < min_raw_evidence or existing_count >= min_total:
        return []
    target = max(min_total - existing_count, 0)
    if target <= 0:
        return []

    clusters = cluster_evidence(evidence)
    entity_clusters = {k: v for k, v in clusters.items() if not k.startswith("guess|")}
    guess_clusters = {k: v for k, v in clusters.items() if k.startswith("guess|")}

    def _rank(items):
        return sorted(items, key=lambda kv: (
            1 if any(e.get("source_category") == "regulatory" for e in kv[1]) else 0,
            1 if any(e.get("source_category") == "company" for e in kv[1]) else 0,
            len(kv[1]),
        ), reverse=True)

    # Tier 1: real entity clusters (trial sponsor, recall firm, label brand) —
    # the strongest deterministic signal.
    take = _rank(entity_clusters.items())[:min(target, max_total)]
    out = [_candidate_from_cluster(k, c, provisional=True) for k, c in take]
    consumed = list(take)  # (key, cluster) actually used, across all tiers

    # Tier 2: title-guess clusters — weaker, only used if tier 1 wasn't enough.
    if len(out) < min_total:
        used_keys = {k for k, _ in take}
        more = [(k, v) for k, v in _rank(guess_clusters.items()) if k not in used_keys]
        for k, c in more:
            if len(out) >= min_total:
                break
            out.append(_candidate_from_cluster(k, c, provisional=True))
            consumed.append((k, c))

    # Tier 3 (last resort): entity/title clustering still short (e.g. generic
    # titles with no proper nouns at all). Group remaining evidence by source +
    # region, splitting any oversized group into sub-chunks, so the floor holds
    # even in the worst case.
    if len(out) < min_total:
        used_urls = {e.get("url") for _, c in consumed for e in c}
        remaining = [e for e in evidence if e.get("url") not in used_urls]
        by_source_region: dict[str, list[dict]] = defaultdict(list)
        for e in remaining:
            key = f"{e.get('source_name','?')}|{e.get('region_hint') or 'unspecified region'}"
            by_source_region[key].append(e)

        still_needed = min_total - len(out)
        chunks: list[tuple[str, list[dict]]] = []
        for key, group in sorted(by_source_region.items(), key=lambda kv: len(kv[1]),
                                 reverse=True):
            if not group:
                continue
            # How many sub-chunks to split this group into: enough to help
            # reach still_needed, capped so each chunk keeps some evidence depth.
            n_chunks = max(1, min(still_needed, len(group) // 5 or 1))
            size = max(1, len(group) // n_chunks)
            for i in range(n_chunks):
                part = group[i * size: (i + 1) * size] if i < n_chunks - 1 else group[i * size:]
                if part:
                    chunks.append((key, part))

        for key, group in chunks:
            if len(out) >= min_total:
                break
            source_name, region = (key.split("|", 1) + ["unspecified region"])[:2]
            combined_text = " ".join((e.get("raw_text", "") + " " + e.get("title", ""))
                                     for e in group)
            problem = guess_problem_category(combined_text)
            opp = {
                "company": None,
                "product": f"Unidentified signal cluster ({source_name}, #{len(out)+1})",
                "region": region,
                "stage": None,
                "problem_signal": problem,
                "problem_category": problem,
                "event_type": guess_event_type(combined_text),
                "signal_status": "needs_verification",
                "confidence": "low",
                "confirmed_fact": (f"{len(group)} evidence item(s) from {source_name} "
                                  "retrieved for this query/region, but no distinct "
                                  "product or company name could be identified "
                                  "automatically."),
                "interpretation": ("No entity could be extracted deterministically "
                                  "and LLM extraction did not resolve one either — "
                                  "manual review of the linked evidence is required."),
                "why_scientific": "Not established — requires manual review of sources.",
                "why_commercial": "Not established — requires manual review of sources.",
                "rescue_strategy": None,
                "next_action": "validate",
                "red_flags": ["no distinct product/company name identified — this is "
                             "a raw evidence cluster, not a resolved opportunity; "
                             "manual review required before any BD action"],
                "failure": bool(guess_event_type(combined_text)),
                "failure_reason": guess_event_type(combined_text),
                "discovery_method": "deterministic-source-region-chunk",
                "provisional": True,
                "evidence": [_to_evidence_entry(e) for e in group[:10]],
            }
            out.append(opp)
    return out[:max_total]


def top_entities(evidence: list[dict], n: int = 10) -> list[dict]:
    """For the debug panel: most frequently mentioned product/company names
    across all evidence, before scoring."""
    counts: dict[str, int] = defaultdict(int)
    for e in evidence:
        ent = e.get("entities") or {}
        for val in (ent.get("company"), ent.get("product")):
            if val:
                counts[val] += 1
        if not ent.get("company") and not ent.get("product"):
            for g in _title_entities(e.get("title", "")):
                counts[g] += 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return [{"name": name, "mentions": c} for name, c in ranked]
