"""
captcha/service.py
Core service managing CAPTCHA challenges, dynamic image manipulation, token signing,
startup bank validation, and multi-worker process-safe verification.
"""

import io
import os
import time
import random
import secrets
from pathlib import Path
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
    CATEGORY_TO_INDEX,
    INDEX_TO_CATEGORY,
)
from captcha.storage import storage


def get_secret_key() -> str:
    try:
        if current_app:
            return current_app.config.get("SECRET_KEY", "pdf-toolbox-secure-fallback-key-32chars!")
    except Exception:
        pass
    return os.environ.get("SECRET_KEY", "pdf-toolbox-secure-fallback-key-32chars!")


class _ChallengeDictProxy:
    def __init__(self, challenge_id: str):
        self.cid = challenge_id

    def __getitem__(self, item):
        ch = storage.get_challenge(self.cid)
        if not ch:
            raise KeyError(self.cid)
        return ch[item]

    def __setitem__(self, key, value):
        if key == "created_at":
            storage.update_challenge_created_at(self.cid, float(value))

    def __contains__(self, item):
        ch = storage.get_challenge(self.cid)
        return ch is not None and item in ch


class _ChallengesAccessor:
    def __getitem__(self, cid: str):
        ch = storage.get_challenge(cid)
        if not ch:
            raise KeyError(cid)
        return _ChallengeDictProxy(cid)

    def __contains__(self, cid: str):
        return storage.get_challenge(cid) is not None


