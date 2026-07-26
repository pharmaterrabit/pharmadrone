"""Deterministic, human-governed links from legacy records to canonical entities."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable, Mapping
from uuid import uuid4


MATCH_RULES = (
    "exact-normalized-name",
    "exact-governed-alias",
    "exact-governed-identifier",
    "exact-stable-lead-id",
    "exact-legacy-opportunity-id",
)
CANDIDATE_STATUSES = (
    "pending-review",
    "accepted",
    "rejected",
    "superseded",
    "requires-more-evidence",
)
ENTITY_TYPES = (
    "pharmaceutical_problem",
    "technology_solution",
    "product",
    "api",
    "organisation",
    "opportunity",
)
DEFAULT_BATCH_SIZE = 25
MAX_BATCH_SIZE = 100
MAX_PAGE_SIZE = 50


class CanonicalisationError(ValueError):
    """Raised when a canonicalisation operation violates governance rules."""


@dataclass(frozen=True)
class CandidateField:
    field: str
    entity_type: str
    rules: tuple[str, ...]


@dataclass(frozen=True)
class SourceAdapter:
    source_table: str
    query: str
    fields: tuple[CandidateField, ...]


@dataclass(frozen=True)
class CandidatePage:
    page: int
    page_size: int
    candidates: tuple[Mapping[str, Any], ...]
    has_more: bool


_SOURCE_ADAPTERS = {
    "opportunity_index": SourceAdapter(
        "opportunity_index",
        """SELECT stable_lead_id AS source_record_id,stable_lead_id,company,product,
        molecule,problem_category,source_type,source_id,evidence_links_json
        FROM opportunity_index
        WHERE COALESCE(novelty_status,'')<>'archived'
        ORDER BY stable_lead_id LIMIT ?""",
        (
            CandidateField("stable_lead_id", "opportunity", ("exact-stable-lead-id",)),
            CandidateField("company", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("product", "product", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("molecule", "api", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("problem_category", "pharmaceutical_problem", ("exact-normalized-name",)),
            CandidateField("source_id", "opportunity", ("exact-governed-identifier",)),
        ),
    ),
    "opportunities": SourceAdapter(
        "opportunities",
        """SELECT id AS source_record_id,id,company,parent_company,product,generic_name,
        brand_name,dev_code,problem_signal,data_json
        FROM opportunities ORDER BY id LIMIT ?""",
        (
            CandidateField("id", "opportunity", ("exact-legacy-opportunity-id",)),
            CandidateField("company", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("parent_company", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("product", "product", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("brand_name", "product", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("generic_name", "api", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("dev_code", "product", ("exact-governed-identifier",)),
            CandidateField("problem_signal", "pharmaceutical_problem", ("exact-normalized-name",)),
        ),
    ),
    "account_organisations": SourceAdapter(
        "account_organisations",
        """SELECT organisation_id AS source_record_id,organisation_id,canonical_name,
        official_website_url AS evidence_url,identity_status AS source_verification_status
        FROM account_organisations WHERE active=1
        ORDER BY organisation_id LIMIT ?""",
        (
            CandidateField("canonical_name", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("organisation_id", "organisation", ("exact-governed-identifier",)),
        ),
    ),
    "research_organisations": SourceAdapter(
        "research_organisations",
        """SELECT research_organisation_id AS source_record_id,research_organisation_id,
        canonical_name,ror_id,openalex_id,official_url AS evidence_url,
        identity_status AS source_verification_status
        FROM research_organisations WHERE active=1
        ORDER BY research_organisation_id LIMIT ?""",
        (
            CandidateField("canonical_name", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("ror_id", "organisation", ("exact-governed-identifier",)),
            CandidateField("openalex_id", "organisation", ("exact-governed-identifier",)),
        ),
    ),
    "research_technologies": SourceAdapter(
        "research_technologies",
        """SELECT research_technology_id AS source_record_id,research_technology_id,
        title,source_type,source_id,evidence_url,evidence_status
        FROM research_technologies WHERE active=1
        ORDER BY research_technology_id LIMIT ?""",
        (
            CandidateField("title", "technology_solution", ("exact-normalized-name",)),
            CandidateField("source_id", "technology_solution", ("exact-governed-identifier",)),
        ),
    ),
    "commercial_events": SourceAdapter(
        "commercial_events",
        """SELECT commercial_event_id AS source_record_id,commercial_event_id,
        party_a_name,party_b_name,subject_name,source_type,source_id,evidence_url,
        evidence_status,validation_status AS source_verification_status
        FROM commercial_events WHERE active=1
        ORDER BY commercial_event_id LIMIT ?""",
        (
            CandidateField("party_a_name", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("party_b_name", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("subject_name", "product", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("source_id", "opportunity", ("exact-governed-identifier",)),
        ),
    ),
    "funding_awards": SourceAdapter(
        "funding_awards",
        """SELECT funding_award_id AS source_record_id,funding_award_id,funder_name,
        recipient_name,award_id,programme_name,source_type,source_id,evidence_url,
        evidence_status,validation_status AS source_verification_status
        FROM funding_awards WHERE active=1
        ORDER BY funding_award_id LIMIT ?""",
        (
            CandidateField("funder_name", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("recipient_name", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("award_id", "opportunity", ("exact-governed-identifier",)),
            CandidateField("programme_name", "technology_solution", ("exact-normalized-name",)),
        ),
    ),
    "lifecycle_products": SourceAdapter(
        "lifecycle_products",
        """SELECT lifecycle_id AS source_record_id,lifecycle_id,trade_name,ingredient,
        application_holder,application_number,product_number,official_source_url AS evidence_url,
        evidence_status,lifecycle_status AS source_verification_status
        FROM lifecycle_products WHERE active=1
        ORDER BY lifecycle_id LIMIT ?""",
        (
            CandidateField("trade_name", "product", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("ingredient", "api", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("application_holder", "organisation", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("application_number", "product", ("exact-governed-identifier",)),
            CandidateField("product_number", "product", ("exact-governed-identifier",)),
        ),
    ),
    "patent_parties": SourceAdapter(
        "patent_parties",
        """SELECT patent_party_id AS source_record_id,patent_party_id,party_name,
        party_type,official_source_url AS evidence_url,evidence_status
        FROM patent_parties ORDER BY patent_party_id LIMIT ?""",
        (
            CandidateField("party_name", "organisation", ("exact-normalized-name", "exact-governed-alias")),
        ),
    ),
    "patent_product_links": SourceAdapter(
        "patent_product_links",
        """SELECT link.patent_product_link_id AS source_record_id,
        link.patent_product_link_id,product.trade_name,product.ingredient,
        link.official_source_url AS evidence_url,link.evidence_status,
        COALESCE(link.verification_status,'requires-review') AS source_verification_status
        FROM patent_product_links link
        JOIN lifecycle_products product ON product.lifecycle_id=link.lifecycle_id
        ORDER BY link.patent_product_link_id LIMIT ?""",
        (
            CandidateField("trade_name", "product", ("exact-normalized-name", "exact-governed-alias")),
            CandidateField("ingredient", "api", ("exact-normalized-name", "exact-governed-alias")),
        ),
    ),
}
SOURCE_TYPES = tuple(_SOURCE_ADAPTERS)


_ENTITY_SPECS = {
    "pharmaceutical_problem": (
        "pharmaceutical_problems", "problem_id", "display_name", "canonical_key",
        "identity_status", "evidence_status",
    ),
    "technology_solution": (
        "technology_solutions", "technology_id", "display_name", "canonical_key",
        "identity_status", "evidence_status",
    ),
    "product": (
        "product_profiles", "product_id", "canonical_name", "normalized_name",
        "identity_status", "evidence_status",
    ),
    "api": (
        "api_profiles", "api_id", "canonical_name", "normalized_name",
        "identity_status", "evidence_status",
    ),
    "organisation": (
        "organisation_profiles", "organisation_profile_id", "canonical_name",
        "normalized_name", "identity_status", "evidence_status",
    ),
    "opportunity": (
        "opportunity_profiles", "opportunity_profile_id", "title", "canonical_key",
        "verification_status", "evidence_status",
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _canonical_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _normalized(value)).strip("-")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _scope_key(principal: Mapping[str, Any]) -> str:
    workspace = str(principal.get("workspace_id") or "").strip()
    organisation = str(principal.get("organisation_id") or "").strip()
    if workspace:
        return f"workspace:{workspace}"
    if organisation:
        return f"organisation:{organisation}"
    return "platform"


def _require_role(principal: Mapping[str, Any], allowed: set[str]) -> None:
    if str(principal.get("role") or "") not in allowed:
        raise CanonicalisationError("This account is not permitted to perform this action.")


def _bounded_page(value: int) -> int:
    return max(1, min(int(value), 100))


def _bounded_page_size(value: int) -> int:
    return max(1, min(int(value), MAX_PAGE_SIZE))


def _first_evidence_url(row: Mapping[str, Any]) -> str:
    direct = str(
        row.get("evidence_url")
        or row.get("official_source_url")
        or row.get("official_url")
        or row.get("official_website_url")
        or ""
    ).strip()
    if direct.startswith(("https://", "http://")):
        return direct
    try:
        links = json.loads(str(row.get("evidence_links_json") or "[]"))
    except (TypeError, ValueError):
        links = []
    for item in links if isinstance(links, list) else []:
        url = item.get("url") if isinstance(item, dict) else item
        if str(url or "").startswith(("https://", "http://")):
            return str(url)
    return ""


def _stable_id(*parts: Any) -> str:
    payload = "\x1f".join(str(part or "") for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class CanonicalisationRepository:
    """Database-only persistence and exact-match queries for canonicalisation."""

    def __init__(self, conn):
        self.conn = conn

    def source_rows(self, source_table: str, limit: int) -> list[dict[str, Any]]:
        adapter = _SOURCE_ADAPTERS[source_table]
        return [dict(row) for row in self.conn.execute(adapter.query, (limit,)).fetchall()]

    def _canonical_name_matches(
        self, entity_type: str, value: str
    ) -> list[dict[str, Any]]:
        table, id_col, name_col, normalized_col, status_col, evidence_col = _ENTITY_SPECS[
            entity_type
        ]
        normalized = (
            _canonical_key(value)
            if normalized_col == "canonical_key"
            else _normalized(value)
        )
        rows = self.conn.execute(
            f"""SELECT {id_col} AS canonical_id,{name_col} AS canonical_name,
            {status_col} AS verification_status,{evidence_col} AS evidence_status
            FROM {table}
            WHERE active=1 AND {normalized_col}=?
              AND {status_col}<>'requires-review'
            ORDER BY {id_col} LIMIT 51""",
            (normalized,),
        ).fetchall()
        return [
            {
                **dict(row),
                "identifier_namespace": "",
                "identifier_value": "",
                "canonical_evidence_url": "",
                "canonical_evidence_basis": "Exact canonical identity value",
            }
            for row in rows
        ]

    def _alias_matches(self, entity_type: str, value: str) -> list[dict[str, Any]]:
        normalized = _normalized(value)
        queries = {
            "product": """SELECT alias.product_id AS canonical_id,
                profile.canonical_name,alias.verification_status,alias.evidence_status,
                alias.evidence_url AS canonical_evidence_url,
                'Governed product alias' AS canonical_evidence_basis
                FROM pharmaceutical_entity_aliases alias
                JOIN product_profiles profile ON profile.product_id=alias.product_id
                WHERE alias.product_id IS NOT NULL AND alias.normalized_alias=?
                  AND alias.verification_status<>'requires-review'
                  AND profile.identity_status<>'requires-review' AND profile.active=1
                ORDER BY alias.product_id LIMIT 51""",
            "api": """SELECT alias.api_id AS canonical_id,
                profile.canonical_name,alias.verification_status,alias.evidence_status,
                alias.evidence_url AS canonical_evidence_url,
                'Governed API alias' AS canonical_evidence_basis
                FROM pharmaceutical_entity_aliases alias
                JOIN api_profiles profile ON profile.api_id=alias.api_id
                WHERE alias.api_id IS NOT NULL AND alias.normalized_alias=?
                  AND alias.verification_status<>'requires-review'
                  AND profile.identity_status<>'requires-review' AND profile.active=1
                ORDER BY alias.api_id LIMIT 51""",
            "organisation": """SELECT alias.organisation_profile_id AS canonical_id,
                profile.canonical_name,alias.verification_status,alias.evidence_status,
                alias.evidence_url AS canonical_evidence_url,
                'Governed organisation alias' AS canonical_evidence_basis
                FROM organisation_aliases alias
                JOIN organisation_profiles profile
                  ON profile.organisation_profile_id=alias.organisation_profile_id
                WHERE alias.normalized_alias=?
                  AND alias.verification_status<>'requires-review'
                  AND profile.identity_status<>'requires-review' AND profile.active=1
                ORDER BY alias.organisation_profile_id LIMIT 51""",
            "opportunity": """SELECT identifier.opportunity_profile_id AS canonical_id,
                profile.title AS canonical_name,identifier.verification_status,
                identifier.evidence_status,
                identifier.evidence_url AS canonical_evidence_url,
                'Governed opportunity alias' AS canonical_evidence_basis
                FROM opportunity_identifiers identifier
                JOIN opportunity_profiles profile
                  ON profile.opportunity_profile_id=identifier.opportunity_profile_id
                WHERE identifier.identifier_type='alias'
                  AND identifier.normalized_identifier=?
                  AND identifier.verification_status<>'requires-review'
                  AND profile.verification_status<>'requires-review' AND profile.active=1
                ORDER BY identifier.opportunity_profile_id LIMIT 51""",
        }
        sql = queries.get(entity_type)
        if not sql:
            return []
        return [
            {
                **dict(row),
                "identifier_namespace": "",
                "identifier_value": "",
            }
            for row in self.conn.execute(sql, (normalized,)).fetchall()
        ]

    def _identifier_matches(
        self, entity_type: str, value: str
    ) -> list[dict[str, Any]]:
        normalized = _normalized(value)
        queries = {
            "product": """SELECT identifier.product_id AS canonical_id,
                profile.canonical_name,identifier.verification_status,
                identifier.evidence_status,identifier.identifier_namespace,
                identifier.identifier_value,
                identifier.evidence_url AS canonical_evidence_url,
                'Governed product external identifier' AS canonical_evidence_basis
                FROM pharmaceutical_entity_identifiers identifier
                JOIN product_profiles profile ON profile.product_id=identifier.product_id
                WHERE identifier.product_id IS NOT NULL
                  AND identifier.normalized_identifier=?
                  AND identifier.verification_status<>'requires-review'
                  AND profile.identity_status<>'requires-review' AND profile.active=1
                ORDER BY identifier.product_id LIMIT 51""",
            "api": """SELECT identifier.api_id AS canonical_id,
                profile.canonical_name,identifier.verification_status,
                identifier.evidence_status,identifier.identifier_namespace,
                identifier.identifier_value,
                identifier.evidence_url AS canonical_evidence_url,
                'Governed API external identifier' AS canonical_evidence_basis
                FROM pharmaceutical_entity_identifiers identifier
                JOIN api_profiles profile ON profile.api_id=identifier.api_id
                WHERE identifier.api_id IS NOT NULL
                  AND identifier.normalized_identifier=?
                  AND identifier.verification_status<>'requires-review'
                  AND profile.identity_status<>'requires-review' AND profile.active=1
                ORDER BY identifier.api_id LIMIT 51""",
            "organisation": """SELECT identifier.organisation_profile_id AS canonical_id,
                profile.canonical_name,identifier.verification_status,
                identifier.evidence_status,identifier.identifier_namespace,
                identifier.identifier_value,
                identifier.evidence_url AS canonical_evidence_url,
                'Governed organisation external identifier' AS canonical_evidence_basis
                FROM organisation_identifiers identifier
                JOIN organisation_profiles profile
                  ON profile.organisation_profile_id=identifier.organisation_profile_id
                WHERE identifier.normalized_identifier=?
                  AND identifier.verification_status<>'requires-review'
                  AND profile.identity_status<>'requires-review' AND profile.active=1
                ORDER BY identifier.organisation_profile_id LIMIT 51""",
            "opportunity": """SELECT identifier.opportunity_profile_id AS canonical_id,
                profile.title AS canonical_name,identifier.verification_status,
                identifier.evidence_status,identifier.identifier_namespace,
                identifier.identifier_value,
                identifier.evidence_url AS canonical_evidence_url,
                'Governed opportunity external identifier' AS canonical_evidence_basis
                FROM opportunity_identifiers identifier
                JOIN opportunity_profiles profile
                  ON profile.opportunity_profile_id=identifier.opportunity_profile_id
                WHERE identifier.identifier_type='external-id'
                  AND identifier.normalized_identifier=?
                  AND identifier.verification_status<>'requires-review'
                  AND profile.verification_status<>'requires-review' AND profile.active=1
                ORDER BY identifier.opportunity_profile_id LIMIT 51""",
        }
        sql = queries.get(entity_type)
        if not sql:
            return []
        return [dict(row) for row in self.conn.execute(sql, (normalized,)).fetchall()]

    def _opportunity_adapter_matches(
        self, value: str, column: str, basis: str
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            f"""SELECT identifier.opportunity_profile_id AS canonical_id,
            profile.title AS canonical_name,identifier.verification_status,
            identifier.evidence_status,identifier.identifier_namespace,
            identifier.identifier_value,
            identifier.evidence_url AS canonical_evidence_url
            FROM opportunity_identifiers identifier
            JOIN opportunity_profiles profile
              ON profile.opportunity_profile_id=identifier.opportunity_profile_id
            WHERE identifier.{column}=?
              AND identifier.verification_status<>'requires-review'
              AND profile.verification_status<>'requires-review' AND profile.active=1
            ORDER BY identifier.opportunity_profile_id LIMIT 51""",
            (value,),
        ).fetchall()
        return [
            {**dict(row), "canonical_evidence_basis": basis}
            for row in rows
        ]

    def matches(
        self, entity_type: str, value: str, match_rule: str
    ) -> list[dict[str, Any]]:
        if match_rule == "exact-normalized-name":
            return self._canonical_name_matches(entity_type, value)
        if match_rule == "exact-governed-alias":
            return self._alias_matches(entity_type, value)
        if match_rule == "exact-governed-identifier":
            return self._identifier_matches(entity_type, value)
        if match_rule == "exact-stable-lead-id" and entity_type == "opportunity":
            return self._opportunity_adapter_matches(
                value, "stable_lead_id", "Exact stable lead ID adapter"
            )
        if match_rule == "exact-legacy-opportunity-id" and entity_type == "opportunity":
            return self._opportunity_adapter_matches(
                value, "legacy_opportunity_id", "Exact legacy opportunity ID adapter"
            )
        return []

    def candidate(self, candidate_id: str, scope_key: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """SELECT canonicalisation_candidate_id,canonicalisation_run_id,scope_key,
            source_table,source_record_id,source_display_value,source_field,
            proposed_entity_type,proposed_canonical_id,match_rule,
            identifier_namespace,identifier_value,evidence_url,evidence_status,
            evidence_basis,verification_status,ambiguous,review_status,
            source_record_json,supporting_evidence_json,created_at,updated_at,
            superseded_by_candidate_id
            FROM canonicalisation_candidates
            WHERE canonicalisation_candidate_id=? AND scope_key=?""",
            (candidate_id, scope_key),
        ).fetchone()
        return dict(row) if row else None

    def target_summary(self, entity_type: str, canonical_id: str) -> dict[str, Any] | None:
        table, id_col, name_col, _, status_col, evidence_col = _ENTITY_SPECS[entity_type]
        row = self.conn.execute(
            f"""SELECT {id_col} AS canonical_id,{name_col} AS canonical_name,
            {status_col} AS verification_status,{evidence_col} AS evidence_status
            FROM {table} WHERE {id_col}=?""",
            (canonical_id,),
        ).fetchone()
        return dict(row) if row else None

    def decision_history(
        self, candidate_id: str, scope_key: str
    ) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.conn.execute(
                """SELECT canonicalisation_decision_id,decision_status,previous_status,
                reviewer_name,reviewer_role,reviewer_notes,decided_at,
                supersedes_decision_id
                FROM canonicalisation_decisions
                WHERE canonicalisation_candidate_id=? AND scope_key=?
                ORDER BY decided_at,canonicalisation_decision_id LIMIT 100""",
                (candidate_id, scope_key),
            ).fetchall()
        ]


class CanonicalisationService:
    """Role- and scope-safe canonicalisation workflow facade."""

    def __init__(self, conn):
        self.conn = conn
        self.repository = CanonicalisationRepository(conn)

    @staticmethod
    def source_types() -> tuple[str, ...]:
        return SOURCE_TYPES

    @staticmethod
    def match_rules() -> tuple[str, ...]:
        return MATCH_RULES

    def generate_candidates(
        self,
        principal: Mapping[str, Any],
        *,
        source_table: str,
        max_records: int = DEFAULT_BATCH_SIZE,
        permitted_rules: Iterable[str] = MATCH_RULES,
    ) -> dict[str, Any]:
        _require_role(principal, {"platform_admin"})
        if source_table not in _SOURCE_ADAPTERS:
            raise CanonicalisationError("Unsupported canonicalisation source type.")
        bounded_max = max(1, min(int(max_records), MAX_BATCH_SIZE))
        requested_rules = tuple(
            rule for rule in dict.fromkeys(permitted_rules) if rule in MATCH_RULES
        )
        if not requested_rules:
            raise CanonicalisationError("Select at least one permitted exact-match rule.")
        run_id = f"canonicalisation-run-{uuid4().hex}"
        scope_key = _scope_key(principal)
        now = _now()
        with self.conn.transaction():
            self.conn.execute(
                """INSERT INTO canonicalisation_runs
                (canonicalisation_run_id,scope_key,source_table,permitted_rules_json,
                max_records,run_status,created_by_name,created_by_role,organisation_id,
                workspace_id,started_at)
                VALUES (?,?,?,?,?,'running',?,?,?,?,?)""",
                (
                    run_id,
                    scope_key,
                    source_table,
                    _json(requested_rules),
                    bounded_max,
                    str(principal.get("display_name") or principal.get("role") or "Administrator"),
                    str(principal.get("role") or ""),
                    str(principal.get("organisation_id") or "") or None,
                    str(principal.get("workspace_id") or "") or None,
                    now,
                ),
            )
        try:
            rows = self.repository.source_rows(source_table, bounded_max)
            adapter = _SOURCE_ADAPTERS[source_table]
            created = 0
            with self.conn.transaction():
                for source_row in rows:
                    source_record_id = str(source_row["source_record_id"])
                    source_evidence_url = _first_evidence_url(source_row)
                    source_evidence_status = str(
                        source_row.get("evidence_status")
                        or source_row.get("source_verification_status")
                        or "source record retained"
                    )
                    for field in adapter.fields:
                        value = " ".join(str(source_row.get(field.field) or "").split())
                        if not value:
                            continue
                        for rule in field.rules:
                            if rule not in requested_rules:
                                continue
                            matches = self.repository.matches(field.entity_type, value, rule)
                            ambiguous = len(
                                {str(match["canonical_id"]) for match in matches}
                            ) > 1
                            for match in matches:
                                candidate_id = "candidate-" + _stable_id(
                                    scope_key,
                                    source_table,
                                    source_record_id,
                                    field.field,
                                    field.entity_type,
                                    match["canonical_id"],
                                    rule,
                                )
                                result = self.conn.execute(
                                    """INSERT INTO canonicalisation_candidates
                                    (canonicalisation_candidate_id,canonicalisation_run_id,
                                    scope_key,source_table,source_record_id,
                                    source_display_value,source_field,proposed_entity_type,
                                    proposed_canonical_id,match_rule,identifier_namespace,
                                    identifier_value,evidence_url,evidence_status,
                                    evidence_basis,verification_status,ambiguous,
                                    review_status,source_record_json,
                                    supporting_evidence_json,created_at,updated_at)
                                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                                    'pending-review',?,?,?,?)
                                    ON CONFLICT(
                                        scope_key,source_table,source_record_id,source_field,
                                        proposed_entity_type,proposed_canonical_id,match_rule
                                    ) DO NOTHING""",
                                    (
                                        candidate_id,
                                        run_id,
                                        scope_key,
                                        source_table,
                                        source_record_id,
                                        value,
                                        field.field,
                                        field.entity_type,
                                        str(match["canonical_id"]),
                                        rule,
                                        str(match.get("identifier_namespace") or "") or None,
                                        str(match.get("identifier_value") or "") or None,
                                        source_evidence_url
                                        or str(match.get("canonical_evidence_url") or ""),
                                        source_evidence_status,
                                        (
                                            f"{rule} on {source_table}.{field.field}; "
                                            f"{match.get('canonical_evidence_basis') or 'stored canonical evidence'}"
                                        ),
                                        str(match.get("verification_status") or ""),
                                        int(ambiguous),
                                        _json(source_row),
                                        _json(
                                            {
                                                "source": {
                                                    "table": source_table,
                                                    "record_id": source_record_id,
                                                    "field": field.field,
                                                    "value": value,
                                                    "evidence_url": source_evidence_url,
                                                },
                                                "canonical": {
                                                    "entity_type": field.entity_type,
                                                    "canonical_id": match["canonical_id"],
                                                    "canonical_name": match["canonical_name"],
                                                    "evidence_url": match.get(
                                                        "canonical_evidence_url"
                                                    )
                                                    or "",
                                                    "evidence_status": match.get(
                                                        "evidence_status"
                                                    )
                                                    or "",
                                                },
                                            }
                                        ),
                                        now,
                                        now,
                                    ),
                                )
                                created += max(0, result.rowcount)
                self.conn.execute(
                    """UPDATE canonicalisation_runs
                    SET run_status='completed',records_scanned=?,
                    candidates_created=?,completed_at=?
                    WHERE canonicalisation_run_id=?""",
                    (len(rows), created, _now(), run_id),
                )
            return {
                "run_id": run_id,
                "source_table": source_table,
                "records_scanned": len(rows),
                "candidates_created": created,
                "max_records": bounded_max,
                "permitted_rules": requested_rules,
                "status": "completed",
            }
        except Exception as exc:
            with self.conn.transaction():
                self.conn.execute(
                    """UPDATE canonicalisation_runs
                    SET run_status='failed',completed_at=?,error_summary=?
                    WHERE canonicalisation_run_id=?""",
                    (_now(), exc.__class__.__name__, run_id),
                )
            raise

    def list_candidates(
        self,
        principal: Mapping[str, Any],
        *,
        page: int = 1,
        page_size: int = 25,
        source_table: str = "",
        entity_type: str = "",
        match_rule: str = "",
        status: str = "pending-review",
    ) -> CandidatePage:
        _require_role(principal, {"analyst_reviewer", "platform_admin"})
        bounded_page = _bounded_page(page)
        bounded_size = _bounded_page_size(page_size)
        clauses = ["scope_key=?"]
        params: list[Any] = [_scope_key(principal)]
        for column, value, allowed in (
            ("source_table", source_table, SOURCE_TYPES),
            ("proposed_entity_type", entity_type, ENTITY_TYPES),
            ("match_rule", match_rule, MATCH_RULES),
            ("review_status", status, CANDIDATE_STATUSES),
        ):
            if value:
                if value not in allowed:
                    raise CanonicalisationError(f"Unsupported {column} filter.")
                clauses.append(f"{column}=?")
                params.append(value)
        offset = (bounded_page - 1) * bounded_size
        rows = self.conn.execute(
            f"""SELECT canonicalisation_candidate_id,source_table,source_record_id,
            source_display_value,source_field,proposed_entity_type,
            proposed_canonical_id,match_rule,identifier_namespace,identifier_value,
            evidence_url,evidence_status,evidence_basis,verification_status,
            ambiguous,review_status,created_at,updated_at
            FROM canonicalisation_candidates
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at,canonicalisation_candidate_id
            LIMIT ? OFFSET ?""",
            (*params, bounded_size + 1, offset),
        ).fetchall()
        return CandidatePage(
            page=bounded_page,
            page_size=bounded_size,
            candidates=tuple(dict(row) for row in rows[:bounded_size]),
            has_more=len(rows) > bounded_size,
        )

    def candidate_detail(
        self, principal: Mapping[str, Any], candidate_id: str
    ) -> dict[str, Any] | None:
        _require_role(principal, {"analyst_reviewer", "platform_admin"})
        scope_key = _scope_key(principal)
        candidate = self.repository.candidate(candidate_id, scope_key)
        if not candidate:
            return None
        candidate["source_record"] = json.loads(candidate.pop("source_record_json"))
        candidate["supporting_evidence"] = json.loads(
            candidate.pop("supporting_evidence_json")
        )
        candidate["canonical_profile"] = self.repository.target_summary(
            str(candidate["proposed_entity_type"]),
            str(candidate["proposed_canonical_id"]),
        )
        candidate["decision_history"] = self.repository.decision_history(
            candidate_id, scope_key
        )
        return candidate

    def _review(
        self,
        principal: Mapping[str, Any],
        candidate_id: str,
        decision_status: str,
        reviewer_notes: str,
    ) -> dict[str, Any]:
        _require_role(principal, {"analyst_reviewer", "platform_admin"})
        if decision_status not in {
            "accepted",
            "rejected",
            "requires-more-evidence",
        }:
            raise CanonicalisationError("Unsupported review decision.")
        scope_key = _scope_key(principal)
        reviewer_name = str(
            principal.get("display_name") or principal.get("role") or ""
        ).strip()
        if not reviewer_name:
            raise CanonicalisationError("Reviewer identity is required.")
        decision_id = f"canonicalisation-decision-{uuid4().hex}"
        now = _now()
        with self.conn.transaction():
            candidate = self.repository.candidate(candidate_id, scope_key)
            if not candidate:
                raise CanonicalisationError("Canonicalisation candidate was not found.")
            current_status = str(candidate["review_status"])
            if current_status not in {"pending-review", "requires-more-evidence"}:
                raise CanonicalisationError(
                    "This candidate already has a final review decision."
                )
            previous_decision = self.conn.execute(
                """SELECT canonicalisation_decision_id
                FROM canonicalisation_decisions
                WHERE canonicalisation_candidate_id=? AND scope_key=?
                ORDER BY decided_at DESC,canonicalisation_decision_id DESC LIMIT 1""",
                (candidate_id, scope_key),
            ).fetchone()
            if decision_status == "accepted":
                existing = self.conn.execute(
                    """SELECT canonical_record_link_id,canonical_id
                    FROM canonical_record_links
                    WHERE scope_key=? AND source_table=? AND source_record_id=?
                      AND canonical_entity_type=? AND active=1""",
                    (
                        scope_key,
                        candidate["source_table"],
                        candidate["source_record_id"],
                        candidate["proposed_entity_type"],
                    ),
                ).fetchone()
                if existing:
                    if str(existing["canonical_id"]) == str(
                        candidate["proposed_canonical_id"]
                    ):
                        raise CanonicalisationError(
                            "An active canonical link already exists for this record."
                        )
                    raise CanonicalisationError(
                        "This source record already links to a conflicting canonical "
                        "entity of the same type."
                    )
            self.conn.execute(
                """INSERT INTO canonicalisation_decisions
                (canonicalisation_decision_id,canonicalisation_candidate_id,
                scope_key,decision_status,previous_status,reviewer_name,
                reviewer_role,reviewer_organisation_id,reviewer_workspace_id,
                reviewer_notes,decided_at,supersedes_decision_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    decision_id,
                    candidate_id,
                    scope_key,
                    decision_status,
                    current_status,
                    reviewer_name,
                    str(principal.get("role") or ""),
                    str(principal.get("organisation_id") or "") or None,
                    str(principal.get("workspace_id") or "") or None,
                    str(reviewer_notes or "").strip(),
                    now,
                    (
                        str(previous_decision["canonicalisation_decision_id"])
                        if previous_decision
                        else None
                    ),
                ),
            )
            self.conn.execute(
                """UPDATE canonicalisation_candidates
                SET review_status=?,updated_at=?
                WHERE canonicalisation_candidate_id=? AND scope_key=?""",
                (decision_status, now, candidate_id, scope_key),
            )
            link_id = ""
            if decision_status == "accepted":
                link_id = "canonical-link-" + _stable_id(
                    scope_key,
                    candidate["source_table"],
                    candidate["source_record_id"],
                    candidate["proposed_entity_type"],
                )
                self.conn.execute(
                    """INSERT INTO canonical_record_links
                    (canonical_record_link_id,scope_key,source_table,
                    source_record_id,canonical_entity_type,canonical_id,
                    canonicalisation_candidate_id,accepted_decision_id,
                    evidence_url,evidence_status,evidence_basis,
                    verification_status,active,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,'human-verified',1,?)""",
                    (
                        link_id,
                        scope_key,
                        candidate["source_table"],
                        candidate["source_record_id"],
                        candidate["proposed_entity_type"],
                        candidate["proposed_canonical_id"],
                        candidate_id,
                        decision_id,
                        candidate["evidence_url"],
                        candidate["evidence_status"],
                        candidate["evidence_basis"],
                        now,
                    ),
                )
        return {
            "candidate_id": candidate_id,
            "decision_id": decision_id,
            "decision_status": decision_status,
            "canonical_record_link_id": link_id,
            "reviewer_name": reviewer_name,
            "decided_at": now,
        }

    def accept_candidate(
        self, principal: Mapping[str, Any], candidate_id: str, reviewer_notes: str = ""
    ) -> dict[str, Any]:
        return self._review(principal, candidate_id, "accepted", reviewer_notes)

    def reject_candidate(
        self, principal: Mapping[str, Any], candidate_id: str, reviewer_notes: str = ""
    ) -> dict[str, Any]:
        return self._review(principal, candidate_id, "rejected", reviewer_notes)

    def request_more_evidence(
        self, principal: Mapping[str, Any], candidate_id: str, reviewer_notes: str = ""
    ) -> dict[str, Any]:
        return self._review(
            principal, candidate_id, "requires-more-evidence", reviewer_notes
        )

    def decision_history(
        self, principal: Mapping[str, Any], candidate_id: str
    ) -> tuple[Mapping[str, Any], ...]:
        _require_role(principal, {"analyst_reviewer", "platform_admin"})
        return tuple(
            self.repository.decision_history(candidate_id, _scope_key(principal))
        )

    def supersede_link(
        self,
        principal: Mapping[str, Any],
        link_id: str,
        reviewer_notes: str,
    ) -> dict[str, Any]:
        """Rollback one accepted link while preserving the link and decision history."""
        _require_role(principal, {"platform_admin"})
        scope_key = _scope_key(principal)
        now = _now()
        with self.conn.transaction():
            link = self.conn.execute(
                """SELECT canonicalisation_candidate_id,accepted_decision_id
                FROM canonical_record_links
                WHERE canonical_record_link_id=? AND scope_key=? AND active=1""",
                (link_id, scope_key),
            ).fetchone()
            if not link:
                raise CanonicalisationError("Active canonical link was not found.")
            candidate_id = str(link["canonicalisation_candidate_id"])
            decision_id = f"canonicalisation-decision-{uuid4().hex}"
            self.conn.execute(
                """INSERT INTO canonicalisation_decisions
                (canonicalisation_decision_id,canonicalisation_candidate_id,
                scope_key,decision_status,previous_status,reviewer_name,
                reviewer_role,reviewer_organisation_id,reviewer_workspace_id,
                reviewer_notes,decided_at,supersedes_decision_id)
                VALUES (?,?,?,'superseded','accepted',?,?,?,?,?,?,?)""",
                (
                    decision_id,
                    candidate_id,
                    scope_key,
                    str(principal.get("display_name") or "Platform Administrator"),
                    str(principal.get("role") or ""),
                    str(principal.get("organisation_id") or "") or None,
                    str(principal.get("workspace_id") or "") or None,
                    str(reviewer_notes or "").strip(),
                    now,
                    str(link["accepted_decision_id"]),
                ),
            )
            self.conn.execute(
                """UPDATE canonicalisation_candidates
                SET review_status='superseded',updated_at=?
                WHERE canonicalisation_candidate_id=?""",
                (now, candidate_id),
            )
            self.conn.execute(
                """UPDATE canonical_record_links
                SET active=0,link_status='rolled-back',superseded_at=?,
                rollback_reason=?
                WHERE canonical_record_link_id=?""",
                (now, str(reviewer_notes or "").strip(), link_id),
            )
        return {
            "canonical_record_link_id": link_id,
            "decision_id": decision_id,
            "status": "rolled-back",
        }
