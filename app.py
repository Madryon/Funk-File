import os
import time
from datetime import timedelta
from flask import Flask, render_template, jsonify, g, request
from flask_wtf.csrf import CSRFError

from auth import login_manager, auth_bp
from admin import admin_bp
from history import history_bp, create_history_entry
from models import db
from scheduler import start_cleanup_scheduler
from extensions import limiter, csrf
from utils import BASE_DIR, UPLOAD_DIR, OUTPUT_DIR

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
                    "CREATE INDEX IF NOT EXISTS ix_login_events_logged_in_at ON login_events (logged_in_at)"
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



def _cleanup_old(max_age_seconds=3600):
    cutoff = time.time() - max_age_seconds
    for d in (UPLOAD_DIR, OUTPUT_DIR):
        if not d.exists():
            continue
        for child in d.iterdir():
            try:
                if child.is_dir() and child.stat().st_mtime < cutoff:
                    for sub in child.rglob("*"):
                        try:
                            sub.unlink()
                        except OSError:
                            pass
                    try:
                        child.rmdir()
                    except OSError:
                        pass
                elif child.is_file() and child.stat().st_mtime < cutoff:
                    child.unlink()
            except OSError:
                pass


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
def too_large(_e):
    return jsonify({"error": "file too large (max 500MB)"}), 413


@app.errorhandler(404)
def not_found(_e):
    return jsonify({"error": "not found"}), 404


@app.errorhandler(500)
def server_error(e):
    import traceback
    traceback.print_exc()
    original_err = getattr(e, "original_exception", e)
    return jsonify({"error": str(original_err) or "Internal server error"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
