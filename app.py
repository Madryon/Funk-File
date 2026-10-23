import os
import time
import shutil
import html
import uuid
from datetime import timedelta, datetime
from flask import Flask, render_template, jsonify, g, request, session, redirect, url_for, abort, send_file, flash
from flask_login import current_user, login_required
from flask_wtf.csrf import CSRFError

from auth import login_manager, auth_bp
from admin import admin_bp, admin_required, is_admin_authenticated
from history import history_bp, create_history_entry
from models import db, User, LoginEvent
from scheduler import start_cleanup_scheduler
from extensions import limiter, csrf
from utils import BASE_DIR, UPLOAD_DIR, OUTPUT_DIR

TICKET_SCREENSHOT_DIR = UPLOAD_DIR / "tickets"
TICKET_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


class Ticket(db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = db.Column(db.String(50), nullable=False)
    subject = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False)
    page_url = db.Column(db.String(500), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    screenshot_path = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(20), default="open", nullable=False, index=True)
    admin_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user = db.relationship("User", backref=db.backref("tickets", cascade="all, delete-orphan", lazy="dynamic", order_by="Ticket.created_at.desc()"))

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
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
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "true").lower() in ("true", "1")
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)

# Initialize Extensions
db.init_app(app)
login_manager.init_app(app)
limiter.init_app(app)
csrf.init_app(app)

# Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(history_bp)

# Hidden Admin Panel: registered and active (defaults to 'AdminPass2026!' if ADMIN_PASSWORD is unset)
app.register_blueprint(admin_bp)

# Register Tool Blueprints
from routes_pdf import pdf_bp
from routes_image import image_bp
from routes_video import video_bp
from routes_scan import scan_bp

app.register_blueprint(pdf_bp)
app.register_blueprint(image_bp)
app.register_blueprint(video_bp)
app.register_blueprint(scan_bp)


# ================= DATABASE INITIALIZATION & ROBUST MIGRATION =================

def migrate_database():
    try:
        db.create_all()
        with db.engine.connect() as conn:
            from sqlalchemy import text
            dialect = db.engine.dialect.name
            if dialect == "postgresql":
                statements = [
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(120)",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS login_count INTEGER DEFAULT 0",
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
                    "UPDATE users SET email = LOWER(username) || '@pdf-toolbox.local' WHERE email IS NULL OR email = ''",
                    "UPDATE users SET is_active = TRUE WHERE is_active IS NULL",
                    "UPDATE users SET login_count = 0 WHERE login_count IS NULL",
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)",
                    """CREATE TABLE IF NOT EXISTS login_events (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        ip VARCHAR(64),
                        user_agent VARCHAR(255),
                        logged_in_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )""",
                    "CREATE INDEX IF NOT EXISTS ix_login_events_user_id ON login_events (user_id)",
                    "CREATE INDEX IF NOT EXISTS ix_login_events_logged_in_at ON login_events (logged_in_at)",
                    """CREATE TABLE IF NOT EXISTS tickets (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        type VARCHAR(50) NOT NULL,
                        subject VARCHAR(120) NOT NULL,
                        message TEXT NOT NULL,
                        page_url VARCHAR(500),
                        user_agent VARCHAR(500),
                        screenshot_path VARCHAR(255),
                        status VARCHAR(20) DEFAULT 'open' NOT NULL,
                        admin_note TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL
                    )""",
                    "CREATE INDEX IF NOT EXISTS ix_tickets_user_id ON tickets (user_id)",
                    "CREATE INDEX IF NOT EXISTS ix_tickets_status ON tickets (status)",
                    "CREATE INDEX IF NOT EXISTS ix_tickets_created_at ON tickets (created_at)"
                ]
                for stmt in statements:
                    try:
                        conn.execute(text(stmt))
                        conn.commit()
                    except Exception:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
            elif dialect == "sqlite":
                res = conn.execute(text("PRAGMA table_info(users)"))
                cols = {row[1] for row in res.fetchall()}
                for col, typ in [
                    ("email", "VARCHAR(120)"),
                    ("last_login", "DATETIME"),
                    ("login_count", "INTEGER DEFAULT 0"),
                    ("is_active", "BOOLEAN DEFAULT 1"),
                ]:
                    if cols and col not in cols:
                        try:
                            conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {typ}"))
                            conn.commit()
                        except Exception:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
    except Exception as e:
        print(f"[Warning] Failed to initialize/migrate database tables: {e}", flush=True)


_db_migrated = False

@app.before_request
def _ensure_migrated():
    global _db_migrated
    if not _db_migrated:
        migrate_database()
        _db_migrated = True


with app.app_context():
    migrate_database()
    _db_migrated = True


@app.route("/migrate-db")
@csrf.exempt
def run_migration_endpoint():
    migrate_database()
    return jsonify({"ok": True, "message": "Database migration executed."})



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


@app.after_request
def _log_history(response):
    if response.status_code >= 400:
        return response
    tool_name = getattr(g, "tool_name", None)
    output_name = getattr(g, "output_name", None)
    output_path = getattr(g, "output_path", None)
    if not tool_name or not output_name or not output_path:
        return response
    try:
        create_history_entry(tool_name, output_name, output_path)
    except Exception:
        pass
    return response


