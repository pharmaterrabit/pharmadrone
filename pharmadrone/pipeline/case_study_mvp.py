"""Read-only, evidence-grounded first case-study workflow."""
from __future__ import annotations

import json
from typing import Any

from . import patent_discovery


CASE_TYPES = (
    "Company opportunity pitch",
    "Product rescue scan",
    "Formulation technology fit",
    "Lifecycle/patent landscape",
    "Partner/BD outreach brief",
)

THEMES = (
    "Particle properties",
    "Poor solubility",
    "Dissolution innovation",
    "Amorphous solid dispersion",
    "Modified release",
    "Bioavailability enhancement",
    "Particle size reduction",
    "Nanocrystals / nanosuspensions",
    "Cocrystals / salt forms",
    "Lipid-based formulations",
    "Spray drying",
    "Hot-melt extrusion",
)

EXAMPLE_CASES = (
    ("Pfizer", "Poor solubility"),
    ("Novartis", "Particle properties"),
    ("AstraZeneca", "Dissolution innovation"),
    ("Roche", "Amorphous solid dispersion"),
    ("Sanofi", "Modified release"),
)

COMPANY_REPORT_SECTIONS = (
    "Executive summary",
    "Target company",
    "Strategic opportunity hypothesis",
    "Why this matters for the company",
    "Evidence snapshot",
    "Company-specific signals",
    "Relevant products/APIs",
    "Technology/problem fit",
    "Patent and innovation landscape",
    "Research/grant and collaboration signals",
    "Regulatory/lifecycle context",
    "Potential pitch angle",
    "Risks and limitations",
    "Recommended outreach/validation steps",
    "Source table",
)

REPORT_SECTIONS = COMPANY_REPORT_SECTIONS

BUCKET_LIMIT = 10
CANDIDATE_LIMIT = 100
EVIDENCE_BUCKETS = (
    "opportunity index",
    "canonical products and APIs",
    "pharmaceutical problems and technology relationships",
    "stored patent and lifecycle records",
    "research grants",
    "regulatory opportunity signals",
)

