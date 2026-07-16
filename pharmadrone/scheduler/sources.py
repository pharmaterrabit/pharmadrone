"""Bounded source jobs for Checkpoint 6C.1.

The scheduler automates only sources already connected to PharmaTune. It does
not alter connector parsing or deterministic classification.
"""
from __future__ import annotations

from datetime import timedelta
import json
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .. import db, settings
from ..connectors import (
    fda_orange_book, ema_medicines, ema_opportunities, mhra_alerts, openfda_enforcement, openfda_shortages, clinicaltrials, openfda,
    europepmc, openalex, crossref, tavily_search, commercial_signals, epo_ops,
)
from ..cost import CostTracker
from ..pipeline import event_discovery, query_safety
from .config import Guardrails, parse_time, utc_now
from .errors import SchedulerError, classify_error, safe_summary


def _deadline(guards: Guardrails) -> float:
    return time.monotonic() + guards.max_processing_seconds


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SchedulerError("processing-time cap reached", "budget limit", retryable=False)


def _result_or_raise(res, source_name: str):
    if not res.ok:
        cls, retryable = classify_error(res.error or "source failed")
        raise SchedulerError(f"{source_name}: {res.error or 'source failed'}", cls, retryable=retryable)
    return list(res.records or []), dict(res.stats or {})


def _date_after_lookback(value: str, watermark: str, lookback_days: int) -> bool:
    if not watermark or not value:
        return True
    value_dt = parse_time(value)
    water_dt = parse_time(watermark)
    if not value_dt or not water_dt:
        return True
    return value_dt >= water_dt - timedelta(days=lookback_days)


def _priority_terms(conn, limit: int = 25) -> list[dict[str, str]]:
    rows = db.fetch_index_records(conn, include_hidden=False)
    rows.sort(key=lambda r: (
        0 if r.get("has_full_report") else 1,
        -int(r.get("score") or 0),
        str(r.get("company") or ""),
    ))
    out = []
    for row in rows[:limit]:
        term = str(row.get("molecule") or row.get("product") or "").strip()
        if not term:
            continue
        out.append({
            "stable_lead_id": str(row.get("stable_lead_id") or ""),
            "term": term,
            "problem": str(row.get("problem_category") or "").strip(),
            "company": str(row.get("company") or "").strip(),
        })
    return out


