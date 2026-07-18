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


def _canonical(value: Any) -> str:
    return "".join(ch for ch in _text(value).upper() if ch.isalnum())


def _party_identity(value: Any) -> str:
    return " ".join(_text(value).casefold().split())


def family_lookup_url(patent_number: str) -> str:
    number = re.sub(r"[^A-Za-z0-9]", "", _text(patent_number))
    return f"https://worldwide.espacenet.com/patent/search?q={quote('pn=US' + number)}" if number else ""


def google_patents_url(publication_number: str, jurisdiction: str = "") -> str:
    number = re.sub(r"[^A-Za-z0-9]", "", _text(publication_number)).upper()
    country = re.sub(r"[^A-Za-z]", "", _text(jurisdiction)).upper()
    if number and country and not number.startswith(country):
        number = country + number
    return f"https://patents.google.com/patent/{number}/en" if number else ""


def uk_register_url(publication_number: str) -> str:
    return "https://www.gov.uk/search-for-patent" if re.search(r"\d", _text(publication_number)) else ""


def fda_application_url(application_number: str) -> str:
    number = re.sub(r"\D", "", _text(application_number))
    return f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo={number}" if number else FDA_SOURCE


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
                 "", fda_application_url(application), family_lookup_url(number), observed, observed, next_review, "{}"),
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
    sync_global(conn, run_id=f"{run_id}:global", observed_at=now)
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
    result["global_documents"] = [dict(item) for item in conn.execute(
        "SELECT d.*,l.link_basis,l.evidence_status AS link_evidence_status,l.verified AS link_verified "
        "FROM patent_product_links l JOIN patent_documents d ON d.patent_document_id=l.patent_document_id "
        "WHERE l.lifecycle_id=? AND d.active=1 ORDER BY d.jurisdiction,d.publication_number", (lifecycle_id,),
    ).fetchall()]
    return result


