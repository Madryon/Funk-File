"""
captcha/routes.py
Flask Blueprint implementing CAPTCHA endpoints:
- GET  /api/captcha/config
- GET  /api/captcha/challenge
- GET  /api/captcha/image/<token>
- GET  /api/captcha/audio/<challenge_id>
- POST /api/captcha/verify
"""

import io
from flask import Blueprint, request, jsonify, send_file, session
from extensions import csrf
from captcha.rate_limiter import rate_limiter
from captcha.service import captcha_service
from captcha.audio import generate_audio_challenge
from captcha.provider import get_captcha_provider

captcha_bp = Blueprint("captcha", __name__, url_prefix="/api/captcha")

# Exempt blueprint from CSRF so challenges and verifications work before/during authentication
csrf.exempt(captcha_bp)


def get_client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


@captcha_bp.route("/config", methods=["GET"])
def get_config():
    """Returns active CAPTCHA provider configuration for frontend initialization."""
    provider = get_captcha_provider()
    return jsonify(provider.get_client_config())


@captcha_bp.route("/challenge", methods=["GET"])
def get_challenge():
    """
    Builds a randomized image challenge. Answers are kept strictly on the server.
    Enforces 10 challenges/min rate limit and 5-min cooldown if triggered.
    """
    ip = get_client_ip()

    # Rate limit check
    allowed, err_msg = rate_limiter.can_request_challenge(ip)
    if not allowed:
        return jsonify({"error": err_msg or "Rate limit exceeded. Please wait a moment."}), 429

    # Check failed logins to determine if a hard (4x4) challenge is required
    failed_logins = captcha_service.get_failed_logins(ip, session)

    try:
        challenge_data = captcha_service.create_challenge(
            ip=ip,
            session_id=session.get("_id"),
            failed_logins=failed_logins,
        )
        return jsonify(challenge_data)
    except Exception as e:
        return jsonify({"error": f"Failed to generate challenge: {e}"}), 500


@captcha_bp.route("/image/<token>", methods=["GET"])
@captcha_bp.route("/img/<token>", methods=["GET"])
def get_image(token: str):
    """
    Serves challenge images dynamically with random crop, brightness, and noise.
    Never exposes category names or original filenames.
    """
    buf = captcha_service.get_challenge_image(token)
    if not buf:
        return jsonify({"error": "Image token expired or invalid."}), 404

    resp = send_file(buf, mimetype="image/jpeg", as_attachment=False)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


@captcha_bp.route("/audio/<challenge_id>", methods=["GET"])
def get_audio(challenge_id: str):
    """
    Synthesizes and serves an audio alternative WAV file for accessibility.
    """
    code = captcha_service.get_audio_code(challenge_id)
    if not code:
        return jsonify({"error": "Audio challenge expired or invalid."}), 404

    try:
        wav_data = generate_audio_challenge(code)
        buf = io.BytesIO(wav_data)
        resp = send_file(buf, mimetype="audio/wav", as_attachment=False)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp
    except Exception as e:
        return jsonify({"error": f"Failed to generate audio: {e}"}), 500


@captcha_bp.route("/verify", methods=["POST"])
def verify_challenge():
    """
    Verifies challenge selection or audio response.
    One-time use: burns the challenge immediately.
    On success returns a 5-minute signed token.
    On failure records attempt toward cooldown limit.
    """
    ip = get_client_ip()

    # Cooldown check
    in_cooldown, remaining = rate_limiter.is_in_cooldown(ip)
    if in_cooldown:
        return jsonify({
            "success": False,
            "error": f"Too many failed verification attempts. Please wait {remaining} seconds."
        }), 429

    payload = request.get_json(silent=True) or request.form

    challenge_id = (payload.get("challengeId") or payload.get("challenge_id") or "").strip()
    selected_indexes = payload.get("selectedIndexes") or payload.get("selected_indexes") or []
    audio_answer = payload.get("audioAnswer") or payload.get("audio_answer")
    honeypot = payload.get("website_hp") or payload.get("b_hp_check")

    if not challenge_id:
        return jsonify({"success": False, "error": "Missing challenge identifier."}), 400

    success, msg, token = captcha_service.verify_challenge(
        challenge_id=challenge_id,
        selected_indexes=selected_indexes,
        audio_answer=audio_answer,
        honeypot=honeypot,
        ip=ip,
        session_id=session.get("_id"),
    )

    if success:
        return jsonify({
            "success": True,
            "captchaToken": token,
            "message": "Verified successfully.",
        }), 200
    else:
        return jsonify({
            "success": False,
            "error": msg or "Incorrect answer. Please try again.",
        }), 400