QUERY_EXPANSIONS = {
    "particle properties": (
        "particle properties", "particle size", "particle morphology",
        "particle engineering", "crystal habit", "polymorph", "solid state",
        "micronization", "nanomilling", "wet milling", "dry milling",
        "nanocrystal", "nanosuspension", "spray drying", "hot-melt extrusion",
        "flowability", "powder properties", "compressibility", "dissolution",
        "solubility", "bioavailability",
    ),
    "poor solubility": (
        "poor solubility", "low solubility", "solubility", "dissolution",
        "dissolution rate", "bioavailability", "absorption", "BCS II",
        "BCS class II", "amorphous solid dispersion", "ASD", "solid dispersion",
        "nanosuspension", "particle size reduction", "micronization",
        "nanocrystal", "lipid formulation", "self-emulsifying", "SEDDS",
        "SMEDDS", "cyclodextrin", "salt form", "cocrystal", "polymorph",
        "spray drying", "hot-melt extrusion",
    ),
    "dissolution innovation": (
        "dissolution innovation", "dissolution", "dissolution rate",
        "drug release", "release profile", "solubility", "bioavailability",
        "amorphous solid dispersion", "solid dispersion", "modified release",
        "controlled release", "particle size reduction", "nanocrystal",
        "wet milling", "micronization",
    ),
    "amorphous solid dispersion": (
        "amorphous solid dispersion", "ASD", "solid dispersion", "spray drying",
        "hot-melt extrusion", "polymer carrier", "supersaturation",
        "precipitation inhibition", "bioavailability", "dissolution",
    ),
    "modified release": (
        "modified release", "controlled release", "sustained release",
        "extended release", "delayed release", "matrix tablet", "drug release",
        "release profile", "osmotic delivery", "coating",
    ),
    "bioavailability enhancement": (
        "bioavailability enhancement", "bioavailability", "absorption",
        "solubility", "dissolution", "permeability", "lipid formulation",
        "SEDDS", "SMEDDS", "nanocrystal", "solid dispersion", "salt form",
        "cocrystal",
    ),
    "particle size reduction": (
        "particle size reduction", "particle size", "particle engineering",
        "micronization", "nanomilling", "wet milling", "dry milling",
        "milling", "nanocrystal", "nanosuspension", "dissolution",
        "solubility", "bioavailability",
    ),
    "nanocrystals / nanosuspensions": (
        "nanocrystal", "nanocrystals", "nanosuspension", "nanosuspensions",
        "nanomilling", "wet milling", "particle size reduction",
        "particle engineering", "dissolution", "solubility", "bioavailability",
    ),
    "cocrystals / salt forms": (
        "cocrystal", "cocrystals", "salt form", "salt forms",
        "crystal engineering", "crystal habit", "solid state", "polymorph",
        "dissolution", "solubility", "bioavailability",
    ),
    "lipid-based formulations": (
        "lipid-based formulation", "lipid formulation", "lipid delivery",
        "self-emulsifying", "SEDDS", "SMEDDS", "absorption", "dissolution",
        "solubility", "bioavailability",
    ),
    "spray drying": (
        "spray drying", "spray-dried dispersion", "amorphous solid dispersion",
        "solid dispersion", "particle engineering", "polymer carrier",
        "dissolution", "solubility", "bioavailability",
    ),
    "hot-melt extrusion": (
        "hot-melt extrusion", "HME", "amorphous solid dispersion",
        "solid dispersion", "polymer carrier", "supersaturation",
        "dissolution", "solubility", "bioavailability",
    ),
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def expand_query_terms(query: str) -> list[str]:
    """Return deterministic, de-duplicated terms for an approved case theme."""
    original = _text(query)
    expanded = QUERY_EXPANSIONS.get(original.casefold(), (original,))
    terms: list[str] = []
    seen: set[str] = set()
    for value in (original, *expanded):
        term = _text(value)
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def _or_like(fields: tuple[str, ...], terms: list[str]) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        pattern = f"%{term.casefold()}%"
        for field in fields:
            clauses.append(f"LOWER(COALESCE({field},'')) LIKE ?")
            params.append(pattern)
    return "(" + " OR ".join(clauses) + ")", tuple(params)


def _matched_terms(row: dict[str, Any], fields: tuple[str, ...], terms: list[str]) -> list[str]:
    haystack = " ".join(_text(row.get(field)).casefold() for field in fields)
    return [term for term in terms if term.casefold() in haystack]


def _evidence_rank(value: Any) -> int:
    text = _text(value).casefold()
    if text in {"a", "tier a"}:
        return 4
    if text in {"b", "tier b"}:
        return 3
    if text in {"c", "tier c"}:
        return 2
    if text in {"d", "tier d"}:
        return 1
    if any(term in text for term in ("human-verified", "primary", "official", "confirmed")):
        return 4
    if any(term in text for term in ("source-derived", "strong", "published")):
        return 3
    if any(term in text for term in ("reported", "retained", "plausible")):
        return 2
    return 1 if text else 0


def _source_rank(value: Any) -> int:
    text = _text(value).casefold()
    if any(term in text for term in ("fda", "ema", "mhra", "epo", "uspto", "official")):
        return 4
    if any(term in text for term in ("clinicaltrials", "europepmc", "openalex", "crossref")):
        return 3
    if any(term in text for term in ("research", "paper", "publication")):
        return 2
    return 1 if text else 0


def _rank_rows(
    rows: list[dict[str, Any]],
    query: str,
    terms: list[str],
    fields: tuple[str, ...],
    *,
    score_field: str = "",
    evidence_field: str = "",
    date_field: str = "",
    source_field: str = "",
    limit: int = BUCKET_LIMIT,
) -> list[dict[str, Any]]:
    query_key = query.casefold()
    for row in rows:
        matches = list(dict.fromkeys([
            *(row.get("matched_terms") or []),
            *_matched_terms(row, fields, terms),
        ]))
        row["matched_terms"] = matches
        row["matched_terms_text"] = ", ".join(matches)
        row["_exact_theme_match"] = any(
            query_key in _text(row.get(field)).casefold() for field in fields
        )
    rows.sort(
        key=lambda row: (
            bool(row.get("_exact_theme_match")),
            len(row.get("matched_terms") or []),
            float(row.get(score_field) or 0) if score_field else 0,
            _evidence_rank(row.get(evidence_field)) if evidence_field else 0,
            _text(row.get(date_field)) if date_field else "",
            _source_rank(row.get(source_field)) if source_field else 0,
        ),
        reverse=True,
    )
    for row in rows:
        row.pop("_exact_theme_match", None)
    return rows[:limit]


def _case_readiness(evidence: dict[str, Any]) -> str:
    opportunities = evidence.get("opportunities") or []
    technologies = evidence.get("technologies") or []
    retained = [
        *opportunities,
        *(evidence.get("products") or []),
        *(evidence.get("problems") or []),
        *technologies,
        *(evidence.get("grants") or []),
        *(evidence.get("patents") or []),
        *(evidence.get("regulatory") or []),
        *(evidence.get("lifecycle") or []),
    ]
    source_link = any(
        _text(row.get("source_url") or row.get("evidence_url") or row.get("external_link"))
        for row in [*opportunities, *technologies]
    )
    if (opportunities or technologies) and source_link:
        return "Ready for analyst review"
    if retained or evidence.get("direct_patents"):
        return "Partial evidence only"
    return "Not enough retained evidence yet"


def _first_url(value: Any) -> str:
    try:
        rows = json.loads(value or "[]")
    except (TypeError, ValueError):
        rows = []
    for row in rows if isinstance(rows, list) else []:
        url = row.get("url") if isinstance(row, dict) else row
        if str(url or "").startswith(("https://", "http://")):
            return str(url)
    return ""


def _source(
    section: str,
    title: Any,
    status: str,
    url: Any = "",
    source_id: Any = "",
    matched_terms: Any = "",
) -> dict[str, str]:
    return {
        "section": section,
        "title": _text(title) or "Untitled evidence",
        "source_status": status,
        "source_id": _text(source_id),
        "source_url": _text(url),
        "matched_terms": (
            ", ".join(_text(item) for item in matched_terms if _text(item))
            if isinstance(matched_terms, (list, tuple))
            else _text(matched_terms)
        ),
    }


def _canonical_links(conn, source_table: str, source_ids: list[str], limit: int) -> list[dict[str, Any]]:
    identifiers = list(dict.fromkeys(_text(item) for item in source_ids if _text(item)))[:limit]
    if not identifiers:
        return []
    placeholders = ",".join("?" for _item in identifiers)
    return [
        dict(row)
        for row in conn.execute(
            f"""SELECT canonical_record_link_id,source_table,source_record_id,
            canonical_entity_type,canonical_id,link_status,evidence_url,evidence_status,
            evidence_basis,verification_status
            FROM canonical_record_links
            WHERE active=1 AND link_status='accepted' AND source_table=?
              AND source_record_id IN ({placeholders})
            ORDER BY canonical_entity_type,canonical_id LIMIT ?""",
            (source_table, *identifiers, limit),
        ).fetchall()
    ]


def _collect_theme_evidence(
    conn,
    query: str,
    case_type: str,
    *,
    direct_patent_result: dict[str, Any] | None = None,
    limit: int = BUCKET_LIMIT,
) -> dict[str, Any]:
    """Collect bounded stored evidence; external results must be supplied explicitly."""
    query = _text(query)
    bounded = max(1, min(int(limit), BUCKET_LIMIT))
    terms = expand_query_terms(query)
    opportunity_fields = (
        "company", "product", "molecule", "problem_category", "source_type",
        "evidence_links_json",
    )
    opportunity_where, opportunity_params = _or_like(opportunity_fields, terms)
    opportunity_candidates = [
        dict(row)
        for row in conn.execute(
            """SELECT stable_lead_id,company,product,molecule,problem_category,source_type,
            source_id,region,evidence_links_json,score,grade,last_updated_at,last_checked_at
            FROM opportunity_index
            WHERE """ + opportunity_where + """
              AND COALESCE(novelty_status,'') NOT IN ('archived','rejected / hidden')
              AND COALESCE(queue_status,'') NOT IN ('archived','rejected')
            ORDER BY COALESCE(score,0) DESC,COALESCE(last_updated_at,last_checked_at) DESC
            LIMIT ?""",
            (*opportunity_params, CANDIDATE_LIMIT),
        ).fetchall()
    ]
    opportunities = _rank_rows(
        opportunity_candidates,
        query,
        terms,
        opportunity_fields,
        score_field="score",
        evidence_field="grade",
        date_field="last_updated_at",
        source_field="source_type",
        limit=bounded,
    )
    for row in opportunities:
        row["source_url"] = _first_url(row.get("evidence_links_json"))

    products: list[dict[str, Any]] = []
    seen_products: set[str] = set()
    for row in opportunities:
        for kind, name in (("product", row.get("product")), ("api", row.get("molecule"))):
            normalized = _text(name).casefold()
            if not normalized or normalized in seen_products:
                continue
            seen_products.add(normalized)
            products.append({
                "entity_type": kind,
                "name": _text(name),
                "identity_status": "retained internal record",
                "evidence_status": "opportunity-index evidence",
                "source_url": row.get("source_url") or "",
                "source_id": _text(row.get("stable_lead_id")),
                "matched_terms": list(row.get("matched_terms") or []),
                "matched_terms_text": row.get("matched_terms_text") or "",
            })
    profile_fields = ("canonical_name",)
    profile_where, profile_params = _or_like(profile_fields, terms)
    for table, id_column, kind in (
        ("product_profiles", "product_id", "product"),
        ("api_profiles", "api_id", "api"),
    ):
        for row in conn.execute(
            f"""SELECT {id_column} AS entity_id,canonical_name,identity_status,evidence_status
            FROM {table} WHERE active=1 AND {profile_where}
            ORDER BY canonical_name LIMIT ?""",
            (*profile_params, CANDIDATE_LIMIT),
        ).fetchall():
            item = dict(row)
            normalized = _text(item["canonical_name"]).casefold()
            if normalized in seen_products:
                continue
            seen_products.add(normalized)
            products.append({
                "entity_type": kind,
                "name": item["canonical_name"],
                "identity_status": item["identity_status"],
                "evidence_status": item["evidence_status"],
                "source_url": "",
                "source_id": item["entity_id"],
            })
    products = _rank_rows(
        products,
        query,
        terms,
        ("name",),
        evidence_field="evidence_status",
        limit=bounded,
    )

    problem_fields = ("p.display_name", "p.definition")
    problem_where, problem_params = _or_like(problem_fields, terms)
    problem_candidates = [
        dict(row)
        for row in conn.execute(
            """SELECT p.problem_id,p.display_name,p.definition,p.identity_status,p.evidence_status,
            t.label AS taxonomy_label
            FROM pharmaceutical_problems p
            JOIN intelligence_taxonomy_terms t ON t.term_id=p.taxonomy_term_id
            WHERE p.active=1 AND """ + problem_where + """
            ORDER BY p.display_name LIMIT ?""",
            (*problem_params, CANDIDATE_LIMIT),
        ).fetchall()
    ]
    problems = _rank_rows(
        problem_candidates,
        query,
        terms,
        ("display_name", "definition"),
        evidence_field="evidence_status",
        limit=bounded,
    )
    technology_sql_fields = (
        "p.display_name", "p.definition", "s.display_name", "s.mechanism_summary",
        "r.relationship_statement",
    )
    technology_where, technology_params = _or_like(technology_sql_fields, terms)
    technology_candidates = [
        dict(row)
        for row in conn.execute(
            """SELECT s.technology_id,s.display_name,s.mechanism_summary,s.maturity_status,
            r.relationship_type,r.relationship_statement,r.evidence_url,r.evidence_status,
            r.inference_status,r.confidence_score,p.display_name AS problem_name
            FROM technology_problem_relationships r
            JOIN technology_solutions s ON s.technology_id=r.technology_id
            JOIN pharmaceutical_problems p ON p.problem_id=r.problem_id
            WHERE r.active=1 AND s.active=1 AND p.active=1
              AND """ + technology_where + """
            ORDER BY r.confidence_score DESC,s.display_name LIMIT ?""",
            (*technology_params, CANDIDATE_LIMIT),
        ).fetchall()
    ]
    technologies = _rank_rows(
        technology_candidates,
        query,
        terms,
        ("problem_name", "display_name", "mechanism_summary", "relationship_statement"),
        score_field="confidence_score",
        evidence_field="evidence_status",
        source_field="inference_status",
        limit=bounded,
    )
    for row in technologies:
        row["canonical_status"] = (
            "human-reviewed canonical link"
            if row.get("inference_status") == "human-verified"
            else "requires review"
        )

    grant_fields = ("funder_name", "recipient_name", "programme_name", "award_id")
    grant_where, grant_params = _or_like(grant_fields, terms)
    grant_candidates = [
        dict(row)
        for row in conn.execute(
            """SELECT funding_award_id,funder_name,recipient_name,award_id,programme_name,
            source_name,source_id,evidence_url,evidence_status,validation_status,last_verified_at
            FROM funding_awards WHERE active=1 AND """ + grant_where + """
            ORDER BY last_verified_at DESC LIMIT ?""",
            (*grant_params, CANDIDATE_LIMIT),
        ).fetchall()
    ]
    grants = _rank_rows(
        grant_candidates,
        query,
        terms,
        grant_fields,
        evidence_field="evidence_status",
        date_field="last_verified_at",
        source_field="source_name",
        limit=bounded,
    )
    canonical_links = [
        *_canonical_links(
            conn,
            "opportunity_index",
            [str(row.get("stable_lead_id") or "") for row in opportunities],
            bounded,
        ),
        *_canonical_links(
            conn,
            "funding_awards",
            [str(row.get("funding_award_id") or "") for row in grants],
            bounded,
        ),
    ][:bounded]

    patent_candidates: dict[str, dict[str, Any]] = {}
    for term in terms:
        for row in patent_discovery.stored_records(conn, term, "Innovation / problem theme"):
            key = _text(
                row.get("patent_document_id")
                or row.get("publication_number")
                or row.get("external_link")
                or row.get("title")
            ).casefold()
            if not key:
                continue
            existing = patent_candidates.get(key)
            if existing:
                existing["matched_terms"] = list(dict.fromkeys([
                    *(existing.get("matched_terms") or []),
                    term,
                    *(row.get("matched_query_terms") or []),
                ]))
            else:
                item = dict(row)
                item["matched_terms"] = list(dict.fromkeys([
                    term,
                    *(row.get("matched_query_terms") or []),
                ]))
                patent_candidates[key] = item
            if len(patent_candidates) >= CANDIDATE_LIMIT:
                break
        if len(patent_candidates) >= CANDIDATE_LIMIT:
            break
    patents = _rank_rows(
        list(patent_candidates.values()),
        query,
        terms,
        ("title", "publication_number", "application_number", "assignee_applicant", "snippet"),
        evidence_field="evidence_status",
        date_field="date",
        source_field="source_type",
        limit=bounded,
    )
    direct_rows = list((direct_patent_result or {}).get("results") or [])[:bounded]
    direct_rows = _rank_rows(
        [dict(row) for row in direct_rows],
        query,
        terms,
        ("title", "publication_number", "application_number", "assignee_applicant", "snippet"),
        evidence_field="evidence_status",
        date_field="date",
        source_field="source_label",
        limit=bounded,
    )
    for row in patents:
        row["_bucket_kind"] = "stored"
    for row in direct_rows:
        row["_bucket_kind"] = "direct"
    patent_results = _rank_rows(
        [*patents, *direct_rows],
        query,
        terms,
        ("title", "publication_number", "application_number", "assignee_applicant", "snippet"),
        evidence_field="evidence_status",
        date_field="date",
        source_field="source_label",
        limit=bounded,
    )
    patents = [row for row in patent_results if row["_bucket_kind"] == "stored"]
    direct_rows = [row for row in patent_results if row["_bucket_kind"] == "direct"]
    for row in patent_results:
        row.pop("_bucket_kind", None)
    routes = patent_discovery.external_discovery_routes(query, "Innovation / problem theme")
    patent_statuses = (direct_patent_result or {}).get("providers") or patent_discovery.patent_source_health()

    regulatory = [
        {
            "title": _text(row.get("product") or row.get("molecule") or row.get("source_id")),
            "regulator": _text(row.get("source_type")),
            "problem_category": _text(row.get("problem_category")),
            "source_id": _text(row.get("source_id")),
            "source_url": row.get("source_url") or _first_url(row.get("evidence_links_json")),
            "source_status": "retained internal record",
            "matched_terms": list(row.get("matched_terms") or []),
            "matched_terms_text": row.get("matched_terms_text") or "",
            "last_verified_at": _text(row.get("last_updated_at") or row.get("last_checked_at")),
        }
        for row in opportunities
        if any(token in _text(row.get("source_type")).casefold() for token in ("fda", "ema", "mhra", "regulator"))
    ][:bounded]
    lifecycle_fields = (
        "trade_name", "ingredient", "application_holder", "dosage_form_route",
        "market_category", "lifecycle_status",
    )
    lifecycle_where, lifecycle_params = _or_like(lifecycle_fields, terms)
    lifecycle_candidates = [
        dict(row)
        for row in conn.execute(
            """SELECT lifecycle_id,trade_name,ingredient,application_number,lifecycle_status,
            application_holder,dosage_form_route,market_category,dataset_mode,
            official_source_url,evidence_status,next_expiry_date,last_verified_at
            FROM lifecycle_products WHERE active=1 AND """ + lifecycle_where + """
            ORDER BY last_verified_at DESC LIMIT ?""",
            (*lifecycle_params, CANDIDATE_LIMIT),
        ).fetchall()
    ]
    lifecycle = _rank_rows(
        lifecycle_candidates,
        query,
        terms,
        lifecycle_fields,
        evidence_field="evidence_status",
        date_field="last_verified_at",
        source_field="dataset_mode",
        limit=bounded,
    )
    for row in regulatory:
        row["_bucket_kind"] = "regulatory"
        row["_bucket_evidence"] = row.get("source_status")
        row["_bucket_source"] = row.get("regulator")
    for row in lifecycle:
        row["_bucket_kind"] = "lifecycle"
        row["_bucket_evidence"] = row.get("evidence_status")
        row["_bucket_source"] = row.get("dataset_mode")
    regulatory_lifecycle = _rank_rows(
        [*regulatory, *lifecycle],
        query,
        terms,
        (
            "title", "trade_name", "ingredient", "problem_category",
            "application_holder", "dosage_form_route", "market_category",
            "lifecycle_status",
        ),
        evidence_field="_bucket_evidence",
        date_field="last_verified_at",
        source_field="_bucket_source",
        limit=bounded,
    )
    regulatory = [row for row in regulatory_lifecycle if row["_bucket_kind"] == "regulatory"]
    lifecycle = [row for row in regulatory_lifecycle if row["_bucket_kind"] == "lifecycle"]
    for row in regulatory_lifecycle:
        row.pop("_bucket_kind", None)
        row.pop("_bucket_evidence", None)
        row.pop("_bucket_source", None)

    sources: list[dict[str, str]] = []
    for row in opportunities:
        sources.append(_source(
            "Opportunities",
            row.get("product") or row.get("company") or row.get("problem_category"),
            "retained internal record",
            _first_url(row.get("evidence_links_json")),
            row.get("source_id"),
            row.get("matched_terms"),
        ))
    for row in products:
        sources.append(_source("Product/API landscape", row["name"], row["identity_status"], row["source_url"], row["source_id"], row.get("matched_terms")))
    for row in problems:
        sources.append(_source(
            "Technology approaches",
            row["display_name"],
            row.get("evidence_status") or "retained internal record",
            "",
            row["problem_id"],
            row.get("matched_terms"),
        ))
    for row in technologies:
        sources.append(_source("Technology approaches", row["display_name"], row["canonical_status"], row["evidence_url"], row["technology_id"], row.get("matched_terms")))
    for row in canonical_links:
        sources.append(_source(
            "Canonical intelligence",
            f"{row['canonical_entity_type']} · {row['canonical_id']}",
            "human-reviewed canonical link",
            row.get("evidence_url"),
            row.get("canonical_record_link_id"),
        ))
    for row in grants:
        sources.append(_source("Research/grant signals", row.get("programme_name") or row.get("award_id"), row.get("evidence_status") or "retained internal record", row.get("evidence_url"), row.get("source_id"), row.get("matched_terms")))
    for row in patents:
        sources.append(_source("Patent and innovation landscape", row.get("title"), "retained internal record", row.get("external_link"), row.get("publication_number"), row.get("matched_terms")))
    for row in direct_rows:
        sources.append(_source("Patent and innovation landscape", row.get("title"), "live/direct discovery result", row.get("external_link"), row.get("publication_number"), row.get("matched_terms")))
    if not patents and not direct_rows:
        for row in routes:
            sources.append(_source("Patent and innovation landscape", row["title"], "generated external route", row["external_link"], row["source_label"]))
    for row in regulatory:
        sources.append(_source("Regulatory/lifecycle context", row["title"], row["source_status"], row["source_url"], row["source_id"], row.get("matched_terms")))
    for row in lifecycle:
        sources.append(_source("Regulatory/lifecycle context", row["trade_name"], row["evidence_status"], row["official_source_url"], row["application_number"], row.get("matched_terms")))

    reviewed_links = [
        *canonical_links,
        *[row for row in technologies if row["canonical_status"] == "human-reviewed canonical link"],
    ]
    limitations: list[str] = []
    for label, rows in (
        ("matching opportunities", opportunities),
        ("product/API records", products),
        ("technology/problem relationships", technologies),
        ("research/grant records", grants),
        ("regulatory/lifecycle records", [*regulatory, *lifecycle]),
    ):
        if not rows:
            limitations.append(f"No {label} were found in the retained database for this query.")
    if not patents and not direct_rows:
        limitations.append("No patent-source results were returned; generated official search routes are provided for manual discovery.")
    if not reviewed_links:
        limitations.append("No reviewed canonical link exists yet. Use Human Validation to approve exact candidates.")

    result = {
        "query": query,
        "expanded_terms": terms,
        "search_buckets": EVIDENCE_BUCKETS,
        "case_type": case_type if case_type in CASE_TYPES else CASE_TYPES[0],
        "opportunities": opportunities,
        "products": products,
        "problems": problems,
        "technologies": technologies,
        "grants": grants,
        "patents": patents,
        "direct_patents": direct_rows,
        "patent_routes": routes[:bounded],
        "patent_provider_statuses": patent_statuses,
        "regulatory": regulatory,
        "lifecycle": lifecycle,
        "reviewed_canonical_links": reviewed_links,
        "requires_review_links": [row for row in technologies if row["canonical_status"] == "requires review"],
        "limitations": limitations,
        "sources": sources[:100],
    }
    result["evidence_counts"] = {
        "opportunities": len(opportunities),
        "products_apis": len(products),
        "pharmaceutical_problems": len(problems),
        "technology_relationships": len(technologies),
        "patent_source_records": len(patents) + len(direct_rows),
        "research_grants": len(grants),
        "regulatory_lifecycle": len(regulatory) + len(lifecycle),
    }
    result["case_readiness"] = _case_readiness(result)
    return result


def validate_case_inputs(company: str, theme: str, mode: str) -> str:
    if mode == "Company-specific pitch" and not _text(company):
        return "Target company is required for a company-specific pitch."
    if not _text(theme):
        return "Technology/opportunity theme is required."
    return ""


def company_options(conn, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """Return bounded retained company choices without external discovery."""
    bounded = max(1, min(int(limit), 100))
    pattern = f"%{_text(search).casefold()}%"
    candidates: list[dict[str, Any]] = []
    queries = (
        (
            """SELECT canonical_name AS company_name,'canonical organisation' AS source,
            organisation_profile_id,'' AS account_organisation_id
            FROM organisation_profiles WHERE active=1 AND LOWER(canonical_name) LIKE ?
            ORDER BY canonical_name LIMIT ?""",
            (pattern, bounded),
        ),
        (
            """SELECT o.canonical_name AS company_name,'canonical organisation alias' AS source,
            o.organisation_profile_id,'' AS account_organisation_id
            FROM organisation_aliases a JOIN organisation_profiles o
              ON o.organisation_profile_id=a.organisation_profile_id
            WHERE o.active=1 AND LOWER(a.alias_name) LIKE ?
            ORDER BY o.canonical_name LIMIT ?""",
            (pattern, bounded),
        ),
        (
            """SELECT canonical_name AS company_name,'account intelligence' AS source,
            '' AS organisation_profile_id,organisation_id AS account_organisation_id
            FROM account_organisations WHERE active=1 AND LOWER(canonical_name) LIKE ?
            ORDER BY canonical_name LIMIT ?""",
            (pattern, bounded),
        ),
        (
            """SELECT o.canonical_name AS company_name,'account alias' AS source,
            '' AS organisation_profile_id,o.organisation_id AS account_organisation_id
            FROM account_aliases a JOIN account_organisations o
              ON o.organisation_id=a.organisation_id
            WHERE o.active=1 AND LOWER(a.alias_name) LIKE ?
            ORDER BY o.canonical_name LIMIT ?""",
            (pattern, bounded),
        ),
        (
            """SELECT company AS company_name,'opportunity index' AS source,
            '' AS organisation_profile_id,'' AS account_organisation_id
            FROM opportunity_index
            WHERE COALESCE(company,'')<>'' AND LOWER(company) LIKE ?
              AND COALESCE(novelty_status,'') NOT IN ('archived','rejected / hidden')
              AND COALESCE(queue_status,'') NOT IN ('archived','rejected')
            GROUP BY company ORDER BY company LIMIT ?""",
            (pattern, bounded),
        ),
        (
            """SELECT application_holder AS company_name,'lifecycle records' AS source,
            '' AS organisation_profile_id,'' AS account_organisation_id
            FROM lifecycle_products
            WHERE active=1 AND COALESCE(application_holder,'')<>''
              AND LOWER(application_holder) LIKE ?
            GROUP BY application_holder ORDER BY application_holder LIMIT ?""",
            (pattern, bounded),
        ),
    )
    for sql, params in queries:
        candidates.extend(dict(row) for row in conn.execute(sql, params).fetchall())
    unique: dict[str, dict[str, Any]] = {}
    for row in candidates:
        name = _text(row.get("company_name"))
        key = name.casefold()
        if not key:
            continue
        if key not in unique:
            unique[key] = row
        else:
            current = unique[key]
            current["organisation_profile_id"] = (
                current.get("organisation_profile_id")
                or row.get("organisation_profile_id")
                or ""
            )
            current["account_organisation_id"] = (
                current.get("account_organisation_id")
                or row.get("account_organisation_id")
                or ""
            )
    return sorted(unique.values(), key=lambda row: _text(row["company_name"]).casefold())[:bounded]


def _company_identity(conn, company: str) -> dict[str, Any]:
    requested = _text(company)
    key = requested.casefold()
    profile = conn.execute(
        """SELECT organisation_profile_id,canonical_name,organisation_type,country_code,
        official_website_url,identity_status,evidence_status
        FROM organisation_profiles o WHERE o.active=1 AND
          (LOWER(o.canonical_name)=? OR EXISTS (
            SELECT 1 FROM organisation_aliases a
            WHERE a.organisation_profile_id=o.organisation_profile_id
              AND LOWER(a.alias_name)=?
          ))
        ORDER BY CASE WHEN LOWER(o.canonical_name)=? THEN 0 ELSE 1 END,
        o.canonical_name LIMIT 1""",
        (key, key, key),
    ).fetchone()
    profile_row = dict(profile or {})
    profile_id = _text(profile_row.get("organisation_profile_id"))
    profile_aliases = [
        _text(row["alias_name"])
        for row in conn.execute(
            """SELECT alias_name FROM organisation_aliases
            WHERE organisation_profile_id=? ORDER BY alias_name LIMIT 20""",
            (profile_id,),
        ).fetchall()
    ] if profile_id else []
    account = conn.execute(
        """SELECT organisation_id,canonical_name,organisation_type,country,
        official_website_url,identity_status
        FROM account_organisations o WHERE o.active=1 AND
          (LOWER(o.canonical_name)=? OR EXISTS (
            SELECT 1 FROM account_aliases a
            WHERE a.organisation_id=o.organisation_id AND LOWER(a.alias_name)=?
          ))
        ORDER BY CASE WHEN LOWER(o.canonical_name)=? THEN 0 ELSE 1 END,
        o.canonical_name LIMIT 1""",
        (key, key, key),
    ).fetchone()
    account_row = dict(account or {})
    account_id = _text(account_row.get("organisation_id"))
    account_aliases = [
        _text(row["alias_name"])
        for row in conn.execute(
            """SELECT alias_name FROM account_aliases
            WHERE organisation_id=? ORDER BY alias_name LIMIT 20""",
            (account_id,),
        ).fetchall()
    ] if account_id else []
    canonical_name = _text(
        profile_row.get("canonical_name")
        or account_row.get("canonical_name")
        or requested
    )
    terms: list[str] = []
    seen: set[str] = set()
    for value in (requested, canonical_name, *profile_aliases, *account_aliases):
        value = _text(value)
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            terms.append(value)
    return {
        "requested_name": requested,
        "canonical_name": canonical_name,
        "company_terms": terms,
        "organisation_profile_id": profile_id,
        "account_organisation_id": account_id,
        "organisation_type": (
            profile_row.get("organisation_type")
            or account_row.get("organisation_type")
            or "Not established"
        ),
        "country": profile_row.get("country_code") or account_row.get("country") or "",
        "official_website_url": (
            profile_row.get("official_website_url")
            or account_row.get("official_website_url")
            or ""
        ),
        "identity_status": (
            profile_row.get("identity_status")
            or account_row.get("identity_status")
            or "manual company entry; requires review"
        ),
        "evidence_status": profile_row.get("evidence_status") or "",
        "known_company": bool(profile_id or account_id),
    }


def _company_specific_evidence(
    conn,
    company_identity: dict[str, Any],
    theme: str,
    theme_evidence: dict[str, Any],
    *,
    limit: int,
) -> dict[str, Any]:
    bounded = max(1, min(int(limit), BUCKET_LIMIT))
    company_terms = list(company_identity.get("company_terms") or [])
    theme_terms = list(theme_evidence.get("expanded_terms") or [theme])
    company_where, company_params = _or_like(("company",), company_terms)
    opportunity_fields = (
        "product", "molecule", "problem_category", "source_type", "evidence_links_json",
    )
    theme_where, theme_params = _or_like(opportunity_fields, theme_terms)
    opportunity_candidates = [
        dict(row)
        for row in conn.execute(
            """SELECT stable_lead_id,company,product,molecule,problem_category,source_type,
            source_id,region,evidence_links_json,score,grade,last_updated_at,last_checked_at
            FROM opportunity_index WHERE """ + company_where + " AND " + theme_where + """
              AND COALESCE(novelty_status,'') NOT IN ('archived','rejected / hidden')
              AND COALESCE(queue_status,'') NOT IN ('archived','rejected')
            ORDER BY COALESCE(score,0) DESC,COALESCE(last_updated_at,last_checked_at) DESC
            LIMIT ?""",
            (*company_params, *theme_params, CANDIDATE_LIMIT),
        ).fetchall()
    ]
    opportunities = _rank_rows(
        opportunity_candidates,
        theme,
        theme_terms,
        opportunity_fields,
        score_field="score",
        evidence_field="grade",
        date_field="last_updated_at",
        source_field="source_type",
        limit=bounded,
    )
    for row in opportunities:
        row["source_url"] = _first_url(row.get("evidence_links_json"))
        row["company_specific"] = True

    products: list[dict[str, Any]] = []
    for row in opportunities:
        for entity_type, name in (("product", row.get("product")), ("api", row.get("molecule"))):
            if _text(name):
                products.append({
                    "entity_type": entity_type,
                    "name": _text(name),
                    "identity_status": "retained company opportunity record",
                    "evidence_status": "company-specific opportunity evidence",
                    "source_url": row.get("source_url") or "",
                    "source_id": row.get("stable_lead_id") or "",
                    "matched_terms": list(row.get("matched_terms") or []),
                    "matched_terms_text": row.get("matched_terms_text") or "",
                    "company_specific": True,
                })

    organisation_id = _text(company_identity.get("organisation_profile_id"))
    technologies: list[dict[str, Any]] = []
    capabilities: list[dict[str, Any]] = []
    if organisation_id:
        for row in conn.execute(
            """SELECT 'product' AS entity_type,p.product_id AS entity_id,
            p.canonical_name AS name,p.identity_status,r.evidence_status,
            r.evidence_url AS source_url,r.source_record_id AS source_id,
            r.relationship_type,r.verification_status
            FROM organisation_product_relationships r
            JOIN product_profiles p ON p.product_id=r.product_id
            WHERE r.organisation_profile_id=? AND r.active=1 AND p.active=1
            ORDER BY p.canonical_name LIMIT ?""",
            (organisation_id, bounded),
        ).fetchall():
            products.append({**dict(row), "company_specific": True})
        for row in conn.execute(
            """SELECT 'api' AS entity_type,a.api_id AS entity_id,
            a.canonical_name AS name,a.identity_status,r.evidence_status,
            r.evidence_url AS source_url,r.source_record_id AS source_id,
            r.relationship_type,r.verification_status
            FROM organisation_api_relationships r
            JOIN api_profiles a ON a.api_id=r.api_id
            WHERE r.organisation_profile_id=? AND r.active=1 AND a.active=1
            ORDER BY a.canonical_name LIMIT ?""",
            (organisation_id, bounded),
        ).fetchall():
            products.append({**dict(row), "company_specific": True})

        solution_fields = ("s.display_name", "s.mechanism_summary", "tpr.relationship_statement")
        solution_where, solution_params = _or_like(solution_fields, theme_terms)
        technology_candidates = [
            dict(row)
            for row in conn.execute(
                """SELECT s.technology_id,s.display_name,s.mechanism_summary,
                r.relationship_type,r.evidence_url,r.evidence_status,
                r.verification_status,r.source_record_id AS source_id,
                COALESCE(tpr.relationship_statement,'') AS relationship_statement,
                COALESCE(tpr.confidence_score,0) AS confidence_score
                FROM organisation_solution_relationships r
                JOIN technology_solutions s ON s.technology_id=r.technology_id
                LEFT JOIN technology_problem_relationships tpr
                  ON tpr.technology_id=s.technology_id AND tpr.active=1
                WHERE r.organisation_profile_id=? AND r.active=1 AND s.active=1
                  AND """ + solution_where + """
                ORDER BY s.display_name LIMIT ?""",
                (organisation_id, *solution_params, CANDIDATE_LIMIT),
            ).fetchall()
        ]
        technologies = _rank_rows(
            technology_candidates,
            theme,
            theme_terms,
            ("display_name", "mechanism_summary", "relationship_statement"),
            score_field="confidence_score",
            evidence_field="evidence_status",
            source_field="verification_status",
            limit=bounded,
        )
        for row in technologies:
            row["canonical_status"] = row.get("verification_status") or "requires review"
            row["company_specific"] = True

        capability_fields = ("c.canonical_name", "c.description")
        capability_where, capability_params = _or_like(capability_fields, theme_terms)
        capability_candidates = [
            dict(row)
            for row in conn.execute(
                """SELECT c.capability_profile_id,c.canonical_name,c.description,
                c.capability_type,r.relationship_type,r.evidence_url,r.evidence_status,
                r.verification_status,r.source_record_id AS source_id
                FROM organisation_capability_relationships r
                JOIN capability_profiles c ON c.capability_profile_id=r.capability_profile_id
                WHERE r.organisation_profile_id=? AND r.active=1 AND c.active=1
                  AND """ + capability_where + """
                ORDER BY c.canonical_name LIMIT ?""",
                (organisation_id, *capability_params, CANDIDATE_LIMIT),
            ).fetchall()
        ]
        capabilities = _rank_rows(
            capability_candidates,
            theme,
            theme_terms,
            ("canonical_name", "description"),
            evidence_field="evidence_status",
            source_field="verification_status",
            limit=bounded,
        )
        for row in capabilities:
            row["company_specific"] = True

    deduped_products: dict[tuple[str, str], dict[str, Any]] = {}
    for row in products:
        key = (_text(row.get("entity_type")), _text(row.get("name")).casefold())
        if key[1] and key not in deduped_products:
            deduped_products[key] = row
    products = _rank_rows(
        list(deduped_products.values()),
        theme,
        theme_terms,
        ("name",),
        evidence_field="evidence_status",
        limit=bounded,
    )

    grant_company_fields = ("funder_name", "recipient_name")
    grant_theme_fields = ("programme_name", "award_id")
    grant_company_where, grant_company_params = _or_like(grant_company_fields, company_terms)
    grant_theme_where, grant_theme_params = _or_like(grant_theme_fields, theme_terms)
    grant_candidates = [
        dict(row)
        for row in conn.execute(
            """SELECT funding_award_id,funder_name,recipient_name,award_id,programme_name,
            source_name,source_id,evidence_url,evidence_status,validation_status,last_verified_at
            FROM funding_awards WHERE active=1 AND """ + grant_company_where + """
              AND """ + grant_theme_where + """
            ORDER BY last_verified_at DESC LIMIT ?""",
            (*grant_company_params, *grant_theme_params, CANDIDATE_LIMIT),
        ).fetchall()
    ]
    grants = _rank_rows(
        grant_candidates,
        theme,
        theme_terms,
        grant_theme_fields,
        evidence_field="evidence_status",
        date_field="last_verified_at",
        source_field="source_name",
        limit=bounded,
    )
    for row in grants:
        row["company_specific"] = True

    patent_candidates: dict[str, dict[str, Any]] = {}
    for company_term in company_terms[:10]:
        for row in patent_discovery.stored_records(conn, company_term, "Company / assignee"):
            theme_matches = _matched_terms(
                row,
                ("title", "snippet", "assignee_applicant"),
                theme_terms,
            )
            company_matches = _matched_terms(row, ("assignee_applicant",), company_terms)
            if not theme_matches or not company_matches:
                continue
            key = _text(
                row.get("patent_document_id")
                or row.get("publication_number")
                or row.get("external_link")
            ).casefold()
            if key and key not in patent_candidates:
                patent_candidates[key] = {
                    **dict(row),
                    "matched_terms": theme_matches,
                    "company_specific": True,
                }
    patents = _rank_rows(
        list(patent_candidates.values()),
        theme,
        theme_terms,
        ("title", "snippet", "assignee_applicant"),
        evidence_field="evidence_status",
        date_field="date",
        source_field="source_type",
        limit=bounded,
    )

    holder_where, holder_params = _or_like(("application_holder",), company_terms)
    lifecycle_candidates = [
        dict(row)
        for row in conn.execute(
            """SELECT lifecycle_id,trade_name,ingredient,application_number,
            application_holder,dosage_form_route,market_category,lifecycle_status,
            dataset_mode,official_source_url,evidence_status,next_expiry_date,last_verified_at
            FROM lifecycle_products WHERE active=1 AND """ + holder_where + """
            ORDER BY last_verified_at DESC LIMIT ?""",
            (*holder_params, CANDIDATE_LIMIT),
        ).fetchall()
    ]
    lifecycle = _rank_rows(
        lifecycle_candidates,
        theme,
        theme_terms,
        (
            "trade_name", "ingredient", "dosage_form_route", "market_category",
            "lifecycle_status",
        ),
        evidence_field="evidence_status",
        date_field="last_verified_at",
        source_field="dataset_mode",
        limit=bounded,
    )
    lifecycle = [row for row in lifecycle if row.get("matched_terms")]
    for row in lifecycle:
        row["company_specific"] = True

    regulatory = [
        {
            "title": _text(row.get("product") or row.get("molecule") or row.get("source_id")),
            "regulator": _text(row.get("source_type")),
            "problem_category": _text(row.get("problem_category")),
            "source_id": _text(row.get("source_id")),
            "source_url": row.get("source_url") or "",
            "source_status": "retained company-specific evidence",
            "matched_terms": list(row.get("matched_terms") or []),
            "matched_terms_text": row.get("matched_terms_text") or "",
            "company_specific": True,
        }
        for row in opportunities
        if any(token in _text(row.get("source_type")).casefold() for token in ("fda", "ema", "mhra", "regulator"))
    ][:bounded]

    sources: list[dict[str, str]] = []
    for row in opportunities:
        sources.append(_source(
            "Company-specific signals",
            row.get("product") or row.get("company") or row.get("problem_category"),
            "retained company-specific evidence",
            row.get("source_url"),
            row.get("source_id"),
            row.get("matched_terms"),
        ))
    for row in products:
        sources.append(_source(
            "Relevant products/APIs", row.get("name"),
            "retained company-specific evidence", row.get("source_url"),
            row.get("source_id"), row.get("matched_terms"),
        ))
    for row in technologies:
        sources.append(_source(
            "Technology/problem fit", row.get("display_name"),
            "retained company-specific evidence", row.get("evidence_url"),
            row.get("source_id"), row.get("matched_terms"),
        ))
    for row in capabilities:
        sources.append(_source(
            "Technology/problem fit", row.get("canonical_name"),
            "retained company-specific evidence", row.get("evidence_url"),
            row.get("source_id"), row.get("matched_terms"),
        ))
    for row in grants:
        sources.append(_source(
            "Research/grant and collaboration signals",
            row.get("programme_name") or row.get("award_id"),
            "retained company-specific evidence", row.get("evidence_url"),
            row.get("source_id"), row.get("matched_terms"),
        ))
    for row in patents:
        sources.append(_source(
            "Patent and innovation landscape", row.get("title"),
            "retained company-specific evidence", row.get("external_link"),
            row.get("publication_number"), row.get("matched_terms"),
        ))
    for row in regulatory:
        sources.append(_source(
            "Regulatory/lifecycle context", row.get("title"),
            "retained company-specific evidence", row.get("source_url"),
            row.get("source_id"), row.get("matched_terms"),
        ))
    for row in lifecycle:
        sources.append(_source(
            "Regulatory/lifecycle context", row.get("trade_name"),
            "retained company-specific evidence", row.get("official_source_url"),
            row.get("application_number"), row.get("matched_terms"),
        ))

    company_source_ids = {
        (_text(row.get("source_id")), _text(row.get("source_url")))
        for row in sources
    }
    theme_sources = []
    for row in theme_evidence.get("sources") or []:
        key = (_text(row.get("source_id")), _text(row.get("source_url")))
        if key in company_source_ids:
            continue
        theme_sources.append({
            **row,
            "source_status": "Theme-level evidence only — not company-specific.",
        })

    specific_items = [
        *opportunities, *products, *technologies, *capabilities, *grants,
        *patents, *regulatory, *lifecycle,
    ]
    source_link = any(_text(row.get("source_url")) for row in sources)
    qualifying = bool(
        opportunities
        or technologies
        or lifecycle
        or any(row.get("matched_terms") for row in products)
    )
    generic_useful = any(
        theme_evidence.get(key)
        for key in (
            "opportunities", "products", "problems", "technologies", "grants",
            "patents", "direct_patents", "regulatory", "lifecycle",
        )
    )
    if specific_items and source_link and qualifying:
        readiness = "Pitch-ready draft"
    elif specific_items:
        readiness = "Partial company evidence"
    elif generic_useful:
        readiness = "Prospecting shell only"
    else:
        readiness = "Not enough evidence"

    if not specific_items:
        company_limitation = (
            "No retained company-specific evidence was found for this target. "
            "The report is a prospecting shell only."
        )
    else:
        company_limitation = (
            "Company-specific retained evidence is incomplete and requires analyst validation."
        )
    return {
        "company": company_identity,
        "company_terms": company_terms,
        "theme": theme,
        "expanded_terms": theme_terms,
        "opportunities": opportunities,
        "products": products,
        "technologies": technologies,
        "capabilities": capabilities,
        "grants": grants,
        "patents": patents,
        "regulatory": regulatory,
        "lifecycle": lifecycle,
        "sources": [*sources, *theme_sources][:100],
        "company_sources": sources,
        "theme_sources": theme_sources,
        "theme_evidence": theme_evidence,
        "company_specific_count": len(specific_items),
        "case_readiness": readiness,
        "limitations": [company_limitation],
        "reviewed_canonical_links": [
            row for row in [*technologies, *capabilities, *products]
            if row.get("verification_status") == "human-verified"
        ],
        "requires_review_links": [
            row for row in [*technologies, *capabilities, *products]
            if row.get("verification_status") == "requires-review"
        ],
        "evidence_counts": {
            "company_opportunities": len(opportunities),
            "company_products_apis": len(products),
            "company_technology_capabilities": len(technologies) + len(capabilities),
            "company_patents": len(patents),
            "company_grants": len(grants),
            "company_regulatory_lifecycle": len(regulatory) + len(lifecycle),
            "theme_level_records": sum(
                len(theme_evidence.get(key) or [])
                for key in (
                    "opportunities", "products", "problems", "technologies",
                    "grants", "patents", "direct_patents", "regulatory", "lifecycle",
                )
            ),
        },
    }


def collect(
    conn,
    query: str,
    case_type: str,
    *,
    company: str = "",
    mode: str = "Company-specific pitch",
    direct_patent_result: dict[str, Any] | None = None,
    limit: int = BUCKET_LIMIT,
) -> dict[str, Any]:
    theme_evidence = _collect_theme_evidence(
        conn,
        query,
        case_type,
        direct_patent_result=direct_patent_result,
        limit=limit,
    )
    if mode != "Company-specific pitch":
        theme_evidence["mode"] = "Theme-only exploration"
        theme_evidence["exploration_warning"] = "Exploration only — not suitable for company pitch."
        return theme_evidence
    error = validate_case_inputs(company, query, mode)
    if error:
        raise ValueError(error)
    result = _company_specific_evidence(
        conn,
        _company_identity(conn, company),
        query,
        theme_evidence,
        limit=limit,
    )
    result.update({
        "mode": mode,
        "case_type": case_type if case_type in CASE_TYPES else CASE_TYPES[0],
        "query": query,
        "patent_routes": patent_discovery.external_discovery_routes(
            f"{company} {query}",
            "Innovation / problem theme",
        )[:BUCKET_LIMIT],
        "patent_provider_statuses": (
            (direct_patent_result or {}).get("providers")
            or patent_discovery.patent_source_health()
        ),
        "direct_patents": list((direct_patent_result or {}).get("results") or [])[:BUCKET_LIMIT],
        "search_buckets": EVIDENCE_BUCKETS,
    })
    return result


def _bullet_rows(rows: list[dict[str, Any]], fields: tuple[str, ...], empty: str) -> list[str]:
    if not rows:
        return [f"- {empty}"]
    output = []
    for row in rows:
        values = [_text(row.get(field)) for field in fields if _text(row.get(field))]
        output.append("- " + " · ".join(values))
    return output


def _render_company_markdown(evidence: dict[str, Any]) -> str:
    company = evidence.get("company") or {}
    company_name = _text(company.get("canonical_name") or company.get("requested_name"))
    theme = _text(evidence.get("theme") or evidence.get("query"))
    case_type = _text(evidence.get("case_type"))
    readiness = _text(evidence.get("case_readiness"))
    counts = evidence.get("evidence_counts") or {}
    theme_evidence = evidence.get("theme_evidence") or {}
    opportunities = evidence.get("opportunities") or []
    products = evidence.get("products") or []
    technologies = evidence.get("technologies") or []
    capabilities = evidence.get("capabilities") or []
    patents = evidence.get("patents") or []
    direct_patents = evidence.get("direct_patents") or []
    grants = evidence.get("grants") or []
    regulatory = [*(evidence.get("regulatory") or []), *(evidence.get("lifecycle") or [])]
    if opportunities:
        summary = (
            f"Retained company-specific opportunity signals were found for {company_name}. "
            f"The {theme} opportunity may justify review, but every pitch claim requires analyst validation."
        )
    elif evidence.get("company_specific_count"):
        summary = (
            f"PharmaTune found limited retained company-specific context for {company_name}, "
            f"but no confirmed {theme} problem is established. This is a potential-fit draft only."
        )
    else:
        summary = (
            f"No retained company-specific evidence was found for {company_name}. "
            "The report is a prospecting shell only and must not be presented as evidence that the company has this problem."
        )
    lines = [
        f"# {company_name} — {theme} opportunity case study",
        "",
        f"**Case-study type:** {case_type}",
        "",
        f"**Readiness:** {readiness}",
        "",
        "## Executive summary",
        "",
        summary,
        "",
        "## Target company",
        "",
        f"- **Company:** {company_name}",
        f"- **Identity status:** {_text(company.get('identity_status'))}",
        f"- **Organisation type:** {_text(company.get('organisation_type'))}",
        f"- **Country:** {_text(company.get('country')) or 'Not established'}",
        f"- **Aliases/search terms:** {', '.join(evidence.get('company_terms') or [company_name])}",
        "",
        "## Strategic opportunity hypothesis",
        "",
        f"{theme} is a possible opportunity area for {company_name}. Retained records may indicate potential fit, "
        "but PharmaTune does not claim that the company has a formulation, particle-property or product-development problem unless a displayed source states it.",
        "",
        "## Why this matters for the company",
        "",
        "A validated fit could support formulation troubleshooting, product rescue, lifecycle planning or partner discovery. "
        "The commercial relevance and responsible business function require analyst validation before outreach.",
        "",
        "## Evidence snapshot",
        "",
        "| Evidence area | Company-specific records |",
        "|---|---:|",
        f"| Opportunities | {counts.get('company_opportunities', 0)} |",
        f"| Products/APIs | {counts.get('company_products_apis', 0)} |",
        f"| Technology/capability links | {counts.get('company_technology_capabilities', 0)} |",
        f"| Patent records | {counts.get('company_patents', 0)} |",
        f"| Research grants | {counts.get('company_grants', 0)} |",
        f"| Regulatory/lifecycle | {counts.get('company_regulatory_lifecycle', 0)} |",
        f"| Theme-level records | {counts.get('theme_level_records', 0)} |",
        "",
        "## Company-specific signals",
        "",
        *_bullet_rows(
            opportunities,
            ("company", "product", "molecule", "problem_category", "source_type", "matched_terms_text"),
            "No retained company-specific opportunity signal was found.",
        ),
        "",
        "## Relevant products/APIs",
        "",
        *_bullet_rows(
            products,
            ("entity_type", "name", "relationship_type", "evidence_status", "matched_terms_text"),
            "No retained company-specific product/API record was found.",
        ),
        "",
        "## Technology/problem fit",
        "",
        "Company-specific technology and capability links:",
        "",
        *_bullet_rows(
            [*technologies, *capabilities],
            (
                "display_name", "canonical_name", "relationship_type",
                "relationship_statement", "description", "evidence_status",
                "matched_terms_text",
            ),
            "No retained company-specific technology or capability link was found.",
        ),
        "",
        "**Theme-level evidence only — not company-specific.**",
        "",
        *_bullet_rows(
            [
                *(theme_evidence.get("problems") or []),
                *(theme_evidence.get("technologies") or []),
            ],
            (
                "display_name", "definition", "relationship_statement",
                "evidence_status", "matched_terms_text",
            ),
            "No retained theme-level problem or technology evidence was found.",
        ),
        "",
        "## Patent and innovation landscape",
        "",
        *_bullet_rows(
            [*patents, *direct_patents],
            (
                "publication_number", "title", "assignee_applicant",
                "source_label", "evidence_status", "matched_terms_text",
            ),
            "No company-specific retained or explicitly requested direct patent result was found.",
        ),
    ]
    if not patents and not direct_patents:
        lines.extend([
            "",
            "Generated official search routes:",
            *_bullet_rows(
                evidence.get("patent_routes") or [],
                ("source_label", "external_link", "evidence_status"),
                "No generated route is available.",
            ),
        ])
    lines.extend([
        "",
        "Patent discovery is a discovery/cross-check activity and is not a legal, validity, enforceability or freedom-to-operate opinion.",
        "",
        "## Research/grant and collaboration signals",
        "",
        *_bullet_rows(
            grants,
            (
                "funder_name", "recipient_name", "programme_name",
                "award_id", "evidence_status", "matched_terms_text",
            ),
            "No retained company-specific grant or collaboration signal was found.",
        ),
        "",
        "**Theme-level evidence only — not company-specific.**",
        "",
        *_bullet_rows(
            theme_evidence.get("grants") or [],
            ("funder_name", "recipient_name", "programme_name", "award_id", "evidence_status"),
            "No retained theme-level grant evidence was found.",
        ),
        "",
        "## Regulatory/lifecycle context",
        "",
        *_bullet_rows(
            regulatory,
            (
                "title", "trade_name", "ingredient", "regulator",
                "lifecycle_status", "evidence_status", "matched_terms_text",
            ),
            "No retained company-specific regulatory/lifecycle context was found.",
        ),
        "",
        "## Potential pitch angle",
        "",
        f"Position {theme} as a **potential fit** for analyst discussion with {company_name}. "
        "The retained signals may justify review as a possible opportunity area; they do not prove technical need, budget, ownership or buying intent.",
        "",
        "## Risks and limitations",
        "",
        *[f"- {item}" for item in evidence.get("limitations") or []],
        "- Generic formulation evidence is not evidence about the target company.",
        "- Company identity, product relevance, technical fit and commercial availability require analyst validation.",
        "- No canonical link, external discovery result or opportunity is automatically accepted or imported.",
        "",
        "## Recommended outreach/validation steps",
        "",
        "- Open every company-specific source and confirm the stated company, product and problem context.",
        "- Use Human Validation before treating any canonical candidate or technology fit as reviewed.",
        "- Confirm the responsible formulation, CMC, product-development or business-development function.",
        "- Convert the hypothesis into outreach language only after evidence and company identity are verified.",
        "",
        "## Source table",
        "",
        "| Section | Evidence | Status | Matched terms | Source ID | Link |",
        "|---|---|---|---|---|---|",
    ])
    for row in evidence.get("sources") or []:
        lines.append("| " + " | ".join([
            _text(row.get("section")).replace("|", "\\|"),
            _text(row.get("title")).replace("|", "\\|"),
            _text(row.get("source_status")).replace("|", "\\|"),
            _text(row.get("matched_terms")).replace("|", "\\|"),
            _text(row.get("source_id")).replace("|", "\\|"),
            _text(row.get("source_url")),
        ]) + " |")
    if not evidence.get("sources"):
        lines.append("| Case study | No matching evidence | no matching evidence |  |  |  |")
    return "\n".join(lines).strip() + "\n"


def render_markdown(evidence: dict[str, Any]) -> str:
    if evidence.get("mode") == "Company-specific pitch":
        return _render_company_markdown(evidence)
    query = _text(evidence.get("query"))
    case_type = _text(evidence.get("case_type"))
    opportunities = evidence.get("opportunities") or []
    products = evidence.get("products") or []
    problems = evidence.get("problems") or []
    technologies = evidence.get("technologies") or []
    grants = evidence.get("grants") or []
    patents = [*(evidence.get("patents") or []), *(evidence.get("direct_patents") or [])]
    regulatory = [*(evidence.get("regulatory") or []), *(evidence.get("lifecycle") or [])]
    counts = evidence.get("evidence_counts") or {}
    readiness = _text(evidence.get("case_readiness"))
    expanded_terms = evidence.get("expanded_terms") or [query]
    strongest = sorted(
        (
            ("opportunities", len(opportunities)),
            ("product/API records", len(products)),
            ("pharmaceutical problem records", len(problems)),
            ("technology relationships", len(technologies)),
            ("patent-source records", len(patents)),
            ("research grants", len(grants)),
            ("regulatory/lifecycle records", len(regulatory)),
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    strongest_text = ", ".join(f"{count} {label}" for label, count in strongest if count)[:300]
    if readiness == "Not enough retained evidence yet":
        executive_summary = (
            "Retained intelligence coverage is not populated enough yet to support this case study. "
            "Generated official patent-search routes are discovery aids and do not make the case ready."
        )
    else:
        executive_summary = (
            f"This evidence-grounded scan retrieved {strongest_text or 'a limited retained evidence set'}. "
            "The strongest available buckets are shown below; missing sections remain explicit evidence gaps."
        )
    lines = [
        f"# PharmaTune case study: {query}",
        "",
        f"**Case-study type:** {case_type}",
        "",
        "## Executive summary",
        "",
        executive_summary,
        "",
        "## Case readiness",
        "",
        f"**{readiness}**",
        "",
        "## Problem definition",
        "",
        f"The case theme supplied by the user is **{query}**. PharmaTune treats this phrase as a discovery scope, not as proof of a product problem.",
        "",
        f"**Original query:** {query}",
        "",
        f"**Expanded search terms used:** {', '.join(expanded_terms)}",
        "",
        "Evidence was retrieved using the case theme and related formulation/problem terms."
        if len(expanded_terms) > 1 and any(counts.values())
        else "No expanded-term retained evidence was found.",
        "",
        "## Why this matters commercially",
        "",
        "This scan is intended to support formulation troubleshooting, technology-partner discovery and analyst validation. "
        "Commercial relevance must be confirmed against the displayed retained evidence.",
        "",
        "## Evidence snapshot",
        "",
        "| Evidence area | Matching records |",
        "|---|---:|",
        f"| Opportunities | {counts.get('opportunities', len(opportunities))} |",
        f"| Products/APIs | {counts.get('products_apis', len(products))} |",
        f"| Pharmaceutical problems | {counts.get('pharmaceutical_problems', len(problems))} |",
        f"| Technology relationships | {counts.get('technology_relationships', len(technologies))} |",
        f"| Patent-source records | {counts.get('patent_source_records', len(patents))} |",
        f"| Research grants | {counts.get('research_grants', len(grants))} |",
        f"| Regulatory/lifecycle records | {counts.get('regulatory_lifecycle', len(regulatory))} |",
        "",
        "## Patent and innovation landscape",
        "",
        *_bullet_rows(patents, ("publication_number", "title", "source_label", "evidence_status", "matched_terms_text"), "No patent-source results were returned."),
        "",
        "Patent provider status:",
    ]
    provider_statuses = evidence.get("patent_provider_statuses") or {}
    for provider in provider_statuses.values():
        lines.append(
            f"- {_text(provider.get('label'))}: {_text(provider.get('status'))} · "
            f"{_text(provider.get('message'))}"
        )
    if not patents:
        lines.extend(["", "Generated official search routes:"])
        lines.extend(_bullet_rows(evidence.get("patent_routes") or [], ("source_label", "external_link", "evidence_status"), "No generated routes are available."))
    lines.extend([
        "",
        "Patent discovery is a discovery/cross-check activity and is not a legal, validity, enforceability or freedom-to-operate opinion.",
        "",
        "## Product/API landscape",
        "",
        *_bullet_rows(products, ("entity_type", "name", "identity_status", "evidence_status", "matched_terms_text"), "No matching product/API evidence was found."),
        "",
        "## Technology approaches",
        "",
        "Retained pharmaceutical problem records:",
        "",
        *_bullet_rows(problems, ("display_name", "definition", "taxonomy_label", "evidence_status", "matched_terms_text"), "No matching pharmaceutical problem record was found."),
        "",
        "Evidence-governed technology relationships:",
        "",
        *_bullet_rows(technologies, ("display_name", "relationship_type", "relationship_statement", "canonical_status", "matched_terms_text"), "No matching technology/problem relationship was found."),
        "",
        "## Companies/organisations involved",
        "",
        *_bullet_rows(opportunities, ("company", "product", "source_type", "matched_terms_text"), "No matching company or organisation evidence was found."),
        "",
        "## Research/grant signals",
        "",
        *_bullet_rows(grants, ("funder_name", "recipient_name", "programme_name", "award_id", "evidence_status", "matched_terms_text"), "No matching research-grant evidence was found."),
        "",
        "## Regulatory/lifecycle context",
        "",
        *_bullet_rows(regulatory, ("title", "trade_name", "regulator", "lifecycle_status", "evidence_status", "matched_terms_text"), "No matching regulatory/lifecycle evidence was found."),
        "",
        "## Opportunity interpretation",
        "",
        "The records above identify areas for analyst review. They do not establish demand, technical fit, ownership or a confirmed commercial transaction.",
        "",
        "## Risks and limitations",
        "",
        *[f"- {item}" for item in evidence.get("limitations") or ["No additional limitations were recorded."]],
        "",
        "## Recommended next validation steps",
        "",
        "- Open the strongest retained source links and confirm the problem statement.",
        "- Use Human Validation to approve exact canonical candidates before treating links as reviewed.",
        "- Review official patent-office routes and obtain qualified patent counsel for legal conclusions.",
        "- Confirm product/API identity, provider capability and commercial availability with authoritative sources.",
        "",
        "## Source table",
        "",
        "| Section | Evidence | Status | Matched terms | Source ID | Link |",
        "|---|---|---|---|---|---|",
    ])
    for row in evidence.get("sources") or []:
        values = [
            _text(row.get("section")),
            _text(row.get("title")).replace("|", "\\|"),
            _text(row.get("source_status")).replace("|", "\\|"),
            _text(row.get("matched_terms")).replace("|", "\\|"),
            _text(row.get("source_id")).replace("|", "\\|"),
            _text(row.get("source_url")),
        ]
        lines.append("| " + " | ".join(values) + " |")
    if not evidence.get("sources"):
        lines.append("| Case study | No matching evidence | no matching evidence |  |  |  |")
    return "\n".join(lines).strip() + "\n"


def build(
    conn,
    query: str,
    case_type: str,
    *,
    company: str = "",
    mode: str = "Company-specific pitch",
    direct_patent_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = collect(
        conn,
        query,
        case_type,
        company=company,
        mode=mode,
        direct_patent_result=direct_patent_result,
    )
    evidence["markdown"] = render_markdown(evidence)
    if evidence.get("mode") == "Company-specific pitch":
        company_name = _text((evidence.get("company") or {}).get("canonical_name") or company)
        evidence["title"] = f"{company_name} — {_text(query)} opportunity case study"
    else:
        evidence["title"] = f"PharmaTune exploration: {_text(query)}"
    return evidence
