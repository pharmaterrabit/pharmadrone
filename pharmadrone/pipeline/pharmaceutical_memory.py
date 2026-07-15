"""Phase 7 pharmaceutical memory derived from governed opportunity evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any


ENTITY_FIELDS = (("company", "company"), ("product", "product"), ("molecule", "molecule"), ("problem", "problem_category"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalise(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _id(prefix: str, *parts: Any) -> str:
    payload = "|".join(_normalise(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _evidence_url(row: dict[str, Any]) -> str:
    try:
        links = json.loads(row.get("evidence_links_json") or "[]")
    except (TypeError, ValueError):
        links = []
    if isinstance(links, list) and links:
        first = links[0]
        return str(first.get("url") if isinstance(first, dict) else first or "")
    return ""


def _upsert_entity(conn, entity_type: str, value: str, observed_at: str) -> str:
    canonical = _normalise(value)
    entity_id = _id("ent", entity_type, canonical)
    conn.execute(
        """INSERT INTO memory_entities
        (entity_id,entity_type,canonical_key,display_name,attributes_json,first_seen_at,last_seen_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(entity_id) DO UPDATE SET display_name=excluded.display_name,last_seen_at=excluded.last_seen_at""",
        (entity_id, entity_type, canonical, value.strip(), "{}", observed_at, observed_at),
    )
    return entity_id


def _upsert_relationship(conn, subject: str, relation: str, object_id: str, row: dict[str, Any], observed_at: str) -> None:
    stable_id = str(row.get("stable_lead_id") or "")
    relationship_id = _id("rel", subject, relation, object_id, stable_id)
    conn.execute(
        """INSERT INTO memory_relationships
        (relationship_id,subject_entity_id,relationship_type,object_entity_id,stable_lead_id,
         source_type,source_id,evidence_url,evidence_status,first_seen_at,last_seen_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(relationship_id) DO UPDATE SET source_type=excluded.source_type,
        source_id=excluded.source_id,evidence_url=excluded.evidence_url,last_seen_at=excluded.last_seen_at""",
        (relationship_id, subject, relation, object_id, stable_id, row.get("source_type"),
         row.get("source_id"), _evidence_url(row), "requires human validation", observed_at, observed_at),
    )


def sync_from_opportunity_index(conn) -> dict[str, int]:
    """Idempotently project stored opportunities into the Phase 7 memory layer."""
    rows = [dict(row) for row in conn.execute("SELECT * FROM opportunity_index ORDER BY stable_lead_id").fetchall()]
    observed_at = _now()
    for row in rows:
        entities: dict[str, str] = {}
        for entity_type, field in ENTITY_FIELDS:
            value = str(row.get(field) or "").strip()
            if value:
                entities[entity_type] = _upsert_entity(conn, entity_type, value, observed_at)
        company = entities.get("company")
        if company:
            for target, relation in (("product", "has_product"), ("molecule", "develops_molecule"), ("problem", "has_public_problem_signal")):
                if entities.get(target):
                    _upsert_relationship(conn, company, relation, entities[target], row, observed_at)
        if entities.get("product") and entities.get("problem"):
            _upsert_relationship(conn, entities["product"], "has_public_problem_signal", entities["problem"], row, observed_at)

        snapshot = {key: row.get(key) for key in (
            "stable_lead_id", "company", "product", "molecule", "problem_category", "source_type",
            "source_id", "region", "score", "grade", "lead_status", "novelty_status", "evidence_hash",
        )}
        encoded = json.dumps(snapshot, sort_keys=True, default=str)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT INTO memory_observations
            (stable_lead_id,observation_hash,observed_at,snapshot_json) VALUES (?,?,?,?)
            ON CONFLICT(stable_lead_id,observation_hash) DO NOTHING""",
            (row.get("stable_lead_id"), digest, observed_at, encoded),
        )
    conn.commit()
    return memory_metrics(conn)


def memory_metrics(conn) -> dict[str, int]:
    entity_counts = {
        str(row["entity_type"]): int(row["n"])
        for row in conn.execute("SELECT entity_type,COUNT(*) AS n FROM memory_entities GROUP BY entity_type").fetchall()
    }
    return {
        **entity_counts,
        "entities": int(conn.execute("SELECT COUNT(*) AS n FROM memory_entities").fetchone()["n"]),
        "relationships": int(conn.execute("SELECT COUNT(*) AS n FROM memory_relationships").fetchone()["n"]),
        "observations": int(conn.execute("SELECT COUNT(*) AS n FROM memory_observations").fetchone()["n"]),
    }


def company_memories(conn, search: str = "", limit: int = 50) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = "e.entity_type='company'"
    if search.strip():
        where += " AND LOWER(e.display_name) LIKE ?"
        params.append(f"%{search.strip().lower()}%")
    params.append(limit)
    rows = conn.execute(
        f"""SELECT e.entity_id,e.display_name,COUNT(DISTINCT r.relationship_id) AS relationships,
        COUNT(DISTINCT r.stable_lead_id) AS opportunity_signals,MAX(r.last_seen_at) AS last_seen_at
        FROM memory_entities e LEFT JOIN memory_relationships r ON r.subject_entity_id=e.entity_id
        WHERE {where} GROUP BY e.entity_id,e.display_name
        ORDER BY opportunity_signals DESC,e.display_name LIMIT ?""",
        tuple(params),
    ).fetchall()
    return [dict(row) for row in rows]


def company_relationships(conn, entity_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.relationship_type,o.entity_type,o.display_name,r.source_type,r.source_id,
        r.evidence_url,r.evidence_status,r.stable_lead_id,r.last_seen_at
        FROM memory_relationships r JOIN memory_entities o ON o.entity_id=r.object_entity_id
        WHERE r.subject_entity_id=? ORDER BY r.last_seen_at DESC,o.display_name""",
        (entity_id,),
    ).fetchall()
    return [dict(row) for row in rows]
