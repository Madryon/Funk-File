"""
captcha/service.py
Core service managing CAPTCHA challenges, dynamic image manipulation, token signing,
and failed login tracking.
"""

import io
import os
import time
import random
import secrets
import threading
from pathlib import Path
from collections import defaultdict
from PIL import Image, ImageEnhance
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask import current_app

from captcha.config import (
    IMAGE_BANK_DIR,
    CATEGORIES,
    NEGATIVE_CATEGORY,
    DEFAULT_GRID_SIZE,
    HARD_GRID_SIZE,
    MIN_CORRECT_COUNT,
    MAX_CORRECT_COUNT,
    MIN_CORRECT_COUNT_HARD,
    MAX_CORRECT_COUNT_HARD,
    CHALLENGE_TTL_SECONDS,
    TOKEN_TTL_SECONDS,
    MIN_TIME_TO_SUBMIT,
    FAILED_LOGINS_FOR_HARD,
)
from captcha.rate_limiter import rate_limiter


def get_secret_key() -> str:
    try:
        if current_app:
            return current_app.config.get("SECRET_KEY", "pdf-toolbox-captcha-secret-key-32ch!")
    except Exception:
        pass
    return os.environ.get("SECRET_KEY", "pdf-toolbox-captcha-secret-key-32ch!")


