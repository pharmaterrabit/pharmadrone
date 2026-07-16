"""Phase 10 evidence-governed research and innovation projection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import itertools
import json
from typing import Any


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


def _canonical_publication_key(record: dict, entities: dict) -> str:
    for prefix, key in (("doi", "doi"), ("pmcid", "pmcid"), ("pmid", "pmid"), ("openalex", "openalex_id")):
        value = _text(entities.get(key)).replace("https://doi.org/", "")
        if value:
            return f"{prefix}:{value.casefold()}"
    return f"source:{_text(record.get('source_type')).casefold()}:{_text(record.get('record_id')).casefold()}"


def _organisation_id(item: dict) -> str:
    return _id("resorg", item.get("ror_id") or item.get("openalex_id") or item.get("name"))


def _author_id(item: dict) -> str:
    return _id("resauthor", item.get("orcid") or item.get("openalex_id") or item.get("name"))


def _upsert_organisation(conn, item: dict, observed: str, next_review: str) -> str:
    existing = conn.execute(
        "SELECT research_organisation_id FROM research_organisations WHERE LOWER(canonical_name)=LOWER(?) LIMIT 1",
        (_text(item.get("name")),),
    ).fetchone()
    organisation_id = str(existing[0]) if existing else _organisation_id(item)
    identity = (
        "Official ROR-linked research organisation" if _text(item.get("ror_id"))
        else "OpenAlex institution identity" if _text(item.get("openalex_id"))
        else _text(item.get("identity_status") or "Source-stated organisation; canonical identity requires validation")
    )
    conn.execute(
        """INSERT INTO research_organisations
        (research_organisation_id,canonical_name,organisation_type,country_code,ror_id,openalex_id,official_url,
         identity_status,first_seen_at,last_verified_at,next_review_at,attributes_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(research_organisation_id) DO UPDATE SET canonical_name=excluded.canonical_name,
        organisation_type=COALESCE(NULLIF(excluded.organisation_type,''),research_organisations.organisation_type),
        country_code=COALESCE(NULLIF(excluded.country_code,''),research_organisations.country_code),
        ror_id=COALESCE(NULLIF(excluded.ror_id,''),research_organisations.ror_id),
        openalex_id=COALESCE(NULLIF(excluded.openalex_id,''),research_organisations.openalex_id),
        official_url=COALESCE(NULLIF(excluded.official_url,''),research_organisations.official_url),
        identity_status=CASE WHEN excluded.ror_id<>'' OR excluded.openalex_id<>'' THEN excluded.identity_status ELSE research_organisations.identity_status END,
        active=1,last_verified_at=excluded.last_verified_at,next_review_at=excluded.next_review_at""",
        (organisation_id, _text(item.get("name")), _text(item.get("organisation_type")),
         _text(item.get("country_code")), _text(item.get("ror_id")), _text(item.get("openalex_id")),
         _text(item.get("official_url")), identity, observed, observed, next_review, "{}"),
    )
    return organisation_id


def _insert_partnership(conn, *, party_a: str, party_b: str, party_a_id: str = "", party_b_id: str = "",
                        partnership_type: str, programme: str, source_type: str, source_id: str,
                        evidence_url: str, evidence_status: str, formal_status: str,
                        observed: str, next_review: str) -> str:
    ordered = sorted(((party_a, party_a_id), (party_b, party_b_id)), key=lambda value: value[0].casefold())
    partnership_id = _id("respartner", ordered[0][0], ordered[1][0], partnership_type, source_type, source_id)
    conn.execute(
        """INSERT INTO research_partnerships
        (research_partnership_id,party_a_name,party_b_name,party_a_organisation_id,party_b_organisation_id,
         partnership_type,programme_name,source_type,source_id,evidence_url,evidence_status,formal_status,
         first_seen_at,last_verified_at,next_review_at,attributes_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(research_partnership_id) DO UPDATE SET programme_name=excluded.programme_name,
        evidence_url=excluded.evidence_url,evidence_status=excluded.evidence_status,formal_status=excluded.formal_status,
        active=1,last_verified_at=excluded.last_verified_at,next_review_at=excluded.next_review_at""",
        (partnership_id, ordered[0][0], ordered[1][0], ordered[0][1], ordered[1][1], partnership_type,
         programme, source_type, source_id, evidence_url, evidence_status, formal_status,
         observed, observed, next_review, "{}"),
    )
    return partnership_id


def _snapshot(conn, organisation_id: str, observed: str) -> bool:
    org = dict(conn.execute("SELECT * FROM research_organisations WHERE research_organisation_id=?", (organisation_id,)).fetchone() or {})
    org.pop("last_verified_at", None); org.pop("next_review_at", None)
    org["publication_ids"] = [row[0] for row in conn.execute(
        "SELECT research_publication_id FROM research_organisation_publications WHERE research_organisation_id=? ORDER BY research_publication_id",
        (organisation_id,),
    ).fetchall()]
    org["partnership_ids"] = [row[0] for row in conn.execute(
        "SELECT research_partnership_id FROM research_partnerships WHERE active=1 AND (party_a_organisation_id=? OR party_b_organisation_id=?) ORDER BY research_partnership_id",
        (organisation_id, organisation_id),
    ).fetchall()]
    org["technology_ids"] = [row[0] for row in conn.execute(
        "SELECT research_technology_id FROM research_technologies WHERE active=1 AND research_organisation_id=? ORDER BY research_technology_id",
        (organisation_id,),
    ).fetchall()]
    encoded = json.dumps(org, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    changed = not conn.execute(
        "SELECT 1 FROM research_organisation_observations WHERE research_organisation_id=? AND observation_hash=?",
        (organisation_id, digest),
    ).fetchone()
    conn.execute(
        "INSERT INTO research_organisation_observations (research_organisation_id,observation_hash,observed_at,snapshot_json) "
        "VALUES (?,?,?,?) ON CONFLICT(research_organisation_id,observation_hash) DO NOTHING",
        (organisation_id, digest, observed, encoded),
    )
    return changed


def sync(conn, *, run_id: str = "manual-research-innovation", observed_at: datetime | None = None) -> dict[str, int]:
    now = observed_at or datetime.now(timezone.utc).replace(microsecond=0)
    observed = _iso(now); next_review = _iso(now + timedelta(days=7))
    conn.execute("UPDATE research_organisations SET active=0")
    conn.execute("UPDATE research_publications SET active=0")
    conn.execute("UPDATE research_authors SET active=0")
    conn.execute("UPDATE research_partnerships SET active=0")
    conn.execute("UPDATE research_technologies SET active=0")
    conn.execute("DELETE FROM research_publication_authors")
    conn.execute("DELETE FROM research_organisation_publications")

    rows = [dict(row) for row in conn.execute(
        "SELECT source_type,source_id,source_name,official_source_url,record_json FROM source_records "
        "WHERE active=1 AND source_type IN ('paper','trial','technology_transfer','university_technology') ORDER BY source_type,source_id"
    ).fetchall()]
    organisations: set[str] = set(); publications: set[str] = set(); authors_seen: set[str] = set()
    partnerships: set[str] = set(); technologies: set[str] = set()

    paper_rows = [row for row in rows if row["source_type"] == "paper"]
    grouped: dict[str, list[tuple[dict, dict, dict]]] = {}
    for stored in paper_rows:
        record = _json(stored["record_json"], {})
        entities = record.get("entities") if isinstance(record.get("entities"), dict) else {}
        grouped.setdefault(_canonical_publication_key(record, entities), []).append((stored, record, entities))

    for canonical_key, versions in grouped.items():
        publication_id = _id("respub", canonical_key); publications.add(publication_id)
        all_sources = sorted({_text(stored["source_name"] or record.get("source_name")) for stored, record, _ in versions if _text(stored["source_name"] or record.get("source_name"))})
        urls = sorted({_text(stored["official_source_url"] or record.get("url")) for stored, record, _ in versions if _text(stored["official_source_url"] or record.get("url")).startswith("http")})
        def best(key: str) -> Any:
            values = [entities.get(key) for _, _, entities in versions if entities.get(key) not in (None, "", [], {})]
            return max(values, key=lambda value: len(_text(value)), default="")
        title = _text(best("publication_title") or versions[0][1].get("title"))
        abstract = _text(best("abstract") or versions[0][1].get("raw_text"))
        conn.execute(
            """INSERT INTO research_publications
            (research_publication_id,canonical_key,title,doi,pmid,pmcid,openalex_id,journal,publication_type,
             publication_date,publication_year,abstract_text,citation_count,open_access,sources_json,evidence_urls_json,
             evidence_status,first_seen_at,last_verified_at,next_review_at,attributes_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(research_publication_id) DO UPDATE SET title=excluded.title,doi=excluded.doi,pmid=excluded.pmid,
            pmcid=excluded.pmcid,openalex_id=excluded.openalex_id,journal=excluded.journal,
            publication_type=excluded.publication_type,publication_date=excluded.publication_date,
            publication_year=excluded.publication_year,abstract_text=excluded.abstract_text,
            citation_count=excluded.citation_count,open_access=excluded.open_access,sources_json=excluded.sources_json,
            evidence_urls_json=excluded.evidence_urls_json,evidence_status=excluded.evidence_status,active=1,
            last_verified_at=excluded.last_verified_at,next_review_at=excluded.next_review_at""",
            (publication_id, canonical_key, title or "Untitled publication", _text(best("doi")), _text(best("pmid")),
             _text(best("pmcid")), _text(best("openalex_id")), _text(best("journal")), _text(best("publication_type")),
             _text(best("publication_date")), _text(best("publication_year")), abstract,
             max([int(entities.get("citation_count") or 0) for _, _, entities in versions] or [0]),
             int(any(bool(entities.get("open_access")) for _, _, entities in versions)), json.dumps(all_sources),
             json.dumps(urls), "Publication metadata from retained public scholarly sources",
             observed, observed, next_review, "{}"),
        )

        publication_orgs: dict[str, tuple[str, str]] = {}
        for stored, record, entities in versions:
            evidence_url = _text(stored["official_source_url"] or record.get("url"))
            source_name = _text(stored["source_name"] or record.get("source_name"))
            institution_keys: dict[str, str] = {}
            for institution in entities.get("institutions", []) or []:
                if not isinstance(institution, dict) or not _text(institution.get("name")):
                    continue
                org_id = _upsert_organisation(conn, institution, observed, next_review)
                organisations.add(org_id)
                key = _text(institution.get("openalex_id") or institution.get("ror_id") or institution.get("name")).casefold()
                institution_keys[key] = org_id
                publication_orgs[org_id] = (_text(institution.get("name")), evidence_url)
                conn.execute(
                    "INSERT INTO research_organisation_publications (research_organisation_id,research_publication_id,affiliation_evidence,evidence_url,evidence_status) "
                    "VALUES (?,?,?,?,?) ON CONFLICT(research_organisation_id,research_publication_id) DO UPDATE SET affiliation_evidence=excluded.affiliation_evidence,evidence_url=excluded.evidence_url,evidence_status=excluded.evidence_status",
                    (org_id, publication_id, "Structured institution affiliation", evidence_url,
                     f"{source_name} publication affiliation metadata"),
                )
            for author in entities.get("authors", []) or []:
                if not isinstance(author, dict) or not _text(author.get("name")):
                    continue
                author_id = _author_id(author); authors_seen.add(author_id)
                orcid = _text(author.get("orcid")).replace("https://orcid.org/", "")
                openalex_id = _text(author.get("openalex_id"))
                profile_url = f"https://orcid.org/{orcid}" if orcid else openalex_id
                conn.execute(
                    """INSERT INTO research_authors
                    (research_author_id,display_name,orcid,openalex_id,profile_url,identity_status,current_role_status,
                     first_seen_at,last_verified_at,next_review_at,attributes_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(research_author_id) DO UPDATE SET display_name=excluded.display_name,orcid=excluded.orcid,
                    openalex_id=excluded.openalex_id,profile_url=excluded.profile_url,identity_status=excluded.identity_status,
                    current_role_status=excluded.current_role_status,active=1,last_verified_at=excluded.last_verified_at,
                    next_review_at=excluded.next_review_at""",
                    (author_id, _text(author.get("name")), orcid, openalex_id, profile_url,
                     "ORCID-linked author" if orcid else f"Author listed by {source_name}",
                     "Publication authorship is not proof of current employment or technology-transfer authority",
                     observed, observed, next_review, "{}"),
                )
                affiliations = [_text(value) for value in author.get("affiliations", []) or [] if _text(value)] or [""]
                linked_keys = [_text(value).casefold() for value in author.get("institution_keys", []) or []]
                linked_orgs = [institution_keys[key] for key in linked_keys if key in institution_keys]
                linked_org = linked_orgs[0] if len(linked_orgs) == 1 else ""
                for affiliation in affiliations:
                    conn.execute(
                        "INSERT INTO research_publication_authors (research_publication_id,research_author_id,affiliation_text,research_organisation_id,evidence_url,evidence_status) "
                        "VALUES (?,?,?,?,?,?) ON CONFLICT(research_publication_id,research_author_id,affiliation_text) DO UPDATE SET research_organisation_id=excluded.research_organisation_id,evidence_url=excluded.evidence_url,evidence_status=excluded.evidence_status",
                        (publication_id, author_id, affiliation, linked_org or None, evidence_url,
                         f"Authorship/affiliation metadata from {source_name}"),
                    )
        for (org_a, (name_a, url_a)), (org_b, (name_b, url_b)) in itertools.combinations(sorted(publication_orgs.items()), 2):
            partnership_id = _insert_partnership(
                conn, party_a=name_a, party_b=name_b, party_a_id=org_a, party_b_id=org_b,
                partnership_type="publication co-authorship", programme=title, source_type="paper",
                source_id=canonical_key, evidence_url=url_a or url_b,
                evidence_status="Both institutions are attached to the same publication record",
                formal_status="Co-authorship evidence only; no formal or commercial partnership established",
                observed=observed, next_review=next_review,
            )
            partnerships.add(partnership_id)

    for stored in [row for row in rows if row["source_type"] == "trial"]:
        record = _json(stored["record_json"], {}); entities = record.get("entities") or {}
        sponsor = _text(entities.get("sponsor") or entities.get("company")); collaborators = entities.get("collaborators") or []
        if not sponsor or not collaborators:
            continue
        sponsor_item = {"name": sponsor, "organisation_type": "clinical research sponsor",
                        "identity_status": "ClinicalTrials.gov registry organisation name; canonical identity requires validation"}
        sponsor_id = _upsert_organisation(conn, sponsor_item, observed, next_review); organisations.add(sponsor_id)
        for collaborator in collaborators:
            collaborator_name = _text(collaborator.get("name") if isinstance(collaborator, dict) else collaborator)
            if not collaborator_name:
                continue
            collaborator_id = _upsert_organisation(conn, {
                "name": collaborator_name, "organisation_type": "clinical research collaborator",
                "identity_status": "ClinicalTrials.gov registry organisation name; canonical identity requires validation",
            }, observed, next_review)
            organisations.add(collaborator_id)
            partnership_id = _insert_partnership(
                conn, party_a=sponsor, party_b=collaborator_name, party_a_id=sponsor_id, party_b_id=collaborator_id,
                partnership_type="clinical research collaboration", programme=_text(record.get("title")),
                source_type="trial", source_id=_text(stored["source_id"]),
                evidence_url=_text(stored["official_source_url"] or record.get("url")),
                evidence_status="Sponsor and collaborator are explicitly listed in ClinicalTrials.gov",
                formal_status="Registry-listed research collaboration; contractual and commercial terms not established",
                observed=observed, next_review=next_review,
            )
            partnerships.add(partnership_id)

    for stored in [row for row in rows if row["source_type"] in {"technology_transfer", "university_technology"}]:
        record = _json(stored["record_json"], {}); entities = record.get("entities") or {}
        evidence_url = _text(stored["official_source_url"] or record.get("url"))
        title = _text(entities.get("technology_title") or record.get("title"))
        organisation_name = _text(entities.get("organisation") or entities.get("company") or entities.get("institution"))
        if not title or not evidence_url.startswith("http"):
            continue
        organisation_id = ""
        if organisation_name:
            organisation_id = _upsert_organisation(conn, {
                "name": organisation_name, "organisation_type": "technology transfer organisation",
                "official_url": _text(entities.get("organisation_url")),
                "identity_status": "Organisation stated by official technology-transfer evidence",
            }, observed, next_review)
            organisations.add(organisation_id)
        technology_id = _id("restech", stored["source_type"], stored["source_id"]); technologies.add(technology_id)
        conn.execute(
            """INSERT INTO research_technologies
            (research_technology_id,research_organisation_id,title,summary,technology_category,licensing_status,
             transfer_contact,source_type,source_id,evidence_url,evidence_status,first_seen_at,last_verified_at,next_review_at,attributes_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(research_technology_id) DO UPDATE SET research_organisation_id=excluded.research_organisation_id,
            title=excluded.title,summary=excluded.summary,technology_category=excluded.technology_category,
            licensing_status=excluded.licensing_status,transfer_contact=excluded.transfer_contact,evidence_url=excluded.evidence_url,
            evidence_status=excluded.evidence_status,active=1,last_verified_at=excluded.last_verified_at,next_review_at=excluded.next_review_at""",
            (technology_id, organisation_id or None, title, _text(entities.get("summary") or record.get("raw_text")),
             _text(entities.get("technology_category")), _text(entities.get("licensing_status") or "Status not stated; human verification required"),
             _text(entities.get("transfer_contact")), stored["source_type"], stored["source_id"], evidence_url,
             "Official technology-transfer page retained; availability must be reconfirmed", observed, observed, next_review, "{}"),
        )

    changed = sum(int(_snapshot(conn, org_id, observed)) for org_id in organisations)
    transfer_due = int(conn.execute(
        "SELECT COUNT(*) AS n FROM research_organisations o WHERE o.active=1 AND NOT EXISTS "
        "(SELECT 1 FROM research_technologies t WHERE t.active=1 AND t.research_organisation_id=o.research_organisation_id)"
    ).fetchone()["n"])
    completed = _iso(datetime.now(timezone.utc).replace(microsecond=0))
    conn.execute(
        """INSERT INTO research_monitor_runs
        (run_id,started_at,completed_at,status,organisations_seen,organisations_changed,publications_seen,authors_seen,
         partnerships_seen,technologies_seen,transfer_resolution_required,metadata_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(run_id) DO UPDATE SET completed_at=excluded.completed_at,status=excluded.status,
        organisations_seen=excluded.organisations_seen,organisations_changed=excluded.organisations_changed,
        publications_seen=excluded.publications_seen,authors_seen=excluded.authors_seen,
        partnerships_seen=excluded.partnerships_seen,technologies_seen=excluded.technologies_seen,
        transfer_resolution_required=excluded.transfer_resolution_required,metadata_json=excluded.metadata_json""",
        (run_id, observed, completed, "Healthy", len(organisations), changed, len(publications), len(authors_seen),
         len(partnerships), len(technologies), transfer_due, json.dumps({
             "governance": "publication, affiliation, co-authorship, registry collaboration and transfer availability remain distinct"
         })),
    )
    return {"organisations_seen": len(organisations), "organisations_changed": changed,
            "publications_seen": len(publications), "authors_seen": len(authors_seen),
            "partnerships_seen": len(partnerships), "technologies_seen": len(technologies),
            "transfer_resolution_required": transfer_due}


def metrics(conn) -> dict[str, Any]:
    def count(table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE active=1").fetchone()["n"])
    return {
        "organisations": count("research_organisations"), "publications": count("research_publications"),
        "authors": count("research_authors"), "partnerships": count("research_partnerships"),
        "technologies": count("research_technologies"),
        "transfer_resolution_required": int(conn.execute(
            "SELECT COUNT(*) AS n FROM research_organisations o WHERE o.active=1 AND NOT EXISTS "
            "(SELECT 1 FROM research_technologies t WHERE t.active=1 AND t.research_organisation_id=o.research_organisation_id)"
        ).fetchone()["n"]),
        "latest_monitor": dict(conn.execute("SELECT * FROM research_monitor_runs ORDER BY completed_at DESC LIMIT 1").fetchone() or {}),
    }


def organisations(conn, *, search: str = "", country: str = "All", limit: int = 250) -> list[dict[str, Any]]:
    clauses = ["o.active=1"]; params: list[Any] = []
    if search.strip():
        q = f"%{search.strip().casefold()}%"; clauses.append("LOWER(o.canonical_name) LIKE ?"); params.append(q)
    if country != "All": clauses.append("o.country_code=?"); params.append(country)
    params.append(max(1, min(int(limit), 1000)))
    rows = conn.execute(f"""SELECT o.*,
        COUNT(DISTINCT op.research_publication_id) AS publication_count,
        COUNT(DISTINCT CASE WHEN p.active=1 THEN p.research_partnership_id END) AS partnership_count,
        COUNT(DISTINCT CASE WHEN t.active=1 THEN t.research_technology_id END) AS technology_count
        FROM research_organisations o
        LEFT JOIN research_organisation_publications op ON op.research_organisation_id=o.research_organisation_id
        LEFT JOIN research_partnerships p ON p.party_a_organisation_id=o.research_organisation_id OR p.party_b_organisation_id=o.research_organisation_id
        LEFT JOIN research_technologies t ON t.research_organisation_id=o.research_organisation_id
        WHERE {' AND '.join(clauses)} GROUP BY o.research_organisation_id,o.canonical_name,o.organisation_type,
        o.country_code,o.ror_id,o.openalex_id,o.official_url,o.identity_status,o.active,o.first_seen_at,
        o.last_verified_at,o.next_review_at,o.attributes_json ORDER BY publication_count DESC,o.canonical_name LIMIT ?""", tuple(params)).fetchall()
    return [dict(row) for row in rows]


def publications(conn, *, search: str = "", limit: int = 250) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "p.active=1"
    if search.strip():
        q = f"%{search.strip().casefold()}%"; where += " AND (LOWER(p.title) LIKE ? OR LOWER(p.abstract_text) LIKE ? OR LOWER(p.doi) LIKE ?)"; params.extend([q, q, q])
    params.append(max(1, min(int(limit), 1000)))
    rows = conn.execute(f"""SELECT p.*,COUNT(DISTINCT op.research_organisation_id) AS organisation_count,
        COUNT(DISTINCT pa.research_author_id) AS author_count FROM research_publications p
        LEFT JOIN research_organisation_publications op ON op.research_publication_id=p.research_publication_id
        LEFT JOIN research_publication_authors pa ON pa.research_publication_id=p.research_publication_id
        WHERE {where} GROUP BY p.research_publication_id,p.canonical_key,p.title,p.doi,p.pmid,p.pmcid,p.openalex_id,
        p.journal,p.publication_type,p.publication_date,p.publication_year,p.abstract_text,p.citation_count,p.open_access,
        p.sources_json,p.evidence_urls_json,p.evidence_status,p.active,p.first_seen_at,p.last_verified_at,p.next_review_at,
        p.attributes_json ORDER BY p.publication_year DESC,p.citation_count DESC,p.title LIMIT ?""", tuple(params)).fetchall()
    return [dict(row) for row in rows]


def partnerships(conn, *, search: str = "", limit: int = 250) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "active=1"
    if search.strip():
        q = f"%{search.strip().casefold()}%"; where += " AND (LOWER(party_a_name) LIKE ? OR LOWER(party_b_name) LIKE ? OR LOWER(programme_name) LIKE ?)"; params.extend([q, q, q])
    params.append(max(1, min(int(limit), 1000)))
    return [dict(row) for row in conn.execute(
        f"SELECT * FROM research_partnerships WHERE {where} ORDER BY last_verified_at DESC,party_a_name LIMIT ?", tuple(params)
    ).fetchall()]


def technologies(conn, *, search: str = "", limit: int = 250) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "t.active=1"
    if search.strip():
        q = f"%{search.strip().casefold()}%"; where += " AND (LOWER(t.title) LIKE ? OR LOWER(t.summary) LIKE ? OR LOWER(o.canonical_name) LIKE ?)"; params.extend([q, q, q])
    params.append(max(1, min(int(limit), 1000)))
    return [dict(row) for row in conn.execute(
        f"SELECT t.*,o.canonical_name AS organisation_name FROM research_technologies t LEFT JOIN research_organisations o ON o.research_organisation_id=t.research_organisation_id WHERE {where} ORDER BY t.last_verified_at DESC,t.title LIMIT ?",
        tuple(params),
    ).fetchall()]


def facets(conn) -> dict[str, list[str]]:
    return {"country": [str(row[0]) for row in conn.execute(
        "SELECT DISTINCT country_code FROM research_organisations WHERE active=1 AND COALESCE(country_code,'')<>'' ORDER BY country_code"
    ).fetchall()]}


def profile(conn, organisation_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM research_organisations WHERE research_organisation_id=?", (organisation_id,)).fetchone()
    if not row: return None
    result = dict(row)
    result["publications"] = [dict(item) for item in conn.execute(
        "SELECT p.*,op.affiliation_evidence,op.evidence_url AS affiliation_url FROM research_publications p "
        "JOIN research_organisation_publications op ON op.research_publication_id=p.research_publication_id "
        "WHERE op.research_organisation_id=? AND p.active=1 ORDER BY p.publication_year DESC,p.citation_count DESC",
        (organisation_id,),
    ).fetchall()]
    result["authors"] = [dict(item) for item in conn.execute(
        "SELECT DISTINCT a.*,pa.affiliation_text,pa.evidence_url FROM research_authors a "
        "JOIN research_publication_authors pa ON pa.research_author_id=a.research_author_id "
        "WHERE pa.research_organisation_id=? AND a.active=1 ORDER BY a.display_name", (organisation_id,),
    ).fetchall()]
    result["partnerships"] = [dict(item) for item in conn.execute(
        "SELECT * FROM research_partnerships WHERE active=1 AND (party_a_organisation_id=? OR party_b_organisation_id=?) ORDER BY last_verified_at DESC",
        (organisation_id, organisation_id),
    ).fetchall()]
    result["technologies"] = [dict(item) for item in conn.execute(
        "SELECT * FROM research_technologies WHERE active=1 AND research_organisation_id=? ORDER BY last_verified_at DESC",
        (organisation_id,),
    ).fetchall()]
    result["history"] = [dict(item) for item in conn.execute(
        "SELECT observed_at,snapshot_json FROM research_organisation_observations WHERE research_organisation_id=? ORDER BY observed_at DESC LIMIT 20",
        (organisation_id,),
    ).fetchall()]
    return result
