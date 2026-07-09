"""Rule-based deterministic BD interpretation.

When the LLM is unavailable (rate-limited / circuit-breaker tripped), the report
writer uses these rules so a deterministic report is still a useful BD screening
memo rather than a placeholder.

Every function here is pure and evidence-disciplined: it maps a *confirmed*
problem category (e.g. the recall reason) to cautious, clearly-labelled
interpretation. It never invents a root cause beyond the recall reason — it
enumerates POSSIBLE opportunity types and validation steps using hedged language
("may indicate", "could suggest", "requires validation").
"""
from __future__ import annotations

# Canonical problem buckets. Each raw problem phrase maps to one bucket key.
_BUCKET_ALIASES = {
    "dissolution failure": "dissolution",
    "failed dissolution": "dissolution",
    "dissolution": "dissolution",
    "stability": "stability",
    "degradation": "stability",
    "impurity": "impurity",
    "impurities": "impurity",
    "degradation products": "impurity",
    "sterility": "sterility",
    "particulate matter": "sterility",
    "particulate": "sterility",
    "contamination": "sterility",
    "microbial contamination": "sterility",
    "packaging defect": "packaging",
    "container closure": "packaging",
    "leakage": "packaging",
    "labeling mix-up": "packaging",
    "label mix-up": "packaging",
    "subpotent": "potency",
    "superpotent": "potency",
    "assay": "potency",
    "cgmp": "gmp",
    "failed specifications": "gmp",
    "out of specification": "gmp",
    "failed release testing": "gmp",
    "manufacturing defect": "gmp",
    "crystallization": "solidstate",
    "precipitation": "solidstate",
}

# Per-bucket rule content. Language is deliberately hedged.
_RULES = {
    "dissolution": {
        "label": "dissolution failure",
        "interpretation": ("may indicate a dissolution, formulation, "
            "manufacturing, QC, or release-testing / batch-performance issue"),
        "partners": ["formulation development CDMO", "dissolution testing specialist",
                     "analytical/QC laboratory", "CMC consultant",
                     "excipient/formulation technology company",
                     "manufacturing troubleshooting partner"],
        "rescue": [
            "verify the dissolution method and specifications",
            "assess API particle size, polymorph, and solid-state form",
            "review excipient compatibility and capsule/tablet performance",
            "review the manufacturing process and batch-to-batch variability",
            "evaluate reformulation or process optimisation if the issue is recurring",
        ],
    },
    "stability": {
        "label": "degradation / stability",
        "interpretation": ("could suggest a stability, formulation, packaging, "
            "storage, or shelf-life opportunity"),
        "partners": ["stability testing provider", "formulation development CDMO",
                     "packaging/container-closure specialist", "CMC consultant",
                     "analytical/QC laboratory"],
        "rescue": [
            "review the stability protocol and storage conditions",
            "assess degradation pathway and degradant identification",
            "evaluate packaging/container-closure protection (moisture, oxygen, light)",
            "review formulation robustness and excipient selection",
            "consider reformulation or protective packaging if degradation is recurring",
        ],
    },
    "impurity": {
        "label": "impurity / degradation products",
        "interpretation": ("may indicate a CMC, analytical, process-control, "
            "supplier, or manufacturing opportunity"),
        "partners": ["analytical/QC laboratory", "CMC consultant",
                     "regulatory CMC consultant", "manufacturing troubleshooting partner",
                     "excipient/formulation technology company"],
        "rescue": [
            "identify and characterise the impurity / degradant",
            "review the analytical method and impurity specifications",
            "assess API supplier and route-of-synthesis process controls",
            "review manufacturing process parameters and in-process controls",
            "evaluate process or analytical remediation if the issue is systematic",
        ],
    },
    "sterility": {
        "label": "sterility / particulate / contamination",
        "interpretation": ("could suggest a sterile-manufacturing, aseptic-process, "
            "filtration, inspection, or quality-system opportunity"),
        "partners": ["sterile/aseptic manufacturing partner",
                     "quality-system consultant", "analytical/QC laboratory",
                     "manufacturing troubleshooting partner",
                     "packaging/container-closure specialist"],
        "rescue": [
            "review the aseptic process and media-fill / sterility assurance data",
            "assess filtration, environmental monitoring, and containment",
            "review particulate sources (components, process, container closure)",
            "evaluate visual inspection and in-process controls",
            "consider quality-system or process remediation if recurring",
        ],
    },
    "packaging": {
        "label": "packaging defect / container closure / leakage",
        "interpretation": ("may indicate a packaging, container-closure integrity "
            "(CCIT), labelling, or device/packaging opportunity"),
        "partners": ["packaging/container-closure specialist",
                     "container-closure integrity (CCIT) testing provider",
                     "labelling/artwork specialist", "CMC consultant",
                     "manufacturing troubleshooting partner"],
        "rescue": [
            "review container-closure integrity (CCIT) data and method",
            "assess packaging components and seal/closure design",
            "review labelling and artwork controls if a mix-up is implicated",
            "evaluate line/handling steps that could cause leakage or defects",
            "consider a packaging redesign or CCIT programme if recurring",
        ],
    },
    "potency": {
        "label": "subpotent / superpotent / assay",
        "interpretation": ("could suggest an assay, content-uniformity, "
            "manufacturing-control, stability, or release-testing opportunity"),
        "partners": ["analytical/QC laboratory", "CMC consultant",
                     "manufacturing troubleshooting partner",
                     "formulation development CDMO", "stability testing provider"],
        "rescue": [
            "verify the assay method and content-uniformity data",
            "review blend uniformity and manufacturing process controls",
            "assess API potency, overage, and stability contribution",
            "review release-testing specifications and sampling",
            "evaluate process or analytical remediation if systematic",
        ],
    },
    "gmp": {
        "label": "cGMP / failed specifications",
        "interpretation": ("may indicate a quality-system, manufacturing, "
            "analytical, or CMC-remediation opportunity"),
        "partners": ["quality-system consultant", "regulatory CMC consultant",
                     "CMC consultant", "manufacturing troubleshooting partner",
                     "analytical/QC laboratory"],
        "rescue": [
            "identify which specification(s) or GMP area failed",
            "review the quality system, deviations, and CAPA history",
            "assess manufacturing process capability and controls",
            "review analytical methods and data integrity",
            "consider a broader CMC/quality remediation if systemic",
        ],
    },
    "solidstate": {
        "label": "crystallization / precipitation (solid-state)",
        "interpretation": ("could suggest a solid-state, formulation, or "
            "manufacturing-process opportunity"),
        "partners": ["formulation development CDMO",
                     "excipient/formulation technology company",
                     "analytical/QC laboratory", "CMC consultant",
                     "solid-state / particle-engineering specialist"],
        "rescue": [
            "characterise the solid-state form (polymorph, salt, hydrate)",
            "assess crystallization / precipitation conditions in process and product",
            "review formulation and excipient stabilisation of the form",
            "evaluate particle engineering or process control options",
            "consider reformulation or solid-state optimisation if recurring",
        ],
    },
}

