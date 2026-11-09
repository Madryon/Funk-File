import html
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import current_user, login_required
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from admin import admin_bp, admin_required, is_admin_authenticated
from auth import auth_bp, login_manager
from extensions import csrf, limiter
from history import create_history_entry, history_bp
from models import (
    ErrorLog,
    FileHistory,
    LoginEvent,
    ProcessingJob,
    SiteSetting,
    Ticket,
    User,
    db,
    init_or_migrate_db,
)
from scheduler import start_cleanup_scheduler
from utils import BASE_DIR, OUTPUT_DIR, UPLOAD_DIR

TICKET_SCREENSHOT_DIR = UPLOAD_DIR / "tickets"
TICKET_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
app.config["JSON_SORT_KEYS"] = False

# Database configuration
raw_db_url = os.environ.get("DATABASE_URL", f"sqlite:///{BASE_DIR / 'app.db'}").strip().strip("'\"")
if raw_db_url.startswith("DATABASE_URL="):
    raw_db_url = raw_db_url[len("DATABASE_URL="):].strip().strip("'\"")

if raw_db_url.startswith("postgres://"):
    database_url = "postgresql+psycopg2://" + raw_db_url[len("postgres://"):]
elif raw_db_url.startswith("postgresql://") and not raw_db_url.startswith("postgresql+"):
    database_url = "postgresql+psycopg2://" + raw_db_url[len("postgresql://"):]
else:
    database_url = raw_db_url

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Secret key & Session security
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "pdf-toolbox-secure-fallback-key-32chars!").strip().strip("'\"")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "false").lower() in ("true", "1")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# Initialize Extensions
db.init_app(app)
login_manager.init_app(app)
limiter.init_app(app)
csrf.init_app(app)