class CaptchaService:
    def __init__(self):
        self._available_categories = {}
        self._challenges = _ChallengesAccessor()
        self._ensure_and_validate_bank()

    def _ensure_and_validate_bank(self):
        """
        Scans the image bank at startup. If any category has fewer than 40 valid images,
        automatically seeds them. Only categories with >= 40 valid images are made active.
        """
        IMAGE_BANK_DIR.mkdir(parents=True, exist_ok=True)
        need_seed = False

        for cat in CATEGORIES:
            cat_dir = IMAGE_BANK_DIR / cat
            if not cat_dir.exists() or len(list(cat_dir.glob("*.jpg"))) < 40:
                need_seed = True
                break

        neg_dir = IMAGE_BANK_DIR / NEGATIVE_CATEGORY
        if not neg_dir.exists() or len(list(neg_dir.glob("*.jpg"))) < 40:
            need_seed = True

        if need_seed:
            try:
                from seed_captcha_images import seed_and_validate
                print("[CAPTCHA] Auto-seeding missing or incomplete image bank...")
                seed_and_validate()
            except Exception as e:
                print(f"[CAPTCHA WARNING] Auto-seeding encountered an error: {e}")

        # Scan and register categories that have at least 40 valid images
        self._available_categories = {}
        for cat, info in CATEGORIES.items():
            cat_dir = IMAGE_BANK_DIR / cat
            valid_images = [f for f in cat_dir.glob("*.jpg") if f.stat().st_size > 100]
            if len(valid_images) >= 40:
                self._available_categories[cat] = info
            else:
                print(f"[CAPTCHA WARNING] Dropping category '{cat}' - only {len(valid_images)} valid images found on disk (minimum is 40).")

        if not self._available_categories:
            print("[CAPTCHA CRITICAL WARNING] No categories meet the minimum image requirement!")

    # ================= FAILED LOGINS TRACKING =================

    def record_failed_login(self, ip: str, session_obj=None) -> int:
        count = storage.record_failed_login(ip)
        if session_obj is not None:
            session_obj["failed_logins"] = session_obj.get("failed_logins", 0) + 1
        return count

    def get_failed_logins(self, ip: str, session_obj=None) -> int:
        ip_count = storage.get_failed_logins(ip)
        session_count = 0
        if session_obj is not None:
            session_count = session_obj.get("failed_logins", 0)
        return max(ip_count, session_count)

    def reset_failed_logins(self, ip: str, session_obj=None) -> None:
        storage.reset_failed_logins(ip)
        if session_obj is not None:
            session_obj.pop("failed_logins", None)

    # ================= CHALLENGE CREATION =================

    def create_challenge(self, ip: str, session_id: str = None, failed_logins: int = 0) -> dict:
        """
        Builds a new randomized challenge. Answers are stored strictly on the server in shared SQLite storage.
        Image tokens are cryptographically signed so any Gunicorn worker can serve them without 404s.
        """
        # Re-check bank if empty
        if not self._available_categories:
            self._ensure_and_validate_bank()

        if not self._available_categories:
            raise RuntimeError("No valid CAPTCHA categories available on server.")

        grid_size = HARD_GRID_SIZE if failed_logins >= FAILED_LOGINS_FOR_HARD else DEFAULT_GRID_SIZE
        total_tiles = grid_size * grid_size

        # Pick random target category
        target_category = random.choice(list(self._available_categories.keys()))
        cat_info = self._available_categories[target_category]

        target_dir = IMAGE_BANK_DIR / target_category
        target_images = [f for f in target_dir.glob("*.jpg") if f.stat().st_size > 100]

        # Collect distractor images
        distractor_dirs = [IMAGE_BANK_DIR / c for c in CATEGORIES if c != target_category]
        distractor_dirs.append(IMAGE_BANK_DIR / NEGATIVE_CATEGORY)
        distractor_images = []
        for d in distractor_dirs:
            if d.exists():
                distractor_images.extend([f for f in d.glob("*.jpg") if f.stat().st_size > 100])

        if grid_size == HARD_GRID_SIZE:
            k_correct = random.randint(MIN_CORRECT_COUNT_HARD, min(MAX_CORRECT_COUNT_HARD, len(target_images)))
        else:
            k_correct = random.randint(MIN_CORRECT_COUNT, min(MAX_CORRECT_COUNT, len(target_images)))

        num_distractors = total_tiles - k_correct

        selected_correct = random.sample(target_images, k_correct)
        selected_distractors = random.sample(distractor_images, num_distractors)

        tile_entries = [{"is_correct": True, "cat": target_category, "file": p.name, "path": p} for p in selected_correct] + \
                       [{"is_correct": False, "cat": p.parent.name, "file": p.name, "path": p} for p in selected_distractors]
        random.shuffle(tile_entries)

        # Before returning challenge, verify that every selected file exists and is readable
        for entry in tile_entries:
            if not entry["path"].exists():
                # Fallback to any valid file in that category
                valid_fallback = list((IMAGE_BANK_DIR / entry["cat"]).glob("*.jpg"))
                if valid_fallback:
                    entry["path"] = valid_fallback[0]
                    entry["file"] = valid_fallback[0].name

        correct_indices = {i for i, entry in enumerate(tile_entries) if entry["is_correct"]}

        now = time.time()
        expires_at = now + CHALLENGE_TTL_SECONDS
        challenge_id = secrets.token_urlsafe(24)
        audio_code = "".join(random.choices("0123456789", k=4))

        # Store challenge in shared process-safe SQLite database
        storage.store_challenge(challenge_id, {
            "category": target_category,
            "grid_size": grid_size,
            "correct_indices": correct_indices,
            "audio_code": audio_code,
            "created_at": now,
            "expires_at": expires_at,
            "ip": ip,
            "session_id": session_id,
        })

        # Generate cryptographically signed, stateless image tokens
        # Payload stores only category integer index and filename (no plain category names)
        img_serializer = URLSafeTimedSerializer(get_secret_key(), salt="captcha-image")
        public_images = []
        for i, entry in enumerate(tile_entries):
            cat_idx = CATEGORY_TO_INDEX.get(entry["cat"], 8)
            img_token = img_serializer.dumps({
                "c": cat_idx,
                "f": entry["file"],
                "cid": challenge_id,
            })
            public_images.append({
                "id": i,
                "url": f"/api/captcha/image/{img_token}",
            })

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
        Resolves image token cryptographically, works across all Gunicorn workers.
        Re-usable for the entire 2-minute lifetime.
        Never exposes category names or original paths to the client.
        """
        img_serializer = URLSafeTimedSerializer(get_secret_key(), salt="captcha-image")
        try:
            payload = img_serializer.loads(token, max_age=CHALLENGE_TTL_SECONDS)
        except (SignatureExpired, BadSignature, Exception):
            return None

        cat_idx = payload.get("c", 8)
        filename = payload.get("f", "img_01.jpg")
        category = INDEX_TO_CATEGORY.get(cat_idx, NEGATIVE_CATEGORY)

        img_path = IMAGE_BANK_DIR / category / filename
        if not img_path.exists():
            # Fallback to any valid image in that category
            cat_dir = IMAGE_BANK_DIR / category
            fallback_list = list(cat_dir.glob("*.jpg"))
            if fallback_list:
                img_path = fallback_list[0]
            else:
                return None

        try:
            with Image.open(img_path) as raw_img:
                img = raw_img.convert("RGB")

            # Dynamic transforms per request:
            # 1. Random subtle crop (92-98%)
            w, h = img.size
            crop_pct = random.uniform(0.92, 0.98)
            cw, ch = int(w * crop_pct), int(h * crop_pct)
            left = random.randint(0, w - cw)
            top = random.randint(0, h - ch)
            img = img.crop((left, top, left + cw, top + ch)).resize((300, 300), Image.Resampling.BILINEAR)

            # 2. Random subtle brightness (0.92 - 1.08)
            factor = random.uniform(0.92, 1.08)
            img = ImageEnhance.Brightness(img).enhance(factor)

            # 3. Save as JPEG without EXIF
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85, optimize=True)
            buf.seek(0)
            return buf
        except Exception:
            return None

    def get_audio_code(self, challenge_id: str) -> str | None:
        ch = storage.get_challenge(challenge_id)
        if ch and time.time() <= ch["expires_at"]:
            return ch["audio_code"]
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
        Verifies challenge selection or audio answer.
        One-time use: challenge is atomically popped from shared SQLite storage.
        """
        if honeypot:
            storage.record_verification_failure(ip)
            return False, "Bot activity detected.", None

        # Atomically pop challenge (ONE-TIME USE)
        challenge = storage.pop_challenge(challenge_id)

        if not challenge:
            storage.record_verification_failure(ip)
            return False, "Challenge not found or already used. Please request a new challenge.", None

        now = time.time()
        if now > challenge["expires_at"]:
            storage.record_verification_failure(ip)
            return False, "Challenge has expired. Please try again.", None

        # Minimum 2-second time-to-submit check
        elapsed = now - challenge["created_at"]
        if elapsed < MIN_TIME_TO_SUBMIT:
            storage.record_verification_failure(ip)
            return False, f"Verification submitted too quickly ({elapsed:.1f}s). Please take your time.", None

        # Verify answer
        is_correct = False
        if audio_answer is not None and str(audio_answer).strip():
            expected = challenge["audio_code"].strip()
            submitted = str(audio_answer).strip()
            is_correct = (submitted == expected)
        else:
            submitted_set = set(selected_indexes or [])
            is_correct = (submitted_set == challenge["correct_indices"])

        if not is_correct:
            in_cooldown, remaining = storage.record_verification_failure(ip)
            if in_cooldown:
                return False, f"Too many failed verification attempts. Please wait {remaining} seconds.", None
            return False, "Incorrect selection. Please try again.", None

        # Verification succeeded
        storage.record_verification_success(ip)

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
        Process-safe across all Gunicorn workers via shared storage.
        """
        if not token or not token.strip():
            return False, "CAPTCHA verification is required. Please check the 'I'm not a robot' box."

        clean_token = token.strip()
        if storage.is_token_burned(clean_token):
            return False, "CAPTCHA token has already been used. Please verify again."

        s = URLSafeTimedSerializer(get_secret_key(), salt="captcha-token")
        try:
            s.loads(clean_token, max_age=TOKEN_TTL_SECONDS)
        except SignatureExpired:
            return False, "CAPTCHA token has expired. Please verify again."
        except BadSignature:
            return False, "CAPTCHA token is invalid. Please verify again."
        except Exception:
            return False, "CAPTCHA verification failed."

        # Burn token so it cannot be reused across any worker
        storage.burn_token(clean_token)
        return True, None


# Global singleton instance
captcha_service = CaptchaService()
