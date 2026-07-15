from __future__ import annotations

import unittest
from contextlib import ExitStack
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest


class NavigationTests(unittest.TestCase):
    def test_each_sidebar_click_opens_the_new_page_on_the_first_rerun(self):
        def render_customer_app():
            from pharmatune_ui.app import run

            run({"role": "analyst_reviewer", "display_name": "Test Analyst"})

        def marker(name: str):
            return lambda *args, **kwargs: st.write(f"PAGE:{name}")

        from pharmatune_ui import app as customer_app

        page_patches = [
            patch.object(customer_app.pages, "overview", marker("Overview")),
            patch.object(customer_app.pages, "explorer", marker("Opportunity Explorer")),
            patch.object(customer_app.pages, "entity_page", marker("Entity")),
        ]
        with ExitStack() as stack:
            stack.enter_context(patch.object(customer_app, "_database_status", return_value={"schema_version": 7}))
            for page_patch in page_patches:
                stack.enter_context(page_patch)
            app = AppTest.from_function(render_customer_app).run()
            self.assertFalse(app.exception)

            app.radio[0].set_value("Opportunity Explorer").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["page"], "Opportunity Explorer")
            self.assertTrue(any(item.value == "PAGE:Opportunity Explorer" for item in app.markdown))

            app.radio[0].set_value("Companies").run()
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state["page"], "Companies")
            self.assertTrue(any(item.value == "PAGE:Entity" for item in app.markdown))


if __name__ == "__main__":
    unittest.main()
