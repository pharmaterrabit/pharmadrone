"""Generate global + multilingual search queries from the Technology Profile."""
from __future__ import annotations
from .. import llm

# Minimal built-in translations so multilingual discovery works even without an
# LLM call. The LLM path (build_llm_queries) produces richer, native-language terms.
SIGNAL_TERMS = {
    "en": ["poorly soluble drug", "low oral bioavailability", "food effect",
           "reformulation", "bioavailability enhancement"],
    "ja": ["難溶性 医薬品", "経口 バイオアベイラビリティ", "食事の影響", "製剤 改良"],
    "zh": ["难溶性 药物", "口服 生物利用度", "食物影响", "制剂 改良"],
    "ar": ["دواء ضعيف الذوبان", "التوافر الحيوي الفموي", "تأثير الطعام"],
    "pt": ["fármaco pouco solúvel", "biodisponibilidade oral", "efeito do alimento"],
    "de": ["schwer lösliches Arzneimittel", "orale Bioverfügbarkeit"],
    "ko": ["난용성 의약품", "경구 생체이용률", "음식 효과"],
}


def build_basic_queries(profile: dict) -> list[dict]:
    """Deterministic query set (no LLM cost). Returns {query, region, lang}."""
    queries = []
    signals = profile.get("problem_signals", [])[:6]
    for region in [r for r in profile["regions"] if r.get("active")]:
        lang = region.get("lang", "en")
        # English structured-source queries (trials/labels/papers are English-indexed)
        for sig in signals:
            queries.append({"query": f"{sig} small molecule oral",
                            "region": region["name"], "lang": "en"})
        # Native-language web-discovery queries for local company/press signal
        for term in SIGNAL_TERMS.get(lang, [])[:3]:
            queries.append({"query": f'{term} {region["name"]}',
                            "region": region["name"], "lang": lang})
    # de-dup identical query strings
    seen, uniq = set(), []
    for q in queries:
        k = (q["query"], q["region"])
        if k not in seen:
            seen.add(k)
            uniq.append(q)
    return uniq


def build_llm_queries(profile: dict, cost, max_per_region: int = 4) -> list[dict]:
    """Ask the LLM for sharper native-language queries. Costs a few tokens."""
    regions = [r["name"] + f" ({r.get('lang','en')})"
               for r in profile["regions"] if r.get("active")]
    signals = ", ".join(profile.get("problem_signals", [])[:8])
    prompt = (
        "You generate web/database search queries for pharma business-development "
        "scouting. Seller focus: formulation / drug-delivery / CDMO for poorly "
        f"soluble small molecules.\nProblem signals: {signals}.\n"
        f"Regions (with language): {', '.join(regions)}.\n"
        f"For each region produce up to {max_per_region} search queries. Use the "
        "region's native language for local company/press discovery and English "
        "for scientific sources. Return a JSON list of objects with keys "
        "'query', 'region', 'lang'."
    )
    try:
        result = llm.complete_json(prompt, cost)
        if isinstance(result, list):
            return [q for q in result if q.get("query")]
    except Exception:
        pass
    return build_basic_queries(profile)
