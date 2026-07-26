"""Application-facing canonical intelligence query service."""
from __future__ import annotations

from .repository import CanonicalIntelligenceRepository


class CanonicalIntelligenceService:
    """Small read-only facade around canonical repository queries."""

    def __init__(self, conn):
        self.repository = CanonicalIntelligenceRepository(conn)

    def problem_profile(self, problem_id: str, **options):
        return self.repository.problem_profile(problem_id, **options)

    def solution_profile(self, technology_id: str, **options):
        return self.repository.solution_profile(technology_id, **options)

    def product_profile(self, product_id: str, **options):
        return self.repository.product_profile(product_id, **options)

    def api_profile(self, api_id: str, **options):
        return self.repository.api_profile(api_id, **options)

    def organisation_profile(self, organisation_id: str, **options):
        return self.repository.organisation_profile(organisation_id, **options)

    def opportunity_profile(self, opportunity_id: str, **options):
        return self.repository.opportunity_profile(opportunity_id, **options)

    def search(self, query: str, **options):
        return self.repository.search(query, **options)

    def traverse(self, entity_type: str, canonical_id: str, **options):
        return self.repository.traverse(entity_type, canonical_id, **options)
