import os

import pytest

from pharmadrone.intelligence import CanonicalIntelligenceService
from tests.test_foundation_pr_e import _seed_graph


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not configured")
def test_real_postgresql_canonical_read_layer_profiles_search_and_traversal():
    from pharmadrone.storage.config import DatabaseConfig, normalize_postgres_url
    from pharmadrone.storage.database import open_connection

    conn = open_connection(
        DatabaseConfig(
            backend="postgresql",
            url=normalize_postgres_url(os.environ["TEST_DATABASE_URL"]),
            app_env="test",
        )
    )
    result = conn.ensure_migrations()
    assert result["schema_version"] >= 19
    _seed_graph(conn)

    service = CanonicalIntelligenceService(conn)
    assert service.problem_profile("problem-e").linked_solutions[0].target_id == "solution-e"
    assert service.product_profile("product-e").linked_organisations[0].target_id == "organisation-e"
    assert service.organisation_profile("organisation-e").related_opportunities[0].target_id == "opportunity-e"
    assert service.opportunity_profile("opportunity-e").related_commercial_events[0].target_id == "event-e"
    assert service.search("stable-lead-e").results[0].canonical_id == "opportunity-e"

    traversal = service.traverse("pharmaceutical_problem", "problem-e", max_depth=5)
    assert ("commercial_event", "event-e") in {
        (node.entity_type, node.canonical_id) for node in traversal.nodes
    }
    assert all(edge.verification_status != "requires-review" for edge in traversal.edges)
    conn.close()

