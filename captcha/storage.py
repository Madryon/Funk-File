"""
captcha/storage.py
Persistent, process-safe, multi-worker storage for CAPTCHA challenges,
rate limits, burned tokens, and failed logins using SQLite in WAL mode.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from captcha.config import (
    BASE_DIR,
    CHALLENGE_TTL_SECONDS,
    FAILED_VERIFICATION_WINDOW_SECONDS,
    COOLDOWN_SECONDS,
    MAX_CHALLENGES_PER_MINUTE,
    MAX_FAILED_VERIFICATIONS,
)

DB_PATH = BASE_DIR / "captcha_store.db"


class CaptchaStorage:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    grid_size INTEGER NOT NULL,
                    correct_indices TEXT NOT NULL,
                    audio_code TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    ip TEXT NOT NULL,
                    session_id TEXT
                );

                CREATE TABLE IF NOT EXISTS burned_tokens (
                    token TEXT PRIMARY KEY,
                    burned_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS failed_logins (
                    ip TEXT PRIMARY KEY,
                    count INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rate_limits (
                    ip TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rate_limits_ip_kind ON rate_limits (ip, kind, created_at);

                CREATE TABLE IF NOT EXISTS cooldowns (
                    ip TEXT PRIMARY KEY,
                    cooldown_until REAL NOT NULL
                );
            """)

    def cleanup_expired(self):
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("DELETE FROM challenges WHERE expires_at < ?", (now,))
            conn.execute("DELETE FROM burned_tokens WHERE burned_at < ?", (now - 900,))
            conn.execute("DELETE FROM rate_limits WHERE created_at < ?", (now - 600,))
            conn.execute("DELETE FROM cooldowns WHERE cooldown_until < ?", (now,))

    # ================= CHALLENGE CRUD =================

    def store_challenge(self, challenge_id: str, data: dict):
        self.cleanup_expired()
        with self._get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO challenges 
                (challenge_id, category, grid_size, correct_indices, audio_code, created_at, expires_at, ip, session_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                challenge_id,
                data["category"],
                data["grid_size"],
                json.dumps(list(data["correct_indices"])),
                data["audio_code"],
                data["created_at"],
                data["expires_at"],
                data["ip"],
                data.get("session_id"),
            ))

    def get_challenge(self, challenge_id: str) -> dict | None:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,))
            row = cur.fetchone()
            if not row:
                return None
            return {
                "id": row["challenge_id"],
                "category": row["category"],
                "grid_size": row["grid_size"],
                "correct_indices": set(json.loads(row["correct_indices"])),
                "audio_code": row["audio_code"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "ip": row["ip"],
                "session_id": row["session_id"],
            }

    def pop_challenge(self, challenge_id: str) -> dict | None:
        """Atomically retrieves and deletes a challenge (ONE-TIME USE)."""
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,))
            row = cur.fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM challenges WHERE challenge_id = ?", (challenge_id,))
            return {
                "id": row["challenge_id"],
                "category": row["category"],
                "grid_size": row["grid_size"],
                "correct_indices": set(json.loads(row["correct_indices"])),
                "audio_code": row["audio_code"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "ip": row["ip"],
                "session_id": row["session_id"],
            }

    def update_challenge_created_at(self, challenge_id: str, created_at: float):
        with self._get_connection() as conn:
            conn.execute("UPDATE challenges SET created_at = ? WHERE challenge_id = ?", (created_at, challenge_id))

    # ================= BURNED TOKENS =================

    def is_token_burned(self, token: str) -> bool:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM burned_tokens WHERE token = ?", (token,))
            return cur.fetchone() is not None

    def burn_token(self, token: str):
        now = time.time()
        with self._get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO burned_tokens (token, burned_at) VALUES (?, ?)", (token, now))

    # ================= FAILED LOGINS =================

    def record_failed_login(self, ip: str) -> int:
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count FROM failed_logins WHERE ip = ?", (ip,))
            row = cur.fetchone()
            count = (row["count"] + 1) if row else 1
            conn.execute("INSERT OR REPLACE INTO failed_logins (ip, count, updated_at) VALUES (?, ?, ?)", (ip, count, now))
            return count

    def get_failed_logins(self, ip: str) -> int:
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT count FROM failed_logins WHERE ip = ?", (ip,))
            row = cur.fetchone()
            return row["count"] if row else 0

    def reset_failed_logins(self, ip: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM failed_logins WHERE ip = ?", (ip,))

    # ================= RATE LIMITING & COOLDOWNS =================

    def is_in_cooldown(self, ip: str) -> tuple[bool, int]:
        now = time.time()
        with self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT cooldown_until FROM cooldowns WHERE ip = ?", (ip,))
            row = cur.fetchone()
            if row:
                until = row["cooldown_until"]
                if until > now:
                    return True, int(until - now)
                else:
                    conn.execute("DELETE FROM cooldowns WHERE ip = ?", (ip,))
            return False, 0

    def can_request_challenge(self, ip: str) -> tuple[bool, str | None]:
        in_cooldown, remaining = self.is_in_cooldown(ip)
        if in_cooldown:
            return False, f"Too many failed verification attempts. Please wait {remaining} seconds."

        now = time.time()
        with self._get_connection() as conn:
            # Prune older than 60s
            conn.execute("DELETE FROM rate_limits WHERE ip = ? AND kind = 'challenge' AND created_at < ?", (ip, now - 60.0))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM rate_limits WHERE ip = ? AND kind = 'challenge'", (ip,))
            cnt = cur.fetchone()["cnt"]

            if cnt >= MAX_CHALLENGES_PER_MINUTE:
                return False, "Challenge rate limit exceeded (maximum 10 per minute). Please try again shortly."

            conn.execute("INSERT INTO rate_limits (ip, kind, created_at) VALUES (?, 'challenge', ?)", (ip, now))
            return True, None

    def record_verification_failure(self, ip: str) -> tuple[bool, int]:
        now = time.time()
        with self._get_connection() as conn:
            # Prune older than window (10 min)
            conn.execute("DELETE FROM rate_limits WHERE ip = ? AND kind = 'failed_verify' AND created_at < ?", (ip, now - FAILED_VERIFICATION_WINDOW_SECONDS))
            conn.execute("INSERT INTO rate_limits (ip, kind, created_at) VALUES (?, 'failed_verify', ?)", (ip, now))

            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM rate_limits WHERE ip = ? AND kind = 'failed_verify'", (ip,))
            failures = cur.fetchone()["cnt"]

            if failures >= MAX_FAILED_VERIFICATIONS:
                cooldown_until = now + COOLDOWN_SECONDS
                conn.execute("INSERT OR REPLACE INTO cooldowns (ip, cooldown_until) VALUES (?, ?)", (ip, cooldown_until))
                conn.execute("DELETE FROM rate_limits WHERE ip = ? AND kind = 'failed_verify'", (ip,))
                return True, COOLDOWN_SECONDS

            return False, 0

    def record_verification_success(self, ip: str):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM rate_limits WHERE ip = ? AND kind = 'failed_verify'", (ip,))
            conn.execute("DELETE FROM cooldowns WHERE ip = ?", (ip,))

    def reset_all(self):
        """Used for test suites."""
        with self._get_connection() as conn:
            conn.execute("DELETE FROM challenges")
            conn.execute("DELETE FROM burned_tokens")
            conn.execute("DELETE FROM failed_logins")
            conn.execute("DELETE FROM rate_limits")
            conn.execute("DELETE FROM cooldowns")


# Global singleton instance
storage = CaptchaStorage()
