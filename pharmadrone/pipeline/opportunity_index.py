"""Persistent Phase 2 opportunity index helpers.

This module is intentionally local and deterministic. It does not call web APIs
or LLMs. It converts discovered/generated opportunity candidates into stable
indexed records so the Streamlit MVP behaves like a persistent opportunity
engine instead of a one-time report generator.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import db
from . import precision_validation


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _norm(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9 ._:/#-]+", "", text)
    return text.strip(" ._:/#-")


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_stringify(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_stringify(v) for v in value)
    return str(value)


def _first_evidence(opp: dict[str, Any]) -> dict[str, Any]:
    ev = opp.get("evidence") or []
    return ev[0] if ev and isinstance(ev[0], dict) else {}


def _evidence_iter(opp: dict[str, Any]):
    for e in opp.get("evidence") or []:
        if isinstance(e, dict):
            yield e


def source_type(opp: dict[str, Any]) -> str:
    for e in _evidence_iter(opp):
        stype = e.get("source_type") or e.get("source_category")
        if stype:
            if stype == "recall":
                source_name = _norm(e.get("source_name"))
                regulator = _norm((e.get("entities") or {}).get("regulator"))
                ema_labels = {
                    "ema direct healthcare professional communication": "EMA safety communication",
                    "ema safety referral": "EMA safety referral",
                    "ema periodic safety assessment outcome": "EMA safety assessment outcome",
                    "ema withdrawn post-authorisation application": "EMA post-authorisation withdrawal",
                }
                if regulator == "ema" and source_name in ema_labels:
                    return ema_labels[source_name]
                if "mhra" in source_name or regulator == "mhra":
                    return "MHRA medicine recall"
                return "FDA recall"
            if stype == "trial":
                return "ClinicalTrials.gov trial"
            if stype == "shortage":
                if _norm((e.get("entities") or {}).get("regulator")) == "ema":
                    return "EMA medicine shortage"
                return "FDA drug shortage"
            return str(stype)
    return str(opp.get("source_type") or "indexed evidence")


def repair_regulator_source_labels(conn) -> int:
    """Repair legacy UK recall rows that were incorrectly hard-coded as FDA."""
    known_mhra_ids = {
        str(dict(item).get("source_id") or "") for item in conn.execute(
            "SELECT source_id FROM source_records WHERE source_name='mhra_medicine_recalls'"
        ).fetchall()
    }
    rows = conn.execute(
        "SELECT stable_lead_id,source_type,source_id,data_json FROM opportunity_index "
        "WHERE source_type IN ('FDA recall','MHRA medicine recall')"
    ).fetchall()
    repaired = 0
    for raw in rows:
        row = dict(raw)
        try:
            data = json.loads(row.get("data_json") or "{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        evidence = next(_evidence_iter(data), {})
        entities = evidence.get("entities") or {}
        fingerprint = _norm(" ".join((
            str(evidence.get("source_name") or ""), str(evidence.get("url") or ""),
            str(entities.get("regulator") or ""), str((entities.get("official_source_url") or "")),
        )))
        if ("mhra" not in fingerprint and "gov.uk" not in fingerprint
                and str(row.get("source_id") or "") not in known_mhra_ids):
            continue
        if row.get("source_type") != "MHRA medicine recall":
            conn.execute("UPDATE opportunity_index SET source_type='MHRA medicine recall' WHERE stable_lead_id=?",
                         (row["stable_lead_id"],))
            repaired += 1
    return repaired


def _ema_company_indexes(conn) -> tuple[dict[str, str], dict[str, str]]:
    rows = conn.execute(
        "SELECT record_json FROM source_records WHERE source_type='ema_medicine' AND active=1"
    ).fetchall()
    products: dict[str, set[str]] = {}
    molecules: dict[str, set[str]] = {}
    for raw in rows:
        try:
            record = json.loads(dict(raw).get("record_json") or "{}")
        except Exception:
            continue
        entities = record.get("entities") or {}
        company = str(entities.get("company") or "").strip()
        if not company:
            continue
        for target, value in ((products, entities.get("product")), (molecules, entities.get("molecule"))):
            key = _norm(value)
            if key:
                target.setdefault(key, set()).add(company)
    unique = lambda values: {key: next(iter(names)) for key, names in values.items() if len(names) == 1}
    return unique(products), unique(molecules)


def enrich_ema_companies(conn, candidates: list[dict[str, Any]]) -> int:
    """Attach only unambiguous EMA authorisation holders from the catalogue."""
    by_product, by_molecule = _ema_company_indexes(conn)
    enriched = 0
    for candidate in candidates:
        if candidate.get("company"):
            continue
        evidence = _first_evidence(candidate)
        entities = evidence.get("entities") or {}
        if _norm(entities.get("regulator")) != "ema":
            continue
        product_key = _norm(candidate.get("product") or entities.get("product"))
        molecule_key = _norm(candidate.get("molecule") or entities.get("molecule"))
        company = by_product.get(product_key) or by_molecule.get(molecule_key)
        if company:
            candidate["company"] = company
            entities["company"] = company
            enriched += 1
    return enriched


def repair_regulator_entities(conn) -> dict[str, int]:
    """Repair existing EMA holders and regulator company/product columns."""
    by_product, by_molecule = _ema_company_indexes(conn)
    counts = {"ema_companies": 0, "regulator_entities": 0}
    rows = conn.execute(
        "SELECT stable_lead_id,company,product,source_type,data_json FROM opportunity_index"
    ).fetchall()
    for raw in rows:
        row = dict(raw)
        try:
            data = json.loads(row.get("data_json") or "{}")
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        evidence = _first_evidence(data)
        entities = evidence.get("entities") or {}
        rf = entities.get("recall_fields") or {}
        company = str(entities.get("company") or rf.get("recalling_firm") or "").strip()
        product = str(entities.get("product") or rf.get("product_description") or row.get("product") or "").strip()
        regulator = _norm(entities.get("regulator"))
        if regulator == "ema" and not company:
            company = by_product.get(_norm(product)) or by_molecule.get(_norm(entities.get("molecule"))) or ""
            if company:
                entities["company"] = company
                counts["ema_companies"] += 1
        if not company and not product:
            continue
        # Never preserve the old bug where a medicine description was copied
        # into Company. A missing company is more honest than a false one.
        if company and product and _norm(company) == _norm(product):
            company = ""
        if company != str(row.get("company") or "") or product != str(row.get("product") or ""):
            data["company"] = company
            data["product"] = product
            conn.execute(
                "UPDATE opportunity_index SET company=?,product=?,data_json=? WHERE stable_lead_id=?",
                (company, product, json.dumps(data, ensure_ascii=False, default=str), row["stable_lead_id"]),
            )
            counts["regulator_entities"] += 1
    return counts


def backfill_missing_scores(conn) -> int:
    """Deterministically score indexed previews whose score/grade was never set.

    This deliberately does not claim that a full report exists. It fills only
    the always-available preview score, grade, status and stored JSON fields.
    """
    from . import score as opportunity_score

    rows = conn.execute(
        "SELECT stable_lead_id,lead_status,data_json FROM opportunity_index "
        "WHERE score IS NULL OR grade IS NULL OR grade=''"
    ).fetchall()
    updated = 0
    for raw in rows:
        row = dict(raw)
        try:
            data = json.loads(row.get("data_json") or "{}")
        except Exception:
            data = {}
        scored = opportunity_score.deterministic_score(data)
        status = lead_status({**scored, "lead_status": row.get("lead_status")})
        payload = json.dumps({**scored, "stable_lead_id": row["stable_lead_id"]}, ensure_ascii=False, default=str)
        conn.execute(
            "UPDATE opportunity_index SET score=?,grade=?,lead_status=?,data_json=? "
            "WHERE stable_lead_id=?",
            (int(scored["score"]), str(scored["grade"]), status, payload, row["stable_lead_id"]),
        )
        updated += 1
    return updated


def source_id(opp: dict[str, Any]) -> str:
    """Best stable regulatory/source identifier available for a lead."""
    # Prefer structured FDA recall number / NCT / regulatory ID.
    for e in _evidence_iter(opp):
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        for val in (
            rf.get("recall_number"),
            ent.get("recall_number"),
            ent.get("trial_id"),
            ent.get("nct_id"),
            ent.get("package_ndc"),
            ent.get("shortage_key"),
            ent.get("source_event_id"),
            ent.get("regulatory_id"),
            e.get("record_id"),
        ):
            if val:
                return str(val)
    for e in _evidence_iter(opp):
        if e.get("url"):
            return str(e["url"])
    first = _first_evidence(opp)
    if first.get("title"):
        return hashlib.sha256(str(first["title"]).encode("utf-8")).hexdigest()[:12]
    return "unknown-source"


def molecule(opp: dict[str, Any]) -> str:
    for key in ("molecule", "generic_name", "brand_name", "product", "dev_code"):
        if opp.get(key):
            return str(opp[key])
    return ""


def clean_problem_category(value: Any) -> str:
    """Return user-facing problem category labels without truncated stems.

    Discovery can intentionally use broad stems such as ``impurit`` for recall
    search recall, but the persistent index/CSV should show clean categories.
    """
    raw = str(value or "").strip()
    text = _norm(raw)
    if not text:
        return ""
    if "dissolution" in text:
        return "dissolution failure"
    if "impurit" in text or "nitrosamine" in text or "related substance" in text:
        return "impurity issue"
    if "stability" in text or "degradation" in text:
        return "stability issue"
    if "sterility" in text or "contamination" in text or "endotoxin" in text:
        return "sterility/contamination issue"
    if "particulate" in text or "visible particles" in text or "foreign particles" in text:
        return "particulate / quality issue"
    if "assay" in text or "potency" in text or "subpotent" in text or "superpotent" in text or "content uniformity" in text:
        return "assay/potency issue"
    if "failed specification" in text or "out of specification" in text or text == "oos":
        return "quality issue"
    if "bioavailability" in text or "solubility" in text:
        return "bioavailability issue"
    if "packag" in text or "container closure" in text or "leachable" in text or "extractable" in text:
        return "packaging / container-closure issue"
    if "manufactur" in text or "scale-up" in text or "batch" in text or "reproduc" in text:
        return "manufacturing variability"
    if "discontinu" in text:
        return "discontinuation signal"
    if "shortage" in text or "supply" in text or "availability" in text:
        return "supply / availability signal"
    return raw


def problem_category(opp: dict[str, Any]) -> str:
    for key in ("problem_category", "problem_signal", "failure_signal", "event_reason", "failure_reason"):
        if opp.get(key):
            return clean_problem_category(opp[key])
    # Look at recall/event reasons only as a fallback.
    blob = []
    for e in _evidence_iter(opp):
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        blob.extend(str(x) for x in (
            rf.get("reason_for_recall"), ent.get("event_reason"), e.get("supports"),
            e.get("english_summary"), e.get("title"),
        ) if x)
    category = clean_problem_category(" ".join(blob))
    return category or "unspecified product/problem signal"


def canonical_key(opp: dict[str, Any]) -> str:
    parts = [
        opp.get("company") or "unknown company",
        opp.get("product") or molecule(opp) or "unknown product",
        problem_category(opp),
        source_type(opp),
        source_id(opp),
        opp.get("region") or "unknown region",
    ]
    return "|".join(_norm(x) or "unknown" for x in parts)


def stable_lead_id(opp: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_key(opp).encode("utf-8")).hexdigest()[:24]


def evidence_hash(opp: dict[str, Any]) -> str:
    signatures: list[str] = []
    for e in _evidence_iter(opp):
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        sig = {
            "source_type": e.get("source_type"),
            "source_name": e.get("source_name"),
            "record_id": e.get("record_id"),
            "url": e.get("url"),
            "title": e.get("title"),
            "supports": e.get("supports"),
            "event_reason": ent.get("event_reason"),
            "trial_id": ent.get("trial_id"),
            "why_stopped": ent.get("why_stopped"),
            "source_event_id": ent.get("source_event_id"),
            "package_ndc": ent.get("package_ndc"),
            "shortage_reason": ent.get("shortage_reason"),
            "shortage_status": ent.get("shortage_status"),
            "recall_number": rf.get("recall_number"),
            "reason_for_recall": rf.get("reason_for_recall"),
            "status": rf.get("status"),
            "report_date": rf.get("report_date"),
        }
        signatures.append(json.dumps(sig, sort_keys=True, ensure_ascii=False, default=str))
    if not signatures:
        signatures.append(json.dumps({
            "company": opp.get("company"),
            "product": opp.get("product"),
            "problem": problem_category(opp),
            "source_id": source_id(opp),
        }, sort_keys=True, ensure_ascii=False, default=str))
    payload = "\n".join(sorted(signatures))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _evidence_links(opp: dict[str, Any]) -> list[str]:
    links: list[str] = []
    for e in _evidence_iter(opp):
        link = e.get("url") or e.get("record_id")
        if link and str(link) not in links:
            links.append(str(link))
    return links


def _normalise_lead_status(value: Any) -> str:
    raw = _norm(value)
    if "monitor" in raw:
        return "monitor only"
    if "outreach" in raw and "ready" in raw:
        return "outreach-ready"
    if "needs" in raw and "validation" in raw:
        return "needs validation"
    if "low" in raw or "archive" in raw:
        return "low priority / archive"
    return "needs validation"


def _stored_report_lead_status(opp: dict[str, Any]) -> str | None:
    report = str(opp.get("report_md") or "")
    if not report.strip():
        return None
    for pattern in (
        r"\*\*Lead classification:\*\*\s*\*\*([^*]+)\*\*",
        r"Lead classification:\*{0,2}\s*\*\*([^*]+)\*\*",
        r"Lead classification:\*{0,2}\s*([^\n—-]+)",
        r"\*\*Lead status:\*\*\s*\*\*([^*]+)\*\*",
        r"Lead status:\*{0,2}\s*\*\*([^*]+)\*\*",
        r"Lead status:\*{0,2}\s*([^\n—-]+)",
    ):
        m = re.search(pattern, report, flags=re.I)
        if m:
            return _normalise_lead_status(m.group(1))
    return None


def _status_text_blob(opp: dict[str, Any]) -> str:
    parts = [_stringify(opp.get(k)) for k in (
        "status", "lead_status", "lead_classification", "signal_status",
        "problem_signal", "problem_category", "event_reason", "failure_reason",
        "report_md",
    )]
    for e in _evidence_iter(opp):
        ent = e.get("entities") or {}
        rf = ent.get("recall_fields") or {}
        parts.extend(_stringify(x) for x in (
            e.get("title"), e.get("supports"), e.get("english_summary"), e.get("raw_text"),
            ent.get("event_type"), ent.get("event_reason"),
            rf.get("status"), rf.get("classification"), rf.get("product_quantity"),
            rf.get("distribution_pattern"), rf.get("reason_for_recall"),
        ))
    return _norm(" ".join(parts))


def _recall_status_meta(opp: dict[str, Any]) -> dict[str, bool]:
    blob = _status_text_blob(opp)
    terminated = any(x in blob for x in (
        "terminated", "recall terminated", "completed", "status terminated"
    ))
    lot_specific = any(x in blob for x in (
        "one lot", "single lot", "one batch", "single batch", "lot #",
        "lot number", "lot numbers", " lot ", " lot:"
    ))
    root_confirmed = any(x in blob for x in (
        "confirmed root cause", "root cause confirmed", "confirmed underlying root cause"
    )) and "not publicly confirmed" not in blob
    repeated_or_current = any(x in blob for x in (
        "repeated", "recurring", "multiple lots", "multiple batches", "ongoing",
        "active recall", "not terminated"
    )) and not terminated
    return {
        "terminated": terminated,
        "lot_specific": lot_specific,
        "root_confirmed": root_confirmed,
        "repeated_or_current": repeated_or_current,
    }


def lead_status(opp: dict[str, Any]) -> str:
    # Report classification is the source of truth for generated leads. This keeps
    # Opportunity Matcher cards, reports, and opportunity_index.csv aligned.
    report_status = _stored_report_lead_status(opp)
    if report_status:
        return report_status

    explicit_status = None
    for key in ("lead_status", "lead_classification"):
        if opp.get(key):
            explicit_status = _normalise_lead_status(opp.get(key))
            break

    meta = _recall_status_meta(opp)
    if meta["terminated"] and meta["lot_specific"] and not meta["root_confirmed"]:
        return "monitor only"
    if explicit_status:
        return explicit_status
    if _norm(opp.get("next_action")):
        return _normalise_lead_status(opp.get("next_action"))
    if _norm(opp.get("signal_status")) in {"indirect", "needsverification", "needs verification"}:
        return "monitor only"
    if bool(opp.get("failure_event_confirmed")) and (opp.get("score") or 0) >= 50:
        return "needs validation"
    return "monitor only" if (opp.get("score") or 0) < 50 and opp.get("score") is not None else "needs validation"


def source_freshness(record: dict[str, Any]) -> str:
    lead_status_value = _norm(record.get("lead_status") or "")
    novelty = _norm(record.get("novelty_status") or "")
    if "monitor" in lead_status_value:
        return "monitor only"
    if novelty == "updated":
        return "updated"
    if novelty == "new":
        return "current"
    # MVP-only freshness: without scheduled re-checks, old checked records are stable/seen.
    return "current" if record.get("last_checked_at") else "stale"


def make_index_record(
    opp: dict[str, Any],
    *,
    queue_rank: int | None = None,
    queue_status: str = "waiting",
    has_full_report: bool = False,
    report_path: str | None = None,
    report_opportunity_id: str | None = None,
) -> dict[str, Any]:
    sid = stable_lead_id(opp)
    stype = source_type(opp)
    sid_source = source_id(opp)
    pcat = problem_category(opp)
    record = {
        "stable_lead_id": sid,
        "company": opp.get("company") or "",
        "product": opp.get("product") or opp.get("brand_name") or opp.get("generic_name") or "",
        "molecule": molecule(opp),
        "problem_category": pcat,
        "source_type": stype,
        "source_id": sid_source,
        "region": opp.get("region") or "",
        "evidence_links_json": json.dumps(_evidence_links(opp), ensure_ascii=False),
        "score": opp.get("score"),
        "grade": opp.get("grade") or "",
        "lead_status": lead_status(opp),
        "novelty_status": "new",
        "queue_status": queue_status,
        "queue_rank": queue_rank,
        "has_full_report": 1 if has_full_report else 0,
        "report_path": report_path or "",
        "report_opportunity_id": report_opportunity_id or sid,
        "evidence_hash": evidence_hash(opp),
        "data_json": json.dumps({**opp, "stable_lead_id": sid}, ensure_ascii=False, default=str),
    }
    if has_full_report:
        record["queue_status"] = "report_generated"
    return record


def upsert_index_records(
    conn,
    candidates: list[dict[str, Any]],
    *,
    queue_status: str = "waiting",
    has_full_report: bool = False,
    starting_rank: int = 1,
    report_paths: dict[str, str] | None = None,
) -> dict[str, int]:
    counts = {"new": 0, "seen": 0, "updated": 0, "saved": 0, "rejected / hidden": 0, "monitor only": 0}
    report_paths = report_paths or {}
    for i, opp in enumerate(candidates, start=starting_rank):
        sid = stable_lead_id(opp)
        rec = make_index_record(
            opp,
            queue_rank=i,
            queue_status=queue_status,
            has_full_report=has_full_report,
            report_path=report_paths.get(sid, ""),
            report_opportunity_id=sid,
        )
        # Entity enrichment can improve a company name without creating a
        # second lead for the same official event.
        existing = conn.execute(
            "SELECT stable_lead_id FROM opportunity_index WHERE source_id=? ORDER BY first_seen_at LIMIT 2",
            (rec.get("source_id"),),
        ).fetchall()
        if len(existing) == 1:
            preserved_sid = dict(existing[0])["stable_lead_id"]
            rec["stable_lead_id"] = preserved_sid
            try:
                payload = json.loads(rec.get("data_json") or "{}")
            except Exception:
                payload = {}
            rec["data_json"] = json.dumps({**payload, "stable_lead_id": preserved_sid}, ensure_ascii=False, default=str)
        status = db.upsert_index_record(conn, rec)
        counts[status] = counts.get(status, 0) + 1
        if rec.get("lead_status") == "monitor only":
            counts["monitor only"] = counts.get("monitor only", 0) + 1
    return counts


def backfill_generated_opportunities(conn) -> dict[str, int]:
    """Index legacy Phase 1 generated reports if opportunity_index is empty/stale.

    Safe to call repeatedly: stable IDs and evidence hashes prevent duplicates.
    """
    opp_rows = db.fetch_all(conn, "opportunities")
    if not opp_rows:
        return {"new": 0, "seen": 0, "updated": 0, "backfilled": 0}
    ev_rows = db.fetch_all(conn, "evidence")
    ev_by_opp: dict[str, list[dict[str, Any]]] = {}
    for e in ev_rows:
        ev_by_opp.setdefault(str(e.get("opportunity_id") or ""), []).append(e)
    counts = {"new": 0, "seen": 0, "updated": 0, "backfilled": 0}
    for row in opp_rows:
        try:
            data = json.loads(row.get("data_json") or "{}")
        except Exception:
            data = {}
        opp = {**dict(row), **data}
        oid = str(row.get("id") or opp.get("id") or "")
        opp.setdefault("evidence", ev_by_opp.get(oid, []))
        report_path = ""
        rec = make_index_record(
            opp,
            queue_status="report_generated",
            has_full_report=True,
            report_path=report_path,
            report_opportunity_id=oid or stable_lead_id(opp),
        )
        status = db.upsert_index_record(conn, rec)
        counts[status] = counts.get(status, 0) + 1
        counts["backfilled"] += 1
    return counts




def _ascii_status_label(value: Any) -> Any:
    # Single status-label normalizer lives in db.py; this alias keeps the export
    # path backward-compatible while ensuring old stored mojibake labels are
    # fixed at CSV generation time.
    return db.normalize_status_label(value)


def export_index_csv(conn, reports_dir: Path) -> Path:
    records = db.fetch_index_records(conn, include_hidden=True)
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / "opportunity_index.csv"
    import csv
    fields = [
        "stable_lead_id", "company", "product", "molecule", "problem_category",
        "source_type", "source_id", "region", "first_seen_at", "last_seen_at",
        "last_updated_at", "last_checked_at", "score", "grade", "lead_status",
        "novelty_status", "queue_status", "queue_rank", "has_full_report",
        "report_path", "source_freshness", "enrichment_status", "corroboration_status",
        "evidence_quality", "source_coverage_count", "last_enrichment_check",
        "tier1_count", "tier2_count", "tier3_count", "tier4_count",
        "official_followup_status", "official_followup_count", "label_context_status",
        "clinical_trial_context_status", "literature_context_status", "best_evidence_tier",
        "official_source_count", "literature_source_count",
        "signal_tier", "signal_type", "broad_problem_category",
        "specific_problem_subcategory", "source_problem_text", "source_company",
        "target_company", "company_role_note", "company_match_warning",
        "company_match_warning_note", "company_identity_mismatch",
        "company_role_difference", "technical_manufacturer_differs",
        "target_is_product_owner_or_sponsor", "target_is_distributor_or_repackager_only",
        "product_owner_warning", "product_type_warning",
        "source_id_verification_status", "verification_method",
        "source_id_verification_note", "official_source_verified",
        "source_record_present", "source_id_verified_by_structured_source",
        "manual_audit_status", "external_case_study_eligible", "exclusion_reason",
        "clinical_trial_signal_code", "clinical_trial_signal_reason",
        "clinical_trial_evidence_field", "clinical_trial_evidence_text",
        "audit_correction_note", "official_source_url",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for rec in records:
            annotated = precision_validation.annotate_record(
                rec,
                seller_profile="Specialist formulation / drug-product technology provider particle engineering solubility enhancement formulation CDMO dissolution testing analytical/QC testing stability troubleshooting impurity investigation",
                official_source_url=precision_validation.extract_stored_official_url(rec),
            )
            row = {k: annotated.get(k, "") for k in fields}
            for _k in tuple(row.keys()):
                if _k.endswith("_status") or _k in {"enrichment_status", "corroboration_status"}:
                    row[_k] = _ascii_status_label(row[_k])
            row["problem_category"] = clean_problem_category(row.get("problem_category")) or row.get("problem_category", "")
            row["source_freshness"] = source_freshness(rec)
            if row.get("enrichment_status") == "source unavailable":
                row["enrichment_status"] = "external enrichment unavailable"
            writer.writerow(row)
    return out


def summarise_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    total = len(records)
    reports = sum(1 for r in records if int(r.get("has_full_report") or 0))
    waiting = sum(1 for r in records if r.get("queue_status") == "waiting" and not int(r.get("has_full_report") or 0))
    updated = sum(1 for r in records if r.get("novelty_status") == "updated")
    new = sum(1 for r in records if r.get("novelty_status") == "new")
    seen = sum(1 for r in records if r.get("novelty_status") == "seen")
    monitor = sum(1 for r in records if r.get("lead_status") == "monitor only")
    archived = sum(1 for r in records if r.get("novelty_status") in {"archived", "rejected / hidden"} or r.get("queue_status") in {"archived", "rejected"})
    return {
        "indexed_total": total,
        "full_reports": reports,
        "waiting_queue": waiting,
        "new_count": new,
        "updated_count": updated,
        "seen_count": seen,
        "monitor_only_count": monitor,
        "archived_hidden_count": archived,
    }