def _upsert_document(conn, entities: dict[str, Any], *, source_name: str, authority: str,
                     observed: str, next_review: str, source_record_id: str = "",
                     source_refresh_id: str = "") -> str:
    publication = re.sub(r"[^A-Za-z0-9]", "", _text(entities.get("publication_number"))).upper()
    jurisdiction = _text(entities.get("jurisdiction")).upper() or publication[:2]
    document_id = _id("patdoc", jurisdiction, publication)
    official = _text(entities.get("official_source_url"))
    google = _text(entities.get("google_patents_url")) or google_patents_url(publication, jurisdiction)
    application = _text(entities.get("application_number"))
    legal_label = _text(entities.get("legal_status_label") or entities.get("legal_status_summary")) or "Legal status not established"
    legal_basis = _text(entities.get("legal_status_basis")) or "Source-reported label; no legal conclusion inferred"
    expiry = _text(entities.get("expiry_date"))
    expiry_basis = _text(entities.get("expiry_basis"))
    expiry_status = _text(entities.get("expiry_status")) or ("source-reported" if expiry else "not-reported")
    source_record_id = _text(source_record_id) or document_id
    source_refresh_id = _text(source_refresh_id)
    conn.execute(
        """INSERT INTO patent_documents
        (patent_document_id,publication_number,application_number,jurisdiction,document_kind,title,abstract_text,
         filing_date,publication_date,grant_date,family_id,family_status,legal_status_summary,legal_status_as_of,
         source_name,source_authority,official_source_url,google_patents_url,uk_register_url,evidence_status,
         first_seen_at,last_verified_at,next_review_at,attributes_json,normalized_publication_number,
         normalized_application_number,publication_kind,legal_status_code,legal_status_label,legal_status_basis,
         status_as_of_date,expiry_date,expiry_basis,expiry_status,expiry_as_of_date,last_source_refresh_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(patent_document_id) DO UPDATE SET application_number=excluded.application_number,
        document_kind=excluded.document_kind,title=excluded.title,abstract_text=excluded.abstract_text,
        filing_date=excluded.filing_date,publication_date=excluded.publication_date,grant_date=excluded.grant_date,
        family_id=excluded.family_id,family_status=excluded.family_status,
        legal_status_summary=excluded.legal_status_summary,legal_status_as_of=excluded.legal_status_as_of,
        normalized_publication_number=excluded.normalized_publication_number,
        normalized_application_number=excluded.normalized_application_number,
        publication_kind=excluded.publication_kind,legal_status_code=excluded.legal_status_code,
        legal_status_label=excluded.legal_status_label,legal_status_basis=excluded.legal_status_basis,
        status_as_of_date=excluded.status_as_of_date,expiry_date=excluded.expiry_date,
        expiry_basis=excluded.expiry_basis,expiry_status=excluded.expiry_status,
        expiry_as_of_date=excluded.expiry_as_of_date,last_source_refresh_id=excluded.last_source_refresh_id,
        source_name=excluded.source_name,source_authority=excluded.source_authority,
        official_source_url=excluded.official_source_url,google_patents_url=excluded.google_patents_url,
        uk_register_url=excluded.uk_register_url,evidence_status=excluded.evidence_status,active=1,
        last_verified_at=excluded.last_verified_at,next_review_at=excluded.next_review_at,
        attributes_json=excluded.attributes_json""",
        (document_id, publication, application, jurisdiction,
         _text(entities.get("document_kind")), _text(entities.get("title")), _text(entities.get("abstract")),
         _text(entities.get("filing_date")), _text(entities.get("publication_date")), _text(entities.get("grant_date")),
         _text(entities.get("family_id")), _text(entities.get("family_status")) or "Family not established",
         legal_label, _text(entities.get("legal_status_as_of")), source_name, authority, official, google,
         _text(entities.get("uk_register_url")) or (uk_register_url(publication) if jurisdiction == "GB" else ""),
         "Official patent-office evidence" if authority == "official" else "Discovery context only",
         observed, observed, next_review, json.dumps({"query_context": _text(entities.get("query_context")), "legal_status_basis": legal_basis}),
         _canonical(publication), _canonical(application), _text(entities.get("document_kind")),
         _text(entities.get("legal_status_code")), legal_label, legal_basis,
         _text(entities.get("legal_status_as_of")), expiry, expiry_basis, expiry_status,
         _text(entities.get("expiry_as_of_date")) or (observed if expiry else ""), source_refresh_id),
    )
    conn.execute(
        """INSERT INTO patent_document_sources
        (patent_document_source_id,patent_document_id,source_system,source_record_id,source_authority,
         official_source_url,evidence_status,first_seen_at,last_verified_at,next_review_at,last_source_refresh_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(patent_document_id,source_system,source_record_id) DO UPDATE SET
        source_authority=excluded.source_authority,official_source_url=excluded.official_source_url,
        evidence_status=excluded.evidence_status,last_verified_at=excluded.last_verified_at,
        next_review_at=excluded.next_review_at,last_source_refresh_id=excluded.last_source_refresh_id""",
        (_id("patsource", document_id, source_name, source_record_id), document_id, source_name, source_record_id,
         authority, official, "Official source evidence" if authority == "official" else "Discovery context only",
         observed, observed, next_review, source_refresh_id),
    )
    family_id = _text(entities.get("family_id"))
    if family_id:
        conn.execute(
            """INSERT INTO patent_families
            (family_id,canonical_family_id,family_status,source_authority,official_source_url,evidence_status,
             first_seen_at,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(family_id) DO UPDATE SET family_status=excluded.family_status,
            source_authority=excluded.source_authority,official_source_url=excluded.official_source_url,
            evidence_status=excluded.evidence_status,last_verified_at=excluded.last_verified_at,
            next_review_at=excluded.next_review_at""",
            (family_id, family_id, _text(entities.get("family_status")) or "Family not established",
             authority, official, "Official family evidence" if authority == "official" else "Family context only",
             observed, observed, next_review),
        )
    for party in entities.get("parties") or []:
        if not isinstance(party, dict) or not _text(party.get("party_name")):
            continue
        party_type = _text(party.get("party_type")) or "party"
        party_name = _text(party.get("party_name"))
        identity = _party_identity(party_name)
        conn.execute(
            """INSERT INTO patent_parties
            (patent_party_id,patent_document_id,party_type,party_name,country_code,sequence_number,evidence_status,
             official_source_url,first_seen_at,last_verified_at,next_review_at,normalized_party_name,
             party_identity_key,party_identity_basis) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(patent_party_id) DO UPDATE SET country_code=excluded.country_code,
            sequence_number=excluded.sequence_number,evidence_status=excluded.evidence_status,
            official_source_url=excluded.official_source_url,last_verified_at=excluded.last_verified_at,
            next_review_at=excluded.next_review_at,normalized_party_name=excluded.normalized_party_name,
            party_identity_key=excluded.party_identity_key,party_identity_basis=excluded.party_identity_basis""",
            (_id("patparty", document_id, party_type, party_name), document_id, party_type, party_name,
             _text(party.get("country_code")), _text(party.get("sequence_number")),
             "Officially reported party; current ownership not inferred", official, observed, observed, next_review,
             identity, identity, "Name normalization only; identity and ownership not verified"),
        )
    for member in entities.get("family_members") or []:
        if not isinstance(member, dict) or not _text(member.get("publication_number")) or not _text(entities.get("family_id")):
            continue
        member_number = _canonical(member.get("publication_number"))
        family_id = _text(entities.get("family_id"))
        conn.execute(
            """INSERT INTO patent_family_members
            (patent_family_member_id,family_id,patent_document_id,publication_number,jurisdiction,relationship_type,
             evidence_status,official_source_url,observed_at) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(patent_family_member_id) DO UPDATE SET evidence_status=excluded.evidence_status,
            official_source_url=excluded.official_source_url,observed_at=excluded.observed_at""",
            (_id("patfam", family_id, member_number), family_id, document_id if member_number == publication else None,
             member_number, _text(member.get("jurisdiction")), _text(member.get("relationship_type")) or "family member",
             "Official patent-office family evidence", official, observed),
        )
    for event in entities.get("legal_events") or []:
        if not isinstance(event, dict) or not _text(event.get("event_text")):
            continue
        conn.execute(
            """INSERT INTO patent_legal_events
            (patent_legal_event_id,patent_document_id,event_code,event_date,event_text,authority,evidence_status,
             official_source_url,observed_at) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(patent_legal_event_id) DO UPDATE SET evidence_status=excluded.evidence_status,
            official_source_url=excluded.official_source_url,observed_at=excluded.observed_at""",
            (_id("patevent", document_id, event.get("event_code"), event.get("event_date"), event.get("event_text")),
             document_id, _text(event.get("event_code")), _text(event.get("event_date")), _text(event.get("event_text")),
             _text(event.get("authority")), "Official patent-office legal-event evidence", official, observed),
        )
    return document_id


