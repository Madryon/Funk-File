import os
import time
import uuid
from pathlib import Path
from flask import Flask, request, send_file, render_template, jsonify, g
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.utils import secure_filename
import pdftool
import pdftool_scan as scanner
import pdftool_video as vidtool
from auth import login_manager, auth_bp
from history import history_bp, create_history_entry
from models import db
from scheduler import start_cleanup_scheduler

from utils import BASE_DIR, UPLOAD_DIR, OUTPUT_DIR

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
app.config["JSON_SORT_KEYS"] = False
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
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-me").strip().strip("'\"")

db.init_app(app)
login_manager.init_app(app)
from extensions import limiter
limiter.init_app(app)
app.register_blueprint(auth_bp)
app.register_blueprint(history_bp)

from routes_pdf import pdf_bp
from routes_image import image_bp
from routes_video import video_bp
from routes_scan import scan_bp

app.register_blueprint(pdf_bp)
app.register_blueprint(image_bp)
app.register_blueprint(video_bp)
app.register_blueprint(scan_bp)


with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print(f"[Warning] Failed to initialize database tables: {e}", flush=True)




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
def health():
    return jsonify({"ok": True, "version": "2.0"})



@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "file too large (max 500MB)"}), 413


@app.errorhandler(404)
def not_found(_e):
    return jsonify({"error": "not found"}), 404


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