# Register Blueprints
from captcha import captcha_bp
app.register_blueprint(captcha_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(history_bp)
app.register_blueprint(admin_bp)

# Register Tool Blueprints
from routes_image import image_bp
from routes_pdf import pdf_bp
from routes_scan import scan_bp
from routes_video import video_bp

app.register_blueprint(pdf_bp)
app.register_blueprint(image_bp)
app.register_blueprint(video_bp)
app.register_blueprint(scan_bp)


# ================= DATABASE INITIALIZATION =================

with app.app_context():
    init_or_migrate_db(app)


# ================= SITE STATUS & 30-SECOND CACHE =================

_site_status_cache = None
_site_status_cache_time = 0


def get_site_status():
    global _site_status_cache, _site_status_cache_time
    now_ts = time.time()
    if _site_status_cache is not None and (now_ts - _site_status_cache_time < 30):
        return _site_status_cache

    try:
        ann_row = SiteSetting.query.filter_by(key="announcement").first()
        ann_data = json.loads(ann_row.value) if ann_row and ann_row.value else {}
    except Exception:
        ann_data = {}

    try:
        maint_row = SiteSetting.query.filter_by(key="maintenance").first()
        maint_data = json.loads(maint_row.value) if maint_row and maint_row.value else {}
    except Exception:
        maint_data = {}

    # Auto-expire announcement check
    ann_active = bool(ann_data.get("active", False))
    ann_expires = ann_data.get("expires_at")
    if ann_active and ann_expires:
        try:
            exp_dt = datetime.fromisoformat(ann_expires.replace("Z", "+00:00")).replace(tzinfo=None)
            if datetime.utcnow() >= exp_dt:
                ann_active = False
        except Exception:
            pass

    # Auto-expire maintenance check
    maint_active = bool(maint_data.get("active", False))
    maint_ends = maint_data.get("ends_at")
    maint_ends_dt = None
    if maint_active and maint_ends:
        try:
            maint_ends_dt = datetime.fromisoformat(maint_ends.replace("Z", "+00:00")).replace(tzinfo=None)
            if datetime.utcnow() >= maint_ends_dt:
                maint_active = False
                # Auto disable in DB
                try:
                    if maint_row:
                        maint_data["active"] = False
                        maint_row.value = json.dumps(maint_data)
                        db.session.commit()
                except Exception:
                    db.session.rollback()
        except Exception:
            pass

    # Format IST for ends_at
    ends_at_ist = None
    if maint_ends_dt:
        ist_dt = maint_ends_dt + timedelta(hours=5, minutes=30)
        ends_at_ist = ist_dt.strftime("%d %b %Y, %I:%M %p IST")

    res = {
        "announcement": {
            "active": ann_active,
            "text": ann_data.get("text", "") if ann_active else "",
            "type": ann_data.get("type", "info"),
            "id": ann_data.get("id") or (str(int(ann_row.updated_at.timestamp())) if ann_row else "ann_0"),
        },
        "maintenance": {
            "active": maint_active,
            "message": maint_data.get("message", "We are currently undergoing scheduled maintenance. Please check back shortly."),
            "ends_at": maint_ends if maint_active else None,
            "ends_at_ist": ends_at_ist if maint_active else None,
        }
    }
    _site_status_cache = res
    _site_status_cache_time = now_ts
    return res


def invalidate_site_status_cache():
    global _site_status_cache, _site_status_cache_time
    _site_status_cache = None
    _site_status_cache_time = 0


@app.route("/api/site-status", methods=["GET"])
@csrf.exempt
def api_site_status():
    status = get_site_status()
    resp = jsonify(status)
    resp.headers["Cache-Control"] = "public, max-age=30"
    return resp


# ================= MAINTENANCE MODE ENFORCEMENT =================

@app.before_request
def check_maintenance():
    # Admin bypass: authenticated admin sessions can test all pages and tools normally
    if is_admin_authenticated():
        return None

    path = request.path
    if (
        path.startswith("/admin")
        or path.startswith("/static/")
        or path in ("/health", "/api/site-status")
        or path.startswith("/favicon")
    ):
        return None

    status = get_site_status()
    maint = status.get("maintenance", {})
    if maint.get("active"):
        ends_at_str = maint.get("ends_at")
        retry_after = 300
        if ends_at_str:
            try:
                ends_dt = datetime.fromisoformat(ends_at_str.replace("Z", "+00:00")).replace(tzinfo=None)
                remaining = int((ends_dt - datetime.utcnow()).total_seconds())
                retry_after = max(10, remaining)
            except Exception:
                retry_after = 300

        msg = maint.get("message", "We are currently undergoing scheduled maintenance. Please check back shortly.")
        ends_at_ist = maint.get("ends_at_ist", "Shortly")

        # JSON response for tool APIs & AJAX
        if request.is_json or request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
            resp = jsonify({
                "error": "maintenance",
                "message": msg,
                "retry_after": retry_after,
                "ends_at_ist": ends_at_ist,
            })
            resp.status_code = 503
            resp.headers["Retry-After"] = str(retry_after)
            return resp

        # Full-screen glass maintenance page for browser navigation
        resp = make_response(render_template(
            "maintenance.html",
            message=msg,
            ends_at=ends_at_str,
            ends_at_ist=ends_at_ist,
            retry_after=retry_after,
        ), 503)
        resp.headers["Retry-After"] = str(retry_after)
        return resp


# ================= SENSITIVE DATA REDACTION & ERROR LOGGING =================

def sanitize_log_text(text: str) -> str:
    """Strips sensitive data: passwords, tokens, auth headers, cookies."""
    if not text:
        return ""
    # Strip password/token fields in urlencoded / form / json strings
    text = re.sub(r'(password|passwd|token|secret|key|csrf_token)=[^&\s]+', r'\1=[REDACTED]', text, flags=re.IGNORECASE)
    text = re.sub(r'("password"|"token"|"secret"|"key")\s*:\s*"[^"]+"', r'\1: "[REDACTED]"', text, flags=re.IGNORECASE)
    text = re.sub(r'(Bearer\s+)[A-Za-z0-9_\-\.]+', r'\1[REDACTED]', text, flags=re.IGNORECASE)
    return text


def log_error_to_db(tool=None, error_type="Error", error_message="", http_status=500, traceback_text=None):
    try:
        from flask import has_request_context
        user_id = None
        username = "System"
        ip = "—"
        ua = "—"
        route = ""
        inferred_tool = "system"

        if has_request_context():
            user_id = current_user.id if current_user.is_authenticated else None
            username = current_user.username if current_user.is_authenticated else "Guest"
            ip = request.headers.get("X-Forwarded-For", request.remote_addr or "—").split(",")[0].strip()
            ua = (request.user_agent.string if request.user_agent else "—")[:250]
            route = request.path[:250]
            inferred_tool = getattr(g, "tool_name", getattr(g, "inferred_tool", "system"))

        clean_msg = sanitize_log_text(error_message)[:500]
        clean_tb = sanitize_log_text(traceback_text)[:4000] if traceback_text else None

        elog = ErrorLog(
            user_id=user_id,
            username_snapshot=username,
            tool=tool or inferred_tool,
            error_type=error_type[:40],
            error_message=clean_msg,
            traceback=clean_tb,
            http_status=http_status,
            route=route,
            ip=ip,
            user_agent=ua,
            created_at=datetime.utcnow(),
            status="new",
        )
        db.session.add(elog)
        db.session.commit()
        if has_request_context():
            g.logged_error_id = elog.id
        return elog
    except Exception as ex:
        try:
            db.session.rollback()
        except Exception:
            pass
        return None


# ================= PROFILING & PROCESSING JOB RECORDING =================

@app.before_request
def start_request_profiling():
    g.request_start_time = time.time()
    g.request_content_length = request.content_length or 0
    endpoint = request.endpoint or ""
    tool_prefixes = ("pdf.", "image.", "video.", "scan.")
    if any(endpoint.startswith(p) for p in tool_prefixes) or request.path in (
        "/merge", "/compress", "/convert", "/pdf-to-word", "/split", "/lock", "/unlock",
        "/images-to-pdf", "/watermark-text", "/watermark-image", "/qr-generate",
        "/scan/process", "/scan/build", "/video-to-images", "/video-to-pdf", "/video-to-mp3"
    ):
        g.is_tool_request = True
        g.inferred_tool = endpoint.split(".")[-1] if "." in endpoint else request.path.strip("/")


@app.after_request
def record_job_and_history(response):
    # Log user file history on successful tool completion
    if response.status_code < 400:
        tool_name = getattr(g, "tool_name", None)
        output_name = getattr(g, "output_name", None)
        output_path = getattr(g, "output_path", None)
        if tool_name and output_name and output_path:
            try:
                create_history_entry(tool_name, output_name, output_path)
            except Exception:
                pass

    # Record processing jobs for tool operations
    if getattr(g, "is_tool_request", False):
        try:
            duration_ms = int((time.time() - getattr(g, "request_start_time", time.time())) * 1000)
            tool_name = getattr(g, "tool_name", getattr(g, "inferred_tool", "unknown"))
            status = "success" if response.status_code < 400 else "failed"
            user_id = current_user.id if current_user.is_authenticated else None
            input_size = getattr(g, "request_content_length", 0)

            error_id = getattr(g, "logged_error_id", None)
            if response.status_code >= 400 and not error_id:
                err_msg = ""
                try:
                    if response.is_json:
                        j = response.get_json()
                        err_msg = j.get("error") or str(j)
                except Exception:
                    pass
                if not err_msg:
                    err_msg = f"HTTP {response.status_code} on {request.path}"

                err_type = "FileTooLarge" if response.status_code == 413 else "ProcessingError"
                elog = log_error_to_db(
                    tool=tool_name,
                    error_type=err_type,
                    error_message=err_msg[:500],
                    http_status=response.status_code,
                    traceback_text=None,
                )
                if elog:
                    error_id = elog.id

            job = ProcessingJob(
                user_id=user_id,
                tool=tool_name,
                status=status,
                started_at=datetime.utcnow() - timedelta(milliseconds=duration_ms),
                finished_at=datetime.utcnow(),
                duration_ms=duration_ms,
                input_size_bytes=input_size,
                error_id=error_id,
            )
            db.session.add(job)
            db.session.commit()
        except Exception:
            try:
                db.session.rollback()
            except Exception:
                pass

    return response


# ================= FILE CLEANUP =================

def _cleanup_old(max_age_seconds: int = 3600) -> None:
    """Safely prune expired UUID job directories and temporary files."""
    now = time.time()
    for base_dir in (UPLOAD_DIR, OUTPUT_DIR):
        if not base_dir or not base_dir.exists():
            continue
        for item in base_dir.iterdir():
            if item.name == "tickets":
                continue
            try:
                if now - item.stat().st_mtime > max_age_seconds:
                    if item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                    else:
                        item.unlink(missing_ok=True)
            except OSError:
                continue


start_cleanup_scheduler(_cleanup_old)


# ================= BASE ROUTES =================

@app.route("/")
def index():
    _cleanup_old()
    return render_template("index.html")


@app.route("/health")
@csrf.exempt
def health():
    return jsonify({"ok": True, "version": "2.0"})


# ================= GLOBAL ERROR HANDLERS =================

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"error": f"CSRF error: {e.description}"}), 400
    return render_template("login.html", error=f"Session expired or invalid security token ({e.description}). Please try again."), 400


