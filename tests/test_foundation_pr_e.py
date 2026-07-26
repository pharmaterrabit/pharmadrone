from pathlib import Path
from unittest.mock import patch

from pharmadrone import db
from pharmadrone.intelligence import CanonicalIntelligenceService
from pharmadrone.pipeline import commercial_intelligence


NOW = "2026-07-26T00:00:00+00:00"


def _seed_graph(conn):
    problem_term = conn.execute(
        "SELECT term_id FROM intelligence_taxonomy_terms "
        "WHERE taxonomy_namespace='problem_domain' ORDER BY term_id LIMIT 1"
    ).fetchone()["term_id"]
    solution_domain = conn.execute(
        "SELECT term_id FROM intelligence_taxonomy_terms "
        "WHERE taxonomy_namespace='solution_domain' ORDER BY term_id LIMIT 1"
    ).fetchone()["term_id"]
    solution_type = conn.execute(
        "SELECT term_id FROM intelligence_taxonomy_terms "
        "WHERE taxonomy_namespace='solution_type' ORDER BY term_id LIMIT 1"
    ).fetchone()["term_id"]
    with conn.transaction():
        conn.execute(
            "INSERT INTO pharmaceutical_problems "
            "(problem_id,canonical_key,display_name,taxonomy_term_id,definition,"
            "identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "problem-e",
                "graph-problem",
                "Graph Problem",
                problem_term,
                "A directly evidenced pharmaceutical problem.",
                "source-derived",
                "official problem evidence",
                NOW,
                NOW,
            ),
        )
        for technology_id, key, name in (
            ("solution-e", "graph-solution", "Graph Solution"),
            ("solution-review-e", "review-solution", "Review Solution"),
        ):
            conn.execute(
                "INSERT INTO technology_solutions "
                "(technology_id,canonical_key,display_name,taxonomy_term_id,"
                "solution_type_term_id,maturity_status,identity_status,evidence_status,"
                "last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    technology_id,
                    key,
                    name,
                    solution_domain,
                    solution_type,
                    "commercial",
                    "source-derived",
                    "official solution evidence",
                    NOW,
                    NOW,
                ),
            )
        conn.execute(
            "INSERT INTO organisation_profiles "
            "(organisation_profile_id,canonical_name,normalized_name,organisation_type,"
            "identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "organisation-e",
                "Shared Entity",
                "shared entity",
                "technology-provider",
                "human-verified",
                "official organisation evidence",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO capability_profiles "
            "(capability_profile_id,canonical_name,normalized_name,capability_type,"
            "identity_status,evidence_status,last_verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                "capability-e",
                "Graph Capability",
                "graph capability",
                "service",
                "human-verified",
                "official capability evidence",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO product_profiles "
            "(product_id,canonical_name,normalized_name,product_type,identity_status,"
            "evidence_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "product-e",
                "Shared Entity",
                "shared entity",
                "medicinal-product",
                "human-verified",
                "official product evidence",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO api_profiles "
            "(api_id,canonical_name,normalized_name,substance_type,identity_status,"
            "evidence_status,last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "api-e",
                "Graph API",
                "graph api",
                "active-substance",
                "human-verified",
                "official API evidence",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO opportunities (id,company,product) VALUES (?,?,?)",
            ("legacy-opportunity-e", "Shared Entity", "Shared Entity"),
        )
        conn.execute(
            "INSERT INTO opportunity_index "
            "(stable_lead_id,company,product,problem_category,source_type,source_id,"
            "queue_status,report_path,report_opportunity_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                "stable-lead-e",
                "Shared Entity",
                "Shared Entity",
                "Graph Problem",
                "official",
                "graph-source",
                "waiting",
                "/reports/graph.md",
                "legacy-opportunity-e",
            ),
        )
        conn.execute(
            "INSERT INTO opportunity_profiles "
            "(opportunity_profile_id,canonical_key,title,opportunity_type,lifecycle_status,"
            "source_type,source_record_id,evidence_url,evidence_status,evidence_basis,"
            "verification_status,observed_at,verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "opportunity-e",
                "graph-opportunity",
                "Graph Opportunity",
                "technology-partnership",
                "validated",
                "official",
                "graph-opportunity",
                "https://example.test/opportunity",
                "official opportunity evidence",
                "Source explicitly describes the opportunity.",
                "human-verified",
                NOW,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO commercial_events "
            "(commercial_event_id,event_type,evidence_class,subject_name,source_type,"
            "source_name,source_id,evidence_url,evidence_status,validation_status,"
            "last_verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-event-e",
                "Partnership",
                "transaction",
                "Graph Commercial Event",
                "official",
                "Company disclosure",
                "graph-event",
                "https://example.test/event",
                "official event evidence",
                "human verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO commercial_event_identity_links "
            "(commercial_event_identity_id,canonical_event_key,commercial_event_id,event_type,"
            "lifecycle_status,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "event-e",
                "graph-event",
                "legacy-event-e",
                "partnership",
                "validated",
                "official",
                "graph-event",
                "https://example.test/event",
                "official event evidence",
                "Existing observed event adapter.",
                "human-verified",
                NOW,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO evidence "
            "(opportunity_id,source_type,source_name,record_id,title,url) "
            "VALUES (?,?,?,?,?,?)",
            (
                "legacy-opportunity-e",
                "official",
                "Company disclosure",
                "graph-evidence",
                "Graph supporting evidence",
                "https://example.test/evidence",
            ),
        )
        evidence_id = conn.execute(
            "SELECT id FROM evidence WHERE record_id='graph-evidence'"
        ).fetchone()["id"]

        conn.execute(
            "INSERT INTO technology_problem_relationships "
            "(relationship_id,technology_id,problem_id,relationship_type,"
            "relationship_statement,source_type,source_id,evidence_url,evidence_status,"
            "inference_status,confidence_score,confidence_basis,verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "problem-solution-e",
                "solution-e",
                "problem-e",
                "addresses",
                "The source directly connects the problem and solution.",
                "official",
                "problem-solution",
                "https://example.test/problem-solution",
                "official relationship evidence",
                "human-verified",
                1.0,
                "Human verified direct source",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO technology_problem_relationships "
            "(relationship_id,technology_id,problem_id,relationship_type,"
            "relationship_statement,source_type,source_id,evidence_url,evidence_status,"
            "inference_status,confidence_score,confidence_basis,verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "problem-solution-review-e",
                "solution-review-e",
                "problem-e",
                "requires-review",
                "A stored relationship awaiting review.",
                "discovery",
                "problem-solution-review",
                "https://example.test/review",
                "discovery evidence",
                "requires-review",
                0.4,
                "Review required",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO product_api_relationships "
            "(product_api_relationship_id,product_id,api_id,relationship_type,source_type,"
            "source_record_id,evidence_url,evidence_status,evidence_basis,"
            "verification_status,observed_at,verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "product-api-e",
                "product-e",
                "api-e",
                "active-ingredient",
                "official",
                "product-api",
                "https://example.test/product-api",
                "official relationship evidence",
                "Source explicitly names the active ingredient.",
                "human-verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO pharmaceutical_entity_aliases "
            "(entity_alias_id,product_id,alias_name,normalized_alias,alias_type,"
            "source_type,source_record_id,evidence_url,evidence_status,"
            "verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "product-alias-e",
                "product-e",
                "Graph Product Alias",
                "graph product alias",
                "brand",
                "official",
                "product-alias",
                "https://example.test/product",
                "official alias evidence",
                "human-verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO pharmaceutical_entity_identifiers "
            "(entity_identifier_id,product_id,identifier_namespace,identifier_value,"
            "normalized_identifier,source_type,source_record_id,evidence_url,"
            "evidence_status,verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "product-identifier-e",
                "product-e",
                "TEST_PRODUCT",
                "PROD-001",
                "prod-001",
                "official",
                "product-id",
                "https://example.test/product",
                "official identifier evidence",
                "human-verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO organisation_aliases "
            "(organisation_alias_id,organisation_profile_id,alias_name,normalized_alias,"
            "alias_type,source_type,source_record_id,evidence_url,evidence_status,"
            "verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "organisation-alias-e",
                "organisation-e",
                "Graph Provider Alias",
                "graph provider alias",
                "trading-name",
                "official",
                "organisation-alias",
                "https://example.test/organisation",
                "official alias evidence",
                "human-verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO organisation_identifiers "
            "(organisation_identifier_id,organisation_profile_id,identifier_namespace,"
            "identifier_value,normalized_identifier,source_type,source_record_id,"
            "evidence_url,evidence_status,verification_status,observed_at,last_verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "organisation-identifier-e",
                "organisation-e",
                "ROR",
                "ROR-001",
                "ror-001",
                "official",
                "organisation-id",
                "https://example.test/organisation",
                "official identifier evidence",
                "human-verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO opportunity_identifiers "
            "(opportunity_identifier_id,opportunity_profile_id,identifier_type,"
            "identifier_namespace,identifier_value,normalized_identifier,"
            "legacy_opportunity_id,source_type,source_record_id,evidence_url,"
            "evidence_status,evidence_basis,verification_status,observed_at,verified_at,"
            "next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "opportunity-legacy-id-e",
                "opportunity-e",
                "legacy-opportunity-id",
                "LEGACY_OPPORTUNITY",
                "legacy-opportunity-e",
                "legacy-opportunity-e",
                "legacy-opportunity-e",
                "PharmaTune",
                "legacy-opportunity-e",
                "https://example.test/opportunity",
                "internal evidence",
                "Adapter to existing opportunity record.",
                "human-verified",
                NOW,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO opportunity_identifiers "
            "(opportunity_identifier_id,opportunity_profile_id,identifier_type,"
            "identifier_namespace,identifier_value,normalized_identifier,stable_lead_id,"
            "source_type,source_record_id,evidence_url,evidence_status,evidence_basis,"
            "verification_status,observed_at,verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "opportunity-stable-id-e",
                "opportunity-e",
                "stable-lead-id",
                "PHARMATUNE_STABLE_LEAD",
                "stable-lead-e",
                "stable-lead-e",
                "stable-lead-e",
                "PharmaTune",
                "stable-lead-e",
                "https://example.test/opportunity",
                "internal evidence",
                "Adapter to existing opportunity queue record.",
                "human-verified",
                NOW,
                NOW,
                NOW,
            ),
        )

        governed_relationships = (
            (
                "organisation_capability_relationships",
                "organisation_capability_relationship_id",
                "capability_profile_id",
                "org-capability-e",
                "capability-e",
                "provides",
            ),
            (
                "organisation_solution_relationships",
                "organisation_solution_relationship_id",
                "technology_id",
                "org-solution-e",
                "solution-e",
                "provides",
            ),
            (
                "organisation_product_relationships",
                "organisation_product_relationship_id",
                "product_id",
                "org-product-e",
                "product-e",
                "manufacturer",
            ),
            (
                "organisation_product_relationships",
                "organisation_product_relationship_id",
                "product_id",
                "org-product-duplicate-e",
                "product-e",
                "manufacturer",
            ),
            (
                "organisation_api_relationships",
                "organisation_api_relationship_id",
                "api_id",
                "org-api-e",
                "api-e",
                "supplier",
            ),
        )
        for table, id_column, target_column, relationship_id, target_id, relationship_type in governed_relationships:
            conn.execute(
                f"INSERT INTO {table} "
                f"({id_column},organisation_profile_id,{target_column},relationship_type,"
                "source_type,source_record_id,evidence_url,evidence_status,evidence_basis,"
                "verification_status,observed_at,verified_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    relationship_id,
                    "organisation-e",
                    target_id,
                    relationship_type,
                    "official",
                    relationship_id,
                    "https://example.test/organisation-relationship",
                    "official relationship evidence",
                    "Source explicitly reports the relationship.",
                    "human-verified",
                    NOW,
                    NOW,
                ),
            )

        opportunity_relationships = (
            (
                "opportunity_problem_relationships",
                "opportunity_problem_relationship_id",
                "problem_id",
                "opportunity-problem-e",
                "problem-e",
                "has-problem",
            ),
            (
                "opportunity_solution_relationships",
                "opportunity_solution_relationship_id",
                "technology_id",
                "opportunity-solution-e",
                "solution-e",
                "seeks",
            ),
            (
                "opportunity_product_relationships",
                "opportunity_product_relationship_id",
                "product_id",
                "opportunity-product-e",
                "product-e",
                "concerns",
            ),
            (
                "opportunity_api_relationships",
                "opportunity_api_relationship_id",
                "api_id",
                "opportunity-api-e",
                "api-e",
                "concerns",
            ),
        )
        for table, id_column, target_column, relationship_id, target_id, relationship_type in opportunity_relationships:
            conn.execute(
                f"INSERT INTO {table} "
                f"({id_column},opportunity_profile_id,{target_column},relationship_type,"
                "source_type,source_record_id,evidence_url,evidence_status,evidence_basis,"
                "verification_status,observed_at,verified_at,next_review_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    relationship_id,
                    "opportunity-e",
                    target_id,
                    relationship_type,
                    "official",
                    relationship_id,
                    "https://example.test/opportunity-relationship",
                    "official relationship evidence",
                    "Source explicitly reports the relationship.",
                    "human-verified",
                    NOW,
                    NOW,
                    NOW,
                ),
            )
        conn.execute(
            "INSERT INTO opportunity_participants "
            "(opportunity_participant_id,opportunity_profile_id,organisation_profile_id,"
            "participant_role,source_type,source_record_id,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "opportunity-participant-e",
                "opportunity-e",
                "organisation-e",
                "technology-provider",
                "official",
                "opportunity-participant",
                "https://example.test/opportunity",
                "official participant evidence",
                "Source explicitly names the participant.",
                "human-verified",
                NOW,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO commercial_event_participants "
            "(commercial_event_participant_id,commercial_event_identity_id,"
            "organisation_profile_id,participant_role,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,"
            "observed_at,verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "event-participant-e",
                "event-e",
                "organisation-e",
                "strategic-partner",
                "official",
                "event-participant",
                "https://example.test/event",
                "official participant evidence",
                "Source explicitly names the event participant.",
                "human-verified",
                NOW,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO opportunity_commercial_event_relationships "
            "(opportunity_commercial_event_relationship_id,opportunity_profile_id,"
            "commercial_event_identity_id,relationship_type,source_type,source_record_id,"
            "evidence_url,evidence_status,evidence_basis,verification_status,"
            "observed_at,verified_at,next_review_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "opportunity-event-e",
                "opportunity-e",
                "event-e",
                "associated-with",
                "official",
                "opportunity-event",
                "https://example.test/event",
                "official relationship evidence",
                "Source explicitly links the opportunity and event.",
                "human-verified",
                NOW,
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO pharmaceutical_evidence_links "
            "(pharmaceutical_evidence_link_id,product_id,evidence_id,source_table,"
            "source_record_id,link_type,evidence_url,evidence_status,evidence_basis,"
            "verification_status,observed_at,verified_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "product-evidence-e",
                "product-e",
                evidence_id,
                "evidence",
                str(evidence_id),
                "identifies",
                "https://example.test/evidence",
                "official product evidence",
                "Evidence identifies the canonical product.",
                "human-verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO organisation_evidence_links "
            "(organisation_evidence_link_id,organisation_profile_id,evidence_id,"
            "source_table,source_record_id,link_type,evidence_url,evidence_status,"
            "evidence_basis,verification_status,observed_at,verified_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "organisation-evidence-e",
                "organisation-e",
                evidence_id,
                "evidence",
                str(evidence_id),
                "identifies",
                "https://example.test/evidence",
                "official organisation evidence",
                "Evidence identifies the canonical organisation.",
                "human-verified",
                NOW,
                NOW,
            ),
        )
        conn.execute(
            "INSERT INTO opportunity_evidence_links "
            "(opportunity_evidence_link_id,opportunity_profile_id,evidence_id,source_table,"
            "source_record_id,link_type,evidence_url,evidence_status,evidence_basis,"
            "verification_status,observed_at,verified_at,next_review_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "opportunity-evidence-e",
                "opportunity-e",
                evidence_id,
                "evidence",
                str(evidence_id),
                "supports-opportunity",
                "https://example.test/evidence",
                "official opportunity evidence",
                "Evidence supports the canonical opportunity.",
                "human-verified",
                NOW,
                NOW,
                NOW,
            ),
        )


