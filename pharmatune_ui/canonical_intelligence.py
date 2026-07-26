"""Read-only Streamlit presentation for the canonical intelligence graph."""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd
import streamlit as st

from pharmadrone.intelligence import (
    EvidenceReference,
    OrganisationProviderProfile,
    OpportunityIntelligenceProfile,
    PharmaceuticalProblemProfile,
    ProductApiProfile,
    RelationshipReference,
    SearchResult,
    TechnologySolutionProfile,
)

from . import data, theme


PAGE_LABEL = "Canonical Intelligence"
PAGE_SIZE_OPTIONS = (10, 25, 50)
GRAPH_DEPTH_OPTIONS = (1, 2, 3, 4, 5)


def _text(value: Any, fallback: str = "Not recorded") -> str:
    return str(value) if value not in (None, "") else fallback


def _label(value: str) -> str:
    return str(value or "").replace("_", " ").replace("-", " ").title()


def _status_badges(verification_status: str, evidence_status: str) -> None:
    st.markdown(
        theme.badge(_text(verification_status, "Verification not recorded"), "violet")
        + theme.badge(_text(evidence_status, "Evidence not recorded"), "blue"),
        unsafe_allow_html=True,
    )


def _mapping_table(title: str, rows: Iterable[Mapping[str, Any]], columns: tuple[str, ...]) -> None:
    values = [{_label(column): row.get(column) for column in columns} for row in rows]
    st.markdown(f"#### {title}")
    if not values:
        st.caption("No governed records are linked.")
        return
    st.dataframe(pd.DataFrame(values), use_container_width=True, hide_index=True)


def _relationship_table(title: str, relationships: Iterable[RelationshipReference]) -> None:
    rows = [
        {
            "Entity type": _label(item.target_type),
            "Canonical name": item.target_name,
            "Canonical ID": item.target_id,
            "Relationship": item.relationship_type,
            "Verification": item.verification_status,
            "Evidence": item.evidence_status,
            "Evidence URL": item.evidence_url,
        }
        for item in relationships
    ]
    st.markdown(f"#### {title}")
    if not rows:
        st.caption("No governed relationships are linked.")
        return
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Evidence URL": st.column_config.LinkColumn(
                "Evidence URL", display_text="Open ↗"
            )
        },
    )


def _evidence_table(evidence: Iterable[EvidenceReference]) -> None:
    rows = [
        {
            "Evidence status": item.evidence_status,
            "Verification status": item.verification_status,
            "Evidence basis": item.evidence_basis,
            "Source record": (
                f"{item.source_table} · {item.source_record_id}"
                if item.source_table
                else item.source_record_id
            ),
            "Evidence URL": item.evidence_url,
        }
        for item in evidence
    ]
    st.markdown("### Supporting evidence")
    if not rows:
        st.info("No governed supporting evidence is linked to this canonical record.")
        return
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "Evidence URL": st.column_config.LinkColumn(
                "Evidence URL", display_text="Open evidence ↗"
            )
        },
    )


def _identity_header(entity) -> None:
    theme.page_header(
        entity.display_name,
        f"{_label(entity.entity_type)} · canonical ID {entity.canonical_id}",
        PAGE_LABEL,
    )
    _status_badges(entity.verification_status, entity.evidence_status)


def render_problem_profile(profile: PharmaceuticalProblemProfile) -> None:
    entity = profile.problem
    _identity_header(entity)
    st.markdown("### Canonical definition")
    st.write(_text(entity.attributes.get("definition")))
    _mapping_table(
        "Taxonomy",
        [profile.taxonomy],
        ("taxonomy_namespace", "term_kind", "code", "label", "taxonomy_definition", "version"),
    )
    _relationship_table("Linked technology solutions", profile.linked_solutions)
    _relationship_table("Related opportunities", profile.related_opportunities)
    _evidence_table(profile.supporting_evidence)


def render_solution_profile(profile: TechnologySolutionProfile) -> None:
    entity = profile.solution
    _identity_header(entity)
    a, b = st.columns(2)
    a.metric("Solution type", _text(profile.solution_type.get("label")))
    b.metric("Maturity", _text(entity.attributes.get("maturity_status")))
    st.markdown("### Solution description")
    st.write(_text(entity.attributes.get("mechanism_summary")))
    if entity.attributes.get("scope_note"):
        st.caption(str(entity.attributes["scope_note"]))
    _mapping_table(
        "Taxonomy",
        [profile.taxonomy],
        ("code", "label", "definition", "version"),
    )
    _relationship_table("Linked pharmaceutical problems", profile.linked_problems)
    _relationship_table("Linked organisations and providers", profile.linked_organisations)
    _relationship_table("Related opportunities", profile.related_opportunities)
    _evidence_table(profile.supporting_evidence)