@app.route("/")
def index():
    _cleanup_old()
    return render_template("index.html")


@app.route("/health")
@csrf.exempt
def health():
    return jsonify({"ok": True, "version": "2.0"})


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"error": f"CSRF error: {e.description}"}), 400
    return render_template("login.html", error=f"Session expired or invalid security token ({e.description}). Please try again."), 400


@app.errorhandler(429)
def ratelimit_handler(_e):
    return jsonify({"error": "Too many attempts. Please wait a minute and try again."}), 429


@app.errorhandler(413)
def request_entity_too_large(error):
    return jsonify({"error": "File is too large. Please upload a smaller file."}), 413


@app.errorhandler(404)
def not_found_error(error):
    if request.path.startswith("/static/") or request.accept_mimetypes.accept_html:
        return error
    return jsonify({"error": "Requested resource or file was not found."}), 404


@app.errorhandler(500)
def internal_server_error(error):
    return jsonify({"error": "Server error while processing your file. Please try again."}), 500


# ================= TICKETS & HELP REPORTING =================

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

    # Sanitize text
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
        "message": f"Thanks, ticket #{ticket.id} received"
    }), 201


@app.route("/api/tickets/mine", methods=["GET"])
@login_required
def get_my_tickets():
    if not current_user.is_authenticated:
        return jsonify({"error": "Please register or log in to view your tickets."}), 401

    tickets = Ticket.query.filter_by(user_id=current_user.id).order_by(Ticket.created_at.desc()).all()
    return jsonify({
        "ok": True,
        "tickets": [
            {
                "id": t.id,
                "type": t.type,
                "subject": t.subject,
                "message": t.message,
                "page_url": t.page_url,
                "status": t.status,
                "admin_note": t.admin_note,
                "has_screenshot": bool(t.screenshot_path),
                "created_at": t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else "",
                "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M") if t.updated_at else "",
            }
            for t in tickets
        ]
    })


# ================= ADMIN TICKETS MANAGEMENT =================

def admin_dashboard_with_tickets():
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    week_start = today_start - timedelta(days=today_start.weekday())

    total_users = User.query.count()
    active_24h = User.query.filter(User.last_login >= now - timedelta(hours=24)).count()
    active_7d = User.query.filter(User.last_login >= now - timedelta(days=7)).count()
    total_logins = db.session.query(db.func.sum(User.login_count)).scalar() or LoginEvent.query.count()

    open_tickets = Ticket.query.filter(Ticket.status.in_(["open", "in_progress"])).count()
    tickets_today = Ticket.query.filter(Ticket.created_at >= today_start).count()
    resolved_this_week = Ticket.query.filter(Ticket.status == "resolved", Ticket.updated_at >= week_start).count()

    users = User.query.order_by(User.id.desc()).all()
    recent_logins = LoginEvent.query.order_by(LoginEvent.logged_in_at.desc()).limit(50).all()
    tickets = Ticket.query.order_by(Ticket.created_at.desc()).all()

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
            "open_tickets": open_tickets,
            "tickets_today": tickets_today,
            "resolved_this_week": resolved_this_week,
        },
        users=users,
        recent_logins=recent_logins,
        tickets=tickets,
        remaining_secs=remaining_secs,
    )

app.view_functions["admin.dashboard"] = admin_required(admin_dashboard_with_tickets)


@app.route("/admin/tickets/<int:ticket_id>/update", methods=["POST"])
@admin_required
def admin_update_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        abort(404)
    status = request.form.get("status")
    admin_note = request.form.get("admin_note", "").strip()
    if status in ("open", "in_progress", "resolved", "closed"):
        ticket.status = status
    ticket.admin_note = admin_note or None
    ticket.updated_at = datetime.utcnow()
    db.session.commit()
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "status": ticket.status, "admin_note": ticket.admin_note})
    flash(f"Ticket #{ticket.id} updated.", "success")
    return redirect(url_for("admin.dashboard") + "#tickets")


@app.route("/admin/tickets/<int:ticket_id>/delete", methods=["POST"])
@admin_required
def admin_delete_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        abort(404)
    if ticket.screenshot_path:
        s_file = TICKET_SCREENSHOT_DIR / ticket.screenshot_path
        try:
            s_file.unlink(missing_ok=True)
        except OSError:
            pass
    db.session.delete(ticket)
    db.session.commit()
    if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    flash(f"Ticket #{ticket_id} deleted.", "success")
    return redirect(url_for("admin.dashboard") + "#tickets")


@app.route("/admin/tickets/<int:ticket_id>/screenshot", methods=["GET"])
@admin_required
def admin_ticket_screenshot(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket or not ticket.screenshot_path:
        abort(404)
    s_path = (TICKET_SCREENSHOT_DIR / ticket.screenshot_path).resolve()
    if not s_path.is_file() or not str(s_path).startswith(str(TICKET_SCREENSHOT_DIR.resolve())):
        abort(404)
    ext = s_path.suffix.lower()
    mimetypes = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    return send_file(s_path, mimetype=mimetypes.get(ext, "application/octet-stream"))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
