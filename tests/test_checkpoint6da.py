from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Checkpoint6DATests(unittest.TestCase):
    def test_entrypoint_uses_customer_platform(self):
        text = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("pharmatune_ui.app", text)
        self.assertTrue((ROOT / "legacy_app.py").exists())

    def test_all_approved_customer_screens_are_routed(self):
        text = (ROOT / "pharmatune_ui" / "app.py").read_text(encoding="utf-8")
        for name in (
            "Overview", "Opportunity Explorer", "Companies", "Products", "Technologies",
            "Research & Innovation", "Regulatory Signals", "Deals & Funding", "Patents",
            "Human Validation", "Case Studies", "Data Sources", "System Health", "Settings",
        ):
            self.assertIn(name, text)

    def test_platform_admin_is_not_in_customer_navigation(self):
        text = (ROOT / "pharmatune_ui" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn('"Platform Admin"', text)
        self.assertNotIn('"Organisations"', text)

    def test_approved_design_tokens_are_centralised(self):
        text = (ROOT / "pharmatune_ui" / "theme.py").read_text(encoding="utf-8")
        for token in ("#070D18", "#0C1526", "#111D33", "#4D8DFF", "#3AC8E6", "#9180F4"):
            self.assertIn(token, text)

    def test_no_numeric_root_cause_probability_copy(self):
        combined = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "pharmatune_ui").glob("*.py"))
        self.assertNotIn("root-cause probability", combined.lower())
        self.assertIn("Requires validation", combined)

    def test_server_side_pagination_is_bounded(self):
        text = (ROOT / "pharmatune_ui" / "data.py").read_text(encoding="utf-8")
        self.assertIn("LIMIT ? OFFSET ?", text)


if __name__ == "__main__":
    unittest.main()