def fetch_openfda_enforcement(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    res = openfda_enforcement.discover_taxonomy(
        page_size=min(100, int(settings.env("OPENFDA_RECALL_PAGE_SIZE", "50") or 50)),
        max_pages=min(guards.max_pages_per_connector, int(settings.env("OPENFDA_RECALL_MAX_PAGES_PER_CATEGORY", "3") or 3)),
        max_results=guards.max_records_per_connector,
    )
    records, stats = _result_or_raise(res, "openfda_enforcement")
    # FDA enforcement has no reliable modified-since cursor in the current
    # connector; use bounded latest/taxonomy lookback plus deterministic checksum.
    watermark = ""
    for rec in records:
        rf = ((rec.get("entities") or {}).get("recall_fields") or {})
        watermark = max(watermark, str(rf.get("report_date") or rf.get("center_classification_date") or ""))
    return {"records": records, "cursor_after": "bounded-taxonomy-sweep", "watermark_after": watermark, "metadata": stats}


def fetch_ema_medicines(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    max_records = min(5000, max(1, int(settings.env("EMA_MEDICINES_MAX_RECORDS", "3000") or 3000)))
    res = ema_medicines.fetch(max_results=max_records)
    records, stats = _result_or_raise(res, "ema_medicines")
    previous = str(state.get("last_watermark") or "")
    if previous and not force:
        records = [record for record in records if _date_after_lookback(
            str((record.get("entities") or {}).get("last_update_date") or ""), previous, guards.lookback_days
        )]
    watermark = max([str((record.get("entities") or {}).get("last_update_date") or "") for record in records] + [previous])
    return {
        "records": records,
        "cursor_after": f"feed:{stats.get('feed_timestamp') or 'unknown'}",
        "watermark_after": watermark,
        "metadata": {**stats, "incremental_strategy": "EMA feed timestamp, record last-update watermark and bounded lookback"},
    }


def fetch_ema_opportunity_feed(feed_name: str, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    res = ema_opportunities.fetch(feed_name, max_results=min(5000, guards.max_records_per_connector))
    records, stats = _result_or_raise(res, feed_name)
    previous = str(state.get("last_watermark") or "")
    if previous and not force:
        records = [item for item in records if _date_after_lookback(
            str((item.get("entities") or {}).get("last_update_date") or ""), previous, guards.lookback_days
        )]
    watermark = max([str((item.get("entities") or {}).get("last_update_date") or "") for item in records] + [previous])
    return {"records": records, "cursor_after": f"feed:{stats.get('feed_timestamp') or 'unknown'}",
            "watermark_after": watermark, "metadata": {**stats, "incremental_strategy": "official EMA event feed, watermark, lookback and checksum"}}


def fetch_ema_shortages(conn, state, guards, *, force=False):
    return fetch_ema_opportunity_feed("ema_shortages", state, guards, force=force)


def fetch_ema_dhpc(conn, state, guards, *, force=False):
    return fetch_ema_opportunity_feed("ema_dhpc", state, guards, force=force)


def fetch_ema_safety_referrals(conn, state, guards, *, force=False):
    return fetch_ema_opportunity_feed("ema_safety_referrals", state, guards, force=force)


def fetch_ema_psusa_outcomes(conn, state, guards, *, force=False):
    return fetch_ema_opportunity_feed("ema_psusa_outcomes", state, guards, force=force)


def fetch_ema_post_authorisation_withdrawals(conn, state, guards, *, force=False):
    return fetch_ema_opportunity_feed("ema_post_authorisation_withdrawals", state, guards, force=force)


def fetch_mhra_medicine_recalls(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    max_records = min(1000, max(1, int(settings.env("MHRA_RECALL_MAX_RECORDS", "1000") or 1000)))
    res = mhra_alerts.fetch(max_results=max_records)
    records, stats = _result_or_raise(res, "mhra_medicine_recalls")
    previous = str(state.get("last_watermark") or "")
    if previous and not force:
        records = [record for record in records if _date_after_lookback(
            str((record.get("entities") or {}).get("last_update_date") or ""), previous, guards.lookback_days
        )]
    watermark = max([str((record.get("entities") or {}).get("last_update_date") or "") for record in records] + [previous])
    return {
        "records": records, "cursor_after": f"newest:{len(records)}", "watermark_after": watermark,
        "metadata": {**stats, "incremental_strategy": "full bounded MHRA medicine-recall index, publication watermark, lookback and checksum"},
    }


def fetch_fda_orange_book(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    max_records = min(5000, max(1, int(settings.env("FDA_ORANGE_BOOK_MAX_RECORDS", "5000") or 5000)))
    res = fda_orange_book.fetch(max_results=max_records)
    records, stats = _result_or_raise(res, "fda_orange_book")
    newest = max([
        str((item.get("entities") or {}).get("approval_date") or "") for item in records
    ] + [str(state.get("last_watermark") or "")])
    return {
        "records": records, "cursor_after": f"archive:{stats.get('products_in_archive', 0)}",
        "watermark_after": newest,
        "metadata": {**stats, "incremental_strategy": "monthly official archive, deterministic record checksum and application/product key"},
    }


def fetch_openfda_shortages(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    res = openfda_shortages.discover_shortages(
        max_results=guards.max_records_per_connector,
        page_size=min(100, int(settings.env("OPENFDA_SHORTAGE_PAGE_SIZE", "50") or 50)),
        max_pages=min(guards.max_pages_per_connector, int(settings.env("OPENFDA_SHORTAGE_MAX_PAGES", "6") or 6)),
    )
    records, stats = _result_or_raise(res, "openfda_shortages")
    previous = str(state.get("last_watermark") or "")
    if previous and not force:
        records = [r for r in records if _date_after_lookback(str((r.get("entities") or {}).get("update_date") or ""), previous, guards.lookback_days)]
    watermark = max([str((r.get("entities") or {}).get("update_date") or "") for r in records] + [previous])
    return {"records": records, "cursor_after": f"skip-pages:{stats.get('pages_run', 0)}", "watermark_after": watermark, "metadata": stats}


def fetch_clinicaltrials(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    """Fetch new/changed medicinal interventional studies incrementally.

    Unlike the interactive stopped-trial discovery, the scheduled refresh also
    retains active/new formulation-development studies. Frozen deterministic
    classification remains unchanged; the scheduler only broadens refresh
    coverage within the already connected ClinicalTrials.gov source.
    """
    unique: dict[str, dict] = {}
    rejected: dict[str, int] = {}
    raw = 0
    deadline = _deadline(guards)
    previous = str(state.get("last_watermark") or "")
    topics_run = 0
    latest_token = ""
    page_size = min(100, int(settings.env("CLINICALTRIALS_PAGE_SIZE", "50") or 50))
    max_pages = min(guards.max_pages_per_connector, int(settings.env("CLINICALTRIALS_MAX_PAGES_PER_TOPIC", "2") or 2))
    for topic in event_discovery.TRIAL_STOP_TOPICS:
        _check_deadline(deadline)
        if len(unique) >= guards.max_records_per_connector:
            break
        next_token = ""
        for _page in range(max_pages):
            _check_deadline(deadline)
            remaining = guards.max_records_per_connector - len(unique)
            if remaining <= 0:
                break
            params = {
                "query.term": topic,
                "pageSize": min(page_size, remaining),
                "format": "json",
                "sort": "LastUpdatePostDate:desc",
            }
            if next_token:
                params["pageToken"] = next_token
            try:
                data = clinicaltrials.get_json(clinicaltrials.BASE, params)
            except Exception as exc:
                cls, retryable = classify_error(str(exc))
                raise SchedulerError(f"clinicaltrials: {exc}", cls, retryable=retryable) from exc
            topics_run += 1 if _page == 0 else 0
            studies = data.get("studies", []) or []
            raw += len(studies)
            for study in studies:
                rec, meta = clinicaltrials._row(study, topic)
                reason = None
                if not meta.get("nct"):
                    reason = "missing NCT ID"
                elif meta.get("study_type") != "INTERVENTIONAL":
                    reason = "not an interventional study"
                elif not meta.get("sponsor"):
                    reason = "missing lead sponsor"
                elif not meta.get("interventions"):
                    reason = "no usable medicinal intervention"
                elif not meta.get("context_supported"):
                    reason = "query topic not supported by stored registry text"
                if reason:
                    rejected[reason] = rejected.get(reason, 0) + 1
                    continue
                updated = str((rec.get("entities") or {}).get("last_update_date") or "")
                if previous and not force and not _date_after_lookback(updated, previous, guards.lookback_days):
                    continue
                unique.setdefault(str(rec.get("record_id") or "").upper(), rec)
            next_token = str(data.get("nextPageToken") or "")
            latest_token = next_token or latest_token
            if not next_token or not studies:
                break
    records = list(unique.values())
    watermark = max([str((r.get("entities") or {}).get("last_update_date") or "") for r in records] + [previous])
    return {
        "records": records,
        "cursor_after": latest_token or f"topics:{topics_run}",
        "watermark_after": watermark,
        "metadata": {
            "topics_run": topics_run, "raw_results": raw,
            "records_rejected": sum(rejected.values()), "rejection_reasons": rejected,
            "incremental_strategy": "LastUpdatePostDate watermark plus bounded lookback and NCT checksum",
        },
    }

def fetch_openfda_labels(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    records: list[dict] = []
    queries = 0
    for item in _priority_terms(conn, min(25, guards.max_records_per_connector)):
        if len(records) >= guards.max_records_per_connector:
            break
        res = openfda.search(item["term"], max_results=3)
        queries += 1
        if res.ok:
            records.extend(res.records or [])
    return {"records": records[:guards.max_records_per_connector], "cursor_after": f"priority-leads:{queries}", "watermark_after": "", "metadata": {"queries": queries}}


def _fetch_literature(conn, guards: Guardrails, connector, source_name: str) -> dict[str, Any]:
    records: list[dict] = []
    queries = 0
    priority = _priority_terms(conn, min(20, guards.max_records_per_connector))
    research_seeds = [
        {"term": "pharmaceutical formulation drug delivery", "problem": ""},
        {"term": "solubility enhancement pharmaceutical", "problem": ""},
        {"term": "modified release formulation", "problem": ""},
        {"term": "nanoparticle drug delivery", "problem": ""},
        {"term": "biologics formulation stability", "problem": ""},
        {"term": "continuous pharmaceutical manufacturing", "problem": ""},
        {"term": "pharmaceutical excipient compatibility", "problem": ""},
        {"term": "university pharmaceutical technology transfer", "problem": ""},
    ]
    seen_queries: set[str] = set()
    for item in [*priority, *research_seeds]:
        if len(records) >= guards.max_records_per_connector:
            break
        query = " ".join(x for x in (item["term"], item["problem"], "formulation") if x).strip()
        if query.casefold() in seen_queries:
            continue
        seen_queries.add(query.casefold())
        res = connector.search(query, max_results=3)
        queries += 1
        if res.ok:
            records.extend(res.records or [])
    return {"records": records[:guards.max_records_per_connector], "cursor_after": f"priority-queries:{queries}", "watermark_after": "", "metadata": {"queries": queries, "source": source_name}}


def fetch_europepmc(conn, state, guards, *, force=False):
    return _fetch_literature(conn, guards, europepmc, "Europe PMC")


def fetch_openalex(conn, state, guards, *, force=False):
    return _fetch_literature(conn, guards, openalex, "OpenAlex")


def fetch_crossref(conn, state, guards, *, force=False):
    return _fetch_literature(conn, guards, crossref, "Crossref")



def fetch_monthly_maintenance(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    """Bounded source-URL checks for the monthly maintenance job.

    Trial status refresh already runs every two days. This monthly job validates
    currently stored official URLs and hands deterministic stale/current-review
    work to the orchestrator without modifying frozen source or audit records.
    """
    limit = min(guards.max_records_per_connector, int(settings.env("SCHEDULER_MONTHLY_URL_CHECK_LIMIT", "50") or 50))
    rows = conn.execute(
        "SELECT source_type, source_id, official_source_url FROM source_records "
        "WHERE active=1 AND official_source_url IS NOT NULL AND official_source_url<>'' "
        "ORDER BY last_seen_at DESC LIMIT ?", (limit,)
    ).fetchall()
    checks: list[dict[str, Any]] = []
    deadline = _deadline(guards)
    timeout = min(15.0, max(3.0, float(settings.env("SCHEDULER_URL_CHECK_TIMEOUT_SECONDS", "8") or 8)))
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": "PharmaTune/6C.1 source validation"}) as client:
        for row in rows:
            if time.monotonic() >= deadline:
                break
            item = dict(row)
            url = str(item.get("official_source_url") or "").strip()
            parsed = urlparse(url)
            result = {
                "source_type": item.get("source_type"), "source_id": item.get("source_id"),
                "official_source_url": url, "status": "unavailable", "http_status": None, "error_summary": "",
            }
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                result.update(status="invalid_url", error_summary="Official source URL is not a valid HTTP(S) URL.")
                checks.append(result)
                continue
            try:
                response = client.head(url)
                if response.status_code in {405, 501}:
                    response = client.get(url, headers={"Range": "bytes=0-1023"})
                code = int(response.status_code)
                result["http_status"] = code
                result["status"] = "available" if code < 400 else ("rejected" if code in {401, 403, 429} else "unavailable")
                if code >= 400:
                    result["error_summary"] = f"HTTP {code} returned during bounded official-source URL check."
            except Exception as exc:
                result["error_summary"] = safe_summary(str(exc), 180)
            checks.append(result)
    return {
        "records": [], "cursor_after": f"url-checks:{len(checks)}",
        "watermark_after": state.get("last_watermark") or "",
        "metadata": {"url_checks": checks, "url_checks_attempted": len(checks), "url_check_limit": limit},
        "partial": len(checks) < len(rows),
    }

def fetch_tavily(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    if not settings.env("TAVILY_API_KEY"):
        raise SchedulerError("TAVILY_API_KEY missing", "authentication failure", retryable=False)
    profile = settings.load_profile()
    cost = CostTracker(profile.get("pricing_usd_per_million_tokens", {}))
    records: list[dict] = []
    queries = 0
    for item in _priority_terms(conn, guards.max_tavily_calls):
        if queries >= guards.max_tavily_calls:
            break
        query = query_safety.sanitize_tavily_query(
            f"{item['company']} {item['term']} {item['problem']} official product company statement"
        )
        res = tavily_search.search(query, max_results=3, cost=cost)
        queries += 1
        if res.ok:
            records.extend(res.records or [])
        if cost.total_usd >= guards.max_estimated_spend_usd:
            break
    partial = queries >= guards.max_tavily_calls or cost.total_usd >= guards.max_estimated_spend_usd
    return {
        "records": records[:guards.max_records_per_connector],
        "cursor_after": f"queries:{queries}", "watermark_after": "",
        "estimated_spend": cost.total_usd,
        "partial": partial,
        "metadata": {"queries": queries, "tavily_calls": cost.tavily_calls, "estimated_spend": cost.total_usd},
    }


def fetch_account_intelligence(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    """The orchestrator projects already-stored evidence; no network fetch is needed."""
    return {
        "records": [], "cursor_after": "weekly-account-projection",
        "watermark_after": state.get("last_watermark") or "",
        "metadata": {"mode": "evidence-governed organisation/contact projection"},
    }


def fetch_patent_lifecycle(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    return {
        "records": [], "cursor_after": "weekly-patent-lifecycle-projection",
        "watermark_after": state.get("last_watermark") or "",
        "metadata": {"mode": "stored FDA Orange Book plus EPO/UK global patent projection; Google discovery links"},
    }


def fetch_epo_ops_patents(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    """Fetch bounded EP and GB patent publications for retained product terms."""
    configured = [item.strip() for item in settings.env("EPO_OPS_QUERIES", "").split(";") if item.strip()]
    if configured:
        queries = configured[:guards.max_pages_per_connector]
    else:
        rows = conn.execute(
            "SELECT DISTINCT ingredient FROM lifecycle_products WHERE active=1 AND COALESCE(ingredient,'')<>'' "
            "ORDER BY last_verified_at DESC LIMIT ?", (min(guards.max_pages_per_connector, 5),)
        ).fetchall()
        queries = []
        for row in rows:
            term = " ".join(str(row["ingredient"] or "").replace('"', "").split())[:100]
            if term:
                queries.append(f'ctxt="{term}" and (pn=EP or pn=GB)')
    if not queries:
        return {"records": [], "cursor_after": "no-retained-product-terms", "watermark_after": "",
                "metadata": {"queries": 0, "mode": "official EPO OPS"}}
    unique: dict[str, dict] = {}; failures: list[str] = []
    per_query = max(1, min(100, guards.max_records_per_connector // max(1, len(queries))))
    for query in queries:
        result = epo_ops.search(query, range_end=per_query)
        if not result.ok:
            failures.append(result.error or "EPO OPS failed")
            continue
        for item in result.records:
            unique[str(item.get("record_id") or "")] = item
    if failures and not unique:
        raise SchedulerError(f"epo_ops_patents: {failures[0]}", "authentication failure" if "required" in failures[0] else "source failure", retryable=False)
    return {
        "records": list(unique.values())[:guards.max_records_per_connector],
        "cursor_after": f"queries:{len(queries)}", "watermark_after": "",
        "metadata": {"queries": len(queries), "failed_queries": len(failures),
                     "documents": len(unique), "jurisdictions": ["EP", "GB"],
                     "source_authority": "official EPO OPS"},
    }


def fetch_research_innovation(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    return {
        "records": [], "cursor_after": "weekly-research-innovation-projection",
        "watermark_after": state.get("last_watermark") or "",
        "metadata": {"mode": "stored publication, institution, trial-collaboration and transfer-evidence projection"},
    }


def fetch_deal_discovery(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    """Discover bounded commercial signals for retained organisations.

    Results remain verification-required unless the result URL matches the
    organisation's retained official domain.
    """
    rows = conn.execute(
        "SELECT canonical_name,official_website_url FROM account_organisations WHERE active=1 "
        "ORDER BY last_verified_at DESC,canonical_name LIMIT ?", (guards.max_tavily_calls,)
    ).fetchall()
    cost = CostTracker(); records: list[dict] = []; queries = 0; failures = 0
    for row in rows:
        if queries >= guards.max_tavily_calls or cost.total_usd >= guards.max_estimated_spend_usd:
            break
        result = commercial_signals.discover(
            str(row["canonical_name"]), str(row["official_website_url"] or ""), max_results=3, cost=cost
        )
        queries += 1
        if result.ok: records.extend(result.records or [])
        else: failures += 1
    return {
        "records": records[:guards.max_records_per_connector], "cursor_after": f"organisations:{queries}",
        "watermark_after": "", "metadata": {"queries": queries, "failed_queries": failures,
        "tavily_calls": cost.tavily_calls, "estimated_spend": cost.total_usd,
        "governance": "discovery signals require primary-source and human verification"},
    }


def fetch_commercial_intelligence(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    return {
        "records": [], "cursor_after": "weekly-commercial-intelligence-projection",
        "watermark_after": state.get("last_watermark") or "",
        "metadata": {"mode": "stored transaction, commercial-signal and scholarly-funding projection"},
    }


def fetch_customer_alerts(conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    return {
        "records": [], "cursor_after": "daily-customer-alert-evaluation",
        "watermark_after": state.get("last_watermark") or "",
        "metadata": {"mode": "stored customer alert rules evaluated against stored intelligence"},
    }


FETCHERS = {
    "fda_orange_book": fetch_fda_orange_book,
    "ema_medicines": fetch_ema_medicines,
    "ema_shortages": fetch_ema_shortages,
    "ema_dhpc": fetch_ema_dhpc,
    "ema_safety_referrals": fetch_ema_safety_referrals,
    "ema_psusa_outcomes": fetch_ema_psusa_outcomes,
    "ema_post_authorisation_withdrawals": fetch_ema_post_authorisation_withdrawals,
    "mhra_medicine_recalls": fetch_mhra_medicine_recalls,
    "openfda_enforcement": fetch_openfda_enforcement,
    "openfda_shortages": fetch_openfda_shortages,
    "openfda_labels": fetch_openfda_labels,
    "clinicaltrials": fetch_clinicaltrials,
    "europepmc": fetch_europepmc,
    "openalex": fetch_openalex,
    "crossref": fetch_crossref,
    "tavily": fetch_tavily,
    "account_intelligence": fetch_account_intelligence,
    "patent_lifecycle": fetch_patent_lifecycle,
    "epo_ops_patents": fetch_epo_ops_patents,
    "research_innovation": fetch_research_innovation,
    "deal_discovery": fetch_deal_discovery,
    "commercial_intelligence": fetch_commercial_intelligence,
    "customer_alerts": fetch_customer_alerts,
    "monthly_maintenance": fetch_monthly_maintenance,
}


def fetch_source(source_name: str, conn, state: dict[str, Any], guards: Guardrails, *, force: bool = False) -> dict[str, Any]:
    try:
        return FETCHERS[source_name](conn, state, guards, force=force)
    except KeyError:
        raise SchedulerError(f"unknown source job: {source_name}", "validation failure", retryable=False)
