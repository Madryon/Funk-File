"""
captcha/rate_limiter.py
Rate limiter and cooldown tracker for CAPTCHA requests and verification attempts.
Delegates to multi-worker persistent storage so rate limits are process-safe.
"""

from captcha.storage import storage


class CaptchaRateLimiter:
    def can_request_challenge(self, ip: str) -> tuple[bool, str | None]:
        return storage.can_request_challenge(ip)

    def record_verification_failure(self, ip: str) -> tuple[bool, int]:
        return storage.record_verification_failure(ip)

    def is_in_cooldown(self, ip: str) -> tuple[bool, int]:
        return storage.is_in_cooldown(ip)

    def record_verification_success(self, ip: str) -> None:
        storage.record_verification_success(ip)

    def reset(self) -> None:
        storage.reset_all()


# Global singleton instance
rate_limiter = CaptchaRateLimiter()
