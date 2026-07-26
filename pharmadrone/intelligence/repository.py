"""Bounded, backend-neutral reads over the canonical intelligence schema."""
from __future__ import annotations

from collections import deque
from dataclasses import replace
import re
from typing import Any, Iterable

from .models import (
    CanonicalEntity,
    EvidenceReference,
    GraphEdge,
    GraphNode,
    GraphTraversal,
    OrganisationProviderProfile,
    OpportunityIntelligenceProfile,
    PharmaceuticalProblemProfile,
    ProductApiProfile,
    RelationshipReference,
    SearchPage,
    SearchResult,
    TechnologySolutionProfile,
)


def _bounded_limit(value: int, *, maximum: int = 100) -> int:
    return max(1, min(int(value), maximum))


def _normalized(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _canonical_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _normalized(value)).strip("-")


class CanonicalIntelligenceRepository:
    """Read-only repository for schema-19 canonical entities and relationships."""

    def __init__(self, conn):
        self.conn = conn

    def _rows(self, sql: str, params: Iterable[Any], limit: int) -> list[dict[str, Any]]:
        bounded = _bounded_limit(limit)
        return [
            dict(row)
            for row in self.conn.execute(f"{sql} LIMIT ?", (*tuple(params), bounded)).fetchall()
        ]

    @staticmethod
    def _entity(
        row: dict[str, Any],
        *,
        entity_type: str,
        id_column: str,
        name_column: str,
        verification_column: str,
    ) -> CanonicalEntity:
        return CanonicalEntity(
            entity_type=entity_type,
            canonical_id=str(row[id_column]),
            display_name=str(row[name_column]),
            verification_status=str(row.get(verification_column) or ""),
            evidence_status=str(row.get("evidence_status") or ""),
            attributes=dict(row),
        )

    @staticmethod
    def _relationships(
        rows: Iterable[dict[str, Any]],
    ) -> tuple[tuple[RelationshipReference, ...], tuple[EvidenceReference, ...]]:
        relationships: list[RelationshipReference] = []
        evidence: list[EvidenceReference] = []
        seen_relationships: set[tuple[str, str, str]] = set()
        seen_evidence: set[tuple[str, str, str]] = set()
        for row in rows:
            relationship = RelationshipReference(
                relationship_id=str(row["relationship_id"]),
                relationship_type=str(row["relationship_type"]),
                target_type=str(row["target_type"]),
                target_id=str(row["target_id"]),
                target_name=str(row["target_name"]),
                verification_status=str(row.get("verification_status") or ""),
                evidence_status=str(row.get("evidence_status") or ""),
                evidence_url=str(row.get("evidence_url") or ""),
            )
            relationship_key = (
                relationship.relationship_type,
                relationship.target_type,
                relationship.target_id,
            )
            if relationship_key not in seen_relationships:
                seen_relationships.add(relationship_key)
                relationships.append(relationship)
            evidence_key = (
                str(row.get("source_type") or ""),
                str(row.get("source_record_id") or ""),
                str(row.get("evidence_url") or ""),
            )
            if evidence_key not in seen_evidence:
                seen_evidence.add(evidence_key)
                evidence.append(
                    EvidenceReference(
                        evidence_id=str(row["relationship_id"]),
                        source_table=str(row.get("source_type") or ""),
                        source_record_id=str(row.get("source_record_id") or ""),
                        evidence_url=str(row.get("evidence_url") or ""),
                        evidence_status=str(row.get("evidence_status") or ""),
                        evidence_basis=str(row.get("evidence_basis") or ""),
                        verification_status=str(row.get("verification_status") or ""),
                    )
                )
        return tuple(relationships), tuple(evidence)

    @staticmethod
    def _merge_evidence(*groups: Iterable[EvidenceReference]) -> tuple[EvidenceReference, ...]:
        merged: list[EvidenceReference] = []
        seen: set[tuple[str, str, str]] = set()
        for group in groups:
            for item in group:
                key = (item.source_table, item.source_record_id, item.evidence_url)
                if key not in seen:
                    seen.add(key)
                    merged.append(item)
        return tuple(merged)

    def _evidence_links(
        self,
        *,
        table: str,
        id_column: str,
        parent_column: str,
        parent_id: str,
        include_requires_review: bool,
        limit: int,
    ) -> tuple[EvidenceReference, ...]:
        review = "" if include_requires_review else " AND verification_status<>'requires-review'"
        rows = self._rows(
            f"""SELECT {id_column} AS evidence_link_id,evidence_id,source_table,
            source_record_id,evidence_url,evidence_status,evidence_basis,verification_status
            FROM {table} WHERE {parent_column}=?{review}
            ORDER BY observed_at DESC,{id_column}""",
            (parent_id,),
            limit,
        )
        return tuple(
            EvidenceReference(
                evidence_id=str(row.get("evidence_id") or row["evidence_link_id"]),
                source_table=str(row["source_table"]),
                source_record_id=str(row["source_record_id"]),
                evidence_url=str(row["evidence_url"]),
                evidence_status=str(row["evidence_status"]),
                evidence_basis=str(row["evidence_basis"]),
                verification_status=str(row["verification_status"]),
            )
            for row in rows
        )

    def problem_profile(
        self,
        problem_id: str,
        *,
        include_requires_review: bool = False,
        limit: int = 50,
    ) -> PharmaceuticalProblemProfile | None:
        row = self.conn.execute(
            """SELECT p.problem_id,p.canonical_key,p.display_name,p.definition,
            p.identity_status,p.evidence_status,p.active,p.last_verified_at,p.next_review_at,
            t.term_id,t.taxonomy_namespace,t.term_kind,t.parent_term_id,t.code,t.label,
            t.definition AS taxonomy_definition,t.version
            FROM pharmaceutical_problems p
            JOIN intelligence_taxonomy_terms t ON t.term_id=p.taxonomy_term_id
            WHERE p.problem_id=?""",
            (problem_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        review = "" if include_requires_review else " AND r.inference_status<>'requires-review'"
        solution_rows = self._rows(
            f"""SELECT r.relationship_id,r.relationship_type,
            'technology_solution' AS target_type,r.technology_id AS target_id,
            s.display_name AS target_name,r.inference_status AS verification_status,
            r.evidence_status,r.evidence_url,r.source_type,r.source_id AS source_record_id,
            r.confidence_basis AS evidence_basis
            FROM technology_problem_relationships r
            JOIN technology_solutions s ON s.technology_id=r.technology_id
            WHERE r.problem_id=? AND r.active=1{review}
            ORDER BY s.display_name,r.relationship_id""",
            (problem_id,),
            limit,
        )
        opportunity_review = (
            "" if include_requires_review else " AND r.verification_status<>'requires-review'"
        )
        opportunity_rows = self._rows(
            f"""SELECT r.opportunity_problem_relationship_id AS relationship_id,
            r.relationship_type,'opportunity' AS target_type,
            r.opportunity_profile_id AS target_id,o.title AS target_name,
            r.verification_status,r.evidence_status,r.evidence_url,r.source_type,
            r.source_record_id,r.evidence_basis
            FROM opportunity_problem_relationships r
            JOIN opportunity_profiles o
              ON o.opportunity_profile_id=r.opportunity_profile_id
            WHERE r.problem_id=? AND r.active=1{opportunity_review}
            ORDER BY o.title,r.opportunity_problem_relationship_id""",
            (problem_id,),
            limit,
        )
        solutions, solution_evidence = self._relationships(solution_rows)
        opportunities, opportunity_evidence = self._relationships(opportunity_rows)
        return PharmaceuticalProblemProfile(
            problem=self._entity(
                data,
                entity_type="pharmaceutical_problem",
                id_column="problem_id",
                name_column="display_name",
                verification_column="identity_status",
            ),
            taxonomy={
                key: data[key]
                for key in (
                    "term_id",
                    "taxonomy_namespace",
                    "term_kind",
                    "parent_term_id",
                    "code",
                    "label",
                    "taxonomy_definition",
                    "version",
                )
            },
            linked_solutions=solutions,
            related_opportunities=opportunities,
            supporting_evidence=self._merge_evidence(
                solution_evidence, opportunity_evidence
            ),
        )

    def solution_profile(
        self,
        technology_id: str,
        *,
        include_requires_review: bool = False,
        limit: int = 50,
    ) -> TechnologySolutionProfile | None:
        row = self.conn.execute(
            """SELECT s.technology_id,s.canonical_key,s.display_name,s.mechanism_summary,
            s.scope_note,s.maturity_status,s.identity_status,s.evidence_status,s.active,
            s.last_verified_at,s.next_review_at,
            d.term_id AS taxonomy_term_id,d.code AS taxonomy_code,d.label AS taxonomy_label,
            d.definition AS taxonomy_definition,d.version AS taxonomy_version,
            k.term_id AS solution_type_term_id,k.code AS solution_type_code,
            k.label AS solution_type_label,k.definition AS solution_type_definition
            FROM technology_solutions s
            JOIN intelligence_taxonomy_terms d ON d.term_id=s.taxonomy_term_id
            JOIN intelligence_taxonomy_terms k ON k.term_id=s.solution_type_term_id
            WHERE s.technology_id=?""",
            (technology_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        problem_review = (
            "" if include_requires_review else " AND r.inference_status<>'requires-review'"
        )
        problem_rows = self._rows(
            f"""SELECT r.relationship_id,r.relationship_type,
            'pharmaceutical_problem' AS target_type,r.problem_id AS target_id,
            p.display_name AS target_name,r.inference_status AS verification_status,
            r.evidence_status,r.evidence_url,r.source_type,r.source_id AS source_record_id,
            r.confidence_basis AS evidence_basis
            FROM technology_problem_relationships r
            JOIN pharmaceutical_problems p ON p.problem_id=r.problem_id
            WHERE r.technology_id=? AND r.active=1{problem_review}
            ORDER BY p.display_name,r.relationship_id""",
            (technology_id,),
            limit,
        )
        governed_review = (
            "" if include_requires_review else " AND r.verification_status<>'requires-review'"
        )
        organisation_rows = self._rows(
            f"""SELECT r.organisation_solution_relationship_id AS relationship_id,
            r.relationship_type,'organisation' AS target_type,
            r.organisation_profile_id AS target_id,o.canonical_name AS target_name,
            r.verification_status,r.evidence_status,r.evidence_url,r.source_type,
            r.source_record_id,r.evidence_basis
            FROM organisation_solution_relationships r
            JOIN organisation_profiles o
              ON o.organisation_profile_id=r.organisation_profile_id
            WHERE r.technology_id=? AND r.active=1{governed_review}
            ORDER BY o.canonical_name,r.organisation_solution_relationship_id""",
            (technology_id,),
            limit,
        )
        opportunity_rows = self._rows(
            f"""SELECT r.opportunity_solution_relationship_id AS relationship_id,
            r.relationship_type,'opportunity' AS target_type,
            r.opportunity_profile_id AS target_id,o.title AS target_name,
            r.verification_status,r.evidence_status,r.evidence_url,r.source_type,
            r.source_record_id,r.evidence_basis
            FROM opportunity_solution_relationships r
            JOIN opportunity_profiles o
              ON o.opportunity_profile_id=r.opportunity_profile_id
            WHERE r.technology_id=? AND r.active=1{governed_review}
            ORDER BY o.title,r.opportunity_solution_relationship_id""",
            (technology_id,),
            limit,
        )
        problems, problem_evidence = self._relationships(problem_rows)
        organisations, organisation_evidence = self._relationships(organisation_rows)
        opportunities, opportunity_evidence = self._relationships(opportunity_rows)
        return TechnologySolutionProfile(
            solution=self._entity(
                data,
                entity_type="technology_solution",
                id_column="technology_id",
                name_column="display_name",
                verification_column="identity_status",
            ),
            taxonomy={
                "term_id": data["taxonomy_term_id"],
                "code": data["taxonomy_code"],
                "label": data["taxonomy_label"],
                "definition": data["taxonomy_definition"],
                "version": data["taxonomy_version"],
            },
            solution_type={
                "term_id": data["solution_type_term_id"],
                "code": data["solution_type_code"],
                "label": data["solution_type_label"],
                "definition": data["solution_type_definition"],
            },
            linked_problems=problems,
            linked_organisations=organisations,
            related_opportunities=opportunities,
            supporting_evidence=self._merge_evidence(
                problem_evidence, organisation_evidence, opportunity_evidence
            ),
        )

    def _product_api_profile(
        self,
        canonical_id: str,
        *,
        entity_type: str,
        include_requires_review: bool,
        limit: int,
    ) -> ProductApiProfile | None:
        is_product = entity_type == "product"
        table = "product_profiles" if is_product else "api_profiles"
        id_column = "product_id" if is_product else "api_id"
        type_column = "product_type" if is_product else "substance_type"
        row = self.conn.execute(
            f"""SELECT {id_column},canonical_name,normalized_name,{type_column},
            identity_status,evidence_status,active,first_seen_at,last_verified_at,
            next_review_at,attributes_json FROM {table} WHERE {id_column}=?""",
            (canonical_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        target_column = id_column
        review = "" if include_requires_review else " AND verification_status<>'requires-review'"
        aliases = self._rows(
            f"""SELECT entity_alias_id,alias_name,normalized_alias,alias_type,language_code,
            source_type,source_record_id,evidence_url,evidence_status,verification_status,
            observed_at,last_verified_at FROM pharmaceutical_entity_aliases
            WHERE {target_column}=?{review}
            ORDER BY normalized_alias,entity_alias_id""",
            (canonical_id,),
            limit,
        )
        identifiers = self._rows(
            f"""SELECT entity_identifier_id,identifier_namespace,identifier_value,
            normalized_identifier,jurisdiction,source_type,source_record_id,evidence_url,
            evidence_status,verification_status,observed_at,last_verified_at
            FROM pharmaceutical_entity_identifiers WHERE {target_column}=?{review}
            ORDER BY identifier_namespace,normalized_identifier,entity_identifier_id""",
            (canonical_id,),
            limit,
        )
        relationship_review = (
            "" if include_requires_review else " AND r.verification_status<>'requires-review'"
        )
        if is_product:
            pharmaceutical_rows = self._rows(
                f"""SELECT r.product_api_relationship_id AS relationship_id,
                r.relationship_type,'api' AS target_type,r.api_id AS target_id,
                a.canonical_name AS target_name,r.verification_status,r.evidence_status,
                r.evidence_url,r.source_type,r.source_record_id,r.evidence_basis
                FROM product_api_relationships r JOIN api_profiles a ON a.api_id=r.api_id
                WHERE r.product_id=? AND r.active=1{relationship_review}
                ORDER BY a.canonical_name,r.product_api_relationship_id""",
                (canonical_id,),
                limit,
            )
            organisation_table = "organisation_product_relationships"
            organisation_id_column = "organisation_product_relationship_id"
            opportunity_table = "opportunity_product_relationships"
            opportunity_id_column = "opportunity_product_relationship_id"
            evidence_parent = "product_id"
        else:
            pharmaceutical_rows = self._rows(
                f"""SELECT r.product_api_relationship_id AS relationship_id,
                r.relationship_type,'product' AS target_type,r.product_id AS target_id,
                p.canonical_name AS target_name,r.verification_status,r.evidence_status,
                r.evidence_url,r.source_type,r.source_record_id,r.evidence_basis
                FROM product_api_relationships r
                JOIN product_profiles p ON p.product_id=r.product_id
                WHERE r.api_id=? AND r.active=1{relationship_review}
                ORDER BY p.canonical_name,r.product_api_relationship_id""",
                (canonical_id,),
                limit,
            )
            organisation_table = "organisation_api_relationships"
            organisation_id_column = "organisation_api_relationship_id"
            opportunity_table = "opportunity_api_relationships"
            opportunity_id_column = "opportunity_api_relationship_id"
            evidence_parent = "api_id"
        organisation_rows = self._rows(
            f"""SELECT r.{organisation_id_column} AS relationship_id,
            r.relationship_type,'organisation' AS target_type,
            r.organisation_profile_id AS target_id,o.canonical_name AS target_name,
            r.verification_status,r.evidence_status,r.evidence_url,r.source_type,
            r.source_record_id,r.evidence_basis
            FROM {organisation_table} r
            JOIN organisation_profiles o
              ON o.organisation_profile_id=r.organisation_profile_id
            WHERE r.{id_column}=? AND r.active=1{relationship_review}
            ORDER BY o.canonical_name,r.{organisation_id_column}""",
            (canonical_id,),
            limit,
        )
        opportunity_rows = self._rows(
            f"""SELECT r.{opportunity_id_column} AS relationship_id,
            r.relationship_type,'opportunity' AS target_type,
            r.opportunity_profile_id AS target_id,o.title AS target_name,
            r.verification_status,r.evidence_status,r.evidence_url,r.source_type,
            r.source_record_id,r.evidence_basis
            FROM {opportunity_table} r
            JOIN opportunity_profiles o
              ON o.opportunity_profile_id=r.opportunity_profile_id
            WHERE r.{id_column}=? AND r.active=1{relationship_review}
            ORDER BY o.title,r.{opportunity_id_column}""",
            (canonical_id,),
            limit,
        )
        pharmaceutical, pharmaceutical_evidence = self._relationships(pharmaceutical_rows)
        organisations, organisation_evidence = self._relationships(organisation_rows)
        opportunities, opportunity_evidence = self._relationships(opportunity_rows)
        direct_evidence = self._evidence_links(
            table="pharmaceutical_evidence_links",
            id_column="pharmaceutical_evidence_link_id",
            parent_column=evidence_parent,
            parent_id=canonical_id,
            include_requires_review=include_requires_review,
            limit=limit,
        )
        return ProductApiProfile(
            entity=self._entity(
                data,
                entity_type=entity_type,
                id_column=id_column,
                name_column="canonical_name",
                verification_column="identity_status",
            ),
            aliases=tuple(aliases),
            identifiers=tuple(identifiers),
            linked_pharmaceutical_entities=pharmaceutical,
            linked_organisations=organisations,
            related_opportunities=opportunities,
            supporting_evidence=self._merge_evidence(
                pharmaceutical_evidence,
                organisation_evidence,
                opportunity_evidence,
                direct_evidence,
            ),
        )

    def product_profile(
        self,
        product_id: str,
        *,
        include_requires_review: bool = False,
        limit: int = 50,
    ) -> ProductApiProfile | None:
        return self._product_api_profile(
            product_id,
            entity_type="product",
            include_requires_review=include_requires_review,
            limit=limit,
        )

    def api_profile(
        self,
        api_id: str,
        *,
        include_requires_review: bool = False,
        limit: int = 50,
    ) -> ProductApiProfile | None:
        return self._product_api_profile(
            api_id,
            entity_type="api",
            include_requires_review=include_requires_review,
            limit=limit,
        )

    def organisation_profile(
        self,
        organisation_id: str,
        *,
        include_requires_review: bool = False,
        limit: int = 50,
    ) -> OrganisationProviderProfile | None:
        row = self.conn.execute(
            """SELECT organisation_profile_id,canonical_name,normalized_name,
            organisation_type,country_code,official_website_url,identity_status,
            evidence_status,active,first_seen_at,last_verified_at,next_review_at,
            attributes_json FROM organisation_profiles WHERE organisation_profile_id=?""",
            (organisation_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        review = "" if include_requires_review else " AND verification_status<>'requires-review'"
        aliases = self._rows(
            f"""SELECT organisation_alias_id,alias_name,normalized_alias,alias_type,
            source_type,source_record_id,evidence_url,evidence_status,verification_status,
            observed_at,last_verified_at FROM organisation_aliases
            WHERE organisation_profile_id=?{review}
            ORDER BY normalized_alias,organisation_alias_id""",
            (organisation_id,),
            limit,
        )
        relationship_review = (
            "" if include_requires_review else " AND r.verification_status<>'requires-review'"
        )
        participant_review = (
            "" if include_requires_review else " AND p.verification_status<>'requires-review'"
        )
        identifiers = self._rows(
            f"""SELECT organisation_identifier_id,identifier_namespace,identifier_value,
            normalized_identifier,jurisdiction,source_type,source_record_id,evidence_url,
            evidence_status,verification_status,observed_at,last_verified_at
            FROM organisation_identifiers WHERE organisation_profile_id=?{review}
            ORDER BY identifier_namespace,normalized_identifier,organisation_identifier_id""",
            (organisation_id,),
            limit,
        )
        relationship_specs = (
            (
                "organisation_capability_relationships",
                "organisation_capability_relationship_id",
                "capability_profile_id",
                "capability_profiles",
                "capability_profile_id",
                "canonical_name",
                "capability",
            ),
            (
                "organisation_solution_relationships",
                "organisation_solution_relationship_id",
                "technology_id",
                "technology_solutions",
                "technology_id",
                "display_name",
                "technology_solution",
            ),
            (
                "organisation_product_relationships",
                "organisation_product_relationship_id",
                "product_id",
                "product_profiles",
                "product_id",
                "canonical_name",
                "product",
            ),
            (
                "organisation_api_relationships",
                "organisation_api_relationship_id",
                "api_id",
                "api_profiles",
                "api_id",
                "canonical_name",
                "api",
            ),
        )
        relationship_groups: list[tuple[tuple[RelationshipReference, ...], tuple[EvidenceReference, ...]]] = []
        for rel_table, rel_id, target_column, target_table, target_id, name, target_type in relationship_specs:
            rows = self._rows(
                f"""SELECT r.{rel_id} AS relationship_id,r.relationship_type,
                '{target_type}' AS target_type,r.{target_column} AS target_id,
                t.{name} AS target_name,r.verification_status,r.evidence_status,
                r.evidence_url,r.source_type,r.source_record_id,r.evidence_basis
                FROM {rel_table} r JOIN {target_table} t
                  ON t.{target_id}=r.{target_column}
                WHERE r.organisation_profile_id=? AND r.active=1{relationship_review}
                ORDER BY t.{name},r.{rel_id}""",
                (organisation_id,),
                limit,
            )
            relationship_groups.append(self._relationships(rows))
        opportunity_rows = self._rows(
            f"""SELECT p.opportunity_participant_id AS relationship_id,
            p.participant_role AS relationship_type,'opportunity' AS target_type,
            p.opportunity_profile_id AS target_id,o.title AS target_name,
            p.verification_status,p.evidence_status,p.evidence_url,p.source_type,
            p.source_record_id,p.evidence_basis
            FROM opportunity_participants p JOIN opportunity_profiles o
              ON o.opportunity_profile_id=p.opportunity_profile_id
            WHERE p.organisation_profile_id=? AND p.active=1{participant_review}
            ORDER BY o.title,p.opportunity_participant_id""",
            (organisation_id,),
            limit,
        )
        event_rows = self._rows(
            f"""SELECT p.commercial_event_participant_id AS relationship_id,
            p.participant_role AS relationship_type,'commercial_event' AS target_type,
            p.commercial_event_identity_id AS target_id,
            COALESCE(c.subject_name,c.party_a_name,f.programme_name,f.award_id,
                     e.canonical_event_key) AS target_name,
            p.verification_status,p.evidence_status,p.evidence_url,p.source_type,
            p.source_record_id,p.evidence_basis
            FROM commercial_event_participants p
            JOIN commercial_event_identity_links e
              ON e.commercial_event_identity_id=p.commercial_event_identity_id
            LEFT JOIN commercial_events c ON c.commercial_event_id=e.commercial_event_id
            LEFT JOIN funding_awards f ON f.funding_award_id=e.funding_award_id
            WHERE p.organisation_profile_id=? AND p.active=1{participant_review}
            ORDER BY target_name,p.commercial_event_participant_id""",
            (organisation_id,),
            limit,
        )
        opportunities, opportunity_evidence = self._relationships(opportunity_rows)
        events, event_evidence = self._relationships(event_rows)
        direct_evidence = self._evidence_links(
            table="organisation_evidence_links",
            id_column="organisation_evidence_link_id",
            parent_column="organisation_profile_id",
            parent_id=organisation_id,
            include_requires_review=include_requires_review,
            limit=limit,
        )
        return OrganisationProviderProfile(
            organisation=self._entity(
                data,
                entity_type="organisation",
                id_column="organisation_profile_id",
                name_column="canonical_name",
                verification_column="identity_status",
            ),
            aliases=tuple(aliases),
            identifiers=tuple(identifiers),
            capabilities=relationship_groups[0][0],
            linked_solutions=relationship_groups[1][0],
            linked_products=relationship_groups[2][0],
            linked_apis=relationship_groups[3][0],
            related_opportunities=opportunities,
            related_commercial_events=events,
            supporting_evidence=self._merge_evidence(
                *(group[1] for group in relationship_groups),
                opportunity_evidence,
                event_evidence,
                direct_evidence,
            ),
        )

    def opportunity_profile(
        self,
        opportunity_id: str,
        *,
        include_requires_review: bool = False,
        limit: int = 50,
    ) -> OpportunityIntelligenceProfile | None:
        row = self.conn.execute(
            """SELECT opportunity_profile_id,canonical_key,title,opportunity_type,summary,
            lifecycle_status,source_type,source_record_id,evidence_url,evidence_status,
            evidence_basis,verification_status,inference_status,observed_at,verified_at,
            next_review_at,active,attributes_json
            FROM opportunity_profiles WHERE opportunity_profile_id=?""",
            (opportunity_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        review = "" if include_requires_review else " AND r.verification_status<>'requires-review'"
        specs = (
            (
                "opportunity_participants",
                "opportunity_participant_id",
                "participant_role",
                "organisation_profile_id",
                "organisation_profiles",
                "organisation_profile_id",
                "canonical_name",
                "organisation",
            ),
            (
                "opportunity_problem_relationships",
                "opportunity_problem_relationship_id",
                "relationship_type",
                "problem_id",
                "pharmaceutical_problems",
                "problem_id",
                "display_name",
                "pharmaceutical_problem",
            ),
            (
                "opportunity_solution_relationships",
                "opportunity_solution_relationship_id",
                "relationship_type",
                "technology_id",
                "technology_solutions",
                "technology_id",
                "display_name",
                "technology_solution",
            ),
            (
                "opportunity_product_relationships",
                "opportunity_product_relationship_id",
                "relationship_type",
                "product_id",
                "product_profiles",
                "product_id",
                "canonical_name",
                "product",
            ),
            (
                "opportunity_api_relationships",
                "opportunity_api_relationship_id",
                "relationship_type",
                "api_id",
                "api_profiles",
                "api_id",
                "canonical_name",
                "api",
            ),
        )
        groups: list[tuple[tuple[RelationshipReference, ...], tuple[EvidenceReference, ...]]] = []
        for rel_table, rel_id, rel_type, target_column, target_table, target_id, name, target_type in specs:
            rows = self._rows(
                f"""SELECT r.{rel_id} AS relationship_id,r.{rel_type} AS relationship_type,
                '{target_type}' AS target_type,r.{target_column} AS target_id,
                t.{name} AS target_name,r.verification_status,r.evidence_status,
                r.evidence_url,r.source_type,r.source_record_id,r.evidence_basis
                FROM {rel_table} r JOIN {target_table} t
                  ON t.{target_id}=r.{target_column}
                WHERE r.opportunity_profile_id=? AND r.active=1{review}
                ORDER BY t.{name},r.{rel_id}""",
                (opportunity_id,),
                limit,
            )
            groups.append(self._relationships(rows))
        event_rows = self._rows(
            f"""SELECT r.opportunity_commercial_event_relationship_id AS relationship_id,
            r.relationship_type,'commercial_event' AS target_type,
            r.commercial_event_identity_id AS target_id,
            COALESCE(c.subject_name,c.party_a_name,f.programme_name,f.award_id,
                     e.canonical_event_key) AS target_name,
            r.verification_status,r.evidence_status,r.evidence_url,r.source_type,
            r.source_record_id,r.evidence_basis
            FROM opportunity_commercial_event_relationships r
            JOIN commercial_event_identity_links e
              ON e.commercial_event_identity_id=r.commercial_event_identity_id
            LEFT JOIN commercial_events c ON c.commercial_event_id=e.commercial_event_id
            LEFT JOIN funding_awards f ON f.funding_award_id=e.funding_award_id
            WHERE r.opportunity_profile_id=? AND r.active=1{review}
            ORDER BY target_name,r.opportunity_commercial_event_relationship_id""",
            (opportunity_id,),
            limit,
        )
        events, event_evidence = self._relationships(event_rows)
        direct_evidence = self._evidence_links(
            table="opportunity_evidence_links",
            id_column="opportunity_evidence_link_id",
            parent_column="opportunity_profile_id",
            parent_id=opportunity_id,
            include_requires_review=include_requires_review,
            limit=limit,
        )
        return OpportunityIntelligenceProfile(
            opportunity=self._entity(
                data,
                entity_type="opportunity",
                id_column="opportunity_profile_id",
                name_column="title",
                verification_column="verification_status",
            ),
            participants=groups[0][0],
            linked_problems=groups[1][0],
            linked_solutions=groups[2][0],
            linked_products=groups[3][0],
            linked_apis=groups[4][0],
            related_commercial_events=events,
            supporting_evidence=self._merge_evidence(
                *(group[1] for group in groups), event_evidence, direct_evidence
            ),
        )

    def search(
        self,
        query: str,
        *,
        page: int = 1,
        page_size: int = 25,
        include_requires_review: bool = False,
    ) -> SearchPage:
        raw = " ".join(str(query or "").split())
        normalized = _normalized(raw)
        if not normalized:
            return SearchPage(raw, 1, _bounded_limit(page_size, maximum=50), (), False, False)
        bounded_page = max(1, min(int(page), 20))
        bounded_size = _bounded_limit(page_size, maximum=50)
        offset = (bounded_page - 1) * bounded_size
        fetch_limit = offset + bounded_size + 1
        key = _canonical_key(raw)
        candidates: list[dict[str, Any]] = []

        canonical_queries = (
            (
                """SELECT 'pharmaceutical_problem' AS entity_type,problem_id AS canonical_id,
                display_name,identity_status AS verification_status,evidence_status,
                CASE WHEN display_name=? THEN 0 ELSE 1 END AS priority
                FROM pharmaceutical_problems
                WHERE active=1 AND (display_name=? OR canonical_key=?)""",
                (raw, raw, key),
                "identity_status",
            ),
            (
                """SELECT 'technology_solution' AS entity_type,technology_id AS canonical_id,
                display_name,identity_status AS verification_status,evidence_status,
                CASE WHEN display_name=? THEN 0 ELSE 1 END AS priority
                FROM technology_solutions
                WHERE active=1 AND (display_name=? OR canonical_key=?)""",
                (raw, raw, key),
                "identity_status",
            ),
            (
                """SELECT 'product' AS entity_type,product_id AS canonical_id,
                canonical_name AS display_name,identity_status AS verification_status,
                evidence_status,CASE WHEN canonical_name=? THEN 0 ELSE 1 END AS priority
                FROM product_profiles
                WHERE active=1 AND (canonical_name=? OR normalized_name=?)""",
                (raw, raw, normalized),
                "identity_status",
            ),
            (
                """SELECT 'api' AS entity_type,api_id AS canonical_id,
                canonical_name AS display_name,identity_status AS verification_status,
                evidence_status,CASE WHEN canonical_name=? THEN 0 ELSE 1 END AS priority
                FROM api_profiles
                WHERE active=1 AND (canonical_name=? OR normalized_name=?)""",
                (raw, raw, normalized),
                "identity_status",
            ),
            (
                """SELECT 'organisation' AS entity_type,organisation_profile_id AS canonical_id,
                canonical_name AS display_name,identity_status AS verification_status,
                evidence_status,CASE WHEN canonical_name=? THEN 0 ELSE 1 END AS priority
                FROM organisation_profiles
                WHERE active=1 AND (canonical_name=? OR normalized_name=?)""",
                (raw, raw, normalized),
                "identity_status",
            ),
            (
                """SELECT 'opportunity' AS entity_type,opportunity_profile_id AS canonical_id,
                title AS display_name,verification_status,evidence_status,
                CASE WHEN title=? THEN 0 ELSE 1 END AS priority
                FROM opportunity_profiles
                WHERE active=1 AND (title=? OR canonical_key=?)""",
                (raw, raw, key),
                "verification_status",
            ),
        )
        for sql, params, status_column in canonical_queries:
            review = "" if include_requires_review else f" AND {status_column}<>'requires-review'"
            candidates.extend(self._rows(f"{sql}{review} ORDER BY priority,display_name", params, fetch_limit))

        alias_queries = (
            (
                """SELECT CASE WHEN a.product_id IS NOT NULL THEN 'product' ELSE 'api' END
                AS entity_type,COALESCE(a.product_id,a.api_id) AS canonical_id,
                COALESCE(p.canonical_name,i.canonical_name) AS display_name,
                a.verification_status,a.evidence_status,2 AS priority
                FROM pharmaceutical_entity_aliases a
                LEFT JOIN product_profiles p ON p.product_id=a.product_id
                LEFT JOIN api_profiles i ON i.api_id=a.api_id
                WHERE a.normalized_alias=?""",
                (normalized,),
                "a.verification_status",
            ),
            (
                """SELECT 'organisation' AS entity_type,a.organisation_profile_id AS canonical_id,
                o.canonical_name AS display_name,a.verification_status,a.evidence_status,
                2 AS priority FROM organisation_aliases a
                JOIN organisation_profiles o
                  ON o.organisation_profile_id=a.organisation_profile_id
                WHERE a.normalized_alias=?""",
                (normalized,),
                "a.verification_status",
            ),
        )
        identifier_queries = (
            (
                """SELECT CASE WHEN i.product_id IS NOT NULL THEN 'product' ELSE 'api' END
                AS entity_type,COALESCE(i.product_id,i.api_id) AS canonical_id,
                COALESCE(p.canonical_name,a.canonical_name) AS display_name,
                i.verification_status,i.evidence_status,3 AS priority
                FROM pharmaceutical_entity_identifiers i
                LEFT JOIN product_profiles p ON p.product_id=i.product_id
                LEFT JOIN api_profiles a ON a.api_id=i.api_id
                WHERE (i.normalized_identifier=? OR i.identifier_value=?)""",
                (normalized, raw),
                "i.verification_status",
            ),
            (
                """SELECT 'organisation' AS entity_type,i.organisation_profile_id AS canonical_id,
                o.canonical_name AS display_name,i.verification_status,i.evidence_status,
                3 AS priority FROM organisation_identifiers i
                JOIN organisation_profiles o
                  ON o.organisation_profile_id=i.organisation_profile_id
                WHERE (i.normalized_identifier=? OR i.identifier_value=?)""",
                (normalized, raw),
                "i.verification_status",
            ),
            (
                """SELECT 'opportunity' AS entity_type,i.opportunity_profile_id AS canonical_id,
                o.title AS display_name,i.verification_status,i.evidence_status,3 AS priority
                FROM opportunity_identifiers i JOIN opportunity_profiles o
                  ON o.opportunity_profile_id=i.opportunity_profile_id
                WHERE (i.normalized_identifier=? OR i.identifier_value=?
                   OR i.stable_lead_id=? OR i.legacy_opportunity_id=?)""",
                (normalized, raw, raw, raw),
                "i.verification_status",
            ),
        )
        for sql, params, status_expression in (*alias_queries, *identifier_queries):
            review = (
                ""
                if include_requires_review
                else f" AND {status_expression}<>'requires-review'"
            )
            candidates.extend(
                self._rows(f"{sql}{review} ORDER BY priority,display_name", params, fetch_limit)
            )

        deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in candidates:
            identity = (str(candidate["entity_type"]), str(candidate["canonical_id"]))
            existing = deduplicated.get(identity)
            if existing is None or int(candidate["priority"]) < int(existing["priority"]):
                deduplicated[identity] = candidate
        ordered = sorted(
            deduplicated.values(),
            key=lambda row: (
                int(row["priority"]),
                str(row["display_name"]).casefold(),
                str(row["entity_type"]),
                str(row["canonical_id"]),
            ),
        )
        selected = ordered[offset : offset + bounded_size]
        ambiguous = len(ordered) > 1
        results = tuple(
            replace(
                SearchResult(
                    entity_type=str(row["entity_type"]),
                    canonical_id=str(row["canonical_id"]),
                    display_name=str(row["display_name"]),
                    match_kind={0: "canonical-name", 1: "normalized-name", 2: "alias", 3: "identifier"}[
                        int(row["priority"])
                    ],
                    verification_status=str(row["verification_status"]),
                    evidence_status=str(row["evidence_status"]),
                ),
                ambiguous=ambiguous,
            )
            for row in selected
        )
        return SearchPage(
            query=raw,
            page=bounded_page,
            page_size=bounded_size,
            results=results,
            has_more=len(ordered) > offset + bounded_size,
            ambiguous=ambiguous,
        )

    def _root_node(self, entity_type: str, canonical_id: str) -> GraphNode | None:
        specs = {
            "pharmaceutical_problem": (
                "pharmaceutical_problems",
                "problem_id",
                "display_name",
                "identity_status",
            ),
            "technology_solution": (
                "technology_solutions",
                "technology_id",
                "display_name",
                "identity_status",
            ),
            "organisation": (
                "organisation_profiles",
                "organisation_profile_id",
                "canonical_name",
                "identity_status",
            ),
            "product": ("product_profiles", "product_id", "canonical_name", "identity_status"),
            "api": ("api_profiles", "api_id", "canonical_name", "identity_status"),
            "opportunity": (
                "opportunity_profiles",
                "opportunity_profile_id",
                "title",
                "verification_status",
            ),
            "commercial_event": (
                "commercial_event_identity_links",
                "commercial_event_identity_id",
                "canonical_event_key",
                "verification_status",
            ),
        }
        spec = specs.get(entity_type)
        if not spec:
            return None
        table, id_column, name_column, verification_column = spec
        row = self.conn.execute(
            f"""SELECT {id_column} AS canonical_id,{name_column} AS display_name,
            {verification_column} AS verification_status FROM {table}
            WHERE {id_column}=?""",
            (canonical_id,),
        ).fetchone()
        if not row:
            return None
        return GraphNode(
            entity_type=entity_type,
            canonical_id=str(row["canonical_id"]),
            display_name=str(row["display_name"]),
            verification_status=str(row["verification_status"]),
        )

    def _graph_neighbors(
        self,
        node: GraphNode,
        *,
        include_requires_review: bool,
        limit: int,
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        queries: list[tuple[str, tuple[Any, ...]]] = []
        if node.entity_type == "pharmaceutical_problem":
            review = "" if include_requires_review else " AND r.inference_status<>'requires-review'"
            queries.append(
                (
                    f"""SELECT 'technology_solution' AS target_type,
                    r.technology_id AS target_id,s.display_name AS target_name,
                    s.identity_status AS target_verification,r.relationship_type,
                    r.inference_status AS verification_status,r.evidence_status,r.evidence_url
                    FROM technology_problem_relationships r
                    JOIN technology_solutions s ON s.technology_id=r.technology_id
                    WHERE r.problem_id=? AND r.active=1{review}
                    ORDER BY s.display_name,r.relationship_id""",
                    (node.canonical_id,),
                )
            )
        elif node.entity_type == "technology_solution":
            review = "" if include_requires_review else " AND r.verification_status<>'requires-review'"
            queries.append(
                (
                    f"""SELECT 'organisation' AS target_type,
                    r.organisation_profile_id AS target_id,o.canonical_name AS target_name,
                    o.identity_status AS target_verification,r.relationship_type,
                    r.verification_status,r.evidence_status,r.evidence_url
                    FROM organisation_solution_relationships r
                    JOIN organisation_profiles o
                      ON o.organisation_profile_id=r.organisation_profile_id
                    WHERE r.technology_id=? AND r.active=1{review}
                    ORDER BY o.canonical_name,r.organisation_solution_relationship_id""",
                    (node.canonical_id,),
                )
            )
        elif node.entity_type == "organisation":
            review = "" if include_requires_review else " AND r.verification_status<>'requires-review'"
            queries.extend(
                (
                    (
                        f"""SELECT 'product' AS target_type,r.product_id AS target_id,
                        p.canonical_name AS target_name,p.identity_status AS target_verification,
                        r.relationship_type,r.verification_status,r.evidence_status,r.evidence_url
                        FROM organisation_product_relationships r
                        JOIN product_profiles p ON p.product_id=r.product_id
                        WHERE r.organisation_profile_id=? AND r.active=1{review}
                        ORDER BY p.canonical_name,r.organisation_product_relationship_id""",
                        (node.canonical_id,),
                    ),
                    (
                        f"""SELECT 'api' AS target_type,r.api_id AS target_id,
                        a.canonical_name AS target_name,a.identity_status AS target_verification,
                        r.relationship_type,r.verification_status,r.evidence_status,r.evidence_url
                        FROM organisation_api_relationships r
                        JOIN api_profiles a ON a.api_id=r.api_id
                        WHERE r.organisation_profile_id=? AND r.active=1{review}
                        ORDER BY a.canonical_name,r.organisation_api_relationship_id""",
                        (node.canonical_id,),
                    ),
                )
            )
        elif node.entity_type in {"product", "api"}:
            review = "" if include_requires_review else " AND r.verification_status<>'requires-review'"
            if node.entity_type == "product":
                table = "opportunity_product_relationships"
                target_column = "product_id"
                rel_id = "opportunity_product_relationship_id"
            else:
                table = "opportunity_api_relationships"
                target_column = "api_id"
                rel_id = "opportunity_api_relationship_id"
            queries.append(
                (
                    f"""SELECT 'opportunity' AS target_type,
                    r.opportunity_profile_id AS target_id,o.title AS target_name,
                    o.verification_status AS target_verification,r.relationship_type,
                    r.verification_status,r.evidence_status,r.evidence_url
                    FROM {table} r JOIN opportunity_profiles o
                      ON o.opportunity_profile_id=r.opportunity_profile_id
                    WHERE r.{target_column}=? AND r.active=1{review}
                    ORDER BY o.title,r.{rel_id}""",
                    (node.canonical_id,),
                )
            )
        elif node.entity_type == "opportunity":
            review = "" if include_requires_review else " AND r.verification_status<>'requires-review'"
            queries.append(
                (
                    f"""SELECT 'commercial_event' AS target_type,
                    r.commercial_event_identity_id AS target_id,
                    COALESCE(c.subject_name,c.party_a_name,f.programme_name,f.award_id,
                             e.canonical_event_key) AS target_name,
                    e.verification_status AS target_verification,r.relationship_type,
                    r.verification_status,r.evidence_status,r.evidence_url
                    FROM opportunity_commercial_event_relationships r
                    JOIN commercial_event_identity_links e
                      ON e.commercial_event_identity_id=r.commercial_event_identity_id
                    LEFT JOIN commercial_events c
                      ON c.commercial_event_id=e.commercial_event_id
                    LEFT JOIN funding_awards f ON f.funding_award_id=e.funding_award_id
                    WHERE r.opportunity_profile_id=? AND r.active=1{review}
                    ORDER BY target_name,r.opportunity_commercial_event_relationship_id""",
                    (node.canonical_id,),
                )
            )
        neighbors: list[GraphNode] = []
        edges: list[GraphEdge] = []
        for sql, params in queries:
            for row in self._rows(sql, params, limit):
                target = GraphNode(
                    entity_type=str(row["target_type"]),
                    canonical_id=str(row["target_id"]),
                    display_name=str(row["target_name"]),
                    verification_status=str(row["target_verification"]),
                )
                neighbors.append(target)
                edges.append(
                    GraphEdge(
                        source_type=node.entity_type,
                        source_id=node.canonical_id,
                        target_type=target.entity_type,
                        target_id=target.canonical_id,
                        relationship_type=str(row["relationship_type"]),
                        verification_status=str(row["verification_status"]),
                        evidence_status=str(row["evidence_status"]),
                        evidence_url=str(row["evidence_url"]),
                    )
                )
        return neighbors, edges

    def traverse(
        self,
        entity_type: str,
        canonical_id: str,
        *,
        max_depth: int = 5,
        max_nodes: int = 100,
        include_requires_review: bool = False,
    ) -> GraphTraversal | None:
        root = self._root_node(entity_type, canonical_id)
        if root is None:
            return None
        bounded_depth = max(0, min(int(max_depth), 6))
        bounded_nodes = _bounded_limit(max_nodes, maximum=200)
        queue = deque([(root, 0)])
        nodes: dict[tuple[str, str], GraphNode] = {
            (root.entity_type, root.canonical_id): root
        }
        edges: dict[tuple[str, str, str, str, str], GraphEdge] = {}
        truncated = False
        while queue:
            current, depth = queue.popleft()
            if depth >= bounded_depth:
                truncated = True
                continue
            neighbors, outgoing = self._graph_neighbors(
                current,
                include_requires_review=include_requires_review,
                limit=min(50, bounded_nodes),
            )
            for target, edge in zip(neighbors, outgoing):
                edge_key = (
                    edge.source_type,
                    edge.source_id,
                    edge.target_type,
                    edge.target_id,
                    edge.relationship_type,
                )
                edges.setdefault(edge_key, edge)
                target_key = (target.entity_type, target.canonical_id)
                if target_key in nodes:
                    continue
                if len(nodes) >= bounded_nodes:
                    truncated = True
                    continue
                nodes[target_key] = target
                queue.append((target, depth + 1))
        return GraphTraversal(
            root=root,
            nodes=tuple(nodes.values()),
            edges=tuple(edges.values()),
            max_depth=bounded_depth,
            truncated=truncated,
        )
