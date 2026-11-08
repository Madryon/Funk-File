"""
tests/test_captcha.py
Comprehensive test suite for the CAPTCHA system covering:
- Challenge generation (3x3 grid, random URLs, no answer leakage)
- Wrong answer fails
- Correct answer passes & issues signed token
- Minimum 2-second time-to-submit check
- Replayed token rejected
- Expired token rejected
- Missing token rejected on auth endpoints
- Honeypot check rejects bots
- Rate limit triggers after 10 challenges / min
- 5 failed verifications trigger 5-minute cooldown
- Audio alternative challenge generation & verification
- Harder (4x4) challenge after 3 failed logins
- End-to-end signup and login
"""

import time
import uuid
import unittest
from itsdangerous import URLSafeTimedSerializer

from app import app
from models import db, User
from captcha.service import captcha_service
from captcha.rate_limiter import rate_limiter


class CaptchaTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.client = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()

        # Reset rate limiter and failed logins before each test
        rate_limiter.reset()
        captcha_service.reset_failed_logins("127.0.0.1")

    def tearDown(self):
        self.app_context.pop()

    def test_challenge_creation(self):
        """GET /api/captcha/challenge returns challengeId, instruction, and 9 image URLs."""
        res = self.client.get("/api/captcha/challenge")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()

        self.assertIn("challengeId", data)
        self.assertIn("instruction", data)
        self.assertIn("category", data)
        self.assertEqual(data["gridSize"], 3)
        self.assertEqual(len(data["images"]), 9)
        self.assertIn("audioUrl", data)

        # Ensure no answer or category name is leaked in the image URLs
        for img in data["images"]:
            self.assertTrue(img["url"].startswith("/api/captcha/image/"))
            self.assertNotIn(data["category"], img["url"])

        # Fetch one image and verify valid JPEG is returned
        img_url = data["images"][0]["url"]
        img_res = self.client.get(img_url)
        self.assertEqual(img_res.status_code, 200)
        self.assertEqual(img_res.mimetype, "image/jpeg")
        self.assertGreater(len(img_res.data), 500)

    def test_wrong_answer_fails(self):
        """Submitting an incorrect image selection returns 400 and failure status."""
        res = self.client.get("/api/captcha/challenge")
        data = res.get_json()
        cid = data["challengeId"]

        # Fast-forward created_at by 2.5s so time-to-submit check passes
        captcha_service._challenges[cid]["created_at"] -= 2.5

        # Invert correct indices to guarantee a wrong answer
        correct = captcha_service._challenges[cid]["correct_indices"]
        wrong = [i for i in range(9) if i not in correct]
        if not wrong:
            wrong = [0]

        verify_res = self.client.post("/api/captcha/verify", json={
            "challengeId": cid,
            "selectedIndexes": wrong,
        })
        self.assertEqual(verify_res.status_code, 400)
        vdata = verify_res.get_json()
        self.assertFalse(vdata["success"])
        self.assertIn("Incorrect", vdata["error"])

    def test_correct_answer_passes(self):
        """Submitting correct image selection returns 200 and a signed captchaToken."""
        res = self.client.get("/api/captcha/challenge")
        data = res.get_json()
        cid = data["challengeId"]

        # Simulate user taking 3 seconds to solve
        captcha_service._challenges[cid]["created_at"] -= 3.0

        correct = list(captcha_service._challenges[cid]["correct_indices"])
        verify_res = self.client.post("/api/captcha/verify", json={
            "challengeId": cid,
            "selectedIndexes": correct,
        })
        self.assertEqual(verify_res.status_code, 200)
        vdata = verify_res.get_json()
        self.assertTrue(vdata["success"])
        self.assertIn("captchaToken", vdata)
        self.assertTrue(len(vdata["captchaToken"]) > 20)

    def test_time_to_submit_under_2_seconds(self):
        """Submitting challenge in under 2.0 seconds is rejected as too fast."""
        res = self.client.get("/api/captcha/challenge")
        data = res.get_json()
        cid = data["challengeId"]

        # Immediately submit without delay
        correct = list(captcha_service._challenges[cid]["correct_indices"])
        verify_res = self.client.post("/api/captcha/verify", json={
            "challengeId": cid,
            "selectedIndexes": correct,
        })
        self.assertEqual(verify_res.status_code, 400)
        vdata = verify_res.get_json()
        self.assertFalse(vdata["success"])
        self.assertIn("too quickly", vdata["error"])

    def test_replayed_token_rejected(self):
        """A captchaToken cannot be reused twice (burned upon first use)."""
        # Generate challenge and solve
        res = self.client.get("/api/captcha/challenge")
        cid = res.get_json()["challengeId"]
        captcha_service._challenges[cid]["created_at"] -= 3.0
        correct = list(captcha_service._challenges[cid]["correct_indices"])

        v_res = self.client.post("/api/captcha/verify", json={
            "challengeId": cid,
            "selectedIndexes": correct,
        })
        token = v_res.get_json()["captchaToken"]

        # First use on /register
        uname = f"user_{uuid.uuid4().hex[:8]}"
        reg_res = self.client.post("/register", json={
            "username": uname,
            "email": f"{uname}@example.com",
            "password": "Password123!",
            "captchaToken": token,
        })
        self.assertEqual(reg_res.status_code, 201)

        # Log out so second registration is unauthenticated
        self.client.post("/logout")

        # Second use (replay attack)
        replay_res = self.client.post("/register", json={
            "username": f"user2_{uuid.uuid4().hex[:8]}",
            "email": f"user2_{uuid.uuid4().hex[:8]}@example.com",
            "password": "Password123!",
            "captchaToken": token,
        })
        self.assertEqual(replay_res.status_code, 400)
        self.assertIn("already been used", replay_res.get_json()["error"])

    def test_expired_token_rejected(self):
        """A token older than 5 minutes (TOKEN_TTL_SECONDS) is rejected."""
        s = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="captcha-token")
        tok = s.dumps({"cid": "old", "ip": "127.0.0.1"})

        # Simulate time moving forward by 350 seconds
        future_time = time.time() + 350
        from unittest.mock import patch
        with patch("time.time", return_value=future_time):
            res = self.client.post("/login", json={
                "username": "testuser",
                "password": "password",
                "captchaToken": tok,
            })
        self.assertEqual(res.status_code, 400)
        self.assertIn("expired", res.get_json()["error"])

    def test_missing_token_rejected(self):
        """POST /login or /register without captchaToken returns 400."""
        res = self.client.post("/login", json={
            "username": "testuser",
            "password": "password",
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn("CAPTCHA verification is required", res.get_json()["error"])

    def test_honeypot_rejected(self):
        """Non-empty honeypot field causes immediate 400 bot detection."""
        # 1. In /api/captcha/verify
        res = self.client.get("/api/captcha/challenge")
        cid = res.get_json()["challengeId"]
        captcha_service._challenges[cid]["created_at"] -= 3.0
        correct = list(captcha_service._challenges[cid]["correct_indices"])

        v_res = self.client.post("/api/captcha/verify", json={
            "challengeId": cid,
            "selectedIndexes": correct,
            "website_hp": "I am a spam bot",
        })
        self.assertEqual(v_res.status_code, 400)
        self.assertIn("Bot", v_res.get_json()["error"])

        # 2. In /register form
        reg_res = self.client.post("/register", json={
            "username": "bot_user",
            "password": "password",
            "b_hp_check": "bot_fill",
            "captchaToken": "fake_token",
        })
        self.assertEqual(reg_res.status_code, 400)
        self.assertIn("Bot", reg_res.get_json()["error"])

    def test_rate_limit_challenges(self):
        """Requesting more than 10 challenges in 1 minute triggers 429."""
        for i in range(10):
            res = self.client.get("/api/captcha/challenge")
            self.assertEqual(res.status_code, 200)

        # 11th request triggers rate limit
        res_limit = self.client.get("/api/captcha/challenge")
        self.assertEqual(res_limit.status_code, 429)
        self.assertIn("rate limit exceeded", res_limit.get_json()["error"].lower())

    def test_failed_verifications_cooldown(self):
        """5 failed verifications within 10 minutes trigger 5-minute cooldown (429)."""
        for i in range(4):
            res = self.client.get("/api/captcha/challenge")
            cid = res.get_json()["challengeId"]
            captcha_service._challenges[cid]["created_at"] -= 3.0
            self.client.post("/api/captcha/verify", json={"challengeId": cid, "selectedIndexes": []})

        # 5th failure
        res = self.client.get("/api/captcha/challenge")
        cid = res.get_json()["challengeId"]
        captcha_service._challenges[cid]["created_at"] -= 3.0
        v5 = self.client.post("/api/captcha/verify", json={"challengeId": cid, "selectedIndexes": []})
        self.assertEqual(v5.status_code, 400)

        # Next challenge or verification attempt is blocked by cooldown (429)
        cooldown_res = self.client.get("/api/captcha/challenge")
        self.assertEqual(cooldown_res.status_code, 429)
        self.assertIn("wait", cooldown_res.get_json()["error"].lower())

    def test_audio_challenge_and_verification(self):
        """Audio alternative generates valid WAV and verifies correct numeric input."""
        res = self.client.get("/api/captcha/challenge")
        data = res.get_json()
        cid = data["challengeId"]

        # Fetch audio WAV
        audio_res = self.client.get(f"/api/captcha/audio/{cid}")
        self.assertEqual(audio_res.status_code, 200)
        self.assertEqual(audio_res.mimetype, "audio/wav")
        self.assertGreater(len(audio_res.data), 1000)

        # Verify with audioAnswer
        expected_code = captcha_service.get_audio_code(cid)
        captcha_service._challenges[cid]["created_at"] -= 3.0

        v_res = self.client.post("/api/captcha/verify", json={
            "challengeId": cid,
            "audioAnswer": expected_code,
        })
        self.assertEqual(v_res.status_code, 200)
        self.assertTrue(v_res.get_json()["success"])
        self.assertIn("captchaToken", v_res.get_json())

    def test_hard_challenge_after_3_failed_logins(self):
        """After 3 failed logins, the next challenge uses a 4x4 grid (16 tiles)."""
        # Record 3 failed logins
        for _ in range(3):
            captcha_service.record_failed_login("127.0.0.1")

        # Request challenge
        res = self.client.get("/api/captcha/challenge")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["gridSize"], 4)
        self.assertEqual(len(data["images"]), 16)

    def test_end_to_end_signup_and_login(self):
        """Complete user signup and login flow protected by CAPTCHA."""
        # 1. Signup
        res_ch1 = self.client.get("/api/captcha/challenge")
        cid1 = res_ch1.get_json()["challengeId"]
        captcha_service._challenges[cid1]["created_at"] -= 3.0
        correct1 = list(captcha_service._challenges[cid1]["correct_indices"])
        v_res1 = self.client.post("/api/captcha/verify", json={
            "challengeId": cid1,
            "selectedIndexes": correct1,
        })
        token1 = v_res1.get_json()["captchaToken"]

        uname = f"test_{uuid.uuid4().hex[:8]}"
        pwd = "SecurePassword123!"
        reg = self.client.post("/register", json={
            "username": uname,
            "email": f"{uname}@example.com",
            "password": pwd,
            "captchaToken": token1,
        })
        self.assertEqual(reg.status_code, 201)

        # Logout
        self.client.post("/logout")

        # 2. Login
        res_ch2 = self.client.get("/api/captcha/challenge")
        cid2 = res_ch2.get_json()["challengeId"]
        captcha_service._challenges[cid2]["created_at"] -= 3.0
        correct2 = list(captcha_service._challenges[cid2]["correct_indices"])
        v_res2 = self.client.post("/api/captcha/verify", json={
            "challengeId": cid2,
            "selectedIndexes": correct2,
        })
        token2 = v_res2.get_json()["captchaToken"]

        login_res = self.client.post("/login", json={
            "username": uname,
            "password": pwd,
            "captchaToken": token2,
        })
        self.assertEqual(login_res.status_code, 200)
        self.assertEqual(login_res.get_json()["username"], uname)

    def test_provider_switch(self):
        """Verifies CAPTCHA_PROVIDER environment variable selects the right provider."""
        import os
        from captcha.provider import get_captcha_provider

        # Default custom
        os.environ["CAPTCHA_PROVIDER"] = "custom"
        p_custom = get_captcha_provider()
        self.assertEqual(p_custom.name, "custom")
        self.assertEqual(p_custom.get_client_config()["provider"], "custom")

        # Turnstile
        os.environ["CAPTCHA_PROVIDER"] = "turnstile"
        os.environ["TURNSTILE_SITE_KEY"] = "0x4AAAAAAATestKey"
        p_turnstile = get_captcha_provider()
        self.assertEqual(p_turnstile.name, "turnstile")
        self.assertEqual(p_turnstile.get_client_config()["siteKey"], "0x4AAAAAAATestKey")

        # reCAPTCHA
        os.environ["CAPTCHA_PROVIDER"] = "recaptcha"
        os.environ["RECAPTCHA_SITE_KEY"] = "6LeTestKey"
        p_recaptcha = get_captcha_provider()
        self.assertEqual(p_recaptcha.name, "recaptcha")
        self.assertEqual(p_recaptcha.get_client_config()["siteKey"], "6LeTestKey")

        # Reset to custom
        os.environ["CAPTCHA_PROVIDER"] = "custom"


if __name__ == "__main__":
    unittest.main()