@app.errorhandler(429)
def ratelimit_handler(_e):
    log_error_to_db(
        tool=getattr(g, "tool_name", getattr(g, "inferred_tool", "rate_limiter")),
        error_type="RateLimitExceeded",
        error_message="Rate limit exceeded",
        http_status=429,
    )
    return jsonify({"error": "Too many attempts. Please wait a minute and try again."}), 429


@app.errorhandler(413)
def request_entity_too_large(error):
    log_error_to_db(
        tool=getattr(g, "tool_name", getattr(g, "inferred_tool", "upload")),
        error_type="PayloadTooLarge",
        error_message="Uploaded file exceeds max content length (500 MB)",
        http_status=413,
    )
    return jsonify({"error": "File is too large. Please upload a smaller file."}), 413


@app.errorhandler(404)
def not_found_error(error):
    if request.path.startswith("/static/") or request.accept_mimetypes.accept_html:
        return error
    return jsonify({"error": "Requested resource or file was not found."}), 404


@app.errorhandler(500)
def internal_server_error(error):
    import traceback
    tb = traceback.format_exc()
    log_error_to_db(
        tool=getattr(g, "tool_name", getattr(g, "inferred_tool", "server")),
        error_type="InternalServerError",
        error_message=str(error),
        http_status=500,
        traceback_text=tb,
    )
    return jsonify({"error": "Server error while processing your file. Please try again."}), 500