def render_product_api_profile(profile: ProductApiProfile) -> None:
    entity = profile.entity
    _identity_header(entity)
    type_field = "product_type" if entity.entity_type == "product" else "substance_type"
    st.metric(_label(type_field), _text(entity.attributes.get(type_field)))
    _mapping_table(
        "Aliases",
        profile.aliases,
        ("alias_name", "alias_type", "language_code", "verification_status", "evidence_status"),
    )
    _mapping_table(
        "Identifiers",
        profile.identifiers,
        (
            "identifier_namespace",
            "identifier_value",
            "jurisdiction",
            "verification_status",
            "evidence_status",
        ),
    )
    title = "Linked APIs" if entity.entity_type == "product" else "Linked products"
    _relationship_table(title, profile.linked_pharmaceutical_entities)
    _relationship_table("Linked organisations", profile.linked_organisations)
    _relationship_table("Related opportunities", profile.related_opportunities)
    _evidence_table(profile.supporting_evidence)


def render_organisation_profile(profile: OrganisationProviderProfile) -> None:
    entity = profile.organisation
    _identity_header(entity)
    a, b = st.columns(2)
    a.metric("Organisation type", _text(entity.attributes.get("organisation_type")))
    b.metric("Country", _text(entity.attributes.get("country_code")))
    _mapping_table(
        "Aliases",
        profile.aliases,
        ("alias_name", "alias_type", "verification_status", "evidence_status"),
    )
    _mapping_table(
        "Identifiers",
        profile.identifiers,
        (
            "identifier_namespace",
            "identifier_value",
            "jurisdiction",
            "verification_status",
            "evidence_status",
        ),
    )
    _relationship_table("Capabilities", profile.capabilities)
    _relationship_table("Linked technology solutions", profile.linked_solutions)
    _relationship_table("Linked products", profile.linked_products)
    _relationship_table("Linked APIs", profile.linked_apis)
    _relationship_table("Related opportunities", profile.related_opportunities)
    _relationship_table("Commercial events", profile.related_commercial_events)
    _evidence_table(profile.supporting_evidence)


def render_opportunity_profile(profile: OpportunityIntelligenceProfile) -> None:
    entity = profile.opportunity
    _identity_header(entity)
    a, b = st.columns(2)
    a.metric("Lifecycle status", _text(entity.attributes.get("lifecycle_status")))
    b.metric("Opportunity type", _text(entity.attributes.get("opportunity_type")))
    st.markdown("### Summary")
    st.write(_text(entity.attributes.get("summary")))
    _relationship_table("Participants and roles", profile.participants)
    _relationship_table("Linked pharmaceutical problems", profile.linked_problems)
    _relationship_table("Linked technology solutions", profile.linked_solutions)
    _relationship_table("Linked products", profile.linked_products)
    _relationship_table("Linked APIs", profile.linked_apis)
    _relationship_table("Commercial events", profile.related_commercial_events)
    _evidence_table(profile.supporting_evidence)


PROFILE_RENDERERS = {
    "pharmaceutical_problem": render_problem_profile,
    "technology_solution": render_solution_profile,
    "product": render_product_api_profile,
    "api": render_product_api_profile,
    "organisation": render_organisation_profile,
    "opportunity": render_opportunity_profile,
}


def _select_result(result: SearchResult) -> None:
    st.session_state["canonical_intelligence_selected"] = {
        "entity_type": result.entity_type,
        "canonical_id": result.canonical_id,
        "display_name": result.display_name,
    }


def _change_page(page: int) -> None:
    st.session_state["canonical_intelligence_page"] = max(1, int(page))


def _render_search_results(search_page) -> None:
    if search_page.ambiguous:
        st.warning(
            "Multiple canonical records match this query. PharmaTune has not selected "
            "or merged them; review the entity type and canonical ID before opening one."
        )
    if not search_page.results:
        theme.empty(
            "No canonical record exists",
            "No stored canonical entity matches this exact name, alias or identifier.",
            "No results",
        )
        return
    st.caption(
        f"Page {search_page.page} · up to {search_page.page_size} results · "
        "database-first canonical records"
    )
    for index, result in enumerate(search_page.results):
        with st.container():
            c1, c2, c3, c4 = st.columns([2.2, 1.2, 1.6, 0.7])
            c1.markdown(f"**{result.display_name}**")
            c1.caption(f"{_label(result.entity_type)} · {result.canonical_id}")
            c2.caption("Match")
            c2.write(_label(result.match_kind))
            c3.caption(
                f"Verification: {_text(result.verification_status)}  \n"
                f"Evidence: {_text(result.evidence_status)}"
            )
            if result.ambiguous:
                c3.warning("Ambiguous match")
            c4.button(
                "Open",
                key=f"canonical_open_{search_page.page}_{index}_{result.entity_type}_{result.canonical_id}",
                on_click=_select_result,
                args=(result,),
                use_container_width=True,
            )
    left, middle, right = st.columns([1, 3, 1])
    left.button(
        "← Previous",
        disabled=search_page.page <= 1,
        on_click=_change_page,
        args=(search_page.page - 1,),
        use_container_width=True,
    )
    middle.markdown(
        f"<div style='text-align:center;padding:8px'>Page {search_page.page}</div>",
        unsafe_allow_html=True,
    )
    right.button(
        "Next →",
        disabled=not search_page.has_more,
        on_click=_change_page,
        args=(search_page.page + 1,),
        use_container_width=True,
    )


