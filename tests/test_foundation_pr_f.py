from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import streamlit as st
from streamlit.testing.v1 import AppTest

from pharmadrone import db
from pharmadrone.intelligence import (
    CanonicalIntelligenceService,
    GraphEdge,
    GraphNode,
    GraphTraversal,
    SearchPage,
    SearchResult,
)
from pharmatune_ui import canonical_intelligence
from tests.test_foundation_pr_e import _seed_graph


def _profile_fixture(tmp_path):
    conn = db.connect(tmp_path / "pr-f-profiles.sqlite")
    _seed_graph(conn)
    service = CanonicalIntelligenceService(conn)
    profiles = {
        "pharmaceutical_problem": service.problem_profile("problem-e"),
        "technology_solution": service.solution_profile("solution-e"),
        "product": service.product_profile("product-e"),
        "api": service.api_profile("api-e"),
        "organisation": service.organisation_profile("organisation-e"),
        "opportunity": service.opportunity_profile("opportunity-e"),
    }
    conn.close()
    return profiles


def _render_with_mock(renderer, profile):
    fake_st = MagicMock()
    fake_st.columns.side_effect = lambda spec: [
        MagicMock() for _ in range(spec if isinstance(spec, int) else len(spec))
    ]
    with (
        patch.object(canonical_intelligence, "st", fake_st),
        patch.object(canonical_intelligence.theme, "page_header"),
        patch.object(canonical_intelligence.theme, "badge", return_value="<badge>"),
    ):
        renderer(profile)
    return fake_st


def test_page_is_registered_without_removing_existing_navigation():
    from pharmatune_ui import app as customer_app

    assert "Canonical Intelligence" in customer_app.NAV["INTELLIGENCE"]
    assert {
        "Overview",
        "Opportunity Explorer",
        "Companies",
        "Products",
        "Technologies",
        "Research & Innovation",
        "Regulatory Signals",
        "Deals & Funding",
        "Patents",
        "System Health",
    }.issubset(set(customer_app.NAV_OPTIONS))

    def render_customer_app():
        from pharmatune_ui.app import run

        run({"role": "analyst_reviewer", "display_name": "Test Analyst"})

    def marker():
        st.write("PAGE:Canonical Intelligence")

    with (
        patch.object(customer_app, "_database_status", return_value={"schema_version": 19}),
        patch.object(customer_app.canonical_intelligence, "render_page", marker),
    ):
        app = AppTest.from_function(render_customer_app).run()
        app.radio[0].set_value("Canonical Intelligence").run()
    assert not app.exception
    assert app.session_state["page"] == "Canonical Intelligence"
    assert any(
        item.value == "PAGE:Canonical Intelligence" for item in app.markdown
    )


def test_empty_search_does_not_query_and_search_state_is_paginated():
    first = SearchPage(
        query="Shared Entity",
        page=1,
        page_size=10,
        results=(
            SearchResult(
                "product",
                "product-e",
                "Shared Entity",
                "alias",
                "human-verified",
                "official evidence",
                True,
            ),
        ),
        has_more=True,
        ambiguous=True,
    )
    second = SearchPage(
        query="Shared Entity",
        page=2,
        page_size=10,
        results=(
            SearchResult(
                "organisation",
                "organisation-e",
                "Shared Entity",
                "canonical-name",
                "human-verified",
                "official evidence",
                True,
            ),
        ),
        has_more=False,
        ambiguous=True,
    )

    def render():
        from pharmatune_ui.canonical_intelligence import render_page

        render_page()

    def search(*args, **kwargs):
        return first if kwargs["page"] == 1 else second

    search_mock = MagicMock(side_effect=search)
    with (
        patch.object(
            canonical_intelligence.data,
            "canonical_intelligence_search",
            search_mock,
        ),
        patch.object(
            canonical_intelligence.data,
            "canonical_intelligence_profile",
            return_value=None,
        ),
    ):
        app = AppTest.from_function(render).run()
        assert not app.exception
        assert search_mock.call_count == 0
        assert any("non-empty" in item.value for item in app.info)

        app.text_input[0].set_value("Shared Entity").run()
        assert not app.exception
        assert search_mock.call_args.kwargs["page"] == 1
        assert any("Multiple canonical records" in item.value for item in app.warning)
        assert any("Ambiguous match" in item.value for item in app.warning)

        next_button = next(button for button in app.button if button.label == "Next →")
        next_button.click().run()
        assert not app.exception
        assert app.session_state["canonical_intelligence_page"] == 2
        assert search_mock.call_args.kwargs["page"] == 2

        open_button = next(button for button in app.button if button.label == "Open")
        open_button.click().run()
        selected = app.session_state["canonical_intelligence_selected"]
        assert selected["entity_type"] == "organisation"
        assert selected["canonical_id"] == "organisation-e"


