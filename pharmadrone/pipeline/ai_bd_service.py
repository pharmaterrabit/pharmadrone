"""Evidence-grounded service layer for the standalone PharmaDrone AI product.

All factual opportunity output is derived from retained PharmaDrone records.
The module intentionally exposes bounded JSON-serialisable structures rather
than database rows or unrestricted query access.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable
from urllib.parse import urlparse
from uuid import uuid4

from pharmadrone import db
from pharmadrone.pipeline import case_study_mvp


MAX_RESULTS = 20
DEFAULT_CASE_TYPE = "Company opportunity pitch"
READINESS_STATUSES = {
    "Pitch-ready draft",
    "Partial company evidence",
    "Prospecting shell only",
    "Not enough evidence",
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _limit(value: int, default: int = 10) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, MAX_RESULTS))


def _theme(value: str) -> str:
    wanted = _text(value).casefold()
    for theme in case_study_mvp.THEMES:
        if wanted == theme.casefold():
            return theme
    raise ValueError("Unsupported opportunity theme.")


def _case_type(value: str) -> str:
    wanted = _text(value).casefold()
    for case_type in case_study_mvp.CASE_TYPES:
        if wanted == case_type.casefold():
            return case_type
    raise ValueError("Unsupported case-study type.")


def _safe_link(value: Any) -> str:
    link = _text(value)
    parsed = urlparse(link)
    return link if parsed.scheme in {"http", "https"} and parsed.netloc else ""


def _links(rows: Iterable[dict[str, Any]], *, cap: int = 30) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        link = _safe_link(
            row.get("source_url")
            or row.get("evidence_url")
            or row.get("external_link")
            or row.get("official_source_url")
        )
        if not link or link in seen:
            continue
        seen.add(link)
        output.append({
            "title": _text(row.get("title") or row.get("name") or row.get("section") or "Evidence source"),
            "url": link,
            "status": _text(row.get("source_status") or row.get("evidence_status") or "retained evidence"),
            "source_id": _text(row.get("source_id") or row.get("publication_number") or row.get("application_number")),
        })
        if len(output) >= cap:
            break
    return output


def _response(
    data: Any,
    *,
    status: str = "ok",
    limitations: list[str] | None = None,
    source_links: list[dict[str, str]] | None = None,
    suggested_next_actions: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "status": status,
        "data": data,
        "limitations": list(limitations or []),
        "source_links": list(source_links or []),
        "suggested_next_actions": list(suggested_next_actions or []),
    }
    json.dumps(payload, ensure_ascii=False)
    return payload


@contextmanager
def _connection(conn=None):
    owned = conn is None
    active = conn or db.connect()
    try:
        yield active
    finally:
        if owned:
            active.close()


def _like_clause(fields: tuple[str, ...], terms: list[str]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    for term in terms:
        pattern = f"%{term.casefold()}%"
        for field in fields:
            clauses.append(f"LOWER(COALESCE({field},'')) LIKE ?")
            params.append(pattern)
    return "(" + " OR ".join(clauses) + ")", params


def search_target_companies(theme: str, limit: int = 10, *, conn=None) -> dict[str, Any]:
    """Find bounded company candidates with retained theme-matching records."""
    selected_theme = _theme(theme)
    bounded = _limit(limit)
    terms = case_study_mvp.expand_query_terms(selected_theme)
    where, params = _like_clause(
        (
            "problem_category", "product", "molecule", "source_type",
            "evidence_links_json",
        ),
        terms,
    )
    with _connection(conn) as active:
        rows = [
            dict(row)
            for row in active.execute(
                """SELECT company,COUNT(*) AS retained_record_count,
                MAX(COALESCE(score,0)) AS strongest_score,
                MAX(COALESCE(last_updated_at,last_checked_at,'')) AS latest_evidence_at
                FROM opportunity_index
                WHERE COALESCE(company,'')<>'' AND """ + where + """
                  AND COALESCE(novelty_status,'') NOT IN ('archived','rejected / hidden')
                  AND COALESCE(queue_status,'') NOT IN ('archived','rejected')
                GROUP BY company
                ORDER BY MAX(COALESCE(score,0)) DESC,
                         MAX(COALESCE(last_updated_at,last_checked_at,'')) DESC,
                         company
                LIMIT ?""",
                (*params, bounded),
            ).fetchall()
        ]
        companies: list[dict[str, Any]] = []
        for row in rows:
            source_rows = [
                dict(source)
                for source in active.execute(
                    """SELECT source_id,product AS title,evidence_links_json AS source_url,
                    grade AS evidence_status FROM opportunity_index
                    WHERE company=? AND """ + where + """
                    ORDER BY COALESCE(score,0) DESC LIMIT 3""",
                    (row["company"], *params),
                ).fetchall()
            ]
            for source in source_rows:
                raw = source.get("source_url")
                try:
                    decoded = json.loads(raw or "[]")
                    source["source_url"] = decoded[0] if isinstance(decoded, list) and decoded else ""
                except (TypeError, ValueError):
                    source["source_url"] = ""
            companies.append({
                "company": _text(row.get("company")),
                "theme": selected_theme,
                "retained_record_count": int(row.get("retained_record_count") or 0),
                "strongest_score": int(row.get("strongest_score") or 0),
                "latest_evidence_at": _text(row.get("latest_evidence_at")),
                "source_links": _links(source_rows, cap=3),
            })
    limitations = [] if companies else [
        "No retained company-specific records matched this theme. Generated search routes alone are not leads."
    ]
    return _response(
        companies,
        status="ok" if companies else "no-evidence",
        limitations=limitations,
        source_links=[link for company in companies for link in company["source_links"]][:20],
        suggested_next_actions=[
            "Open the strongest source links and validate the company/problem relationship.",
            "Build a company pitch only after checking retained company-specific evidence.",
        ],
    )


def _evidence_summary(report: dict[str, Any]) -> str:
    counts = report.get("evidence_counts") or {}
    company_count = sum(
        int(value or 0)
        for key, value in counts.items()
        if key.startswith("company_")
    )
    if company_count:
        return (
            f"{company_count} bounded company-specific retained evidence items were found. "
            "The relationship and commercial relevance require validation."
        )
    if int(counts.get("theme_level_records") or 0):
        return "Only theme-level evidence was found; it is not evidence about the target company."
    return "No useful retained evidence was found beyond generated discovery routes."


def _lead_from_report(report: dict[str, Any]) -> dict[str, Any]:
    company = report.get("company") or {}
    company_name = _text(company.get("canonical_name") or company.get("requested_name"))
    theme = _text(report.get("theme") or report.get("query"))
    readiness = _text(report.get("case_readiness"))
    if readiness not in READINESS_STATUSES:
        readiness = "Not enough evidence"
    sources = _links(report.get("company_sources") or [], cap=20)
    evidence_ids = sorted(
        _text(row.get("source_id"))
        for row in report.get("company_sources") or []
        if _text(row.get("source_id"))
    )
    identity = "|".join([company_name.casefold(), theme.casefold(), *evidence_ids])
    lead_id = "bd-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    if readiness == "Pitch-ready draft":
        hypothesis = (
            f"Retained records indicate that {theme} may be a possible opportunity area for {company_name}."
        )
    elif readiness == "Partial company evidence":
        hypothesis = (
            f"Limited company-specific context suggests a potential fit between {company_name} and {theme}."
        )
    elif readiness == "Prospecting shell only":
        hypothesis = (
            f"{theme} is a prospecting hypothesis for {company_name}; no retained company-specific evidence supports it yet."
        )
    else:
        hypothesis = (
            f"PharmaDrone does not currently have enough retained evidence to support a {theme} opportunity for {company_name}."
        )
    pitch_angle = (
        f"Position {theme} as a potential fit for analyst discussion with {company_name}; "
        "validate technical need, product relevance and the responsible BD function before outreach."
    )
    return {
        "lead_id": lead_id,
        "target_company": company_name,
        "theme": theme,
        "readiness_status": readiness,
        "opportunity_hypothesis": hypothesis,
        "pitch_angle": pitch_angle,
        "evidence_summary": _evidence_summary(report),
        "evidence_counts": dict(report.get("evidence_counts") or {}),
        "source_links": sources,
        "limitations": list(report.get("limitations") or []),
        "recommended_next_action": (
            "Open the retained sources and submit the company/problem fit for analyst validation."
        ),
        "created_from": "retained-pharmadrone-intelligence",
    }


def generate_bd_leads(
    theme: str,
    company_filters: list[str] | None = None,
    limit: int = 10,
    *,
    conn=None,
) -> dict[str, Any]:
    """Generate evidence-grounded, bounded company lead cards."""
    selected_theme = _theme(theme)
    bounded = _limit(limit)
    filters = [_text(value) for value in (company_filters or []) if _text(value)][:bounded]
    with _connection(conn) as active:
        if filters:
            company_names = filters
        else:
            search = search_target_companies(selected_theme, bounded, conn=active)
            company_names = [row["company"] for row in search["data"]]
        leads: list[dict[str, Any]] = []
        for company in company_names[:bounded]:
            report = case_study_mvp.build(
                active,
                selected_theme,
                DEFAULT_CASE_TYPE,
                company=company,
                mode="Company-specific pitch",
            )
            lead = _lead_from_report(report)
            if not filters and lead["readiness_status"] in {
                "Prospecting shell only", "Not enough evidence",
            }:
                continue
            leads.append(lead)
    readiness_rank = {
        "Pitch-ready draft": 4,
        "Partial company evidence": 3,
        "Prospecting shell only": 2,
        "Not enough evidence": 1,
    }
    leads.sort(
        key=lambda lead: (
            readiness_rank[lead["readiness_status"]],
            len(lead["source_links"]),
            sum(int(v or 0) for v in lead["evidence_counts"].values()),
            lead["target_company"].casefold(),
        ),
        reverse=True,
    )
    source_links = [link for lead in leads for link in lead["source_links"]][:30]
    limitations = [] if leads else [
        "No retained company-specific evidence produced a supportable lead for this theme."
    ]
    return _response(
        leads[:bounded],
        status="ok" if leads else "no-evidence",
        limitations=limitations,
        source_links=source_links,
        suggested_next_actions=[
            "Review source links before using a lead externally.",
            "Build a company pitch report for a selected target.",
            "Submit uncertain company and technology links to Human Validation.",
        ],
    )


def _section(markdown: str, heading: str) -> str:
    marker = f"## {heading}"
    if marker not in markdown:
        return ""
    remainder = markdown.split(marker, 1)[1].lstrip("\n")
    return remainder.split("\n## ", 1)[0].strip()


def build_company_pitch(
    company: str,
    theme: str,
    case_type: str = DEFAULT_CASE_TYPE,
    *,
    conn=None,
) -> dict[str, Any]:
    """Build a pitch-support report by reusing the PR-46 case-study workflow."""
    company_name = _text(company)
    if not company_name:
        raise ValueError("Target company is required.")
    selected_theme = _theme(theme)
    selected_case_type = _case_type(case_type)
    with _connection(conn) as active:
        retained = case_study_mvp.build(
            active,
            selected_theme,
            selected_case_type,
            company=company_name,
            mode="Company-specific pitch",
        )
    canonical_company = _text(
        (retained.get("company") or {}).get("canonical_name") or company_name
    )
    title = f"{canonical_company} — {selected_theme} opportunity pitch report"
    markdown = retained["markdown"]
    markdown = re.sub(r"^# .*?$", f"# {title}", markdown, count=1, flags=re.M)
    report_id = "report-" + hashlib.sha256(
        f"{canonical_company.casefold()}|{selected_theme.casefold()}|{selected_case_type.casefold()}".encode("utf-8")
    ).hexdigest()[:20]
    source_table = list(retained.get("sources") or [])[:100]
    result = {
        "report_id": report_id,
        "report_title": title,
        "target_company": canonical_company,
        "theme": selected_theme,
        "case_type": selected_case_type,
        "readiness_status": retained.get("case_readiness") or "Not enough evidence",
        "executive_summary": _section(markdown, "Executive summary"),
        "strategic_opportunity_hypothesis": _section(markdown, "Strategic opportunity hypothesis"),
        "pitch_angle": _section(markdown, "Potential pitch angle"),
        "evidence_sections": {
            heading: _section(markdown, heading)
            for heading in (
                "Evidence snapshot", "Company-specific signals", "Relevant products/APIs",
                "Technology/problem fit", "Patent and innovation landscape",
                "Research/grant and collaboration signals", "Regulatory/lifecycle context",
            )
        },
        "source_table": source_table,
        "limitations": list(retained.get("limitations") or []),
        "recommended_next_actions": [
            "Open every company-specific source and validate the stated company/product context.",
            "Use Human Validation before treating canonical or technology links as reviewed.",
            "Confirm technical need and the responsible BD function before outreach.",
        ],
        "markdown_report": markdown,
    }
    return _response(
        result,
        status="ok" if result["readiness_status"] != "Not enough evidence" else "no-evidence",
        limitations=result["limitations"],
        source_links=_links(source_table, cap=30),
        suggested_next_actions=result["recommended_next_actions"],
    )


def _context(company: str, theme: str, keys: tuple[str, ...], *, conn=None) -> dict[str, Any]:
    pitch = build_company_pitch(company, theme, conn=conn)
    report = pitch["data"]
    sections = report.get("evidence_sections") or {}
    data = {key: sections.get(key, "") for key in keys}
    return _response(
        data,
        status=pitch["status"],
        limitations=pitch["limitations"],
        source_links=pitch["source_links"],
        suggested_next_actions=pitch["suggested_next_actions"],
    )


def get_lead_evidence(company: str, theme: str, *, conn=None) -> dict[str, Any]:
    return _context(
        company, theme,
        ("Company-specific signals", "Relevant products/APIs", "Technology/problem fit"),
        conn=conn,
    )


def get_patent_discovery_context(company: str, theme: str, *, conn=None) -> dict[str, Any]:
    return _context(company, theme, ("Patent and innovation landscape",), conn=conn)


def get_research_grant_context(company: str, theme: str, *, conn=None) -> dict[str, Any]:
    return _context(company, theme, ("Research/grant and collaboration signals",), conn=conn)


def get_regulatory_lifecycle_context(company: str, theme: str, *, conn=None) -> dict[str, Any]:
    return _context(company, theme, ("Regulatory/lifecycle context",), conn=conn)


def get_source_table(company: str, theme: str, *, conn=None) -> dict[str, Any]:
    pitch = build_company_pitch(company, theme, conn=conn)
    return _response(
        pitch["data"].get("source_table") or [],
        status=pitch["status"],
        limitations=pitch["limitations"],
        source_links=pitch["source_links"],
        suggested_next_actions=pitch["suggested_next_actions"],
    )


def _require_membership(conn, user_id: str, workspace_id: str) -> None:
    row = conn.execute(
        """SELECT membership_id FROM saas_workspace_memberships
        WHERE user_id=? AND workspace_id=? AND active=1 LIMIT 1""",
        (_text(user_id), _text(workspace_id)),
    ).fetchone()
    if not row:
        raise PermissionError("Authenticated user does not belong to this workspace.")


def save_lead(user_id: str, workspace_id: str, lead: dict[str, Any], *, conn=None) -> dict[str, Any]:
    required = ("lead_id", "target_company", "theme", "readiness_status", "pitch_angle")
    if any(not _text(lead.get(key)) for key in required):
        raise ValueError("Lead payload is incomplete.")
    if lead["readiness_status"] not in READINESS_STATUSES:
        raise ValueError("Lead readiness status is invalid.")
    with _connection(conn) as active:
        _require_membership(active, user_id, workspace_id)
        existing = active.execute(
            """SELECT saved_lead_id FROM saas_saved_leads
            WHERE workspace_id=? AND user_id=? AND lead_id=? LIMIT 1""",
            (workspace_id, user_id, lead["lead_id"]),
        ).fetchone()
        saved_id = existing["saved_lead_id"] if existing else f"saved-lead-{uuid4().hex}"
        if not existing:
            active.execute(
                """INSERT INTO saas_saved_leads
                (saved_lead_id,lead_id,workspace_id,user_id,target_company,theme,
                 readiness_status,pitch_angle,evidence_summary,source_links_json,
                 limitations_json,lead_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    saved_id, lead["lead_id"], workspace_id, user_id,
                    _text(lead["target_company"]), _theme(lead["theme"]),
                    lead["readiness_status"], _text(lead["pitch_angle"]),
                    _text(lead.get("evidence_summary")),
                    json.dumps(lead.get("source_links") or [], ensure_ascii=False),
                    json.dumps(lead.get("limitations") or [], ensure_ascii=False),
                    json.dumps(lead, ensure_ascii=False), _now(),
                ),
            )
            active.commit()
    return _response({"saved_lead_id": saved_id, "lead_id": lead["lead_id"]})


