import hmac
import re
import time
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from extensions import limiter, login_manager
from models import LoginEvent, User, db

auth_bp = Blueprint("auth", __name__)


def get_client_ip() -> str:
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def get_user_agent() -> str:
    return (request.headers.get("User-Agent") or "")[:255]


def record_login_event(user: User) -> None:
    try:
        now = datetime.utcnow()
        event = LoginEvent(
            user_id=user.id,
            ip=get_client_ip(),
            user_agent=get_user_agent(),
            logged_in_at=now,
        )
        user.last_login = now
        user.login_count = (user.login_count or 0) + 1
        db.session.add(event)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        current_app.logger.warning(f"Failed to record login event for user {user.id}: {e}")


def generate_reset_token(user_id: int) -> str:
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    return s.dumps(user_id, salt="pdf-toolbox-reset")


def verify_reset_token(token: str, max_age: int = 3600):
    s = URLSafeTimedSerializer(current_app.config["SECRET_KEY"])
    try:
        return s.loads(token, salt="pdf-toolbox-reset", max_age=max_age)
    except (SignatureExpired, BadSignature):
        return None


@login_manager.user_loader
def load_user(user_id):
    try:
        user = db.session.get(User, int(user_id))
        if user and not user.is_active:
            return None
        return user
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


@login_manager.unauthorized_handler
def unauthorized():
    # If the request is AJAX / JSON or targeted at API endpoints, respond with 401 JSON
    if (
        request.is_json
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.path.startswith((
            "/merge",
            "/compress",
            "/convert",
            "/pdf-to-word",
            "/split",
            "/lock",
            "/unlock",
            "/video-",
            "/images-",
            "/watermark",
            "/qr-",
            "/scan/",
            "/history",
        ))
    ):
        return jsonify({"error": "Login required. Please log in to use this feature."}), 401
    return redirect(url_for("auth.login_view", next=request.path))


# ================= USER AUTH ROUTES =================

@auth_bp.route("/register", methods=["GET", "POST"])
def register_view():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "GET":
        return render_template("login.html", mode="register")

    payload = request.get_json(silent=True) if request.is_json else request.form
    username = (payload.get("username") or "").strip()
    email = (payload.get("email") or "").strip().lower()
    password = payload.get("password") or ""

    # Basic validations
    if not username or not password:
        msg = "Username and password are required."
        return jsonify({"error": msg}), 400 if request.is_json else (render_template("login.html", mode="register", error=msg), 400)

    if not email:
        # Fallback email if user registered via brief modal without email field
        email = f"{username.lower()}@pdf-toolbox.local"

    email_regex = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
    if not re.match(email_regex, email):
        msg = "Please provide a valid email address."
        return jsonify({"error": msg}), 400 if request.is_json else (render_template("login.html", mode="register", error=msg), 400)

    if len(username) < 3 or len(username) > 80:
        msg = "Username must be between 3 and 80 characters."
        return jsonify({"error": msg}), 400 if request.is_json else (render_template("login.html", mode="register", error=msg), 400)

    if len(password) < 6:
        msg = "Password must be at least 6 characters long."
        return jsonify({"error": msg}), 400 if request.is_json else (render_template("login.html", mode="register", error=msg), 400)

    if User.query.filter_by(username=username).first():
        msg = "Username is already taken. Please choose another."
        return jsonify({"error": msg}), 409 if request.is_json else (render_template("login.html", mode="register", error=msg), 409)

    if User.query.filter_by(email=email).first():
        msg = "An account with this email already exists."
        return jsonify({"error": msg}), 409 if request.is_json else (render_template("login.html", mode="register", error=msg), 409)

    user = User(username=username, email=email, is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    record_login_event(user)
    login_user(user, remember=True)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return jsonify({"id": user.id, "username": user.username, "email": user.email}), 201

    next_url = request.args.get("next") or url_for("index")
    return redirect(next_url)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute", methods=["POST"])
def login_view():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    if request.method == "GET":
        return render_template("login.html", mode="login")

    payload = request.get_json(silent=True) if request.is_json else request.form
    identifier = (payload.get("username") or payload.get("email") or payload.get("identifier") or "").strip()
    password = payload.get("password") or ""

    if not identifier or not password:
        msg = "Username/email and password are required."
        return jsonify({"error": msg}), 400 if request.is_json else (render_template("login.html", mode="login", error=msg), 400)

    # Allow login with either username or email
    user = User.query.filter(
        (User.username == identifier) | (User.email == identifier.lower())
    ).first()

    if user is None or not user.check_password(password):
        msg = "Invalid username/email or password."
        return jsonify({"error": msg}), 401 if request.is_json else (render_template("login.html", mode="login", error=msg), 401)

    if not user.is_active:
        msg = "This account has been disabled. Please contact an administrator."
        return jsonify({"error": msg}), 403 if request.is_json else (render_template("login.html", mode="login", error=msg), 403)

    record_login_event(user)
    login_user(user, remember=True)

    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        return jsonify({"id": user.id, "username": user.username, "email": user.email}), 200

    next_url = request.args.get("next") or url_for("index")
    return redirect(next_url)


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout_view():
    if current_user.is_authenticated:
        logout_user()
    if request.is_json or request.method == "POST":
        return jsonify({"ok": True})
    return redirect(url_for("index"))


@auth_bp.route("/auth/me", methods=["GET"])
def me_alias():
    if not current_user.is_authenticated:
        return jsonify({"authenticated": False})
    return jsonify({
        "authenticated": True,
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
    })


# Legacy /auth/* aliases for client compatibility
@auth_bp.route("/auth/register", methods=["POST"])
def auth_register_alias():
    return register_view()


@auth_bp.route("/auth/login", methods=["POST"])
@limiter.limit("5 per minute")
def auth_login_alias():
    return login_view()


@auth_bp.route("/auth/logout", methods=["POST"])
def auth_logout_alias():
    return logout_view()


# ================= PASSWORD RESET ROUTE =================

@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token") or (request.form.get("token") if request.method == "POST" else None)
    if not token:
        flash("Invalid or missing password reset link.", "error")
        return redirect(url_for("auth.login_view"))

    user_id = verify_reset_token(token)
    if not user_id:
        flash("Password reset link has expired or is invalid.", "error")
        return redirect(url_for("auth.login_view"))

    user = db.session.get(User, user_id)
    if not user or not user.is_active:
        flash("User account not found or disabled.", "error")
        return redirect(url_for("auth.login_view"))

    if request.method == "POST":
        new_password = (request.form.get("password") or "").strip()
        if len(new_password) < 6:
            return render_template("login.html", mode="reset", token=token, username=user.username, error="Password must be at least 6 characters.")
        user.set_password(new_password)
        db.session.commit()
        flash("Password has been reset successfully. Please log in.", "success")
        return redirect(url_for("auth.login_view"))

    return render_template("login.html", mode="reset", token=token, username=user.username)


# ================= ADMIN LOGIN WEB API =================

@auth_bp.route("/auth/admin-login", methods=["POST"])
@auth_bp.route("/admin-login", methods=["POST"])
def auth_admin_login():
    from admin import get_admin_password
    admin_pass = get_admin_password()
    payload = request.get_json(silent=True) if request.is_json else request.form
    password = (payload.get("password") or "").strip()
    if not password or not hmac.compare_digest(password.encode("utf-8"), admin_pass.encode("utf-8")):
        return jsonify({"error": "Invalid admin password."}), 401
    session["admin_authenticated"] = True
    session["admin_login_time"] = time.time()
    return jsonify({"ok": True, "redirect": "/admin"})