def _render_graph(entity_type: str, canonical_id: str, include_requires_review: bool) -> None:
    st.markdown("### Relationship graph")
    show_graph = st.checkbox(
        "Show bounded relationship graph",
        value=False,
        key="canonical_intelligence_show_graph",
    )
    if not show_graph:
        st.caption("Graph traversal is optional and uses stored canonical relationships only.")
        return
    depth = st.select_slider(
        "Maximum depth",
        options=GRAPH_DEPTH_OPTIONS,
        value=3,
        key="canonical_intelligence_graph_depth",
    )
    graph = data.canonical_intelligence_graph(
        entity_type,
        canonical_id,
        max_depth=depth,
        include_requires_review=include_requires_review,
    )
    if graph is None:
        st.info("No relationship graph exists for this canonical record.")
        return
    st.caption(
        f"{len(graph.nodes)} nodes · {len(graph.edges)} relationships · "
        f"maximum depth {graph.max_depth}"
    )
    if graph.truncated:
        st.warning("The bounded traversal stopped at its depth or node limit.")
    node_rows = [
        {
            "Entity type": _label(node.entity_type),
            "Canonical name": node.display_name,
            "Canonical ID": node.canonical_id,
            "Verification": node.verification_status,
        }
        for node in graph.nodes
    ]
    edge_rows = [
        {
            "From": f"{_label(edge.source_type)} · {edge.source_id}",
            "Relationship": edge.relationship_type,
            "To": f"{_label(edge.target_type)} · {edge.target_id}",
            "Verification": edge.verification_status,
            "Evidence": edge.evidence_status,
            "Evidence URL": edge.evidence_url,
        }
        for edge in graph.edges
    ]
    _mapping_table(
        "Nodes",
        node_rows,
        ("Entity type", "Canonical name", "Canonical ID", "Verification"),
    )
    st.markdown("#### Relationships")
    if edge_rows:
        st.dataframe(
            pd.DataFrame(edge_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Evidence URL": st.column_config.LinkColumn(
                    "Evidence URL", display_text="Open evidence ↗"
                )
            },
        )
    else:
        st.caption("No outbound governed relationships are stored.")


def _render_selected_profile(include_requires_review: bool) -> None:
    selected = st.session_state.get("canonical_intelligence_selected")
    if not selected:
        return
    entity_type = str(selected.get("entity_type") or "")
    canonical_id = str(selected.get("canonical_id") or "")
    profile = data.canonical_intelligence_profile(
        entity_type,
        canonical_id,
        include_requires_review=include_requires_review,
    )
    if profile is None:
        st.warning("The selected canonical record is no longer available.")
        return
    st.markdown("---")
    renderer = PROFILE_RENDERERS.get(entity_type)
    if renderer is None:
        st.warning("This canonical entity type does not have a profile renderer.")
        return
    renderer(profile)
    _render_graph(entity_type, canonical_id, include_requires_review)


def render_page() -> None:
    """Render the read-only canonical search and profile workspace."""
    theme.page_header(
        PAGE_LABEL,
        "Search stored canonical problems, solutions, products, APIs, organisations "
        "and opportunities with governed evidence.",
        "Intelligence",
    )
    st.caption(
        "Read-only · internal database results only · ambiguous records are never "
        "silently merged or selected"
    )
    query_col, size_col = st.columns([4, 1])
    query = query_col.text_input(
        "Unified canonical search",
        key="canonical_intelligence_query",
        placeholder="Canonical name, alias, external identifier or opportunity ID",
    )
    page_size = size_col.selectbox(
        "Rows",
        PAGE_SIZE_OPTIONS,
        index=1,
        key="canonical_intelligence_page_size",
    )
    include_requires_review = st.checkbox(
        "Include requires-review records",
        value=False,
        key="canonical_intelligence_include_review",
        help="Disabled by default so only governed relationships are shown.",
    )
    normalized_query = " ".join(query.split())
    previous_query = st.session_state.get("canonical_intelligence_last_query")
    if normalized_query != previous_query:
        st.session_state["canonical_intelligence_last_query"] = normalized_query
        st.session_state["canonical_intelligence_page"] = 1
    if not normalized_query:
        st.info("Enter a non-empty canonical name, alias or identifier to search.")
    else:
        page = int(st.session_state.get("canonical_intelligence_page", 1))
        search_page = data.canonical_intelligence_search(
            normalized_query,
            page=page,
            page_size=page_size,
            include_requires_review=include_requires_review,
        )
        _render_search_results(search_page)
    _render_selected_profile(include_requires_review)