def sync_global(conn, *, run_id: str = "manual-global-patents", observed_at: datetime | None = None) -> dict[str, int]:
    now = observed_at or datetime.now(timezone.utc).replace(microsecond=0)
    observed = _iso(now); next_review = _iso(now + timedelta(days=7))
    document_ids: set[str] = set()
    # Orange Book patent numbers become US documents with an explicit regulatory link.
    rows = conn.execute(
        "SELECT pt.*,p.trade_name,p.application_number FROM lifecycle_patents pt JOIN lifecycle_products p ON p.lifecycle_id=pt.lifecycle_id "
        "WHERE pt.active=1 AND p.active=1"
    ).fetchall()
    for row in rows:
        publication = "US" + re.sub(r"\D", "", _text(row["patent_number"]))
        entities = {
            "publication_number": publication, "jurisdiction": "US", "title": "",
            "family_status": _text(row["family_status"]), "family_id": _text(row["family_id"]),
            "legal_status_summary": "Not established by Orange Book listing",
            "legal_status_basis": "Orange Book listing does not establish legal status",
            "expiry_date": _text(row.get("expiry_date")),
            "expiry_basis": "FDA Orange Book listed expiry (source-reported)" if _text(row.get("expiry_date")) else "",
            "expiry_status": "source-reported" if _text(row.get("expiry_date")) else "not-reported",
            "official_source_url": fda_application_url(_text(row["application_number"])),
            "google_patents_url": google_patents_url(publication),
        }
        document_id = _upsert_document(conn, entities, source_name="FDA Orange Book", authority="official",
                                       observed=observed, next_review=next_review,
                                       source_record_id=f"{row['lifecycle_id']}:{row['patent_number']}",
                                       source_refresh_id=run_id)
        document_ids.add(document_id)
        conn.execute(
            """INSERT INTO patent_product_links
            (patent_product_link_id,patent_document_id,lifecycle_id,link_basis,evidence_status,official_source_url,
             verified,observed_at,evidence_basis,evidence_source_record_id,verification_status,verified_at,verification_basis) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(patent_product_link_id) DO UPDATE SET evidence_status=excluded.evidence_status,
            official_source_url=excluded.official_source_url,observed_at=excluded.observed_at,
            evidence_basis=excluded.evidence_basis,evidence_source_record_id=excluded.evidence_source_record_id,
            verification_status=excluded.verification_status,verified_at=excluded.verified_at,
            verification_basis=excluded.verification_basis""",
            (_id("patlink", document_id, row["lifecycle_id"], "orange-book-listed"), document_id,
             row["lifecycle_id"], "Patent number listed for FDA application/product",
             "Verified Orange Book listing; scope, validity and ownership not inferred", fda_application_url(_text(row["application_number"])), 1, observed,
             "Orange Book application/product listing", f"{row['lifecycle_id']}:{row['patent_number']}", "verified", observed,
             "Explicit FDA Orange Book listing; no ownership or validity inference"),
        )
    # EPO OPS records include EP and GB publications and official reported parties.
    stored = conn.execute(
        "SELECT source_id,source_name,record_json FROM source_records WHERE source_type='epo_patent_document' AND active=1"
    ).fetchall()
    for item in stored:
        record = _json(item["record_json"], {})
        entities = record.get("entities") if isinstance(record.get("entities"), dict) else {}
        if not _text(entities.get("publication_number")):
            continue
        document_ids.add(_upsert_document(conn, entities, source_name=_text(item["source_name"]) or "EPO OPS",
                                          authority="official", observed=observed, next_review=next_review,
                                          source_record_id=_text(item["source_id"]), source_refresh_id=run_id))
    counts = {
        "documents_seen": len(document_ids),
        "eu_documents_seen": int(conn.execute("SELECT COUNT(*) AS n FROM patent_documents WHERE active=1 AND jurisdiction='EP'").fetchone()["n"]),
        "uk_documents_seen": int(conn.execute("SELECT COUNT(*) AS n FROM patent_documents WHERE active=1 AND jurisdiction='GB'").fetchone()["n"]),
        "parties_seen": int(conn.execute("SELECT COUNT(*) AS n FROM patent_parties").fetchone()["n"]),
        "families_seen": int(conn.execute("SELECT COUNT(DISTINCT family_id) AS n FROM patent_documents WHERE COALESCE(family_id,'')<>''").fetchone()["n"]),
        "legal_events_seen": int(conn.execute("SELECT COUNT(*) AS n FROM patent_legal_events").fetchone()["n"]),
    }
    conn.execute(
        """INSERT INTO patent_global_monitor_runs
        (run_id,started_at,completed_at,status,documents_seen,eu_documents_seen,uk_documents_seen,parties_seen,
         families_seen,legal_events_seen,metadata_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id) DO UPDATE SET completed_at=excluded.completed_at,status=excluded.status,
        documents_seen=excluded.documents_seen,eu_documents_seen=excluded.eu_documents_seen,
        uk_documents_seen=excluded.uk_documents_seen,parties_seen=excluded.parties_seen,
        families_seen=excluded.families_seen,legal_events_seen=excluded.legal_events_seen,
        metadata_json=excluded.metadata_json""",
        (run_id, observed, _iso(datetime.now(timezone.utc).replace(microsecond=0)), "Healthy", *counts.values(),
         json.dumps({"google_patents": "discovery only", "legal_boundary": "patent intelligence; not legal advice"})),
    )
    return counts