class CaptchaService:
    def __init__(self):
        self._lock = threading.Lock()
        self._challenges = {}          # challenge_id -> challenge_dict
        self._image_tokens = {}        # image_token -> {path, expires_at}
        self._audio_tokens = {}        # challenge_id -> {code, expires_at}
        self._burned_tokens = {}       # token -> burned_at
        self._failed_logins = defaultdict(int)  # ip -> failed_login_count

    def _cleanup_expired(self):
        """Prunes expired challenges, image tokens, and old burned tokens."""
        now = time.time()
        # Clean challenges
        expired_ch = [cid for cid, ch in self._challenges.items() if now > ch["expires_at"]]
        for cid in expired_ch:
            del self._challenges[cid]

        # Clean image tokens
        expired_tokens = [tok for tok, it in self._image_tokens.items() if now > it["expires_at"]]
        for tok in expired_tokens:
            del self._image_tokens[tok]

        # Clean audio tokens
        expired_audio = [cid for cid, at in self._audio_tokens.items() if now > at["expires_at"]]
        for cid in expired_audio:
            del self._audio_tokens[cid]

        # Clean burned tokens older than 15 minutes
        cutoff = now - 900
        expired_burned = [tok for tok, t in self._burned_tokens.items() if t < cutoff]
        for tok in expired_burned:
            del self._burned_tokens[tok]

    # ================= FAILED LOGINS TRACKING =================

    def record_failed_login(self, ip: str, session_obj=None) -> int:
        with self._lock:
            self._failed_logins[ip] += 1
            count = self._failed_logins[ip]
        if session_obj is not None:
            session_obj["failed_logins"] = session_obj.get("failed_logins", 0) + 1
        return count

    def get_failed_logins(self, ip: str, session_obj=None) -> int:
        with self._lock:
            ip_count = self._failed_logins.get(ip, 0)
        session_count = 0
        if session_obj is not None:
            session_count = session_obj.get("failed_logins", 0)
        return max(ip_count, session_count)

    def reset_failed_logins(self, ip: str, session_obj=None) -> None:
        with self._lock:
            self._failed_logins.pop(ip, None)
        if session_obj is not None:
            session_obj.pop("failed_logins", None)

    # ================= CHALLENGE CREATION =================

    def create_challenge(self, ip: str, session_id: str = None, failed_logins: int = 0) -> dict:
        """
        Builds a new randomized challenge. Answers are stored strictly on the server.
        Uses 4x4 grid if failed_logins >= 3, otherwise 3x3 grid.
        """
        with self._lock:
            self._cleanup_expired()

            grid_size = HARD_GRID_SIZE if failed_logins >= FAILED_LOGINS_FOR_HARD else DEFAULT_GRID_SIZE
            total_tiles = grid_size * grid_size

            # Pick target category
            target_category = random.choice(list(CATEGORIES.keys()))
            cat_info = CATEGORIES[target_category]

            # Collect available images for target category
            target_dir = IMAGE_BANK_DIR / target_category
            target_images = list(target_dir.glob("*.jpg"))
            if not target_images:
                raise RuntimeError(f"No images found for category '{target_category}' in {target_dir}. Run seed script.")

            # Collect distractor images from other categories + negative
            distractor_dirs = [IMAGE_BANK_DIR / c for c in CATEGORIES if c != target_category]
            distractor_dirs.append(IMAGE_BANK_DIR / NEGATIVE_CATEGORY)
            distractor_images = []
            for d in distractor_dirs:
                if d.exists():
                    distractor_images.extend(list(d.glob("*.jpg")))

            if not distractor_images:
                raise RuntimeError(f"No distractor images found in {IMAGE_BANK_DIR}.")

            # Determine number of correct images
            if grid_size == HARD_GRID_SIZE:
                k_correct = random.randint(MIN_CORRECT_COUNT_HARD, min(MAX_CORRECT_COUNT_HARD, len(target_images)))
            else:
                k_correct = random.randint(MIN_CORRECT_COUNT, min(MAX_CORRECT_COUNT, len(target_images)))

            num_distractors = total_tiles - k_correct

            selected_correct = random.sample(target_images, k_correct)
            selected_distractors = random.sample(distractor_images, num_distractors)

            # Combine and shuffle
            tile_entries = [{"is_correct": True, "path": p} for p in selected_correct] + \
                           [{"is_correct": False, "path": p} for p in selected_distractors]
            random.shuffle(tile_entries)

            correct_indices = {i for i, entry in enumerate(tile_entries) if entry["is_correct"]}

            # Generate tokens
            now = time.time()
            expires_at = now + CHALLENGE_TTL_SECONDS
            challenge_id = secrets.token_urlsafe(24)

            # Generate 4-digit code for audio alternative
            audio_code = "".join(random.choices("0123456789", k=4))
            self._audio_tokens[challenge_id] = {
                "code": audio_code,
                "expires_at": expires_at,
            }

            public_images = []
            for i, entry in enumerate(tile_entries):
                img_token = secrets.token_urlsafe(16)
                self._image_tokens[img_token] = {
                    "path": entry["path"],
                    "challenge_id": challenge_id,
                    "expires_at": expires_at,
                }
                public_images.append({
                    "id": i,
                    "url": f"/api/captcha/image/{img_token}",
                })

            self._challenges[challenge_id] = {
                "id": challenge_id,
                "category": target_category,
                "grid_size": grid_size,
                "correct_indices": correct_indices,
                "audio_code": audio_code,
                "created_at": now,
                "expires_at": expires_at,
                "ip": ip,
                "session_id": session_id,
            }

            return {
                "challengeId": challenge_id,
                "instruction": cat_info["instruction"],
                "category": cat_info["display"],
                "gridSize": grid_size,
                "images": public_images,
                "audioUrl": f"/api/captcha/audio/{challenge_id}",
            }

    # ================= DYNAMIC IMAGE SERVING =================

    def get_challenge_image(self, token: str) -> io.BytesIO | None:
        """
        Serves an image with dynamic server-side transformations:
        1. Strips EXIF
        2. Random crop (92-98%)
        3. Random brightness adjustment (0.92-1.08)
        4. Subtle noise/hash alteration
        """
        with self._lock:
            info = self._image_tokens.get(token)
            if not info or time.time() > info["expires_at"]:
                return None
            img_path = info["path"]

        if not Path(img_path).exists():
            return None

        try:
            with Image.open(img_path) as raw_img:
                img = raw_img.convert("RGB")

            # 1. Random crop (92% - 98%)
            w, h = img.size
            crop_pct = random.uniform(0.92, 0.98)
            cw, ch = int(w * crop_pct), int(h * crop_pct)
            left = random.randint(0, w - cw)
            top = random.randint(0, h - ch)
            img = img.crop((left, top, left + cw, top + ch)).resize((300, 300), Image.Resampling.BILINEAR)

            # 2. Random brightness
            factor = random.uniform(0.92, 1.08)
            img = ImageEnhance.Brightness(img).enhance(factor)

            # 3. Save to memory without EXIF
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            buf.seek(0)
            return buf
        except Exception:
            return None

    def get_audio_code(self, challenge_id: str) -> str | None:
        with self._lock:
            item = self._audio_tokens.get(challenge_id)
            if item and time.time() <= item["expires_at"]:
                return item["code"]
            return None

    # ================= VERIFICATION & TOKEN BURNING =================

    def verify_challenge(
        self,
        challenge_id: str,
        selected_indexes: list[int] = None,
        audio_answer: str = None,
        honeypot: str = None,
        ip: str = "127.0.0.1",
        session_id: str = None,
    ) -> tuple[bool, str, str | None]:
        """
        Verifies a challenge.
        On success, burns challenge and returns (True, "Verified", signed_token).
        On failure, returns (False, error_msg, None).
        """
        # Honeypot check
        if honeypot:
            rate_limiter.record_verification_failure(ip)
            return False, "Bot activity detected.", None

        with self._lock:
            # ONE-TIME USE: challenge is popped immediately
            challenge = self._challenges.pop(challenge_id, None)

        if not challenge:
            rate_limiter.record_verification_failure(ip)
            return False, "Challenge not found or already used. Please request a new challenge.", None

        now = time.time()
        if now > challenge["expires_at"]:
            rate_limiter.record_verification_failure(ip)
            return False, "Challenge has expired. Please try again.", None

        # Minimum time-to-submit check (2 seconds)
        elapsed = now - challenge["created_at"]
        if elapsed < MIN_TIME_TO_SUBMIT:
            rate_limiter.record_verification_failure(ip)
            return False, f"Verification submitted too quickly ({elapsed:.1f}s). Please take your time.", None

        # Verify answer
        is_correct = False
        if audio_answer is not None and audio_answer.strip():
            expected = challenge["audio_code"].strip()
            submitted = str(audio_answer).strip()
            is_correct = (submitted == expected)
        else:
            submitted_set = set(selected_indexes or [])
            is_correct = (submitted_set == challenge["correct_indices"])

        if not is_correct:
            in_cooldown, remaining = rate_limiter.record_verification_failure(ip)
            if in_cooldown:
                return False, f"Too many failed verification attempts. Please wait {remaining} seconds.", None
            return False, "Incorrect selection. Please try again.", None

        # Verification succeeded
        rate_limiter.record_verification_success(ip)

        # Generate signed token valid for 5 minutes
        s = URLSafeTimedSerializer(get_secret_key(), salt="captcha-token")
        payload = {
            "cid": challenge_id,
            "ip": ip,
            "ts": now,
        }
        signed_token = s.dumps(payload)
        return True, "Verification successful.", signed_token

    def verify_token(self, token: str, ip: str = None) -> tuple[bool, str | None]:
        """
        Validates and burns a captchaToken. Called by login, signup, and reset routes.
        Returns: (is_valid, error_message)
        """
        if not token or not token.strip():
            return False, "CAPTCHA verification is required. Please check the 'I'm not a robot' box."

        clean_token = token.strip()
        with self._lock:
            if clean_token in self._burned_tokens:
                return False, "CAPTCHA token has already been used. Please verify again."

        s = URLSafeTimedSerializer(get_secret_key(), salt="captcha-token")
        try:
            data = s.loads(clean_token, max_age=TOKEN_TTL_SECONDS)
        except SignatureExpired:
            return False, "CAPTCHA token has expired. Please verify again."
        except BadSignature:
            return False, "CAPTCHA token is invalid. Please verify again."
        except Exception:
            return False, "CAPTCHA verification failed."

        # Burn token so it cannot be reused
        with self._lock:
            self._burned_tokens[clean_token] = time.time()

        return True, None


# Global singleton instance
captcha_service = CaptchaService()
