import hmac
import os
import time
from datetime import datetime, timedelta
from functools import wraps

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


DEFAULT_ADMIN_PASSWORD = "AdminPass2026!"


def get_admin_password() -> str:
    raw = os.environ.get("ADMIN_PASSWORD")
    if not raw:
        return DEFAULT_ADMIN_PASSWORD
    val = raw.strip().strip("'\"")
    if val.startswith("ADMIN_PASSWORD="):
        val = val[len("ADMIN_PASSWORD="):].strip().strip("'\"")
    return val or DEFAULT_ADMIN_PASSWORD


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
        # Must refuse if ADMIN_PASSWORD is not configured in env
        if not get_admin_password():
            abort(404)
        # Return 404 (not 403) for /admin/* to non-admins
        if not is_admin_authenticated():
            abort(404)
        return f(*args, **kwargs)
    return decorated_function


@admin_bp.before_request
def check_admin_env():
    # If the env var is missing, refuse all admin routes with 404
    if not get_admin_password():
        abort(404)


# ================= ADMIN AUTH =================

@admin_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per minute", methods=["POST"])
def login():
    if is_admin_authenticated():
        return redirect(url_for("admin.dashboard"))

    if request.method == "GET":
        return render_template("admin.html", mode="login")

    payload = request.get_json(silent=True) if request.is_json else request.form
    password = (payload.get("password") or "").strip()

    admin_pass = get_admin_password()
    if not admin_pass:
        abort(404)

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