def test_each_canonical_profile_query_preserves_governed_relationships_and_evidence(tmp_path):
    conn = db.connect(tmp_path / "pr-e-profiles.sqlite")
    _seed_graph(conn)
    service = CanonicalIntelligenceService(conn)

    problem = service.problem_profile("problem-e")
    assert problem.problem.display_name == "Graph Problem"
    assert problem.taxonomy["taxonomy_namespace"] == "problem_domain"
    assert [item.target_id for item in problem.linked_solutions] == ["solution-e"]
    assert [item.target_id for item in problem.related_opportunities] == ["opportunity-e"]

    solution = service.solution_profile("solution-e")
    assert solution.solution.display_name == "Graph Solution"
    assert solution.solution_type["label"]
    assert solution.linked_problems[0].target_id == "problem-e"
    assert solution.linked_organisations[0].target_id == "organisation-e"

    product = service.product_profile("product-e")
    api = service.api_profile("api-e")
    assert product.linked_pharmaceutical_entities[0].target_id == "api-e"
    assert api.linked_pharmaceutical_entities[0].target_id == "product-e"
    assert product.aliases[0]["alias_name"] == "Graph Product Alias"
    assert product.identifiers[0]["identifier_value"] == "PROD-001"
    assert product.supporting_evidence[-1].evidence_status == "official product evidence"

    organisation = service.organisation_profile("organisation-e")
    assert organisation.capabilities[0].target_id == "capability-e"
    assert organisation.linked_solutions[0].target_id == "solution-e"
    assert organisation.related_commercial_events[0].target_id == "event-e"

    opportunity = service.opportunity_profile("opportunity-e")
    assert opportunity.participants[0].target_id == "organisation-e"
    assert opportunity.linked_problems[0].target_id == "problem-e"
    assert opportunity.related_commercial_events[0].target_id == "event-e"
    assert opportunity.supporting_evidence[-1].verification_status == "human-verified"


