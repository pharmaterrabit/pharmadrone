"""Checkpoint 8.3 evidence-governed organisation and contact intelligence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from . import opportunity_index


LEGAL_SUFFIXES = {
    "ag", "bv", "co", "company", "corp", "corporation", "gmbh", "inc", "incorporated",
    "kg", "kgaa", "limited", "llc", "lp", "ltd", "nv", "oy", "plc", "pte", "sa", "sas",
    "spa", "srl",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _key(value: Any) -> str:
    words = re.findall(r"[a-z0-9]+", _text(value).casefold())
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def _id(prefix: str, *parts: Any) -> str:
    payload = "|".join(_key(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _valid_url(value: Any) -> str:
    value = _text(value)
    return value if value.startswith(("https://", "http://")) else ""


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _organisation_type(name: str, entities: dict[str, Any]) -> str:
    explicit = _text(entities.get("organisation_type") or entities.get("sponsor_class")).casefold()
    blob = f"{name} {explicit}".casefold()
    if any(term in blob for term in ("university", "hospital", "institute", "foundation", "research centre", "research center")):
        return "research / healthcare organisation"
    if any(term in blob for term in ("agency", "ministry", "authority", "government")):
        return "public-sector organisation"
    return "commercial organisation"


def _organisation_name(entities: dict[str, Any]) -> str:
    recall = entities.get("recall_fields") if isinstance(entities.get("recall_fields"), dict) else {}
    for value in (
        entities.get("company"), entities.get("sponsor"), entities.get("lead_sponsor"),
        entities.get("marketing_authorisation_holder"),
        entities.get("marketing_authorisation_developer_applicant_holder"),
        recall.get("recalling_firm"), entities.get("manufacturer"),
    ):
        if _text(value):
            return _text(value)
    return ""


def _source_relationship(source_type: str) -> str:
    source = source_type.casefold()
    if "ema_medicine" in source:
        return "marketing authorisation holder for"
    if "orange_book" in source:
        return "FDA application holder for"
    if "trial" in source:
        return "clinical trial sponsor for"
    if "recall" in source or "enforcement" in source:
        return "recalling / responsible organisation for"
    if "shortage" in source:
        return "associated with shortage signal for"
    return "evidence-linked organisation for"


def _upsert_organisation(conn, name: str, entities: dict[str, Any], observed_at: str,
                         next_review: str) -> str:
    canonical_key = _key(name)
    organisation_id = _id("acct", canonical_key)
    country = _text(entities.get("country") or entities.get("region"))
    website = _valid_url(
        entities.get("official_website_url") or entities.get("company_website_url")
        or entities.get("organisation_website_url")
    )
    organisation_type = _organisation_type(name, entities)
    attributes = {key: entities.get(key) for key in (
        "regulator", "sponsor_class", "countries", "marketing_authorisation_status"
    ) if entities.get(key) not in (None, "", [])}
    conn.execute(
        """INSERT INTO account_organisations
        (organisation_id,canonical_key,canonical_name,organisation_type,country,official_website_url,
         identity_status,first_seen_at,last_seen_at,last_verified_at,next_review_at,attributes_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(organisation_id) DO UPDATE SET
        canonical_name=excluded.canonical_name,organisation_type=excluded.organisation_type,
        country=CASE WHEN excluded.country<>'' THEN excluded.country ELSE account_organisations.country END,
        official_website_url=CASE WHEN excluded.official_website_url<>'' THEN excluded.official_website_url ELSE account_organisations.official_website_url END,
        last_seen_at=excluded.last_seen_at,last_verified_at=excluded.last_verified_at,
        next_review_at=excluded.next_review_at,active=1""",
        (organisation_id, canonical_key, name, organisation_type, country, website, "source-derived",
         observed_at, observed_at, observed_at, next_review, json.dumps(attributes, ensure_ascii=False, default=str)),
    )
    return organisation_id


def _upsert_alias(conn, organisation_id: str, name: str, source_type: str, source_id: str,
                  evidence_url: str, observed_at: str) -> None:
    alias_id = _id("alias", organisation_id, name, source_type, source_id)
    conn.execute(
        """INSERT INTO account_aliases
        (alias_id,organisation_id,alias_name,alias_key,source_type,source_id,evidence_url,
         verification_status,first_seen_at,last_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(alias_id) DO UPDATE SET evidence_url=excluded.evidence_url,last_seen_at=excluded.last_seen_at""",
        (alias_id, organisation_id, name, _key(name), source_type, source_id, evidence_url,
         "observed in stored source evidence", observed_at, observed_at),
    )


def _upsert_relationship(conn, organisation_id: str, relationship_type: str, object_type: str,
                         object_name: str, source_type: str, source_id: str, evidence_url: str,
                         observed_at: str, stable_lead_id: str = "") -> None:
    if not _text(object_name):
        return
    relationship_id = _id(
        "acctrel", organisation_id, relationship_type, object_type, object_name,
        source_type, source_id, stable_lead_id,
    )
    conn.execute(
        """INSERT INTO account_relationships
        (relationship_id,organisation_id,relationship_type,object_type,object_name,object_key,
         stable_lead_id,source_type,source_id,evidence_url,evidence_status,active,first_seen_at,last_seen_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(relationship_id) DO UPDATE SET evidence_url=excluded.evidence_url,
        evidence_status=excluded.evidence_status,active=1,last_seen_at=excluded.last_seen_at""",
        (relationship_id, organisation_id, relationship_type, object_type, _text(object_name),
         _key(object_name), stable_lead_id, source_type, source_id, evidence_url,
         "stored public-source evidence; human validation required", 1, observed_at, observed_at),
    )


def _contact_function(title: str, source_type: str) -> str:
    blob = f"{title} {source_type}".casefold()
    if any(term in blob for term in ("quality", "manufactur", "technical", "cmc")):
        return "Quality / CMC"
    if any(term in blob for term in ("supply", "procurement", "purchasing")):
        return "Supply Chain / Procurement"
    if any(term in blob for term in ("safety", "pharmacovigilance", "regulatory")):
        return "Pharmacovigilance / Regulatory Affairs"
    if any(term in blob for term in ("clinical", "study", "investigator", "trial")):
        return "Clinical Development / Business Development"
    return "External Innovation / Business Development"


def _contact_rows(entities: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("contacts", "central_contacts", "study_contacts", "organisation_contacts"):
        value = entities.get(key) or []
        if isinstance(value, dict):
            value = [value]
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _upsert_contact(conn, organisation_id: str, contact: dict[str, Any], source_type: str,
                    source_id: str, evidence_url: str, product: str, observed_at: str,
                    next_review: str) -> tuple[str, bool]:
    person = _text(contact.get("name") or contact.get("person_name") or contact.get("contact_name"))
    if not person or not evidence_url:
        return "", False
    title = _text(contact.get("role") or contact.get("title") or contact.get("job_title"))
    email = _text(contact.get("email"))
    phone = _text(contact.get("phone"))
    contact_id = _id("contact", organisation_id, person, title, email, source_type, source_id)
    snapshot = {
        "person_name": person, "job_title": title, "email": email, "phone": phone,
        "product_scope": product, "source_type": source_type, "source_id": source_id,
        "evidence_url": evidence_url,
    }
    encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    changed = not conn.execute(
        "SELECT 1 FROM account_contact_observations WHERE contact_id=? AND observation_hash=?",
        (contact_id, digest),
    ).fetchone()
    conn.execute(
        """INSERT INTO account_contacts
        (contact_id,organisation_id,person_name,job_title,contact_function,email,phone,product_scope,
         source_type,source_id,evidence_url,verification_status,confidence_note,active,first_seen_at,
         last_verified_at,next_review_at,attributes_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(contact_id) DO UPDATE SET job_title=excluded.job_title,
        contact_function=excluded.contact_function,email=excluded.email,phone=excluded.phone,
        product_scope=excluded.product_scope,evidence_url=excluded.evidence_url,
        verification_status=excluded.verification_status,confidence_note=excluded.confidence_note,
        active=1,last_verified_at=excluded.last_verified_at,next_review_at=excluded.next_review_at""",
        (contact_id, organisation_id, person, title, _contact_function(title, source_type), email, phone,
         product, source_type, source_id, evidence_url, "listed in an official public source",
         "Public-source verified listing; current responsibility must still be confirmed before outreach.",
         1, observed_at, observed_at, next_review, "{}"),
    )
    conn.execute(
        """INSERT INTO account_contact_observations
        (contact_id,observation_hash,observed_at,snapshot_json) VALUES (?,?,?,?)
        ON CONFLICT(contact_id,observation_hash) DO NOTHING""",
        (contact_id, digest, observed_at, encoded),
    )
    return contact_id, changed


def _upsert_route(conn, organisation_id: str, row: dict[str, Any], observed_at: str,
                  next_review: str) -> None:
    qualification = opportunity_index.commercial_qualification(row)
    role = qualification["recommended_contact_role"]
    source_type = _text(row.get("source_type"))
    source_id = _text(row.get("source_id"))
    product = _text(row.get("product"))
    signal = _text(row.get("problem_category"))
    stable_lead_id = _text(row.get("stable_lead_id"))
    evidence_url = _valid_url(row.get("official_source_url"))
    if not evidence_url:
        links = _json(row.get("evidence_links_json"), [])
        evidence_url = next((_valid_url(link.get("url") if isinstance(link, dict) else link) for link in links if _valid_url(link.get("url") if isinstance(link, dict) else link)), "")
    route_id = _id("route", organisation_id, role, product, signal, source_type, source_id, stable_lead_id)
    conn.execute(
        """INSERT INTO account_contact_routes
        (route_id,organisation_id,contact_function,product_scope,signal_scope,rationale,stable_lead_id,
         source_type,source_id,evidence_url,route_status,last_verified_at,next_review_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(route_id) DO UPDATE SET rationale=excluded.rationale,evidence_url=excluded.evidence_url,
        last_verified_at=excluded.last_verified_at,next_review_at=excluded.next_review_at""",
        (route_id, organisation_id, role, product, signal, qualification["contact_rationale"],
         stable_lead_id, source_type, source_id, evidence_url,
         "responsible function inferred from evidence; named person not guaranteed", observed_at, next_review),
    )


def _organisation_snapshot(conn, organisation_id: str, observed_at: str) -> bool:
    org = dict(conn.execute(
        "SELECT canonical_name,organisation_type,country,official_website_url,identity_status,active "
        "FROM account_organisations WHERE organisation_id=?", (organisation_id,)
    ).fetchone() or {})
    org["relationships"] = int(conn.execute(
        "SELECT COUNT(*) AS n FROM account_relationships WHERE organisation_id=? AND active=1",
        (organisation_id,),
    ).fetchone()["n"])
    org["contacts"] = int(conn.execute(
        "SELECT COUNT(*) AS n FROM account_contacts WHERE organisation_id=? AND active=1",
        (organisation_id,),
    ).fetchone()["n"])
    encoded = json.dumps(org, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    changed = not conn.execute(
        "SELECT 1 FROM account_organisation_observations WHERE organisation_id=? AND observation_hash=?",
        (organisation_id, digest),
    ).fetchone()
    conn.execute(
        """INSERT INTO account_organisation_observations
        (organisation_id,observation_hash,observed_at,snapshot_json) VALUES (?,?,?,?)
        ON CONFLICT(organisation_id,observation_hash) DO NOTHING""",
        (organisation_id, digest, observed_at, encoded),
    )
    return changed


def sync_account_intelligence(conn, *, run_id: str = "manual-account-sync",
                              observed_at: datetime | None = None) -> dict[str, int]:
    """Rebuild the governed account projection from all active stored evidence."""
    now = observed_at or _now()
    observed = _iso(now)
    next_review = _iso(now + timedelta(days=7))
    organisation_ids: set[str] = set()
    contact_ids: set[str] = set()
    contacts_changed = 0

    source_rows = conn.execute(
        "SELECT source_type,source_id,official_source_url,record_json FROM source_records "
        "WHERE active=1 ORDER BY source_type,source_id"
    ).fetchall()
    for stored in source_rows:
        item = dict(stored)
        record = _json(item.get("record_json"), {})
        entities = record.get("entities") if isinstance(record.get("entities"), dict) else {}
        name = _organisation_name(entities)
        product = _text(entities.get("product") or entities.get("molecule") or record.get("title"))
        if not name or (product and _key(name) == _key(product)):
            continue
        source_type = _text(item.get("source_type") or record.get("source_type"))
        source_id = _text(item.get("source_id") or record.get("record_id"))
        evidence_url = _valid_url(item.get("official_source_url") or record.get("url") or entities.get("official_source_url"))
        organisation_id = _upsert_organisation(conn, name, entities, observed, next_review)
        organisation_ids.add(organisation_id)
        _upsert_alias(conn, organisation_id, name, source_type, source_id, evidence_url, observed)
        _upsert_relationship(
            conn, organisation_id, _source_relationship(source_type), "product / programme",
            product, source_type, source_id, evidence_url, observed,
        )
        route_row = {
            "company": name, "product": product,
            "problem_category": entities.get("issue_category") or entities.get("event_type") or record.get("source_category"),
            "source_type": source_type, "source_id": source_id,
            "official_source_url": evidence_url,
        }
        _upsert_route(conn, organisation_id, route_row, observed, next_review)
        for contact in _contact_rows(entities):
            contact_id, changed = _upsert_contact(
                conn, organisation_id, contact, source_type, source_id, evidence_url,
                product, observed, next_review,
            )
            if contact_id:
                contact_ids.add(contact_id)
                contacts_changed += int(changed)

    opportunity_rows = [dict(row) for row in conn.execute(
        "SELECT stable_lead_id,company,product,molecule,problem_category,source_type,source_id,region,"
        "evidence_links_json,last_checked_at FROM opportunity_index WHERE COALESCE(company,'')<>''"
    ).fetchall()]
    for row in opportunity_rows:
        name = _text(row.get("company"))
        product = _text(row.get("product") or row.get("molecule"))
        if product and _key(name) == _key(product):
            continue
        entities = {"country": row.get("region")}
        organisation_id = _upsert_organisation(conn, name, entities, observed, next_review)
        organisation_ids.add(organisation_id)
        evidence_url = next((_valid_url(link.get("url") if isinstance(link, dict) else link) for link in _json(row.get("evidence_links_json"), []) if _valid_url(link.get("url") if isinstance(link, dict) else link)), "")
        _upsert_alias(conn, organisation_id, name, _text(row.get("source_type")), _text(row.get("source_id")), evidence_url, observed)
        _upsert_relationship(
            conn, organisation_id, "has public opportunity signal for", "product / programme",
            product or _text(row.get("problem_category")), _text(row.get("source_type")),
            _text(row.get("source_id")), evidence_url, observed, _text(row.get("stable_lead_id")),
        )
        row["official_source_url"] = evidence_url
        _upsert_route(conn, organisation_id, row, observed, next_review)

    organisations_changed = 0
    for organisation_id in organisation_ids:
        counts = conn.execute(
            """SELECT COUNT(DISTINCT source_type) AS source_count,COUNT(*) AS relationship_count
            FROM account_relationships WHERE organisation_id=? AND active=1""",
            (organisation_id,),
        ).fetchone()
        conn.execute(
            "UPDATE account_organisations SET source_count=?,relationship_count=? WHERE organisation_id=?",
            (int(counts["source_count"] or 0), int(counts["relationship_count"] or 0), organisation_id),
        )
        organisations_changed += int(_organisation_snapshot(conn, organisation_id, observed))

    due = int(conn.execute(
        "SELECT COUNT(*) AS n FROM account_contacts WHERE active=1 AND next_review_at<?", (observed,)
    ).fetchone()["n"])
    conn.execute(
        "UPDATE account_contacts SET verification_status='weekly revalidation due' "
        "WHERE active=1 AND next_review_at<?", (observed,)
    )
    completed = _iso(_now())
    metrics = {
        "organisations_seen": len(organisation_ids),
        "organisations_changed": organisations_changed,
        "contacts_seen": len(contact_ids),
        "contacts_changed": contacts_changed,
        "contacts_due_review": due,
    }
    conn.execute(
        """INSERT INTO account_monitor_runs
        (run_id,started_at,completed_at,status,organisations_seen,organisations_changed,
         contacts_seen,contacts_changed,contacts_due_review,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id) DO UPDATE SET completed_at=excluded.completed_at,status=excluded.status,
        organisations_seen=excluded.organisations_seen,organisations_changed=excluded.organisations_changed,
        contacts_seen=excluded.contacts_seen,contacts_changed=excluded.contacts_changed,
        contacts_due_review=excluded.contacts_due_review,metadata_json=excluded.metadata_json""",
        (run_id, observed, completed, "Healthy", metrics["organisations_seen"],
         metrics["organisations_changed"], metrics["contacts_seen"], metrics["contacts_changed"],
         metrics["contacts_due_review"], json.dumps({"policy": "weekly public-source revalidation"})),
    )
    return metrics


def metrics(conn) -> dict[str, Any]:
    latest = dict(conn.execute(
        "SELECT * FROM account_monitor_runs ORDER BY completed_at DESC LIMIT 1"
    ).fetchone() or {})
    return {
        "organisations": int(conn.execute("SELECT COUNT(*) AS n FROM account_organisations WHERE active=1").fetchone()["n"]),
        "relationships": int(conn.execute("SELECT COUNT(*) AS n FROM account_relationships WHERE active=1").fetchone()["n"]),
        "contact_routes": int(conn.execute("SELECT COUNT(*) AS n FROM account_contact_routes").fetchone()["n"]),
        "named_contacts": int(conn.execute("SELECT COUNT(*) AS n FROM account_contacts WHERE active=1").fetchone()["n"]),
        "contacts_due_review": int(conn.execute(
            "SELECT COUNT(*) AS n FROM account_contacts WHERE active=1 AND verification_status='weekly revalidation due'"
        ).fetchone()["n"]),
        "latest_monitor": latest,
    }


def organisations(conn, search: str = "", limit: int = 100) -> list[dict[str, Any]]:
    where = "o.active=1"
    params: list[Any] = []
    if search.strip():
        where += " AND (LOWER(o.canonical_name) LIKE ? OR EXISTS (SELECT 1 FROM account_aliases a WHERE a.organisation_id=o.organisation_id AND LOWER(a.alias_name) LIKE ?))"
        query = f"%{search.strip().casefold()}%"
        params.extend([query, query])
    params.append(max(1, min(int(limit), 500)))
    rows = conn.execute(
        f"""SELECT o.*,COUNT(DISTINCT c.contact_id) AS named_contacts,
        COUNT(DISTINCT r.route_id) AS contact_routes
        FROM account_organisations o
        LEFT JOIN account_contacts c ON c.organisation_id=o.organisation_id AND c.active=1
        LEFT JOIN account_contact_routes r ON r.organisation_id=o.organisation_id
        WHERE {where} GROUP BY o.organisation_id,o.canonical_key,o.canonical_name,o.organisation_type,
        o.country,o.official_website_url,o.identity_status,o.source_count,o.relationship_count,o.active,
        o.first_seen_at,o.last_seen_at,o.last_verified_at,o.next_review_at,o.attributes_json
        ORDER BY o.relationship_count DESC,o.canonical_name LIMIT ?""",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def profile(conn, organisation_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM account_organisations WHERE organisation_id=?", (organisation_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["aliases"] = [dict(item) for item in conn.execute(
        "SELECT * FROM account_aliases WHERE organisation_id=? ORDER BY last_seen_at DESC", (organisation_id,)
    ).fetchall()]
    result["relationships"] = [dict(item) for item in conn.execute(
        "SELECT * FROM account_relationships WHERE organisation_id=? AND active=1 ORDER BY last_seen_at DESC", (organisation_id,)
    ).fetchall()]
    result["routes"] = [dict(item) for item in conn.execute(
        "SELECT * FROM account_contact_routes WHERE organisation_id=? ORDER BY last_verified_at DESC", (organisation_id,)
    ).fetchall()]
    result["contacts"] = [dict(item) for item in conn.execute(
        "SELECT * FROM account_contacts WHERE organisation_id=? AND active=1 ORDER BY last_verified_at DESC", (organisation_id,)
    ).fetchall()]
    result["changes"] = [dict(item) for item in conn.execute(
        "SELECT observed_at,snapshot_json FROM account_organisation_observations WHERE organisation_id=? ORDER BY observed_at DESC LIMIT 20",
        (organisation_id,),
    ).fetchall()]
    return result
