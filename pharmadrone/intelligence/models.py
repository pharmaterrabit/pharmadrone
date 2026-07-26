"""Typed read models for the canonical intelligence graph."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class CanonicalEntity:
    entity_type: str
    canonical_id: str
    display_name: str
    verification_status: str
    evidence_status: str
    attributes: Mapping[str, Any]


@dataclass(frozen=True)
class EvidenceReference:
    evidence_id: str
    source_table: str
    source_record_id: str
    evidence_url: str
    evidence_status: str
    evidence_basis: str
    verification_status: str


@dataclass(frozen=True)
class RelationshipReference:
    relationship_id: str
    relationship_type: str
    target_type: str
    target_id: str
    target_name: str
    verification_status: str
    evidence_status: str
    evidence_url: str


@dataclass(frozen=True)
class SearchResult:
    entity_type: str
    canonical_id: str
    display_name: str
    match_kind: str
    verification_status: str
    evidence_status: str
    ambiguous: bool = False


@dataclass(frozen=True)
class SearchPage:
    query: str
    page: int
    page_size: int
    results: tuple[SearchResult, ...]
    has_more: bool
    ambiguous: bool


@dataclass(frozen=True)
class PharmaceuticalProblemProfile:
    problem: CanonicalEntity
    taxonomy: Mapping[str, Any]
    linked_solutions: tuple[RelationshipReference, ...]
    related_opportunities: tuple[RelationshipReference, ...]
    supporting_evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class TechnologySolutionProfile:
    solution: CanonicalEntity
    taxonomy: Mapping[str, Any]
    solution_type: Mapping[str, Any]
    linked_problems: tuple[RelationshipReference, ...]
    linked_organisations: tuple[RelationshipReference, ...]
    related_opportunities: tuple[RelationshipReference, ...]
    supporting_evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class ProductApiProfile:
    entity: CanonicalEntity
    aliases: tuple[Mapping[str, Any], ...]
    identifiers: tuple[Mapping[str, Any], ...]
    linked_pharmaceutical_entities: tuple[RelationshipReference, ...]
    linked_organisations: tuple[RelationshipReference, ...]
    related_opportunities: tuple[RelationshipReference, ...]
    supporting_evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class OrganisationProviderProfile:
    organisation: CanonicalEntity
    aliases: tuple[Mapping[str, Any], ...]
    identifiers: tuple[Mapping[str, Any], ...]
    capabilities: tuple[RelationshipReference, ...]
    linked_solutions: tuple[RelationshipReference, ...]
    linked_products: tuple[RelationshipReference, ...]
    linked_apis: tuple[RelationshipReference, ...]
    related_opportunities: tuple[RelationshipReference, ...]
    related_commercial_events: tuple[RelationshipReference, ...]
    supporting_evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class OpportunityIntelligenceProfile:
    opportunity: CanonicalEntity
    participants: tuple[RelationshipReference, ...]
    linked_problems: tuple[RelationshipReference, ...]
    linked_solutions: tuple[RelationshipReference, ...]
    linked_products: tuple[RelationshipReference, ...]
    linked_apis: tuple[RelationshipReference, ...]
    related_commercial_events: tuple[RelationshipReference, ...]
    supporting_evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class GraphNode:
    entity_type: str
    canonical_id: str
    display_name: str
    verification_status: str


@dataclass(frozen=True)
class GraphEdge:
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    relationship_type: str
    verification_status: str
    evidence_status: str
    evidence_url: str


@dataclass(frozen=True)
class GraphTraversal:
    root: GraphNode
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    max_depth: int
    truncated: bool

