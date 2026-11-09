import hmac
import os
import time
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from auth import generate_reset_token
from extensions import limiter
from models import LoginEvent, User, db

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def get_admin_password() -> str:
    """
    Retrieve the admin master password.
    Sources (in order of priority):
    1. os.environ['ADMIN_PASSWORD']
    2. .env file in project root (for local development)
    Returns empty string if not configured (NO hardcoded default password).
    """
    raw = os.environ.get("ADMIN_PASSWORD")
    if not raw:
        env_file = Path(__file__).resolve().parent / ".env"
        if env_file.is_file():
            try:
                with open(env_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or not line:
                            continue
                        if line.startswith("ADMIN_PASSWORD="):
                            raw = line.split("=", 1)[1].strip()
                            break
            except Exception:
                pass
    if not raw:
        return ""
    val = raw.strip().strip("'\"")
    if val.startswith("ADMIN_PASSWORD="):
        val = val[len("ADMIN_PASSWORD="):].strip().strip("'\"")
    return val


def is_admin_authenticated() -> bool:
    if not session.get("admin_authenticated"):
        return False
    login_time = session.get("admin_login_time", 0)
    # Session expires after 30 minutes (1800 seconds)
    if time.time() - login_time > 1800:
        session.pop("admin_authenticated", None)
        session.pop("admin_login_time", None)
        return False
    return True


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_admin_authenticated():
            if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
                return jsonify({"error": "Admin authentication required.", "redirect": url_for("admin.login")}), 401
            return redirect(url_for("admin.login"))
        return f(*args, **kwargs)
    return decorated_function


# ================= ADMIN AUTH =================

@admin_bp.route("/login", methods=["GET", "POST"])
@admin_bp.route("/login/", methods=["GET", "POST"])
@limiter.limit("15 per minute", methods=["POST"])
def login():
    if is_admin_authenticated():
        return redirect(url_for("admin.dashboard"))

    admin_pass = get_admin_password()

    if request.method == "GET":
        cfg_warning = None
        if not admin_pass:
            cfg_warning = "ADMIN_PASSWORD is not configured in server environment variables. Admin login is currently disabled."
        return render_template("admin.html", mode="login", error=cfg_warning)

    if not admin_pass:
        msg = "Admin access is disabled because ADMIN_PASSWORD is not configured on the server."
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"error": msg}), 503
        return render_template("admin.html", mode="login", error=msg), 503

    payload = request.get_json(silent=True) if request.is_json else request.form
    password = (payload.get("password") or "").strip()

    # Secure comparison using hmac.compare_digest
    if not password or not hmac.compare_digest(password.encode("utf-8"), admin_pass.encode("utf-8")):
        msg = "Invalid admin password."
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"error": msg}), 401
        return render_template("admin.html", mode="login", error=msg), 401

    session["admin_authenticated"] = True
    session["admin_login_time"] = time.time()

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return jsonify({"ok": True, "redirect": url_for("admin.dashboard")})
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop("admin_authenticated", None)
    session.pop("admin_login_time", None)
    return redirect(url_for("index"))


# ================= ADMIN DASHBOARD =================

@admin_bp.route("", methods=["GET"])
@admin_bp.route("/", methods=["GET"])
@admin_required
def dashboard():
    now = datetime.utcnow()
    total_users = User.query.count()
    active_24h = User.query.filter(User.last_login >= now - timedelta(hours=24)).count()
    active_7d = User.query.filter(User.last_login >= now - timedelta(days=7)).count()
    total_logins = db.session.query(db.func.sum(User.login_count)).scalar() or LoginEvent.query.count()

    users = User.query.order_by(User.id.desc()).all()
    recent_logins = LoginEvent.query.order_by(LoginEvent.logged_in_at.desc()).limit(50).all()

    login_time = session.get("admin_login_time", time.time())
    remaining_secs = max(0, int(1800 - (time.time() - login_time)))

    return render_template(
        "admin.html",
        mode="dashboard",
        stats={
            "total_users": total_users,
            "active_24h": active_24h,
            "active_7d": active_7d,
            "total_logins": total_logins,
        },
        users=users,
        recent_logins=recent_logins,
        remaining_secs=remaining_secs,
    )


# ================= ADMIN ACTIONS =================

@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def toggle_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.dashboard"))

    user.is_active = not user.is_active
    db.session.commit()
    status_str = "enabled" if user.is_active else "disabled"
    flash(f"User '{user.username}' has been {status_str}.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.", "error")
        return redirect(url_for("admin.dashboard"))

    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f"User '{username}' and their associated data have been deleted.", "success")
    return redirect(url_for("admin.dashboard"))


@admin_bp.route("/users/<int:user_id>/reset-link", methods=["POST"])
@admin_required
def generate_reset_link_action(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404 if request.is_json else redirect(url_for("admin.dashboard"))

    token = generate_reset_token(user.id)
    reset_url = url_for("auth.reset_password", token=token, _external=True)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return jsonify({"ok": True, "username": user.username, "reset_url": reset_url})

    flash(f"Password reset link generated for {user.username}: {reset_url}", "reset_link")
    return redirect(url_for("admin.dashboard"))
