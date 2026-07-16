"""Phase 11 evidence-governed deals, funding and commercial signals."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from ..connectors.commercial_signals import classify


EVENT_SOURCE_TYPES = {
    "deal", "licensing", "merger_acquisition", "acquisition", "commercial_partnership",
    "corporate_financing", "commercial_signal",
}


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)): return value
    try: parsed = json.loads(value or "")
    except (TypeError, ValueError): return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _id(prefix: str, *parts: Any) -> str:
    payload = "|".join(_text(part).casefold() for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _number(value: Any) -> float | None:
    if value in (None, ""): return None
    try: return float(value)
    except (TypeError, ValueError): return None


def event_type(source_type: str, entities: dict, text: str) -> str:
    explicit = _text(entities.get("deal_type") or entities.get("event_type"))
    if explicit: return explicit
    mapped = {
        "licensing": "Licensing", "merger_acquisition": "M&A", "acquisition": "M&A",
        "commercial_partnership": "Commercial partnership", "corporate_financing": "Corporate financing",
    }
    return mapped.get(source_type, classify(text))


def _publication_key(record: dict, entities: dict) -> str:
    for prefix, key in (("doi", "doi"), ("pmcid", "pmcid"), ("pmid", "pmid"), ("openalex", "openalex_id")):
        value = _text(entities.get(key)).replace("https://doi.org/", "")
        if value: return f"{prefix}:{value.casefold()}"
    return f"source:{_text(record.get('source_type')).casefold()}:{_text(record.get('record_id')).casefold()}"


def _snapshot(conn, event_id: str, observed: str) -> bool:
    event = dict(conn.execute("SELECT * FROM commercial_events WHERE commercial_event_id=?", (event_id,)).fetchone() or {})
    event.pop("last_verified_at", None); event.pop("next_review_at", None)
    encoded = json.dumps(event, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    changed = not conn.execute(
        "SELECT 1 FROM commercial_event_observations WHERE commercial_event_id=? AND observation_hash=?",
        (event_id, digest),
    ).fetchone()
    conn.execute(
        "INSERT INTO commercial_event_observations (commercial_event_id,observation_hash,observed_at,snapshot_json) "
        "VALUES (?,?,?,?) ON CONFLICT(commercial_event_id,observation_hash) DO NOTHING",
        (event_id, digest, observed, encoded),
    )
    return changed


def sync(conn, *, run_id: str = "manual-commercial-intelligence", observed_at: datetime | None = None) -> dict[str, int]:
    now = observed_at or datetime.now(timezone.utc).replace(microsecond=0)
    observed = _iso(now); next_review = _iso(now + timedelta(days=7))
    conn.execute("UPDATE commercial_events SET active=0")
    conn.execute("UPDATE funding_awards SET active=0")
    rows = [dict(row) for row in conn.execute(
        "SELECT source_type,source_id,source_name,official_source_url,record_json FROM source_records "
        "WHERE active=1 AND (source_type IN ('deal','licensing','merger_acquisition','acquisition','commercial_partnership','corporate_financing','commercial_signal') OR source_type='paper') "
        "ORDER BY source_type,source_id"
    ).fetchall()]
    event_ids: set[str] = set(); grant_ids: set[str] = set(); counts: dict[str, int] = {}
    for stored in rows:
        record = _json(stored["record_json"], {}); entities = record.get("entities") if isinstance(record.get("entities"), dict) else {}
        evidence_url = _text(stored["official_source_url"] or record.get("url"))
        if stored["source_type"] in EVENT_SOURCE_TYPES:
            kind = event_type(stored["source_type"], entities, f"{record.get('title', '')} {record.get('raw_text', '')}")
            if not kind: continue
            event_id = _id("comevent", stored["source_type"], stored["source_id"], kind); event_ids.add(event_id)
            primary = bool(entities.get("primary_source_verified"))
            value_amount = _number(entities.get("value_amount") or entities.get("transaction_value"))
            currency = _text(entities.get("currency"))
            value_text = _text(entities.get("value_text"))
            if value_amount is not None and not currency:
                value_text = value_text or str(value_amount)
            evidence_class = "Primary transaction disclosure" if primary else "Commercial discovery signal"
            evidence_status = (
                "Official/primary source URL retained; transaction facts still require human review"
                if primary else "Web-discovered signal; primary transaction evidence not yet established"
            )
            validation = "Human validation required" if primary else "Primary-source verification required"
            conn.execute(
                """INSERT INTO commercial_events
                (commercial_event_id,event_type,evidence_class,announcement_date,event_status,party_a_name,party_b_name,
                 subject_name,value_amount,currency,value_text,geography,source_type,source_name,source_id,evidence_url,
                 primary_source_verified,evidence_status,validation_status,first_seen_at,last_verified_at,next_review_at,attributes_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(commercial_event_id) DO UPDATE SET event_type=excluded.event_type,evidence_class=excluded.evidence_class,
                announcement_date=excluded.announcement_date,event_status=excluded.event_status,party_a_name=excluded.party_a_name,
                party_b_name=excluded.party_b_name,subject_name=excluded.subject_name,value_amount=excluded.value_amount,
                currency=excluded.currency,value_text=excluded.value_text,geography=excluded.geography,
                source_name=excluded.source_name,evidence_url=excluded.evidence_url,
                primary_source_verified=excluded.primary_source_verified,evidence_status=excluded.evidence_status,
                validation_status=excluded.validation_status,active=1,last_verified_at=excluded.last_verified_at,
                next_review_at=excluded.next_review_at""",
                (event_id, kind, evidence_class, _text(entities.get("announcement_date") or entities.get("date")),
                 _text(entities.get("status")), _text(entities.get("party_a") or entities.get("company")),
                 _text(entities.get("party_b") or entities.get("counterparty")),
                 _text(entities.get("subject") or entities.get("asset") or record.get("title")),
                 value_amount, currency, value_text, _text(entities.get("geography") or entities.get("country")),
                 stored["source_type"], _text(stored["source_name"] or record.get("source_name")), stored["source_id"],
                 evidence_url, int(primary), evidence_status, validation, observed, observed, next_review,
                 json.dumps({"raw_title": record.get("title") or ""})),
            )
            counts[kind] = counts.get(kind, 0) + 1
            continue

        if stored["source_type"] == "paper":
            grants = [item for item in entities.get("grants", []) or [] if isinstance(item, dict)]
            if not grants: continue
            publication_id = _id("respub", _publication_key(record, entities))
            for grant in grants:
                funder = _text(grant.get("funder")); award_id = _text(grant.get("award_id"))
                if not funder and not award_id: continue
                funding_id = _id("funding", publication_id, funder, award_id); grant_ids.add(funding_id)
                conn.execute(
                    """INSERT INTO funding_awards
                    (funding_award_id,funding_type,funder_name,recipient_name,award_id,programme_name,linked_publication_id,
                     amount_value,currency,value_text,award_date,source_type,source_name,source_id,evidence_url,
                     primary_source_verified,evidence_status,validation_status,first_seen_at,last_verified_at,next_review_at,attributes_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(funding_award_id) DO UPDATE SET funder_name=excluded.funder_name,award_id=excluded.award_id,
                    programme_name=excluded.programme_name,evidence_url=excluded.evidence_url,evidence_status=excluded.evidence_status,
                    validation_status=excluded.validation_status,active=1,last_verified_at=excluded.last_verified_at,
                    next_review_at=excluded.next_review_at""",
                    (funding_id, "Publication-linked research grant", funder, "", award_id,
                     _text(entities.get("publication_title") or record.get("title")), publication_id,
                     None, "", "", "", "paper", _text(stored["source_name"]), stored["source_id"], evidence_url, 0,
                     "Scholarly metadata links a funder/award to this publication",
                     "Recipient, award value and current grant status are not established by retained metadata",
                     observed, observed, next_review, "{}"),
                )

    changed = sum(int(_snapshot(conn, event_id, observed)) for event_id in event_ids)
    verification_required = int(conn.execute(
        "SELECT COUNT(*) AS n FROM commercial_events WHERE active=1 AND primary_source_verified=0"
    ).fetchone()["n"])
    completed = _iso(datetime.now(timezone.utc).replace(microsecond=0))
    conn.execute(
        """INSERT INTO commercial_monitor_runs
        (run_id,started_at,completed_at,status,events_seen,events_changed,licensing_seen,mergers_acquisitions_seen,
         partnerships_seen,financing_seen,grants_seen,primary_verification_required,metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id) DO UPDATE SET completed_at=excluded.completed_at,status=excluded.status,
        events_seen=excluded.events_seen,events_changed=excluded.events_changed,licensing_seen=excluded.licensing_seen,
        mergers_acquisitions_seen=excluded.mergers_acquisitions_seen,partnerships_seen=excluded.partnerships_seen,
        financing_seen=excluded.financing_seen,grants_seen=excluded.grants_seen,
        primary_verification_required=excluded.primary_verification_required,metadata_json=excluded.metadata_json""",
        (run_id, observed, completed, "Healthy", len(event_ids), changed, counts.get("Licensing", 0),
         counts.get("M&A", 0), counts.get("Commercial partnership", 0), counts.get("Corporate financing", 0),
         len(grant_ids), verification_required, json.dumps({
             "governance": "transaction facts, web signals and publication-linked grants remain separate"
         })),
    )
    return {"events_seen": len(event_ids), "events_changed": changed, "licensing_seen": counts.get("Licensing", 0),
            "mergers_acquisitions_seen": counts.get("M&A", 0), "partnerships_seen": counts.get("Commercial partnership", 0),
            "financing_seen": counts.get("Corporate financing", 0), "grants_seen": len(grant_ids),
            "primary_verification_required": verification_required}


def metrics(conn) -> dict[str, Any]:
    return {
        "events": int(conn.execute("SELECT COUNT(*) AS n FROM commercial_events WHERE active=1").fetchone()["n"]),
        "licensing": int(conn.execute("SELECT COUNT(*) AS n FROM commercial_events WHERE active=1 AND event_type='Licensing'").fetchone()["n"]),
        "mergers_acquisitions": int(conn.execute("SELECT COUNT(*) AS n FROM commercial_events WHERE active=1 AND event_type='M&A'").fetchone()["n"]),
        "partnerships": int(conn.execute("SELECT COUNT(*) AS n FROM commercial_events WHERE active=1 AND event_type='Commercial partnership'").fetchone()["n"]),
        "financing": int(conn.execute("SELECT COUNT(*) AS n FROM commercial_events WHERE active=1 AND event_type='Corporate financing'").fetchone()["n"]),
        "grants": int(conn.execute("SELECT COUNT(*) AS n FROM funding_awards WHERE active=1").fetchone()["n"]),
        "primary_verified": int(conn.execute("SELECT COUNT(*) AS n FROM commercial_events WHERE active=1 AND primary_source_verified=1").fetchone()["n"]),
        "verification_required": int(conn.execute("SELECT COUNT(*) AS n FROM commercial_events WHERE active=1 AND primary_source_verified=0").fetchone()["n"]),
        "latest_monitor": dict(conn.execute("SELECT * FROM commercial_monitor_runs ORDER BY completed_at DESC LIMIT 1").fetchone() or {}),
    }


def events(conn, *, search: str = "", event_filter: str = "All", evidence_filter: str = "All", limit: int = 250) -> list[dict[str, Any]]:
    clauses = ["active=1"]; params: list[Any] = []
    if search.strip():
        q = f"%{search.strip().casefold()}%"; clauses.append("(LOWER(party_a_name) LIKE ? OR LOWER(party_b_name) LIKE ? OR LOWER(subject_name) LIKE ?)"); params.extend([q, q, q])
    if event_filter != "All": clauses.append("event_type=?"); params.append(event_filter)
    if evidence_filter == "Primary source verified": clauses.append("primary_source_verified=1")
    elif evidence_filter == "Verification required": clauses.append("primary_source_verified=0")
    params.append(max(1, min(int(limit), 1000)))
    return [dict(row) for row in conn.execute(
        f"SELECT * FROM commercial_events WHERE {' AND '.join(clauses)} ORDER BY COALESCE(announcement_date,last_verified_at) DESC,party_a_name LIMIT ?", tuple(params)
    ).fetchall()]


def funding(conn, *, search: str = "", limit: int = 250) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "active=1"
    if search.strip():
        q = f"%{search.strip().casefold()}%"; where += " AND (LOWER(funder_name) LIKE ? OR LOWER(recipient_name) LIKE ? OR LOWER(programme_name) LIKE ? OR LOWER(award_id) LIKE ?)"; params.extend([q, q, q, q])
    params.append(max(1, min(int(limit), 1000)))
    return [dict(row) for row in conn.execute(
        f"SELECT * FROM funding_awards WHERE {where} ORDER BY last_verified_at DESC,funder_name LIMIT ?", tuple(params)
    ).fetchall()]


def facets(conn) -> dict[str, list[str]]:
    return {"event_type": [str(row[0]) for row in conn.execute(
        "SELECT DISTINCT event_type FROM commercial_events WHERE active=1 ORDER BY event_type"
    ).fetchall()]}


def profile(conn, event_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM commercial_events WHERE commercial_event_id=?", (event_id,)).fetchone()
    if not row: return None
    result = dict(row)
    result["history"] = [dict(item) for item in conn.execute(
        "SELECT observed_at,snapshot_json FROM commercial_event_observations WHERE commercial_event_id=? ORDER BY observed_at DESC LIMIT 20",
        (event_id,),
    ).fetchall()]
    return result
