"""
tests/test_auth_retry_flow.py
Tests verifying the login/signup retry flow and password toggle requirements:
1. Wrong password then retry works with fresh token (status 401, generic error message).
2. Wrong CAPTCHA then retry works (status 400, recovery with fresh challenge).
3. Used/burned token cannot be replayed on retry; fresh token is required.
4. Cooldown message and 429 status code triggered after failed attempts.
5. Harder 4x4 CAPTCHA challenge generated after 3 failed logins.
6. Password toggle accessibility attributes, type="button", and show/hide functionality.
"""

import time
import uuid
import unittest
from app import app
from models import db, User
from captcha.service import captcha_service
from captcha.rate_limiter import rate_limiter
from captcha.storage import storage
from extensions import limiter


class AuthRetryFlowTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.app_context = app.app_context()
        self.app_context.push()
        self.client = app.test_client()

        # Reset rate limiting and failed logins
        storage.reset_all()
        captcha_service.reset_failed_logins("127.0.0.1")
        try:
            limiter.reset()
        except Exception:
            pass

        # Create a test user
        self.username = f"retry_user_{uuid.uuid4().hex[:6]}"
        self.password = "CorrectPass123!"
        self.email = f"{self.username}@example.com"

        user = User(username=self.username, email=self.email, is_active=True)
        user.set_password(self.password)
        db.session.add(user)
        db.session.commit()

    def tearDown(self):
        self.app_context.pop()

    def _get_valid_captcha_token(self):
        """Helper to solve a CAPTCHA challenge and acquire a signed token."""
        res = self.client.get("/api/captcha/challenge")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        cid = data["challengeId"]

        # Fast forward time-to-submit check (2.5s)
        captcha_service._challenges[cid]["created_at"] -= 2.5
        correct_indices = list(captcha_service._challenges[cid]["correct_indices"])

        v_res = self.client.post("/api/captcha/verify", json={
            "challengeId": cid,
            "selectedIndexes": correct_indices,
        })
        self.assertEqual(v_res.status_code, 200)
        return v_res.get_json()["captchaToken"]

    def test_wrong_password_then_retry_works_without_reload(self):
        """
        Attempt 1: Wrong password returns 401 with generic error message ('Invalid username or password.').
        Token is burned on Attempt 1.
        Attempt 2: Immediate retry with fresh token and correct password succeeds with 200 OK.
        """
        token1 = self._get_valid_captcha_token()

        # 1. Attempt with wrong password
        res1 = self.client.post("/login", json={
            "username": self.username,
            "password": "WrongPassword999!",
            "captchaToken": token1,
        })
        self.assertEqual(res1.status_code, 401)
        data1 = res1.get_json()
        self.assertEqual(data1.get("error"), "Invalid username or password.")

        # 2. Replaying token1 fails because token is burned after attempt
        replay_res = self.client.post("/login", json={
            "username": self.username,
            "password": self.password,
            "captchaToken": token1,
        })
        self.assertEqual(replay_res.status_code, 400)
        self.assertIn("already been used", replay_res.get_json()["error"])

        # 3. Fresh token acquired without reloading page
        token2 = self._get_valid_captcha_token()
        res2 = self.client.post("/login", json={
            "username": self.username,
            "password": self.password,
            "captchaToken": token2,
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.get_json()
        self.assertEqual(data2.get("username"), self.username)

    def test_wrong_captcha_then_retry_works(self):
        """
        Attempting verify with wrong selections returns 400.
        Client requests a new challenge, solves it correctly, and signs in successfully.
        """
        res = self.client.get("/api/captcha/challenge")
        cid = res.get_json()["challengeId"]
        captcha_service._challenges[cid]["created_at"] -= 2.5

        # Intentionally wrong indexes
        wrong_indexes = [999]
        v_fail = self.client.post("/api/captcha/verify", json={
            "challengeId": cid,
            "selectedIndexes": wrong_indexes,
        })
        self.assertEqual(v_fail.status_code, 400)
        self.assertFalse(v_fail.get_json()["success"])

        # Recovery: request new challenge and solve
        token = self._get_valid_captcha_token()
        login_res = self.client.post("/login", json={
            "username": self.username,
            "password": self.password,
            "captchaToken": token,
        })
        self.assertEqual(login_res.status_code, 200)

    def test_failed_attempts_trigger_cooldown_429(self):
        """
        Triggering cooldown via rate limits returns HTTP 429 with cooldown duration.
        """
        # Set cooldown directly in storage for test
        storage.reset_all()
        now = time.time()
        with storage._get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO cooldowns (ip, cooldown_until) VALUES (?, ?)", ("127.0.0.1", now + 120))

        # Check /api/captcha/challenge returns 429
        ch_res = self.client.get("/api/captcha/challenge")
        self.assertEqual(ch_res.status_code, 429)

        # Check /login returns 429 with cooldown info
        login_res = self.client.post("/login", json={
            "username": self.username,
            "password": self.password,
            "captchaToken": "fake_token",
        })
        self.assertEqual(login_res.status_code, 429)
        self.assertIn("Too many attempts", login_res.get_json()["error"])
        self.assertIn("cooldown", login_res.get_json())
        self.assertGreater(login_res.get_json()["cooldown"], 0)

    def test_harder_challenge_after_3_failed_logins(self):
        """
        After 3 failed login attempts, the next CAPTCHA challenge is escalated to 4x4 (16 tiles).
        """
        for _ in range(3):
            token = self._get_valid_captcha_token()
            self.client.post("/login", json={
                "username": self.username,
                "password": "BadPassword",
                "captchaToken": token,
            })

        # Next challenge should now have gridSize == 4
        ch_res = self.client.get("/api/captcha/challenge")
        self.assertEqual(ch_res.status_code, 200)
        ch_data = ch_res.get_json()
        self.assertEqual(ch_data["gridSize"], 4)
        self.assertEqual(len(ch_data["images"]), 16)

    def test_signup_retry_flow(self):
        """
        Signing up with an existing username returns 409 without crashing.
        Submitting with a new username and fresh token immediately succeeds.
        """
        token1 = self._get_valid_captcha_token()

        # 1. Existing username fails
        res1 = self.client.post("/register", json={
            "username": self.username,
            "email": "different@example.com",
            "password": "NewPassword123!",
            "captchaToken": token1,
        })
        self.assertEqual(res1.status_code, 409)
        self.assertIn("already taken", res1.get_json()["error"])

        # 2. Retry with unique username and fresh token succeeds
        token2 = self._get_valid_captcha_token()
        new_user = f"unique_{uuid.uuid4().hex[:6]}"
        res2 = self.client.post("/register", json={
            "username": new_user,
            "email": f"{new_user}@example.com",
            "password": "NewPassword123!",
            "captchaToken": token2,
        })
        self.assertEqual(res2.status_code, 201)
        self.assertEqual(res2.get_json()["username"], new_user)

    def test_password_toggle_files_and_markup(self):
        """
        Verifies presence and attributes of the show/hide password toggle:
        - static/password-toggle.js exists with 44px touch target, aria-label, aria-pressed, type="button", 30s auto-hide.
        - static/captcha.css contains required styling and theme tokens.
        - templates/login.html, templates/index.html, templates/admin.html include password-toggle.js.
        """
        import os
        from pathlib import Path

        base = Path(app.root_path)

        # 1. Check static/password-toggle.js
        toggle_js_path = base / "static" / "password-toggle.js"
        self.assertTrue(toggle_js_path.exists(), "password-toggle.js missing")
        with open(toggle_js_path, "r", encoding="utf-8") as f:
            js_code = f.read()

        self.assertIn('aria-label', js_code)
        self.assertIn('aria-pressed', js_code)
        self.assertIn('type = \'button\'', js_code)
        self.assertIn('30000', js_code)  # 30-second auto-hide
        self.assertIn('submit', js_code)  # auto-hide on form submit

        # 2. Check static/captcha.css
        css_path = base / "static" / "captcha.css"
        with open(css_path, "r", encoding="utf-8") as f:
            css_code = f.read()

        self.assertIn(".password-input-wrap", css_code)
        self.assertIn(".password-toggle-btn", css_code)
        self.assertIn("44px", css_code)  # mobile touch target
        self.assertIn('data-theme="batman"', css_code)
        self.assertIn('data-theme="dark"', css_code)

        # 3. Check templates inclusion
        for tmpl in ["login.html", "index.html", "admin.html"]:
            t_path = base / "templates" / tmpl
            with open(t_path, "r", encoding="utf-8") as f:
                html = f.read()
            self.assertIn("password-toggle.js", html, f"{tmpl} missing password-toggle.js inclusion")

    def test_admin_panel_security_and_accessibility(self):
        """Verify default password is removed, admin routes work, and unauthenticated access redirects."""
        import os
        from pathlib import Path
        from admin import get_admin_password

        # 1. No hardcoded default password in codebase
        base = Path(app.root_path)
        for rel in ["admin.py", "app.py", "auth.py", "templates/index.html", "templates/admin.html"]:
            p = base / rel
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    content = f.read()
                self.assertNotIn("AdminPass2026!", content, f"{rel} still contains old hardcoded default password")

        # 2. Unauthenticated access to /admin and /admin/ returns 404 Not Found (never redirects)
        r1 = self.client.get("/admin")
        self.assertEqual(r1.status_code, 404, "Unauthenticated /admin must return 404 Not Found")

        r2 = self.client.get("/admin/")
        self.assertEqual(r2.status_code, 404, "Unauthenticated /admin/ must return 404 Not Found")

        # 3. With ADMIN_PASSWORD set, verify authentication flow
        orig_pass = os.environ.get("ADMIN_PASSWORD")
        try:
            os.environ["ADMIN_PASSWORD"] = "Secr3tAdminKey!"
            self.assertEqual(get_admin_password(), "Secr3tAdminKey!")

            # Wrong password returns 401 and does NOT leak password
            import re
            r_login_page = self.client.get("/admin/login")
            self.assertEqual(r_login_page.status_code, 200)
            m = re.search(r'name="csrf-token" content="([^"]+)"', r_login_page.data.decode("utf-8"))
            csrf = m.group(1) if m else None

            r_wrong = self.client.post(
                "/admin/login",
                json={"password": "WrongPassword99!"},
                headers={"X-CSRFToken": csrf, "Accept": "application/json"}
            )
            self.assertEqual(r_wrong.status_code, 401)
            self.assertEqual(r_wrong.json.get("error"), "Invalid admin password.")

            # Correct password returns 200 and logs in
            r_ok = self.client.post(
                "/admin/login",
                json={"password": "Secr3tAdminKey!"},
                headers={"X-CSRFToken": csrf, "Accept": "application/json"}
            )
            self.assertEqual(r_ok.status_code, 200)
            self.assertTrue(r_ok.json.get("ok"))

            # Dashboard now loads 200
            r_dash = self.client.get("/admin")
            self.assertEqual(r_dash.status_code, 200)

            # Trailing slash also loads 200
            r_dash_slash = self.client.get("/admin/")
            self.assertEqual(r_dash_slash.status_code, 200)

            # Logout
            r_logout = self.client.post("/admin/logout", headers={"X-CSRFToken": csrf})
            self.assertEqual(r_logout.status_code, 302)

            # Back to redirecting
            r_dash_after = self.client.get("/admin")
            self.assertEqual(r_dash_after.status_code, 404, "After logout, /admin must return 404 Not Found")
        finally:
            if orig_pass is not None:
                os.environ["ADMIN_PASSWORD"] = orig_pass
            elif "ADMIN_PASSWORD" in os.environ:
                del os.environ["ADMIN_PASSWORD"]


if __name__ == "__main__":
    unittest.main()