def save_report(user_id: str, workspace_id: str, report: dict[str, Any], *, conn=None) -> dict[str, Any]:
    required = ("report_id", "report_title", "target_company", "theme", "case_type", "readiness_status", "markdown_report")
    if any(not _text(report.get(key)) for key in required):
        raise ValueError("Report payload is incomplete.")
    if report["readiness_status"] not in READINESS_STATUSES:
        raise ValueError("Report readiness status is invalid.")
    with _connection(conn) as active:
        _require_membership(active, user_id, workspace_id)
        existing = active.execute(
            """SELECT saved_report_id FROM saas_saved_reports
            WHERE workspace_id=? AND user_id=? AND report_id=? LIMIT 1""",
            (workspace_id, user_id, report["report_id"]),
        ).fetchone()
        saved_id = existing["saved_report_id"] if existing else f"saved-report-{uuid4().hex}"
        if not existing:
            active.execute(
                """INSERT INTO saas_saved_reports
                (saved_report_id,report_id,workspace_id,user_id,report_title,
                 target_company,theme,case_type,readiness_status,markdown_report,
                 source_table_json,report_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    saved_id, report["report_id"], workspace_id, user_id,
                    _text(report["report_title"]), _text(report["target_company"]),
                    _theme(report["theme"]), _case_type(report["case_type"]),
                    report["readiness_status"], str(report["markdown_report"]),
                    json.dumps(report.get("source_table") or [], ensure_ascii=False),
                    json.dumps(report, ensure_ascii=False), _now(),
                ),
            )
            active.commit()
    return _response({"saved_report_id": saved_id, "report_id": report["report_id"]})