# Roles to contact (req 7) — generic but role-specific, shown for every recall.
CONTACT_ROLES = [
    "Head of Quality / QA",
    "CMC lead",
    "Manufacturing operations lead",
    "Regulatory CMC lead",
    "Business development / external innovation lead",
    "Product lifecycle management lead",
    "Supplier quality lead (if a supplier/API issue is implicated)",
]

_DEFAULT = {
    "label": "product-quality signal",
    "interpretation": ("may indicate a product-quality issue that could map to a "
        "formulation, manufacturing, analytical, or CMC opportunity"),
    "partners": ["formulation development CDMO", "analytical/QC laboratory",
                 "CMC consultant", "manufacturing troubleshooting partner"],
    "rescue": [
        "confirm the exact nature of the quality issue from the recall reason",
        "assess whether it is formulation-, process-, analytical-, or supplier-related",
        "review manufacturing and QC controls for the affected product",
        "evaluate targeted remediation if the issue is recurring or strategic",
    ],
}


def bucket_for(problem: str | None) -> str | None:
    if not problem:
        return None
    p = problem.strip().lower()
    if p in _BUCKET_ALIASES:
        return _BUCKET_ALIASES[p]
    for alias, bucket in _BUCKET_ALIASES.items():
        if alias in p:
            return bucket
    return None


def rules_for(problem: str | None) -> dict:
    """Return the rule content dict for a problem category (never raises)."""
    bucket = bucket_for(problem)
    return _RULES.get(bucket, _DEFAULT)


def interpretation(problem: str | None) -> str:
    return rules_for(problem)["interpretation"]


def partners(problem: str | None) -> list[str]:
    return rules_for(problem)["partners"]


def rescue_steps(problem: str | None) -> list[str]:
    return rules_for(problem)["rescue"]


def outreach_angle(problem: str | None) -> str:
    """Cautious, validation-first outreach angle (req 8)."""
    r = rules_for(problem)
    label = r["label"]
    return (f"This recall indicates a confirmed product-quality signal involving "
            f"{label}. Before any outreach, validate whether the issue was isolated "
            "to one lot, one supplier, or one manufacturing site, or whether it is a "
            "recurring formulation / manufacturing problem. If repeated or "
            "strategically relevant, this may create an opportunity for "
            + _partners_phrase(r["partners"]) + ". Do not initiate outreach until "
            "the recall record and its scope have been validated.")


def _partners_phrase(partners_list: list[str]) -> str:
    # turn a partner list into a readable clause of opportunity types
    types = {
        "formulation development CDMO": "formulation troubleshooting",
        "dissolution testing specialist": "dissolution method support",
        "analytical/QC laboratory": "analytical/QC work",
        "CMC consultant": "CMC remediation",
        "manufacturing troubleshooting partner": "manufacturing process support",
        "stability testing provider": "stability support",
        "packaging/container-closure specialist": "packaging / container-closure support",
    }
    phrases = []
    for p in partners_list:
        phrases.append(types.get(p, p))
    # de-dup preserving order
    seen, uniq = set(), []
    for x in phrases:
        if x not in seen:
            seen.add(x); uniq.append(x)
    if len(uniq) > 4:
        uniq = uniq[:4]
    if len(uniq) == 1:
        return uniq[0]
    return ", ".join(uniq[:-1]) + ", or " + uniq[-1]