def global_metrics(conn) -> dict[str, Any]:
    counts = dict(conn.execute(
        "SELECT COUNT(*) AS documents,SUM(CASE WHEN jurisdiction='EP' THEN 1 ELSE 0 END) AS eu_documents,"
        "SUM(CASE WHEN jurisdiction='GB' THEN 1 ELSE 0 END) AS uk_documents,"
        "SUM(CASE WHEN jurisdiction='US' THEN 1 ELSE 0 END) AS us_documents FROM patent_documents WHERE active=1"
    ).fetchone() or {})
    counts.update({
        "parties": int(conn.execute("SELECT COUNT(*) AS n FROM patent_parties").fetchone()["n"]),
        "families": int(conn.execute("SELECT COUNT(DISTINCT family_id) AS n FROM patent_documents WHERE COALESCE(family_id,'')<>''").fetchone()["n"]),
        "legal_events": int(conn.execute("SELECT COUNT(*) AS n FROM patent_legal_events").fetchone()["n"]),
        "latest_monitor": dict(conn.execute("SELECT * FROM patent_global_monitor_runs ORDER BY completed_at DESC LIMIT 1").fetchone() or {}),
    })
    return {key: int(value or 0) if key != "latest_monitor" else value for key, value in counts.items()}


def global_facets(conn) -> dict[str, list[str]]:
    return {"jurisdiction": [str(row[0]) for row in conn.execute(
        "SELECT DISTINCT jurisdiction FROM patent_documents WHERE active=1 ORDER BY jurisdiction"
    ).fetchall()]}