def test_cross_entity_search_alias_identifier_ambiguity_and_adapters(tmp_path):
    conn = db.connect(tmp_path / "pr-e-search.sqlite")
    _seed_graph(conn)
    service = CanonicalIntelligenceService(conn)

    assert service.search("Graph Product Alias").results[0].match_kind == "alias"
    assert service.search("PROD-001").results[0].canonical_id == "product-e"
    assert service.search("ROR-001").results[0].canonical_id == "organisation-e"
    assert service.search("stable-lead-e").results[0].canonical_id == "opportunity-e"
    assert service.search("legacy-opportunity-e").results[0].canonical_id == "opportunity-e"

    ambiguous = service.search("Shared Entity")
    assert ambiguous.ambiguous is True
    assert {item.entity_type for item in ambiguous.results} == {"product", "organisation"}
    assert all(item.ambiguous for item in ambiguous.results)
    assert all(item.evidence_status for item in ambiguous.results)


def test_search_pagination_is_bounded_and_empty_data_is_safe(tmp_path):
    conn = db.connect(tmp_path / "pr-e-pagination.sqlite")
    service = CanonicalIntelligenceService(conn)
    assert service.search("missing").results == ()
    assert service.problem_profile("missing") is None
    assert service.traverse("pharmaceutical_problem", "missing") is None

    _seed_graph(conn)
    first = service.search("Shared Entity", page=1, page_size=1)
    second = service.search("Shared Entity", page=2, page_size=1)
    assert len(first.results) == 1
    assert first.has_more is True
    assert len(second.results) == 1
    assert first.results[0].canonical_id != second.results[0].canonical_id
    assert service.search("Shared Entity", page_size=500).page_size == 50
    assert service.search("Shared Entity", page=500).page == 20


