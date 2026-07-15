from __future__ import annotations

import json

from pharmadrone import db
from pharmadrone.pipeline import pharmaceutical_memory


def _lead(score: int = 72) -> tuple:
    payload = {
        "stable_lead_id": "lead-memory-1", "company": "Example Pharma", "product": "Example Tablet",
        "molecule": "Example API", "problem_category": "dissolution", "source_type": "openfda_enforcement",
        "source_id": "REC-1", "region": "US", "score": score, "grade": "B", "lead_status": "needs validation",
        "novelty_status": "new", "evidence_links_json": json.dumps([{"url": "https://example.test/evidence"}]),
        "evidence_hash": f"hash-{score}",
    }
    fields = (
        "stable_lead_id","company","product","molecule","problem_category","source_type","source_id","region",
        "score","grade","lead_status","novelty_status","evidence_links_json","evidence_hash",
    )
    return fields, tuple(payload[field] for field in fields)


def test_memory_sync_is_idempotent_and_preserves_evidence_links(tmp_path):
    conn = db.connect(tmp_path / "memory-idempotent.sqlite")
    fields, values = _lead()
    conn.execute(f"INSERT INTO opportunity_index ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", values)
    conn.commit()
    first = pharmaceutical_memory.sync_from_opportunity_index(conn)
    second = pharmaceutical_memory.sync_from_opportunity_index(conn)
    assert first == second
    assert second["entities"] == 4
    assert second["relationships"] == 4
    assert second["observations"] == 1
    relationship = conn.execute("SELECT * FROM memory_relationships WHERE relationship_type='has_product'").fetchone()
    assert relationship["stable_lead_id"] == "lead-memory-1"
    assert relationship["evidence_url"] == "https://example.test/evidence"


def test_changed_lead_adds_observation_without_changing_identity(tmp_path):
    conn = db.connect(tmp_path / "memory-change.sqlite")
    fields, values = _lead()
    conn.execute(f"INSERT INTO opportunity_index ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", values)
    conn.commit()
    pharmaceutical_memory.sync_from_opportunity_index(conn)
    conn.execute("UPDATE opportunity_index SET score=81,evidence_hash='hash-81' WHERE stable_lead_id='lead-memory-1'")
    conn.commit()
    metrics = pharmaceutical_memory.sync_from_opportunity_index(conn)
    assert metrics["entities"] == 4
    assert metrics["relationships"] == 4
    assert metrics["observations"] == 2