def global_documents(conn, *, search: str = "", jurisdiction: str = "All", source: str = "All", limit: int = 500) -> list[dict[str, Any]]:
    clauses = ["d.active=1"]; params: list[Any] = []
    if search.strip():
        q = f"%{search.strip().casefold()}%"
        clauses.append("(LOWER(d.publication_number) LIKE ? OR LOWER(d.title) LIKE ? OR EXISTS "
                       "(SELECT 1 FROM patent_parties pt WHERE pt.patent_document_id=d.patent_document_id "
                       "AND LOWER(pt.party_name) LIKE ?))")
        params.extend([q, q, q])
    if jurisdiction != "All": clauses.append("d.jurisdiction=?"); params.append(jurisdiction)
    if source == "FDA Orange Book": clauses.append("d.source_name=?"); params.append(source)
    elif source == "EPO / EP": clauses.append("d.jurisdiction='EP'")
    elif source == "UK / GB": clauses.append("d.jurisdiction='GB'")
    params.append(max(1, min(int(limit), 1000)))
    rows = [dict(row) for row in conn.execute(
        f"SELECT d.* FROM patent_documents d WHERE {' AND '.join(clauses)} "
        "ORDER BY d.publication_date DESC,d.publication_number LIMIT ?", tuple(params),
    ).fetchall()]
    ids = [row["patent_document_id"] for row in rows]
    parties: dict[str, list[str]] = {}
    if ids:
        placeholders = ",".join("?" for _ in ids)
        for party in conn.execute(
            f"SELECT patent_document_id,party_name FROM patent_parties WHERE patent_document_id IN ({placeholders}) "
            "ORDER BY party_type,party_name", tuple(ids),
        ).fetchall():
            parties.setdefault(str(party["patent_document_id"]), []).append(str(party["party_name"]))
    for row in rows:
        row["reported_parties"] = " · ".join(parties.get(row["patent_document_id"], []))
    return rows


def global_document_profile(conn, patent_document_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM patent_documents WHERE patent_document_id=?", (patent_document_id,)).fetchone()
    if not row: return None
    result = dict(row)
    result["parties"] = [dict(item) for item in conn.execute(
        "SELECT * FROM patent_parties WHERE patent_document_id=? ORDER BY party_type,party_name", (patent_document_id,)
    ).fetchall()]
    result["family_members"] = [dict(item) for item in conn.execute(
        "SELECT * FROM patent_family_members WHERE patent_document_id=? OR family_id=? ORDER BY jurisdiction,publication_number",
        (patent_document_id, result.get("family_id") or "__none__"),
    ).fetchall()]
    result["legal_events"] = [dict(item) for item in conn.execute(
        "SELECT * FROM patent_legal_events WHERE patent_document_id=? ORDER BY event_date DESC", (patent_document_id,)
    ).fetchall()]
    result["product_links"] = [dict(item) for item in conn.execute(
        "SELECT l.*,p.trade_name,p.ingredient FROM patent_product_links l JOIN lifecycle_products p ON p.lifecycle_id=l.lifecycle_id "
        "WHERE l.patent_document_id=? ORDER BY p.trade_name", (patent_document_id,),
    ).fetchall()]
    return result