def test_requires_review_filter_and_bounded_graph_traversal(tmp_path):
    conn = db.connect(tmp_path / "pr-e-traversal.sqlite")
    _seed_graph(conn)
    service = CanonicalIntelligenceService(conn)

    default_problem = service.problem_profile("problem-e")
    review_problem = service.problem_profile("problem-e", include_requires_review=True)
    assert {item.target_id for item in default_problem.linked_solutions} == {"solution-e"}
    assert {item.target_id for item in review_problem.linked_solutions} == {
        "solution-e",
        "solution-review-e",
    }

    graph = service.traverse("pharmaceutical_problem", "problem-e", max_depth=5)
    assert graph.max_depth == 5
    assert {(node.entity_type, node.canonical_id) for node in graph.nodes} >= {
        ("pharmaceutical_problem", "problem-e"),
        ("technology_solution", "solution-e"),
        ("organisation", "organisation-e"),
        ("product", "product-e"),
        ("api", "api-e"),
        ("opportunity", "opportunity-e"),
        ("commercial_event", "event-e"),
    }
    assert len({(node.entity_type, node.canonical_id) for node in graph.nodes}) == len(graph.nodes)
    assert len(
        {
            (
                edge.source_type,
                edge.source_id,
                edge.target_type,
                edge.target_id,
                edge.relationship_type,
            )
            for edge in graph.edges
        }
    ) == len(graph.edges)
    assert all(edge.verification_status != "requires-review" for edge in graph.edges)

    review_graph = service.traverse(
        "pharmaceutical_problem",
        "problem-e",
        max_depth=6,
        include_requires_review=True,
    )
    assert ("technology_solution", "solution-review-e") in {
        (node.entity_type, node.canonical_id) for node in review_graph.nodes
    }
    assert service.traverse("pharmaceutical_problem", "problem-e", max_depth=500).max_depth == 6
    assert len(service.traverse("pharmaceutical_problem", "problem-e", max_nodes=3).nodes) == 3


def test_read_layer_is_internal_only_and_preserves_existing_services(tmp_path):
    conn = db.connect(tmp_path / "pr-e-compatibility.sqlite")
    _seed_graph(conn)
    service = CanonicalIntelligenceService(conn)
    with patch(
        "pharmadrone.connectors.tavily_search.search",
        side_effect=AssertionError("external search must not be called"),
    ):
        assert service.search("Graph Product Alias").results
        assert service.traverse("pharmaceutical_problem", "problem-e")

    existing = commercial_intelligence.profile(conn, "legacy-event-e")
    assert existing["commercial_event_id"] == "legacy-event-e"
    assert conn.execute(
        "SELECT MAX(version) AS version FROM schema_migrations"
    ).fetchone()["version"] == 20
    source = Path("pharmadrone/intelligence/repository.py").read_text()
    assert "SELECT *" not in source
    assert "tavily" not in source.casefold()