@app.errorhandler(Exception)
def unhandled_exception(e):
    if isinstance(e, HTTPException):
        if e.code == 404:
            return not_found_error(e)
        if e.code == 413:
            return request_entity_too_large(e)
        if e.code == 429:
            return ratelimit_handler(e)
        return e

    import traceback
    tb = traceback.format_exc()
    log_error_to_db(
        tool=getattr(g, "tool_name", getattr(g, "inferred_tool", "unknown")),
        error_type=type(e).__name__,
        error_message=str(e),
        http_status=500,
        traceback_text=tb,
    )
    if request.is_json or request.path.startswith("/api/") or "application/json" in request.headers.get("Accept", ""):
        return jsonify({"error": "An unexpected error occurred during processing."}), 500
    return jsonify({"error": "Internal server error"}), 500


# ================= SUPPORT TICKETS (USER ENDPOINTS) =================

@app.route("/api/tickets", methods=["POST"])
@login_required
@limiter.limit("5 per hour")
def create_ticket():
    if not current_user.is_authenticated:
        return jsonify({"error": "Please register or log in to report a problem."}), 401

    t_type = (request.form.get("type") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    message = (request.form.get("message") or "").strip()
    page_url = (request.form.get("page_url") or "").strip()[:500]
    user_agent = (request.form.get("user_agent") or request.user_agent.string or "").strip()[:500]

    valid_types = {"Bug", "Problem", "Feature request", "Help / Question", "Help/Question"}
    if t_type not in valid_types:
        t_type = "Problem"
    elif t_type == "Help/Question":
        t_type = "Help / Question"

    if not subject:
        return jsonify({"error": "Subject is required."}), 400
    if len(subject) > 120:
        return jsonify({"error": "Subject must not exceed 120 characters."}), 400

    if not message:
        return jsonify({"error": "Message is required."}), 400
    if len(message) > 2000:
        return jsonify({"error": "Message must not exceed 2000 characters."}), 400

    subject = html.escape(subject)
    message = html.escape(message)
    page_url = html.escape(page_url) if page_url else None
    user_agent = html.escape(user_agent) if user_agent else None

    # Screenshot upload handling
    screenshot_filename = None
    if "screenshot" in request.files:
        f = request.files["screenshot"]
        if f and f.filename:
            ext = os.path.splitext(f.filename)[1].lower()
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                return jsonify({"error": "Invalid screenshot format. Only PNG, JPG, and WEBP are supported."}), 400

            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(0)
            if size > 3 * 1024 * 1024:
                return jsonify({"error": "Screenshot size exceeds the 3 MB limit."}), 400

            try:
                from PIL import Image
                im = Image.open(f)
                im.verify()
                f.seek(0)
            except Exception:
                return jsonify({"error": "Uploaded file is not a valid image."}), 400

            random_name = f"{uuid.uuid4().hex}{ext}"
            save_path = TICKET_SCREENSHOT_DIR / random_name
            f.save(save_path)
            screenshot_filename = random_name

    ticket = Ticket(
        user_id=current_user.id,
        type=t_type,
        subject=subject,
        message=message,
        page_url=page_url,
        user_agent=user_agent,
        screenshot_path=screenshot_filename,
        status="open",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.session.add(ticket)
    db.session.commit()

    return jsonify({
        "ok": True,
        "ticket_id": ticket.id,
        "message": f"Thanks, ticket #{ticket.id} received",
    }), 201


@app.route("/api/tickets/mine", methods=["GET"])
@login_required
def get_my_tickets():
    if not current_user.is_authenticated:
        return jsonify({"error": "Please register or log in to view your tickets."}), 401

    tickets = Ticket.query.filter_by(user_id=current_user.id).order_by(Ticket.created_at.desc()).all()
    return jsonify({
        "ok": True,
        "tickets": [t.to_dict() for t in tickets],
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
