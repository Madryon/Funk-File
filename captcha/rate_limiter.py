"""
captcha/rate_limiter.py
Thread-safe rate limiter and cooldown tracker for CAPTCHA requests and verification attempts.
Rules:
- 10 challenges / minute per IP
- 5 failed verifications / 10 minutes per IP -> 5 minute cooldown
"""

import time
import threading
from collections import defaultdict
from captcha.config import (
    MAX_CHALLENGES_PER_MINUTE,
    MAX_FAILED_VERIFICATIONS,
    FAILED_VERIFICATION_WINDOW_SECONDS,
    COOLDOWN_SECONDS,
)


class CaptchaRateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._challenges = defaultdict(list)        # ip -> list of timestamps
        self._failed_verifications = defaultdict(list)  # ip -> list of timestamps
        self._cooldown_until = {}                   # ip -> timestamp

    def can_request_challenge(self, ip: str) -> tuple[bool, str | None]:
        with self._lock:
            now = time.time()

            # Check if in cooldown
            if ip in self._cooldown_until:
                remaining = int(self._cooldown_until[ip] - now)
                if remaining > 0:
                    return False, f"Too many failed verification attempts. Please wait {remaining} seconds."
                else:
                    del self._cooldown_until[ip]

            # Prune challenges older than 60s
            cutoff = now - 60.0
            self._challenges[ip] = [t for t in self._challenges[ip] if t > cutoff]

            if len(self._challenges[ip]) >= MAX_CHALLENGES_PER_MINUTE:
                return False, "Challenge rate limit exceeded (maximum 10 per minute). Please try again shortly."

            self._challenges[ip].append(now)
            return True, None

    def record_verification_failure(self, ip: str) -> tuple[bool, int]:
        """
        Records a failed verification. If failures exceed the threshold within
        the window, puts the IP on cooldown.
        Returns: (is_now_in_cooldown, cooldown_seconds_remaining)
        """
        with self._lock:
            now = time.time()
            cutoff = now - FAILED_VERIFICATION_WINDOW_SECONDS
            self._failed_verifications[ip] = [t for t in self._failed_verifications[ip] if t > cutoff]
            self._failed_verifications[ip].append(now)

            if len(self._failed_verifications[ip]) >= MAX_FAILED_VERIFICATIONS:
                self._cooldown_until[ip] = now + COOLDOWN_SECONDS
                self._failed_verifications[ip].clear()
                return True, COOLDOWN_SECONDS

            return False, 0

    def is_in_cooldown(self, ip: str) -> tuple[bool, int]:
        with self._lock:
            now = time.time()
            if ip in self._cooldown_until:
                remaining = int(self._cooldown_until[ip] - now)
                if remaining > 0:
                    return True, remaining
                del self._cooldown_until[ip]
            return False, 0

    def record_verification_success(self, ip: str) -> None:
        """Resets failed verification counts on successful verification."""
        with self._lock:
            self._failed_verifications.pop(ip, None)
            self._cooldown_until.pop(ip, None)

    def reset(self) -> None:
        """Resets all rate limiting state (useful for tests)."""
        with self._lock:
            self._challenges.clear()
            self._failed_verifications.clear()
            self._cooldown_until.clear()


# Global singleton instance
rate_limiter = CaptchaRateLimiter()