def test_each_entity_profile_renderer_and_evidence_columns(tmp_path):
    profiles = _profile_fixture(tmp_path)
    renderers = {
        "pharmaceutical_problem": canonical_intelligence.render_problem_profile,
        "technology_solution": canonical_intelligence.render_solution_profile,
        "product": canonical_intelligence.render_product_api_profile,
        "api": canonical_intelligence.render_product_api_profile,
        "organisation": canonical_intelligence.render_organisation_profile,
        "opportunity": canonical_intelligence.render_opportunity_profile,
    }
    for entity_type, renderer in renderers.items():
        fake_st = _render_with_mock(renderer, profiles[entity_type])
        assert fake_st.dataframe.called
        frames = [
            call.args[0]
            for call in fake_st.dataframe.call_args_list
            if call.args and hasattr(call.args[0], "columns")
        ]
        assert any(
            "evidence status" in {str(column).casefold() for column in frame.columns}
            for frame in frames
        )
        assert any(
            "evidence url" in {str(column).casefold() for column in frame.columns}
            for frame in frames
        )


def test_evidence_renderer_preserves_governance_and_clickable_source(tmp_path):
    profile = _profile_fixture(tmp_path)["opportunity"]
    fake_st = _render_with_mock(
        canonical_intelligence.render_opportunity_profile,
        profile,
    )
    evidence_frames = [
        call.args[0]
        for call in fake_st.dataframe.call_args_list
        if call.args
        and hasattr(call.args[0], "columns")
        and "Source record" in call.args[0].columns
    ]
    assert evidence_frames
    frame = evidence_frames[-1]
    assert {
        "Evidence status",
        "Verification status",
        "Evidence basis",
        "Source record",
        "Evidence URL",
    }.issubset(frame.columns)
    assert frame["Evidence URL"].astype(str).str.startswith("https://").any()


def test_requires_review_option_and_graph_depth_are_bounded():
    with patch.object(
        canonical_intelligence.data,
        "_canonical_intelligence_call",
        return_value="result",
    ) as call:
        canonical_intelligence.data.canonical_intelligence_profile.clear()
        canonical_intelligence.data.canonical_intelligence_profile(
            "organisation",
            "organisation-e",
            include_requires_review=True,
        )
        assert call.call_args.kwargs["include_requires_review"] is True

        canonical_intelligence.data.canonical_intelligence_graph.clear()
        canonical_intelligence.data.canonical_intelligence_graph(
            "organisation",
            "organisation-e",
            max_depth=99,
            include_requires_review=False,
        )
        assert call.call_args.kwargs["max_depth"] == 5
        assert call.call_args.kwargs["max_nodes"] == 100
        assert call.call_args.kwargs["include_requires_review"] is False
    assert canonical_intelligence.GRAPH_DEPTH_OPTIONS == (1, 2, 3, 4, 5)


def test_graph_display_uses_bounded_tables_without_new_visualisation_library():
    graph = GraphTraversal(
        root=GraphNode(
            "pharmaceutical_problem", "problem-e", "Graph Problem", "human-verified"
        ),
        nodes=(
            GraphNode(
                "pharmaceutical_problem",
                "problem-e",
                "Graph Problem",
                "human-verified",
            ),
            GraphNode(
                "technology_solution",
                "solution-e",
                "Graph Solution",
                "human-verified",
            ),
        ),
        edges=(
            GraphEdge(
                "pharmaceutical_problem",
                "problem-e",
                "technology_solution",
                "solution-e",
                "addresses",
                "human-verified",
                "official evidence",
                "https://example.test/evidence",
            ),
        ),
        max_depth=2,
        truncated=False,
    )
    fake_st = MagicMock()
    fake_st.checkbox.return_value = True
    fake_st.select_slider.return_value = 2
    with (
        patch.object(canonical_intelligence, "st", fake_st),
        patch.object(
            canonical_intelligence.data,
            "canonical_intelligence_graph",
            return_value=graph,
        ) as traversal,
    ):
        canonical_intelligence._render_graph(
            "pharmaceutical_problem",
            "problem-e",
            False,
        )
    traversal.assert_called_once_with(
        "pharmaceutical_problem",
        "problem-e",
        max_depth=2,
        include_requires_review=False,
    )
    assert fake_st.dataframe.call_count == 2


def test_page_has_no_direct_sql_writes_or_external_api_calls():
    page_source = Path("pharmatune_ui/canonical_intelligence.py").read_text()
    data_source = Path("pharmatune_ui/data.py").read_text()
    assert ".execute(" not in page_source
    assert "SELECT " not in page_source
    assert "INSERT " not in page_source
    assert "UPDATE " not in page_source
    assert "DELETE " not in page_source
    assert "tavily" not in page_source.casefold()
    assert "google" not in page_source.casefold()
    assert "httpx" not in page_source.casefold()
    assert "requests" not in page_source.casefold()
    assert "CanonicalIntelligenceService" in data_source
