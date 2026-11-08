"""
captcha/provider.py
Captcha provider abstraction supporting "custom" (default), "turnstile", and "recaptcha".
Selected via CAPTCHA_PROVIDER environment variable.
"""

import os
import json
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from flask import Request

from captcha.service import captcha_service
from captcha.config import FORM_HONEYPOT_NAME, HONEYPOT_FIELD_NAME


class BaseCaptchaProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def get_client_config(self) -> dict:
        """Returns provider metadata and public site keys for the frontend."""
        pass

    @abstractmethod
    def verify_token(self, token: str, remote_ip: str = None) -> tuple[bool, str | None]:
        """Validates and burns the CAPTCHA token. Returns (is_valid, error_message)."""
        pass

    def verify_request(self, request: Request) -> tuple[bool, str | None]:
        """
        Extracts token and honeypot values from a Flask request (JSON payload or Form data).
        Rejects immediately if honeypot is triggered.
        """
        payload = request.get_json(silent=True) if request.is_json else request.form

        # 1. Honeypot check
        if payload.get(FORM_HONEYPOT_NAME) or payload.get(HONEYPOT_FIELD_NAME):
            return False, "Bot activity detected."

        # 2. Extract captcha token
        token = (
            payload.get("captchaToken")
            or payload.get("captcha_token")
            or payload.get("cf-turnstile-response")
            or payload.get("g-recaptcha-response")
            or request.headers.get("X-Captcha-Token")
            or ""
        )

        remote_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "127.0.0.1").split(",")[0].strip()
        return self.verify_token(token, remote_ip=remote_ip)


class CustomCaptchaProvider(BaseCaptchaProvider):
    @property
    def name(self) -> str:
        return "custom"

    def get_client_config(self) -> dict:
        return {
            "provider": "custom",
        }

    def verify_token(self, token: str, remote_ip: str = None) -> tuple[bool, str | None]:
        return captcha_service.verify_token(token, ip=remote_ip)


class TurnstileCaptchaProvider(BaseCaptchaProvider):
    @property
    def name(self) -> str:
        return "turnstile"

    def __init__(self):
        self.site_key = os.environ.get("TURNSTILE_SITE_KEY", "").strip()
        self.secret_key = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()

    def get_client_config(self) -> dict:
        return {
            "provider": "turnstile",
            "siteKey": self.site_key,
        }

    def verify_token(self, token: str, remote_ip: str = None) -> tuple[bool, str | None]:
        if not token or not token.strip():
            return False, "Cloudflare Turnstile verification required."
        if not self.secret_key:
            # Fallback if unconfigured in development
            return True, None

        url = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
        post_data = {
            "secret": self.secret_key,
            "response": token.strip(),
        }
        if remote_ip:
            post_data["remoteip"] = remote_ip

        data = urllib.parse.urlencode(post_data).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "FunkFiles/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("success"):
                    return True, None
                return False, "Cloudflare Turnstile verification failed."
        except Exception as e:
            return False, f"Turnstile verification error: {e}"


class RecaptchaCaptchaProvider(BaseCaptchaProvider):
    @property
    def name(self) -> str:
        return "recaptcha"

    def __init__(self):
        self.site_key = os.environ.get("RECAPTCHA_SITE_KEY", "").strip()
        self.secret_key = os.environ.get("RECAPTCHA_SECRET_KEY", "").strip()

    def get_client_config(self) -> dict:
        return {
            "provider": "recaptcha",
            "siteKey": self.site_key,
        }

    def verify_token(self, token: str, remote_ip: str = None) -> tuple[bool, str | None]:
        if not token or not token.strip():
            return False, "Google reCAPTCHA verification required."
        if not self.secret_key:
            # Fallback if unconfigured in development
            return True, None

        url = "https://www.google.com/recaptcha/api/siteverify"
        post_data = {
            "secret": self.secret_key,
            "response": token.strip(),
        }
        if remote_ip:
            post_data["remoteip"] = remote_ip

        data = urllib.parse.urlencode(post_data).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "FunkFiles/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("success"):
                    return True, None
                return False, "Google reCAPTCHA verification failed."
        except Exception as e:
            return False, f"reCAPTCHA verification error: {e}"


def get_captcha_provider() -> BaseCaptchaProvider:
    provider_name = os.environ.get("CAPTCHA_PROVIDER", "custom").strip().lower()
    if provider_name == "turnstile":
        return TurnstileCaptchaProvider()
    elif provider_name == "recaptcha":
        return RecaptchaCaptchaProvider()
    return CustomCaptchaProvider()
