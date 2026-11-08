"""
captcha/config.py
Configuration settings for the image-selection CAPTCHA system.
All parameters (categories, grid size, TTL, rate limits, provider) are defined here.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
IMAGE_BANK_DIR = BASE_DIR / "captcha" / "images"

# Target categories and user-facing instructions
CATEGORIES = {
    "cat": {
        "display": "cats",
        "instruction": "Select all images with cats",
    },
    "dog": {
        "display": "dogs",
        "instruction": "Select all images with dogs",
    },
    "car": {
        "display": "cars",
        "instruction": "Select all images with cars",
    },
    "bicycle": {
        "display": "bicycles",
        "instruction": "Select all images with bicycles",
    },
    "traffic_light": {
        "display": "traffic lights",
        "instruction": "Select all images with traffic lights",
    },
    "airplane": {
        "display": "airplanes",
        "instruction": "Select all images with airplanes",
    },
    "boat": {
        "display": "boats",
        "instruction": "Select all images with boats",
    },
    "tree": {
        "display": "trees",
        "instruction": "Select all images with trees",
    },
}

NEGATIVE_CATEGORY = "negative"

CATEGORIES_LIST = ["cat", "dog", "car", "bicycle", "traffic_light", "airplane", "boat", "tree", "negative"]
CATEGORY_TO_INDEX = {c: i for i, c in enumerate(CATEGORIES_LIST)}
INDEX_TO_CATEGORY = {i: c for i, c in enumerate(CATEGORIES_LIST)}

# Grid configuration
DEFAULT_GRID_SIZE = 3  # 3x3 = 9 images
HARD_GRID_SIZE = 4     # 4x4 = 16 images

# Number of correct answers in challenge
MIN_CORRECT_COUNT = 2
MAX_CORRECT_COUNT = 5
MIN_CORRECT_COUNT_HARD = 4
MAX_CORRECT_COUNT_HARD = 7

# Expiry / TTL
CHALLENGE_TTL_SECONDS = 120   # 2 minutes
TOKEN_TTL_SECONDS = 300       # 5 minutes (for signed verification token)

# Bot protection timing & honeypot
MIN_TIME_TO_SUBMIT = 2.0      # Minimum 2 seconds to solve challenge
HONEYPOT_FIELD_NAME = "website_hp"
FORM_HONEYPOT_NAME = "b_hp_check"

# Rate limits
MAX_CHALLENGES_PER_MINUTE = 10
MAX_FAILED_VERIFICATIONS = 5
FAILED_VERIFICATION_WINDOW_SECONDS = 600  # 10 minutes
COOLDOWN_SECONDS = 300                    # 5 minutes

# Failed login threshold to trigger hard (4x4) challenge
FAILED_LOGINS_FOR_HARD = 3

# Provider settings
DEFAULT_PROVIDER = "custom"  # "custom", "turnstile", or "recaptcha"
