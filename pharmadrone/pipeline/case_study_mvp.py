"""Read-only, evidence-grounded first case-study workflow."""
from __future__ import annotations

import json
from typing import Any

from . import patent_discovery


CASE_TYPES = (
    "Formulation innovation",
    "Product rescue",
    "Lifecycle/patent landscape",
    "Company opportunity scan",
)

EXAMPLE_CASES = (
    "Poor solubility",
    "Dissolution innovation",
    "Amorphous solid dispersion",
    "Modified release",
    "Bioavailability enhancement",
)

REPORT_SECTIONS = (
    "Executive summary",
    "Problem definition",
    "Why this matters commercially",
    "Evidence snapshot",
    "Patent and innovation landscape",
    "Product/API landscape",
    "Technology approaches",
    "Companies/organisations involved",
    "Research/grant signals",
    "Regulatory/lifecycle context",
    "Opportunity interpretation",
    "Risks and limitations",
    "Recommended next validation steps",
    "Source table",
)


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


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
) -> dict[str, str]:
    return {
        "section": section,
        "title": _text(title) or "Untitled evidence",
        "source_status": status,
        "source_id": _text(source_id),
        "source_url": _text(url),
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


def collect(
    conn,
    query: str,
    case_type: str,
    *,
    direct_patent_result: dict[str, Any] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Collect bounded stored evidence; external results must be supplied explicitly."""
    query = _text(query)
    bounded = max(1, min(int(limit), 20))
    pattern = f"%{query.casefold()}%"
    opportunities = [
        dict(row)
        for row in conn.execute(
            """SELECT stable_lead_id,company,product,molecule,problem_category,source_type,
            source_id,region,evidence_links_json,score,grade,last_updated_at,last_checked_at
            FROM opportunity_index
            WHERE (LOWER(COALESCE(company,'')) LIKE ?
                OR LOWER(COALESCE(product,'')) LIKE ?
                OR LOWER(COALESCE(molecule,'')) LIKE ?
                OR LOWER(COALESCE(problem_category,'')) LIKE ?)
              AND COALESCE(novelty_status,'') NOT IN ('archived','rejected / hidden')
              AND COALESCE(queue_status,'') NOT IN ('archived','rejected')
            ORDER BY COALESCE(score,0) DESC,COALESCE(last_updated_at,last_checked_at) DESC
            LIMIT ?""",
            (pattern, pattern, pattern, pattern, bounded),
        ).fetchall()
    ]

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
                "source_url": _first_url(row.get("evidence_links_json")),
                "source_id": _text(row.get("stable_lead_id")),
            })
    for table, id_column, kind in (
        ("product_profiles", "product_id", "product"),
        ("api_profiles", "api_id", "api"),
    ):
        for row in conn.execute(
            f"""SELECT {id_column} AS entity_id,canonical_name,identity_status,evidence_status
            FROM {table} WHERE active=1 AND LOWER(canonical_name) LIKE ?
            ORDER BY canonical_name LIMIT ?""",
            (pattern, bounded),
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

    problems = [
        dict(row)
        for row in conn.execute(
            """SELECT p.problem_id,p.display_name,p.definition,p.identity_status,p.evidence_status,
            t.label AS taxonomy_label
            FROM pharmaceutical_problems p
            JOIN intelligence_taxonomy_terms t ON t.term_id=p.taxonomy_term_id
            WHERE p.active=1 AND (LOWER(p.display_name) LIKE ? OR LOWER(p.definition) LIKE ?)
            ORDER BY p.display_name LIMIT ?""",
            (pattern, pattern, bounded),
        ).fetchall()
    ]
    technologies = [
        dict(row)
        for row in conn.execute(
            """SELECT s.technology_id,s.display_name,s.mechanism_summary,s.maturity_status,
            r.relationship_type,r.relationship_statement,r.evidence_url,r.evidence_status,
            r.inference_status,r.confidence_score,p.display_name AS problem_name
            FROM technology_problem_relationships r
            JOIN technology_solutions s ON s.technology_id=r.technology_id
            JOIN pharmaceutical_problems p ON p.problem_id=r.problem_id
            WHERE r.active=1 AND s.active=1 AND p.active=1
              AND (LOWER(p.display_name) LIKE ? OR LOWER(p.definition) LIKE ?
                   OR LOWER(s.display_name) LIKE ? OR LOWER(s.mechanism_summary) LIKE ?)
            ORDER BY r.confidence_score DESC,s.display_name LIMIT ?""",
            (pattern, pattern, pattern, pattern, bounded),
        ).fetchall()
    ]
    for row in technologies:
        row["canonical_status"] = (
            "human-reviewed canonical link"
            if row.get("inference_status") == "human-verified"
            else "requires review"
        )

    grants = [
        dict(row)
        for row in conn.execute(
            """SELECT funding_award_id,funder_name,recipient_name,award_id,programme_name,
            source_name,source_id,evidence_url,evidence_status,validation_status,last_verified_at
            FROM funding_awards WHERE active=1 AND
              (LOWER(COALESCE(funder_name,'')) LIKE ? OR LOWER(COALESCE(recipient_name,'')) LIKE ?
               OR LOWER(COALESCE(programme_name,'')) LIKE ? OR LOWER(COALESCE(award_id,'')) LIKE ?)
            ORDER BY last_verified_at DESC LIMIT ?""",
            (pattern, pattern, pattern, pattern, bounded),
        ).fetchall()
    ]
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

    patents = patent_discovery.stored_records(conn, query, "Innovation / problem theme")
    direct_rows = list((direct_patent_result or {}).get("results") or [])
    routes = patent_discovery.external_discovery_routes(query, "Innovation / problem theme")
    patent_statuses = (direct_patent_result or {}).get("providers") or patent_discovery.patent_source_health()

    regulatory = [
        {
            "title": _text(row.get("product") or row.get("molecule") or row.get("source_id")),
            "regulator": _text(row.get("source_type")),
            "problem_category": _text(row.get("problem_category")),
            "source_id": _text(row.get("source_id")),
            "source_url": _first_url(row.get("evidence_links_json")),
            "source_status": "retained internal record",
        }
        for row in opportunities
        if any(token in _text(row.get("source_type")).casefold() for token in ("fda", "ema", "mhra", "regulator"))
    ][:bounded]
    lifecycle = [
        dict(row)
        for row in conn.execute(
            """SELECT lifecycle_id,trade_name,ingredient,application_number,lifecycle_status,
            dataset_mode,official_source_url,evidence_status,next_expiry_date
            FROM lifecycle_products WHERE active=1 AND
              (LOWER(trade_name) LIKE ? OR LOWER(COALESCE(ingredient,'')) LIKE ?)
            ORDER BY last_verified_at DESC LIMIT ?""",
            (pattern, pattern, bounded),
        ).fetchall()
    ]

    sources: list[dict[str, str]] = []
    for row in opportunities:
        sources.append(_source(
            "Opportunities",
            row.get("product") or row.get("company") or row.get("problem_category"),
            "retained internal record",
            _first_url(row.get("evidence_links_json")),
            row.get("source_id"),
        ))
    for row in products:
        sources.append(_source("Product/API landscape", row["name"], row["identity_status"], row["source_url"], row["source_id"]))
    for row in technologies:
        sources.append(_source("Technology approaches", row["display_name"], row["canonical_status"], row["evidence_url"], row["technology_id"]))
    for row in canonical_links:
        sources.append(_source(
            "Canonical intelligence",
            f"{row['canonical_entity_type']} · {row['canonical_id']}",
            "human-reviewed canonical link",
            row.get("evidence_url"),
            row.get("canonical_record_link_id"),
        ))
    for row in grants:
        sources.append(_source("Research/grant signals", row.get("programme_name") or row.get("award_id"), row.get("evidence_status") or "retained internal record", row.get("evidence_url"), row.get("source_id")))
    for row in patents:
        sources.append(_source("Patent and innovation landscape", row.get("title"), "retained internal record", row.get("external_link"), row.get("publication_number")))
    for row in direct_rows:
        sources.append(_source("Patent and innovation landscape", row.get("title"), "live/direct discovery result", row.get("external_link"), row.get("publication_number")))
    if not patents and not direct_rows:
        for row in routes:
            sources.append(_source("Patent and innovation landscape", row["title"], "generated external route", row["external_link"], row["source_label"]))
    for row in regulatory:
        sources.append(_source("Regulatory/lifecycle context", row["title"], row["source_status"], row["source_url"], row["source_id"]))
    for row in lifecycle:
        sources.append(_source("Regulatory/lifecycle context", row["trade_name"], row["evidence_status"], row["official_source_url"], row["application_number"]))

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

    return {
        "query": query,
        "case_type": case_type if case_type in CASE_TYPES else CASE_TYPES[0],
        "opportunities": opportunities,
        "products": products[:bounded],
        "problems": problems,
        "technologies": technologies,
        "grants": grants,
        "patents": patents[:bounded],
        "direct_patents": direct_rows[:bounded],
        "patent_routes": routes,
        "patent_provider_statuses": patent_statuses,
        "regulatory": regulatory,
        "lifecycle": lifecycle,
        "reviewed_canonical_links": reviewed_links,
        "requires_review_links": [row for row in technologies if row["canonical_status"] == "requires review"],
        "limitations": limitations,
        "sources": sources[:100],
    }


def _bullet_rows(rows: list[dict[str, Any]], fields: tuple[str, ...], empty: str) -> list[str]:
    if not rows:
        return [f"- {empty}"]
    output = []
    for row in rows:
        values = [_text(row.get(field)) for field in fields if _text(row.get(field))]
        output.append("- " + " · ".join(values))
    return output


def render_markdown(evidence: dict[str, Any]) -> str:
    query = _text(evidence.get("query"))
    case_type = _text(evidence.get("case_type"))
    opportunities = evidence.get("opportunities") or []
    products = evidence.get("products") or []
    technologies = evidence.get("technologies") or []
    grants = evidence.get("grants") or []
    patents = [*(evidence.get("patents") or []), *(evidence.get("direct_patents") or [])]
    regulatory = [*(evidence.get("regulatory") or []), *(evidence.get("lifecycle") or [])]
    lines = [
        f"# PharmaTune case study: {query}",
        "",
        f"**Case-study type:** {case_type}",
        "",
        "## Executive summary",
        "",
        f"This evidence-grounded scan found {len(opportunities)} matching retained opportunities, "
        f"{len(products)} product/API records, {len(technologies)} technology relationships, "
        f"{len(patents)} patent-source records and {len(grants)} research-grant records. "
        "Missing sections are reported as evidence gaps rather than inferred facts.",
        "",
        "## Problem definition",
        "",
        f"The case theme supplied by the user is **{query}**. PharmaTune treats this phrase as a discovery scope, not as proof of a product problem.",
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
        f"| Opportunities | {len(opportunities)} |",
        f"| Products/APIs | {len(products)} |",
        f"| Technology relationships | {len(technologies)} |",
        f"| Patent-source records | {len(patents)} |",
        f"| Research grants | {len(grants)} |",
        f"| Regulatory/lifecycle records | {len(regulatory)} |",
        "",
        "## Patent and innovation landscape",
        "",
        *_bullet_rows(patents, ("publication_number", "title", "source_label", "evidence_status"), "No patent-source results were returned."),
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
        *_bullet_rows(products, ("entity_type", "name", "identity_status", "evidence_status"), "No matching product/API evidence was found."),
        "",
        "## Technology approaches",
        "",
        *_bullet_rows(technologies, ("display_name", "relationship_type", "relationship_statement", "canonical_status"), "No matching technology/problem relationship was found."),
        "",
        "## Companies/organisations involved",
        "",
        *_bullet_rows(opportunities, ("company", "product", "source_type"), "No matching company or organisation evidence was found."),
        "",
        "## Research/grant signals",
        "",
        *_bullet_rows(grants, ("funder_name", "recipient_name", "programme_name", "award_id", "evidence_status"), "No matching research-grant evidence was found."),
        "",
        "## Regulatory/lifecycle context",
        "",
        *_bullet_rows(regulatory, ("title", "trade_name", "regulator", "lifecycle_status", "evidence_status"), "No matching regulatory/lifecycle evidence was found."),
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
        "| Section | Evidence | Status | Source ID | Link |",
        "|---|---|---|---|---|",
    ])
    for row in evidence.get("sources") or []:
        values = [
            _text(row.get("section")),
            _text(row.get("title")).replace("|", "\\|"),
            _text(row.get("source_status")).replace("|", "\\|"),
            _text(row.get("source_id")).replace("|", "\\|"),
            _text(row.get("source_url")),
        ]
        lines.append("| " + " | ".join(values) + " |")
    if not evidence.get("sources"):
        lines.append("| Case study | No matching evidence | no matching evidence |  |  |")
    return "\n".join(lines).strip() + "\n"


def build(
    conn,
    query: str,
    case_type: str,
    *,
    direct_patent_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = collect(
        conn,
        query,
        case_type,
        direct_patent_result=direct_patent_result,
    )
    evidence["markdown"] = render_markdown(evidence)
    evidence["title"] = f"PharmaTune case study: {_text(query)}"
    return evidence
