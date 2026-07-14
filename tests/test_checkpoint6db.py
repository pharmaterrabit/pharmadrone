from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from pharmadrone import admin, db
from pharmadrone.storage import dispose_engines
from pharmadrone.storage.migrations import MIGRATIONS


class Checkpoint6DBTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "admin.sqlite"
        self.real_connect = db.connect
        self.conn = self.real_connect(self.path)
        self.conn.close()
        self.platform = {"role": admin.PLATFORM_ADMIN, "display_name": "Platform Test", "organisation_id": ""}

    def tearDown(self):
        dispose_engines(); self.tmp.cleanup()

    def _connect(self, *args, **kwargs):
        return self.real_connect(self.path)

    def test_migration_6_administration_tables(self):
        self.assertGreaterEqual(max(m.version for m in MIGRATIONS), 6)
        conn = self._connect()
        for table in ("organisations","workspaces","admin_users","workspace_settings","admin_audit_events","backup_records","feature_flags","api_usage_daily"):
            self.assertTrue(conn.has_table(table), table)
        conn.close()

    def test_platform_provisioning_is_durable_and_audited(self):
        with patch("pharmadrone.admin.db.connect", side_effect=self._connect):
            org = admin.create_organisation(self.platform,"Test Therapeutics","Enterprise")
            ws = admin.create_workspace(self.platform,org,"Intelligence")
            user = admin.invite_user(self.platform,org,"analyst@example.com","Analyst",admin.ANALYST,ws)
        conn=self._connect()
        self.assertEqual(conn.execute("SELECT status FROM organisations WHERE organisation_id=?",(org,)).fetchone()["status"],"active")
        self.assertEqual(conn.execute("SELECT status FROM admin_users WHERE user_id=?",(user,)).fetchone()["status"],"invited")
        self.assertGreaterEqual(conn.execute("SELECT COUNT(*) AS n FROM admin_audit_events").fetchone()["n"],3)
        conn.close()

    def test_workspace_admin_cannot_cross_tenant_or_assign_platform_role(self):
        with patch("pharmadrone.admin.db.connect", side_effect=self._connect):
            org1=admin.create_organisation(self.platform,"Tenant One")
            org2=admin.create_organisation(self.platform,"Tenant Two")
            workspace_admin={"role":admin.WORKSPACE_ADMIN,"display_name":"Scoped","organisation_id":org1}
            with self.assertRaises(PermissionError): admin.create_workspace(workspace_admin,org2,"Forbidden")
            with self.assertRaises(PermissionError): admin.invite_user(workspace_admin,org1,"p@example.com","P",admin.PLATFORM_ADMIN)

    def test_workspace_governance_values_and_memberships_are_validated(self):
        with patch("pharmadrone.admin.db.connect", side_effect=self._connect):
            org1=admin.create_organisation(self.platform,"Governed Tenant")
            org2=admin.create_organisation(self.platform,"Other Tenant")
            ws2=admin.create_workspace(self.platform,org2,"Other Workspace")
            scoped={"role":admin.WORKSPACE_ADMIN,"display_name":"Scoped","organisation_id":org1}
            with self.assertRaises(ValueError):
                admin.invite_user(scoped,org1,"a@example.com","A",admin.ANALYST,ws2)
            with self.assertRaises(ValueError):
                admin.update_workspace_settings(scoped,org1,export_policy="open",notification_mode="daily_digest",retention_days=365,mfa_required=True)
            with self.assertRaises(ValueError):
                admin.update_workspace_settings(scoped,org1,export_policy="disabled",notification_mode="always",retention_days=365,mfa_required=True)
            with self.assertRaises(ValueError):
                admin.update_workspace_settings(scoped,org1,export_policy="disabled",notification_mode="disabled",retention_days=5000,mfa_required=True)

    def test_snapshot_hides_global_operations_from_workspace_admin(self):
        with patch("pharmadrone.admin.db.connect", side_effect=self._connect):
            org=admin.create_organisation(self.platform,"Scoped Tenant")
            scoped={"role":admin.WORKSPACE_ADMIN,"display_name":"Scoped","organisation_id":org}
            with patch("pharmadrone.admin.db.database_status",return_value={"backend":"sqlite","schema_version":6,"migration_count":6,"connection_status":"healthy"}):
                state=admin.snapshot(scoped)
        self.assertEqual(state["usage"],[]); self.assertEqual(state["flags"],[]); self.assertEqual(state["backups"],[]); self.assertEqual(state["failed_runs"],[])
        self.assertEqual([r["organisation_id"] for r in state["organisations"]],[org])

    def test_root_routes_admin_roles_separately(self):
        text=(Path(__file__).resolve().parents[1]/"app.py").read_text()
        self.assertIn("pharmatune_admin.app",text)
        customer=(Path(__file__).resolve().parents[1]/"pharmatune_ui"/"app.py").read_text()
        self.assertNotIn('"Platform Admin"',customer)

    @staticmethod
    def _ui_state():
        return {
            "organisations": [{"organisation_id": "org-test", "name": "Test Therapeutics", "slug": "test-therapeutics", "plan_name": "Enterprise", "status": "active", "retention_days": 2555, "created_at": "2026-07-14"}],
            "workspaces": [{"workspace_id": "ws-test", "organisation_id": "org-test", "name": "Intelligence", "status": "active", "created_at": "2026-07-14"}],
            "users": [{"user_id": "usr-test", "display_name": "Reviewer", "email": "reviewer@example.com", "organisation_id": "org-test", "role_name": admin.ANALYST, "status": "active", "mfa_enabled": 1, "export_allowed": 1, "outreach_allowed": 0, "last_login_at": None}],
            "settings": {"export_policy": "workspace_admin_approval", "notification_mode": "daily_digest", "retention_days": 2555, "mfa_required": 1},
            "events": [], "flags": [], "usage": [], "backups": [], "failed_runs": [],
            "database": {"backend": "sqlite", "schema_version": 6, "migration_count": 6, "connection_status": "healthy"},
            "scheduler": {"enabled_sources": 9, "failed_sources": 0, "scheduler_status": "healthy", "next_orchestrator_run": "2026-07-14T12:00:00Z", "latest_run": {}, "sources": []},
        }

    def test_platform_admin_shell_renders_each_route_without_exception(self):
        from pharmatune_admin.app import PLATFORM_NAV
        def render_platform():
            from pharmatune_admin.app import run
            run({"role": "platform_admin", "display_name": "Platform Test", "organisation_id": ""})
        with patch("pharmadrone.admin.snapshot", return_value=self._ui_state()):
            app = AppTest.from_function(render_platform).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Platform Overview" in item.value for item in app.markdown))
            for route in PLATFORM_NAV[1:]:
                app.radio[0].set_value(route).run()
                self.assertFalse(app.exception, route)

    def test_workspace_admin_shell_hides_platform_navigation(self):
        def render_workspace():
            from pharmatune_admin.app import run
            run({"role": "workspace_admin", "display_name": "Scoped", "organisation_id": "org-test"})
        with patch("pharmadrone.admin.snapshot", return_value=self._ui_state()):
            app = AppTest.from_function(render_workspace).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.radio), 0)
        self.assertTrue(any("Workspace Administration" in item.value for item in app.markdown))


if __name__ == "__main__": unittest.main()
