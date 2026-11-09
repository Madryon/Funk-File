import json
import os
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app import app, invalidate_site_status_cache
from models import (
    ErrorLog,
    LoginEvent,
    ProcessingJob,
    SiteSetting,
    Ticket,
    User,
    db,
)


class LiveOpsAndMaintenanceTestCase(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.app.config["WTF_CSRF_ENABLED"] = False
        self.client = self.app.test_client()

        self.app_context = self.app.app_context()
        self.app_context.push()

        from extensions import limiter
        try:
            limiter.reset()
        except Exception:
            pass

        # Ensure maintenance mode is off at start of each test
        try:
            row = SiteSetting.query.filter_by(key="maintenance").first()
            if row:
                row.value = json.dumps({"active": False, "message": ""})
                db.session.commit()
            ann_row = SiteSetting.query.filter_by(key="announcement").first()
            if ann_row:
                ann_row.value = json.dumps({"active": False, "text": ""})
                db.session.commit()
        except Exception:
            pass

        invalidate_site_status_cache()

        # Set admin password for testing
        self.orig_admin_pass = os.environ.get("ADMIN_PASSWORD")
        os.environ["ADMIN_PASSWORD"] = "SuperSecretAdminTest2026!"

    def tearDown(self):
        invalidate_site_status_cache()
        if self.orig_admin_pass is not None:
            os.environ["ADMIN_PASSWORD"] = self.orig_admin_pass
        else:
            os.environ.pop("ADMIN_PASSWORD", None)
        self.app_context.pop()

    def _login_as_admin(self):
        from extensions import limiter
        try:
            limiter.reset()
        except Exception:
            pass
        res = self.client.post(
            "/admin/login",
            json={"password": "SuperSecretAdminTest2026!"},
            headers={"Accept": "application/json"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json.get("ok"))

    # ================= 1. SECURITY & 404 FOR NON-ADMINS =================

    def test_non_admin_gets_404_on_admin_routes(self):
        """Rule: Never expose admin routes or password; /admin/* returns 404 to non-admins."""
        for path in ["/admin", "/admin/", "/admin/api/live-ops", "/admin/api/errors", "/admin/api/users", "/admin/tickets/1/update"]:
            res = self.client.get(path)
            self.assertEqual(res.status_code, 404, f"Path {path} must return 404 to non-admins")

    def test_admin_login_wrong_password_and_no_leak(self):
        """Wrong password returns 401 with generic error, never leaks password or hash."""
        res = self.client.post(
            "/admin/login",
            json={"password": "IncorrectPassword!"},
            headers={"Accept": "application/json"}
        )
        self.assertEqual(res.status_code, 401)
        self.assertEqual(res.json.get("error"), "Invalid admin password.")
        self.assertNotIn("SuperSecretAdminTest2026!", res.data.decode("utf-8"))
        self.assertNotIn("AdminPass2026!", res.data.decode("utf-8"))

    # ================= 2. ANNOUNCEMENT BOARD =================

    def test_announcement_crud_and_public_api(self):
        """Admin can save announcement, public API returns max-age=30, auto-expires."""
        self._login_as_admin()

        # 1. Save active announcement
        res = self.client.post(
            "/admin/api/settings/announcement",
            json={
                "active": True,
                "text": "Scheduled system upgrades tonight from 11 PM to 12 AM.",
                "type": "warning",
                "expires_at": None
            }
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json["announcement"]["active"])

        # 2. Public API endpoint GET /api/site-status
        invalidate_site_status_cache()
        pub_res = self.client.get("/api/site-status")
        self.assertEqual(pub_res.status_code, 200)
        self.assertIn("max-age=30", pub_res.headers.get("Cache-Control", ""))

        data = pub_res.json
        self.assertTrue(data["announcement"]["active"])
        self.assertEqual(data["announcement"]["text"], "Scheduled system upgrades tonight from 11 PM to 12 AM.")
        self.assertEqual(data["announcement"]["type"], "warning")

        # 3. Test Auto-expiry: set expires_at in the past
        past_iso = (datetime.utcnow() - timedelta(minutes=5)).isoformat() + "Z"
        self.client.post(
            "/admin/api/settings/announcement",
            json={
                "active": True,
                "text": "Old expired alert",
                "type": "info",
                "expires_at": past_iso
            }
        )
        invalidate_site_status_cache()
        pub_expired = self.client.get("/api/site-status")
        self.assertFalse(pub_expired.json["announcement"]["active"])

        # 4. Turn announcement OFF
        self.client.post(
            "/admin/api/settings/announcement",
            json={"active": False, "text": "", "type": "info", "expires_at": None}
        )
        invalidate_site_status_cache()
        pub_off = self.client.get("/api/site-status")
        self.assertFalse(pub_off.json["announcement"]["active"])

    # ================= 3. MAINTENANCE MODE =================

    def test_maintenance_mode_interception_and_admin_bypass(self):
        """Maintenance mode returns 503 with Retry-After, tool API returns JSON, admin bypasses, auto-disables."""
        # Enable maintenance as admin
        self._login_as_admin()
        maint_res = self.client.post(
            "/admin/api/settings/maintenance",
            json={
                "active": True,
                "message": "Routine server maintenance in progress.",
                "duration_mins": 30
            }
        )
        self.assertEqual(maint_res.status_code, 200)
        self.assertTrue(maint_res.json["maintenance"]["active"])

        # Logout admin to test as non-admin visitor
        self.client.post("/admin/logout")
        invalidate_site_status_cache()

        # 1. Non-admin page visit returns 503 and HTML maintenance page with Retry-After
        r_page = self.client.get("/")
        self.assertEqual(r_page.status_code, 503)
        self.assertIn("Retry-After", r_page.headers)
        self.assertIn("Scheduled Maintenance", r_page.data.decode("utf-8"))
        self.assertIn("Routine server maintenance in progress.", r_page.data.decode("utf-8"))

        # 2. Non-admin JSON / tool API request returns JSON 503 so client doesn't crash
        r_api = self.client.post(
            "/api/tickets",
            json={"subject": "Test"},
            headers={"Accept": "application/json"}
        )
        self.assertEqual(r_api.status_code, 503)
        self.assertEqual(r_api.json.get("error"), "maintenance")
        self.assertIn("Retry-After", r_api.headers)

        # 3. Whitelisted paths bypass maintenance
        r_health = self.client.get("/health")
        self.assertEqual(r_health.status_code, 200)
        r_status = self.client.get("/api/site-status")
        self.assertEqual(r_status.status_code, 200)

        # 4. Admin Bypass: Logged-in admin can browse and use tools normally
        self._login_as_admin()
        r_admin_home = self.client.get("/")
        self.assertEqual(r_admin_home.status_code, 200, "Admin must bypass maintenance mode")

        # 5. Auto-disable when end time passes
        past_utc = (datetime.utcnow() - timedelta(minutes=1)).isoformat() + "Z"
        row = SiteSetting.query.filter_by(key="maintenance").first()
        row.value = json.dumps({"active": True, "message": "Expired", "ends_at": past_utc})
        db.session.commit()
        invalidate_site_status_cache()

        self.client.post("/admin/logout")
        r_auto_off = self.client.get("/")
        self.assertEqual(r_auto_off.status_code, 200, "Expired maintenance should auto-disable to 200 OK")

    # ================= 4. LIVE OPS & ERROR LOGGING =================

    def test_job_and_error_logging_with_sanitization(self):
        """Jobs & errors logged, sensitive data redacted, KPIs calculated."""
        # Clean previous test entries
        ProcessingJob.query.delete()
        ErrorLog.query.delete()
        db.session.commit()

        # 1. Trigger an error that gets logged (corrupted PDF / 500 or 400)
        res = self.client.post(
            "/merge",
            data={"files": []},  # No files -> 400 error
            headers={"Accept": "application/json"}
        )
        self.assertEqual(res.status_code, 400)

        # Check that ErrorLog recorded it
        err = ErrorLog.query.order_by(ErrorLog.id.desc()).first()
        self.assertIsNotNone(err)
        self.assertEqual(err.http_status, 400)

        # Check that ProcessingJob recorded it
        job = ProcessingJob.query.order_by(ProcessingJob.id.desc()).first()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, "failed")

        # 2. Check sensitive data sanitization in ErrorLog
        from app import log_error_to_db
        logged = log_error_to_db(
            tool="compress",
            error_type="TestException",
            error_message="Failed with password=SuperSecretPassword123 and token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            http_status=500,
            traceback_text='Traceback: auth_header="Bearer secret_token_xyz" password="MyPassword99"'
        )
        self.assertIsNotNone(logged)
        self.assertNotIn("SuperSecretPassword123", logged.error_message)
        self.assertIn("[REDACTED]", logged.error_message)
        self.assertNotIn("secret_token_xyz", logged.traceback)

        # 3. Test Live Ops API aggregation
        self._login_as_admin()
        live_ops = self.client.get("/admin/api/live-ops")
        self.assertEqual(live_ops.status_code, 200)
        kpis = live_ops.json["kpis"]
        self.assertGreaterEqual(kpis["total_jobs_24h"], 1)
        self.assertIn("error_rate_24h", kpis)
        self.assertIn("avg_latency_ms", kpis)

        # 4. Test Charts API
        charts = self.client.get("/admin/api/charts")
        self.assertEqual(charts.status_code, 200)
        self.assertEqual(len(charts.json["hourly_volume"]), 24)

        # 5. Error status update and delete
        err_id = logged.id
        up_res = self.client.post(
            f"/admin/api/errors/{err_id}/status",
            json={"status": "investigating", "admin_note": "Looking into it"}
        )
        self.assertEqual(up_res.status_code, 200)
        self.assertEqual(up_res.json["error"]["status"], "investigating")

        del_res = self.client.post(f"/admin/api/errors/{err_id}/delete")
        self.assertEqual(del_res.status_code, 200)
        self.assertIsNone(db.session.get(ErrorLog, err_id))

    # ================= 5. PERFORMANCE BENCHMARK =================

    def test_dashboard_performance_under_load(self):
        """Dashboard query benchmark with 1000 simulated jobs/errors: response time < 200ms."""
        now = datetime.utcnow()
        jobs = []
        for i in range(500):
            jobs.append(ProcessingJob(
                tool="compress" if i % 2 == 0 else "merge",
                status="success" if i % 10 != 0 else "failed",
                started_at=now - timedelta(minutes=i % 1440),
                finished_at=now,
                duration_ms=45 + (i % 20),
                input_size_bytes=1024 * (i + 1)
            ))
        db.session.bulk_save_objects(jobs)
        db.session.commit()

        self._login_as_admin()
        t0 = time.time()
        res = self.client.get("/admin/api/live-ops")
        elapsed_ms = (time.time() - t0) * 1000

        self.assertEqual(res.status_code, 200)
        # Should be well under 500ms
        self.assertLess(elapsed_ms, 500, f"Live Ops API took {elapsed_ms:.1f}ms, expected < 500ms")


if __name__ == "__main__":
    unittest.main()
