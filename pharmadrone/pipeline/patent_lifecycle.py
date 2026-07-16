"""Phase 9 FDA Orange Book patent and lifecycle intelligence projection."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any
from urllib.parse import quote


FDA_SOURCE = "https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, type(fallback)):
        return value
    try:
        parsed = json.loads(value or "")
    except (TypeError, ValueError):
        return fallback
    return parsed if isinstance(parsed, type(fallback)) else fallback


def _id(prefix: str, *parts: Any) -> str:
    payload = "|".join(_text(part).casefold() for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(_text(value))
    except ValueError:
        return None


def family_lookup_url(patent_number: str) -> str:
    number = re.sub(r"[^A-Za-z0-9]", "", _text(patent_number))
    return f"https://worldwide.espacenet.com/patent/search?q={quote('pn=US' + number)}" if number else ""


def lifecycle_state(patents: list[dict[str, Any]], exclusivities: list[dict[str, Any]], *, today: date | None = None,
                    dataset_mode: str = "") -> tuple[str, str]:
    today = today or date.today()
    expiries = [parsed for parsed in (
        _date(item.get("expiry_date")) for item in [*patents, *exclusivities]
    ) if parsed and parsed >= today]
    if "fallback" in dataset_mode.casefold():
        return "Lifecycle evidence unavailable", ""
    if not expiries:
        return "No unexpired listed protection", ""
    next_expiry = min(expiries)
    state = "Expiry within 24 months" if next_expiry <= today + timedelta(days=730) else "Unexpired listed protection"
    return state, next_expiry.isoformat()


def _snapshot(conn, lifecycle_id: str, observed_at: str) -> bool:
    product = dict(conn.execute("SELECT * FROM lifecycle_products WHERE lifecycle_id=?", (lifecycle_id,)).fetchone() or {})
    product.pop("last_verified_at", None); product.pop("next_review_at", None)
    product["patents"] = [dict(row) for row in conn.execute(
        "SELECT patent_number,expiry_date,drug_substance_flag,drug_product_flag,use_code,delist_requested,"
        "ownership_status,family_status,family_id FROM lifecycle_patents WHERE lifecycle_id=? AND active=1 ORDER BY patent_number,use_code",
        (lifecycle_id,),
    ).fetchall()]
    product["exclusivities"] = [dict(row) for row in conn.execute(
        "SELECT exclusivity_code,expiry_date FROM lifecycle_exclusivities WHERE lifecycle_id=? AND active=1 ORDER BY exclusivity_code,expiry_date",
        (lifecycle_id,),
    ).fetchall()]
    encoded = json.dumps(product, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    changed = not conn.execute(
        "SELECT 1 FROM lifecycle_observations WHERE lifecycle_id=? AND observation_hash=?", (lifecycle_id, digest)
    ).fetchone()
    conn.execute(
        "INSERT INTO lifecycle_observations (lifecycle_id,observation_hash,observed_at,snapshot_json) VALUES (?,?,?,?) "
        "ON CONFLICT(lifecycle_id,observation_hash) DO NOTHING", (lifecycle_id, digest, observed_at, encoded),
    )
    return changed


def sync(conn, *, run_id: str = "manual-patent-lifecycle", observed_at: datetime | None = None) -> dict[str, int]:
    now = observed_at or datetime.now(timezone.utc).replace(microsecond=0)
    observed = _iso(now); next_review = _iso(now + timedelta(days=7))
    rows = conn.execute(
        "SELECT source_id,official_source_url,record_json FROM source_records "
        "WHERE source_type='fda_orange_book_product' AND active=1 ORDER BY source_id"
    ).fetchall()
    lifecycle_ids: set[str] = set(); patent_ids: set[str] = set(); exclusivity_ids: set[str] = set()
    for stored in rows:
        record = _json(stored["record_json"], {})
        entities = record.get("entities") if isinstance(record.get("entities"), dict) else {}
        application = _text(entities.get("application_number")); product_number = _text(entities.get("product_number"))
        trade_name = _text(entities.get("product"))
        if not application or not product_number or not trade_name:
            continue
        lifecycle_id = _id("life", application, product_number)
        lifecycle_ids.add(lifecycle_id)
        patents = [item for item in (entities.get("patents") or []) if isinstance(item, dict)]
        exclusivities = [item for item in (entities.get("exclusivities") or []) if isinstance(item, dict)]
        dataset_mode = _text(entities.get("dataset_mode") or "Orange Book archive")
        state, next_expiry = lifecycle_state(patents, exclusivities, today=now.date(), dataset_mode=dataset_mode)
        evidence_url = _text(stored["official_source_url"] or entities.get("official_source_url") or FDA_SOURCE)
        conn.execute(
            """INSERT INTO lifecycle_products
            (lifecycle_id,application_number,product_number,trade_name,ingredient,application_holder,application_type,
             dosage_form_route,strength,approval_date,reference_listed_drug,reference_standard,therapeutic_equivalence_code,
             market_category,dataset_mode,official_source_url,evidence_status,lifecycle_status,next_expiry_date,
             first_seen_at,last_verified_at,next_review_at,attributes_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(lifecycle_id) DO UPDATE SET trade_name=excluded.trade_name,ingredient=excluded.ingredient,
            application_holder=excluded.application_holder,application_type=excluded.application_type,
            dosage_form_route=excluded.dosage_form_route,strength=excluded.strength,approval_date=excluded.approval_date,
            reference_listed_drug=excluded.reference_listed_drug,reference_standard=excluded.reference_standard,
            therapeutic_equivalence_code=excluded.therapeutic_equivalence_code,market_category=excluded.market_category,
            dataset_mode=excluded.dataset_mode,official_source_url=excluded.official_source_url,
            evidence_status=excluded.evidence_status,lifecycle_status=excluded.lifecycle_status,
            next_expiry_date=excluded.next_expiry_date,active=1,last_verified_at=excluded.last_verified_at,
            next_review_at=excluded.next_review_at""",
            (lifecycle_id, application, product_number, trade_name, _text(entities.get("molecule")),
             _text(entities.get("company")), _text(entities.get("application_type")),
             _text(entities.get("dosage_form_route")), _text(entities.get("strength")),
             _text(entities.get("approval_date")), _text(entities.get("reference_listed_drug")),
             _text(entities.get("reference_standard")), _text(entities.get("therapeutic_equivalence_code")),
             _text(entities.get("market_category")), dataset_mode, evidence_url,
             "Official FDA Orange Book listing" if "fallback" not in dataset_mode.casefold() else "Official Drugs@FDA product fallback; lifecycle fields unavailable",
             state, next_expiry, observed, observed, next_review, "{}"),
        )
        for patent in patents:
            number = _text(patent.get("patent_number"))
            if not number:
                continue
            patent_id = _id("lifepat", lifecycle_id, number, patent.get("use_code"))
            patent_ids.add(patent_id)
            conn.execute(
                """INSERT INTO lifecycle_patents
                (lifecycle_patent_id,lifecycle_id,patent_number,expiry_date,drug_substance_flag,drug_product_flag,
                 use_code,delist_requested,submission_date,application_holder_context,ownership_status,family_status,
                 family_id,official_source_url,family_lookup_url,first_seen_at,last_verified_at,next_review_at,attributes_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(lifecycle_patent_id) DO UPDATE SET expiry_date=excluded.expiry_date,
                drug_substance_flag=excluded.drug_substance_flag,drug_product_flag=excluded.drug_product_flag,
                delist_requested=excluded.delist_requested,submission_date=excluded.submission_date,
                application_holder_context=excluded.application_holder_context,official_source_url=excluded.official_source_url,
                family_lookup_url=excluded.family_lookup_url,active=1,last_verified_at=excluded.last_verified_at,
                next_review_at=excluded.next_review_at""",
                (patent_id, lifecycle_id, number, _text(patent.get("expiry_date")),
                 _text(patent.get("drug_substance")), _text(patent.get("drug_product")),
                 _text(patent.get("use_code")), _text(patent.get("delist_requested")),
                 _text(patent.get("submission_date")), _text(entities.get("company")),
                 "Patent owner not established by Orange Book", "Family resolution required from patent-office evidence",
                 "", evidence_url, family_lookup_url(number), observed, observed, next_review, "{}"),
            )
        for exclusivity in exclusivities:
            code = _text(exclusivity.get("code")); expiry = _text(exclusivity.get("expiry_date"))
            if not code:
                continue
            exclusivity_id = _id("lifeexc", lifecycle_id, code, expiry)
            exclusivity_ids.add(exclusivity_id)
            conn.execute(
                """INSERT INTO lifecycle_exclusivities
                (lifecycle_exclusivity_id,lifecycle_id,exclusivity_code,expiry_date,official_source_url,
                 first_seen_at,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(lifecycle_exclusivity_id) DO UPDATE SET expiry_date=excluded.expiry_date,
                official_source_url=excluded.official_source_url,active=1,last_verified_at=excluded.last_verified_at,
                next_review_at=excluded.next_review_at""",
                (exclusivity_id, lifecycle_id, code, expiry, evidence_url, observed, observed, next_review),
            )
    changed = sum(int(_snapshot(conn, lifecycle_id, observed)) for lifecycle_id in lifecycle_ids)
    completed = _iso(datetime.now(timezone.utc).replace(microsecond=0))
    family_due = int(conn.execute(
        "SELECT COUNT(*) AS n FROM lifecycle_patents WHERE active=1 AND COALESCE(family_id,'')=''"
    ).fetchone()["n"])
    conn.execute(
        """INSERT INTO lifecycle_monitor_runs
        (run_id,started_at,completed_at,status,products_seen,products_changed,patents_seen,exclusivities_seen,
         family_resolution_required,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id) DO UPDATE SET completed_at=excluded.completed_at,status=excluded.status,
        products_seen=excluded.products_seen,products_changed=excluded.products_changed,patents_seen=excluded.patents_seen,
        exclusivities_seen=excluded.exclusivities_seen,family_resolution_required=excluded.family_resolution_required,
        metadata_json=excluded.metadata_json""",
        (run_id, observed, completed, "Healthy", len(lifecycle_ids), changed, len(patent_ids),
         len(exclusivity_ids), family_due, json.dumps({"legal_boundary": "regulatory lifecycle intelligence; not legal advice"})),
    )
    return {"products_seen": len(lifecycle_ids), "products_changed": changed, "patents_seen": len(patent_ids),
            "exclusivities_seen": len(exclusivity_ids), "family_resolution_required": family_due}


def metrics(conn) -> dict[str, Any]:
    return {
        "products": int(conn.execute("SELECT COUNT(*) AS n FROM lifecycle_products WHERE active=1").fetchone()["n"]),
        "patents": int(conn.execute("SELECT COUNT(*) AS n FROM lifecycle_patents WHERE active=1").fetchone()["n"]),
        "exclusivities": int(conn.execute("SELECT COUNT(*) AS n FROM lifecycle_exclusivities WHERE active=1").fetchone()["n"]),
        "approaching_expiry": int(conn.execute("SELECT COUNT(*) AS n FROM lifecycle_products WHERE active=1 AND lifecycle_status='Expiry within 24 months'").fetchone()["n"]),
        "family_resolution_required": int(conn.execute("SELECT COUNT(*) AS n FROM lifecycle_patents WHERE active=1 AND COALESCE(family_id,'')='' ").fetchone()["n"]),
        "latest_monitor": dict(conn.execute("SELECT * FROM lifecycle_monitor_runs ORDER BY completed_at DESC LIMIT 1").fetchone() or {}),
    }


def products(conn, *, search: str = "", status: str = "All", holder: str = "All", limit: int = 250) -> list[dict[str, Any]]:
    clauses = ["p.active=1"]; params: list[Any] = []
    if search.strip():
        q = f"%{search.strip().casefold()}%"; clauses.append("(LOWER(p.trade_name) LIKE ? OR LOWER(p.ingredient) LIKE ? OR LOWER(p.application_number) LIKE ?)"); params.extend([q, q, q])
    if status != "All": clauses.append("p.lifecycle_status=?"); params.append(status)
    if holder != "All": clauses.append("p.application_holder=?"); params.append(holder)
    params.append(max(1, min(int(limit), 1000)))
    rows = conn.execute(
        f"""SELECT p.*,COUNT(DISTINCT pt.lifecycle_patent_id) AS patent_count,
        COUNT(DISTINCT ex.lifecycle_exclusivity_id) AS exclusivity_count
        FROM lifecycle_products p LEFT JOIN lifecycle_patents pt ON pt.lifecycle_id=p.lifecycle_id AND pt.active=1
        LEFT JOIN lifecycle_exclusivities ex ON ex.lifecycle_id=p.lifecycle_id AND ex.active=1
        WHERE {' AND '.join(clauses)} GROUP BY p.lifecycle_id,p.application_number,p.product_number,p.trade_name,
        p.ingredient,p.application_holder,p.application_type,p.dosage_form_route,p.strength,p.approval_date,
        p.reference_listed_drug,p.reference_standard,p.therapeutic_equivalence_code,p.market_category,p.dataset_mode,
        p.official_source_url,p.evidence_status,p.lifecycle_status,p.next_expiry_date,p.active,p.first_seen_at,
        p.last_verified_at,p.next_review_at,p.attributes_json
        ORDER BY CASE p.lifecycle_status WHEN 'Expiry within 24 months' THEN 1 WHEN 'Unexpired listed protection' THEN 2 ELSE 3 END,
        p.next_expiry_date,p.trade_name LIMIT ?""", tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def facets(conn) -> dict[str, list[str]]:
    return {
        "status": [str(row[0]) for row in conn.execute("SELECT DISTINCT lifecycle_status FROM lifecycle_products WHERE active=1 ORDER BY lifecycle_status").fetchall()],
        "holder": [str(row[0]) for row in conn.execute("SELECT DISTINCT application_holder FROM lifecycle_products WHERE active=1 AND COALESCE(application_holder,'')<>'' ORDER BY application_holder").fetchall()],
    }


def profile(conn, lifecycle_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM lifecycle_products WHERE lifecycle_id=?", (lifecycle_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    result["patents"] = [dict(item) for item in conn.execute("SELECT * FROM lifecycle_patents WHERE lifecycle_id=? AND active=1 ORDER BY expiry_date,patent_number", (lifecycle_id,)).fetchall()]
    result["exclusivities"] = [dict(item) for item in conn.execute("SELECT * FROM lifecycle_exclusivities WHERE lifecycle_id=? AND active=1 ORDER BY expiry_date,exclusivity_code", (lifecycle_id,)).fetchall()]
    result["history"] = [dict(item) for item in conn.execute("SELECT observed_at,snapshot_json FROM lifecycle_observations WHERE lifecycle_id=? ORDER BY observed_at DESC LIMIT 20", (lifecycle_id,)).fetchall()]
    return result
