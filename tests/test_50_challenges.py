"""
tests/test_50_challenges.py
Script and test case verifying 50 consecutive CAPTCHA challenges:
- Every image URL returns HTTP 200 with Content-Type: image/jpeg.
- Valid image payload size (> 500 bytes).
- Image URLs are reusable for the full challenge lifetime (not consumed on first fetch).
- Grid contains the correct count of images matching the requested category.
- Exits with 0 on total success, or 1 on failure.
"""

import sys
import unittest
from itsdangerous import URLSafeTimedSerializer
from app import app
from captcha.config import (
    MIN_CORRECT_COUNT,
    MAX_CORRECT_COUNT,
    CATEGORY_TO_INDEX,
    INDEX_TO_CATEGORY,
)
from captcha.service import get_secret_key
from captcha.storage import storage


class FiftyChallengesTestCase(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        app.config["WTF_CSRF_ENABLED"] = False
        self.app_context = app.app_context()
        self.app_context.push()
        self.client = app.test_client()
        storage.reset_all()

    def tearDown(self):
        self.app_context.pop()

    def test_50_challenges_image_grid_integrity(self):
        serializer = URLSafeTimedSerializer(get_secret_key(), salt="captcha-image")
        total_images_checked = 0

        print(f"\n[TEST] Running 50-challenge image grid integrity verification...")

        for i in range(50):
            # Reset rate limits per batch so we can test 50 rapid challenges
            storage.reset_all()

            res = self.client.get("/api/captcha/challenge")
            self.assertEqual(res.status_code, 200, f"Challenge {i+1} failed with status {res.status_code}")
            data = res.get_json()

            cid = data["challengeId"]
            category_display = data["category"]
            grid_size = data["gridSize"]
            images = data["images"]

            expected_tiles = grid_size * grid_size
            self.assertEqual(len(images), expected_tiles, f"Challenge {i+1} tile count mismatch")

            # Check target category in DB
            ch_data = storage.get_challenge(cid)
            self.assertIsNotNone(ch_data, f"Challenge {cid} not found in storage")
            target_cat = ch_data["category"]
            correct_indices = ch_data["correct_indices"]

            # Number of correct tiles must be within bounds (2-5 for 3x3)
            self.assertTrue(
                MIN_CORRECT_COUNT <= len(correct_indices) <= MAX_CORRECT_COUNT,
                f"Challenge {i+1}: expected between {MIN_CORRECT_COUNT} and {MAX_CORRECT_COUNT} correct images, got {len(correct_indices)}"
            )

            # Inspect and fetch EVERY image URL
            for idx, img_info in enumerate(images):
                img_url = img_info["url"]
                self.assertTrue(img_url.startswith("/api/captcha/image/"), f"Unexpected url format: {img_url}")

                # 1. Fetch image
                img_res = self.client.get(img_url)
                self.assertEqual(img_res.status_code, 200, f"Image {img_url} returned {img_res.status_code}")
                self.assertEqual(img_res.mimetype, "image/jpeg", f"Mime type was {img_res.mimetype}")
                self.assertGreater(len(img_res.data), 500, "Image payload is too small")

                # 2. Verify reusability (fetch a second time)
                img_res2 = self.client.get(img_url)
                self.assertEqual(img_res2.status_code, 200, f"Second fetch of {img_url} failed")

                # 3. Decode signed token to inspect category classification
                token = img_url.replace("/api/captcha/image/", "")
                payload = serializer.loads(token)
                cat_idx = payload["c"]
                img_cat = INDEX_TO_CATEGORY.get(cat_idx)

                if idx in correct_indices:
                    self.assertEqual(img_cat, target_cat, f"Tile {idx} marked correct but category is {img_cat} != {target_cat}")
                else:
                    self.assertNotEqual(img_cat, target_cat, f"Tile {idx} marked distractor but category is target {target_cat}")

                total_images_checked += 1

            if (i + 1) % 10 == 0:
                print(f"  > Verified {i + 1}/50 challenges ({total_images_checked} images OK)...")

        print(f"[TEST SUCCESS] Verified 50/50 challenges and {total_images_checked} image tiles with 100% 200 OK.")


if __name__ == "__main__":
    unittest.main()
