import hmac
import json
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
    make_response,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from auth import generate_reset_token
from extensions import limiter
from models import (
    ErrorLog,
    LoginEvent,
    ProcessingJob,
    SiteSetting,
    Ticket,
    User,
    db,
)
from utils import UPLOAD_DIR

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

TICKET_SCREENSHOT_DIR = UPLOAD_DIR / "tickets"
TICKET_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

# ----------------------------------------------------------------------
# Admin Authentication & Password Helpers
# ----------------------------------------------------------------------

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
            # Security Rule: Never expose admin routes or existence to non-admins.
            # Non-admins always receive a standard 404 Not Found.
            abort(404)
        return f(*args, **kwargs)
    return decorated_function


# ----------------------------------------------------------------------
# 30-Day Retention Auto-Purge
# ----------------------------------------------------------------------

_last_purge_time = 0

def maybe_purge_old_records():
    global _last_purge_time
    now = time.time()
    # Run at most once every 6 hours
    if now - _last_purge_time < 21600:
        return
    _last_purge_time = now
    try:
        cutoff = datetime.utcnow() - timedelta(days=30)
        ProcessingJob.query.filter(ProcessingJob.started_at < cutoff).delete()
        ErrorLog.query.filter(ErrorLog.created_at < cutoff).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()


# ----------------------------------------------------------------------
# Auth Endpoints
# ----------------------------------------------------------------------

@admin_bp.route("/login", methods=["GET", "POST"])
@admin_bp.route("/login/", methods=["GET", "POST"])
@limiter.limit("5 per 15 minutes", methods=["POST"])
def login():
    if is_admin_authenticated():
        return redirect(url_for("admin.dashboard"))

    admin_pass = get_admin_password()

    if request.method == "GET":
        cfg_warning = None
        if not admin_pass:
            cfg_warning = "ADMIN_PASSWORD is not configured on server. Admin login is currently disabled."
        return render_template("admin.html", mode="login", error=cfg_warning)

    if not admin_pass:
        msg = "Admin access is disabled because ADMIN_PASSWORD is not configured on the server."
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            return jsonify({"error": msg}), 503
        return render_template("admin.html", mode="login", error=msg), 503

    payload = request.get_json(silent=True) if request.is_json else request.form
    password = (payload.get("password") or "").strip()

    # Compare constant time to avoid timing attacks
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


# ----------------------------------------------------------------------
# Admin Dashboard Shell View
# ----------------------------------------------------------------------

@admin_bp.route("", methods=["GET"])
@admin_bp.route("/", methods=["GET"])
@admin_required
def dashboard():
    login_time = session.get("admin_login_time", time.time())
    remaining_secs = max(0, int(1800 - (time.time() - login_time)))

    # Initial lightweight query for top shell counts
    open_tickets = Ticket.query.filter(Ticket.status.in_(["open", "in_progress"])).count()

    return render_template(
        "admin.html",
        mode="dashboard",
        remaining_secs=remaining_secs,
        open_tickets_count=open_tickets,
    )


# ----------------------------------------------------------------------
# Live Ops & Dashboard Aggregate Stats API
# ----------------------------------------------------------------------

@admin_bp.route("/api/live-ops", methods=["GET"])
@admin_required
def api_live_ops():
    maybe_purge_old_records()

    now = datetime.utcnow()
    last_24h = now - timedelta(hours=24)
    last_15m = now - timedelta(minutes=15)

    # 1. Single aggregate query for ProcessingJob stats in last 24h
    job_stats = db.session.query(
        db.func.count(ProcessingJob.id).label("total_24h"),
        db.func.sum(db.case((ProcessingJob.status == "failed", 1), else_=0)).label("failed_24h"),
        db.func.avg(db.case((ProcessingJob.status == "success", ProcessingJob.duration_ms), else_=None)).label("avg_latency_24h"),
    ).filter(ProcessingJob.started_at >= last_24h).one()

    total_jobs_24h = job_stats.total_24h or 0
    failed_jobs_24h = int(job_stats.failed_24h or 0)
    avg_latency_ms = round(float(job_stats.avg_latency_24h or 0), 1)

    error_rate_24h = 0.0
    if total_jobs_24h > 0:
        error_rate_24h = round((failed_jobs_24h / total_jobs_24h) * 100.0, 1)

    # 2. Total all-time jobs count
    total_jobs_all_time = ProcessingJob.query.count()

    # 3. Active users in last 15 minutes
    active_users_15m = User.query.filter(User.last_login >= last_15m).count()

    # 4. Total users and open tickets
    total_users = User.query.count()
    open_tickets = Ticket.query.filter(Ticket.status.in_(["open", "in_progress"])).count()
    new_errors_count = ErrorLog.query.filter_by(status="new").count()

    # 5. Site status settings
    ann_setting = SiteSetting.query.filter_by(key="announcement").first()
    ann_data = json.loads(ann_setting.value) if ann_setting and ann_setting.value else {}
    ann_active = bool(ann_data.get("active", False))

    maint_setting = SiteSetting.query.filter_by(key="maintenance").first()
    maint_data = json.loads(maint_setting.value) if maint_setting and maint_setting.value else {}
    maint_active = bool(maint_data.get("active", False))

    login_time = session.get("admin_login_time", time.time())
    remaining_secs = max(0, int(1800 - (time.time() - login_time)))

    return jsonify({
        "ok": True,
        "kpis": {
            "total_jobs_24h": total_jobs_24h,
            "total_jobs_all_time": total_jobs_all_time,
            "failed_jobs_24h": failed_jobs_24h,
            "error_rate_24h": error_rate_24h,
            "avg_latency_ms": avg_latency_ms,
            "active_users_15m": active_users_15m,
            "total_users": total_users,
            "open_tickets": open_tickets,
            "new_errors_count": new_errors_count,
            "announcement_active": ann_active,
            "maintenance_active": maint_active,
        },
        "session": {
            "remaining_secs": remaining_secs,
        }
    })


# ----------------------------------------------------------------------
# Visual Charts API (Lightweight JSON for SVG/Canvas)
# ----------------------------------------------------------------------

@admin_bp.route("/api/charts", methods=["GET"])
@admin_required
def api_charts():
    now = datetime.utcnow()
    last_24h = now - timedelta(hours=24)

    # 1. Jobs processed per tool in last 24h
    tool_counts = db.session.query(
        ProcessingJob.tool,
        db.func.count(ProcessingJob.id).label("count")
    ).filter(ProcessingJob.started_at >= last_24h).group_by(ProcessingJob.tool).order_by(db.desc("count")).limit(10).all()

    tool_data = [{"tool": r[0] or "unknown", "count": r[1]} for r in tool_counts]

    # 2. Errors by type in last 24h
    error_counts = db.session.query(
        ErrorLog.error_type,
        db.func.count(ErrorLog.id).label("count")
    ).filter(ErrorLog.created_at >= last_24h).group_by(ErrorLog.error_type).order_by(db.desc("count")).limit(8).all()

    error_data = [{"type": r[0] or "Error", "count": r[1]} for r in error_counts]

    # 3. 24h hourly volume (24 discrete buckets)
    # Generate 24 hour slots starting from 23 hours ago to current hour
    hourly_slots = []
    current_hour_start = datetime(now.year, now.month, now.day, now.hour)
    for h in range(23, -1, -1):
        slot_dt = current_hour_start - timedelta(hours=h)
        hourly_slots.append(slot_dt)

    # Fetch all jobs in last 24h with timestamps
    recent_jobs = db.session.query(ProcessingJob.started_at).filter(ProcessingJob.started_at >= last_24h).all()
    hourly_counts = {slot: 0 for slot in hourly_slots}
    for (st,) in recent_jobs:
        if st:
            bucket = datetime(st.year, st.month, st.day, st.hour)
            if bucket in hourly_counts:
                hourly_counts[bucket] += 1

    hourly_volume = [
        {"hour": slot.strftime("%H:00"), "count": hourly_counts[slot]}
        for slot in hourly_slots
    ]

    return jsonify({
        "ok": True,
        "tools": tool_data,
        "errors": error_data,
        "hourly_volume": hourly_volume,
    })


# ----------------------------------------------------------------------
# Paginated Errors Log API & Actions
# ----------------------------------------------------------------------

@admin_bp.route("/api/errors", methods=["GET"])
@admin_required
def api_errors_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 25
    tool_filter = (request.args.get("tool") or "").strip()
    type_filter = (request.args.get("type") or "").strip()
    status_filter = (request.args.get("status") or "").strip()
    date_filter = (request.args.get("date") or "").strip()

    query = ErrorLog.query

    if tool_filter and tool_filter != "all":
        query = query.filter(ErrorLog.tool == tool_filter)
    if type_filter and type_filter != "all":
        query = query.filter(ErrorLog.error_type == type_filter)
    if status_filter and status_filter != "all":
        query = query.filter(ErrorLog.status == status_filter)
    if date_filter:
        try:
            target_date = datetime.strptime(date_filter, "%Y-%m-%d").date()
            start_dt = datetime.combine(target_date, datetime.min.time())
            end_dt = datetime.combine(target_date, datetime.max.time())
            query = query.filter(ErrorLog.created_at >= start_dt, ErrorLog.created_at <= end_dt)
        except ValueError:
            pass

    pagination = query.order_by(ErrorLog.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "ok": True,
        "items": [e.to_dict() for e in pagination.items],
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
    })


@admin_bp.route("/api/errors/<int:error_id>/status", methods=["POST"])
@admin_required
def api_update_error_status(error_id):
    elog = db.session.get(ErrorLog, error_id)
    if not elog:
        return jsonify({"error": "Error log not found"}), 404

    payload = request.get_json(silent=True) or request.form
    new_status = (payload.get("status") or "").strip()
    note = (payload.get("admin_note") or "").strip()

    if new_status in ("new", "investigating", "fixed"):
        elog.status = new_status
    if "admin_note" in payload:
        elog.admin_note = note or None

    db.session.commit()
    return jsonify({"ok": True, "error": elog.to_dict()})


@admin_bp.route("/api/errors/<int:error_id>/delete", methods=["POST"])
@admin_required
def api_delete_error(error_id):
    elog = db.session.get(ErrorLog, error_id)
    if not elog:
        return jsonify({"error": "Error log not found"}), 404

    db.session.delete(elog)
    db.session.commit()
    return jsonify({"ok": True})


# ----------------------------------------------------------------------
# Paginated Jobs API
# ----------------------------------------------------------------------

@admin_bp.route("/api/jobs", methods=["GET"])
@admin_required
def api_jobs_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 25
    tool_filter = (request.args.get("tool") or "").strip()
    status_filter = (request.args.get("status") or "").strip()

    query = ProcessingJob.query
    if tool_filter and tool_filter != "all":
        query = query.filter(ProcessingJob.tool == tool_filter)
    if status_filter and status_filter != "all":
        query = query.filter(ProcessingJob.status == status_filter)

    pagination = query.order_by(ProcessingJob.started_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "ok": True,
        "items": [j.to_dict() for j in pagination.items],
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
    })


# ----------------------------------------------------------------------
# Paginated Users & Logins API
# ----------------------------------------------------------------------

@admin_bp.route("/api/users", methods=["GET"])
@admin_required
def api_users_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 25
    search = (request.args.get("search") or "").strip().lower()

    query = User.query
    if search:
        query = query.filter(
            (User.username.ilike(f"%{search}%")) | (User.email.ilike(f"%{search}%"))
        )

    pagination = query.order_by(User.id.desc()).paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "ok": True,
        "items": [u.to_dict() for u in pagination.items],
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
    })


@admin_bp.route("/api/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def api_toggle_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    user.is_active = not user.is_active
    db.session.commit()
    return jsonify({"ok": True, "is_active": user.is_active, "username": user.username})


@admin_bp.route("/api/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def api_delete_user(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    username = user.username
    db.session.delete(user)
    db.session.commit()
    return jsonify({"ok": True, "message": f"User '{username}' deleted."})


@admin_bp.route("/api/users/<int:user_id>/reset-link", methods=["POST"])
@admin_required
def api_generate_reset_link(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    token = generate_reset_token(user.id)
    reset_url = url_for("auth.reset_password", token=token, _external=True)
    return jsonify({"ok": True, "username": user.username, "reset_url": reset_url})


@admin_bp.route("/api/logins", methods=["GET"])
@admin_required
def api_logins_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 25

    pagination = LoginEvent.query.order_by(LoginEvent.logged_in_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "ok": True,
        "items": [l.to_dict() for l in pagination.items],
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
    })


# ----------------------------------------------------------------------
# Paginated Tickets API & Actions
# ----------------------------------------------------------------------

@admin_bp.route("/api/tickets", methods=["GET"])
@admin_required
def api_tickets_list():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 25
    status_filter = (request.args.get("status") or "").strip()

    query = Ticket.query
    if status_filter and status_filter != "all":
        query = query.filter(Ticket.status == status_filter)

    pagination = query.order_by(Ticket.created_at.desc()).paginate(page=page, per_page=per_page, error_out=False)

    return jsonify({
        "ok": True,
        "items": [t.to_dict() for t in pagination.items],
        "page": pagination.page,
        "pages": pagination.pages,
        "total": pagination.total,
    })


@admin_bp.route("/api/tickets/<int:ticket_id>/update", methods=["POST"])
@admin_required
def api_update_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    payload = request.get_json(silent=True) or request.form
    status = (payload.get("status") or "").strip()
    admin_note = (payload.get("admin_note") or "").strip()

    if status in ("open", "in_progress", "resolved", "closed"):
        ticket.status = status
    ticket.admin_note = admin_note or None
    ticket.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({"ok": True, "ticket": ticket.to_dict()})


@admin_bp.route("/api/tickets/<int:ticket_id>/delete", methods=["POST"])
@admin_required
def api_delete_ticket(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    if ticket.screenshot_path:
        sc_file = TICKET_SCREENSHOT_DIR / ticket.screenshot_path
        try:
            sc_file.unlink(missing_ok=True)
        except OSError:
            pass

    db.session.delete(ticket)
    db.session.commit()
    return jsonify({"ok": True})


@admin_bp.route("/tickets/<int:ticket_id>/screenshot", methods=["GET"])
@admin_required
def ticket_screenshot(ticket_id):
    ticket = db.session.get(Ticket, ticket_id)
    if not ticket or not ticket.screenshot_path:
        abort(404)

    s_path = (TICKET_SCREENSHOT_DIR / ticket.screenshot_path).resolve()
    if not s_path.is_file() or not str(s_path).startswith(str(TICKET_SCREENSHOT_DIR.resolve())):
        abort(404)

    ext = s_path.suffix.lower()
    mimes = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp"
    }
    return send_file(str(s_path), mimetype=mimes.get(ext, "application/octet-stream"))


# ----------------------------------------------------------------------
# Site Settings: Announcement Board API
# ----------------------------------------------------------------------

@admin_bp.route("/api/settings/announcement", methods=["GET"])
@admin_required
def api_get_announcement():
    row = SiteSetting.query.filter_by(key="announcement").first()
    data = json.loads(row.value) if row and row.value else {}
    return jsonify({
        "ok": True,
        "announcement": {
            "active": bool(data.get("active", False)),
            "text": data.get("text", ""),
            "type": data.get("type", "info"),
            "expires_at": data.get("expires_at"),
            "id": data.get("id", "ann_0"),
        }
    })


@admin_bp.route("/api/settings/announcement", methods=["POST"])
@admin_required
def api_save_announcement():
    payload = request.get_json(silent=True) or request.form
    active = bool(payload.get("active"))
    text = (payload.get("text") or "").strip()
    ann_type = payload.get("type", "info")
    expires_at = payload.get("expires_at") or None

    if ann_type not in ("info", "warning", "success"):
        ann_type = "info"

    if active and not text:
        return jsonify({"error": "Announcement text is required when active."}), 400

    if len(text) > 500:
        return jsonify({"error": "Announcement text cannot exceed 500 characters."}), 400

    row = SiteSetting.query.filter_by(key="announcement").first()
    if not row:
        row = SiteSetting(key="announcement")
        db.session.add(row)

    # Unique id to track dismissals per announcement text update
    ann_id = f"ann_{int(time.time())}"

    config = {
        "active": active,
        "text": text,
        "type": ann_type,
        "expires_at": expires_at,
        "id": ann_id,
    }
    row.value = json.dumps(config)
    row.updated_at = datetime.utcnow()
    db.session.commit()

    # Invalidate in-memory status cache
    try:
        from app import invalidate_site_status_cache
        invalidate_site_status_cache()
    except Exception:
        pass

    return jsonify({"ok": True, "announcement": config})


# ----------------------------------------------------------------------
# Site Settings: Maintenance Mode API
# ----------------------------------------------------------------------

@admin_bp.route("/api/settings/maintenance", methods=["GET"])
@admin_required
def api_get_maintenance():
    row = SiteSetting.query.filter_by(key="maintenance").first()
    data = json.loads(row.value) if row and row.value else {}
    ends_at_str = data.get("ends_at")
    ends_at_ist = None
    if ends_at_str:
        try:
            dt = datetime.fromisoformat(ends_at_str.replace("Z", "+00:00")).replace(tzinfo=None)
            ist_dt = dt + timedelta(hours=5, minutes=30)
            ends_at_ist = ist_dt.strftime("%d %b %Y, %I:%M %p IST")
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "maintenance": {
            "active": bool(data.get("active", False)),
            "message": data.get("message", "We are currently undergoing scheduled maintenance. Please check back shortly."),
            "ends_at": ends_at_str,
            "ends_at_ist": ends_at_ist,
        }
    })


@admin_bp.route("/api/settings/maintenance", methods=["POST"])
@admin_required
def api_save_maintenance():
    payload = request.get_json(silent=True) or request.form
    active = bool(payload.get("active"))
    message = (payload.get("message") or "").strip()
    raw_mins = payload.get("duration_mins")
    try:
        duration_mins = int(raw_mins) if raw_mins is not None else None
    except (ValueError, TypeError):
        duration_mins = None
    ends_at_custom = payload.get("ends_at_custom")

    if not message:
        message = "We are currently undergoing scheduled maintenance. Please check back shortly."

    ends_at_iso = None

    if active:
        now = datetime.utcnow()
        if duration_mins and duration_mins > 0:
            target_dt = now + timedelta(minutes=duration_mins)
            ends_at_iso = target_dt.isoformat() + "Z"
        elif ends_at_custom:
            try:
                # Expects 'YYYY-MM-DDTHH:MM' or similar in local IST time
                raw = str(ends_at_custom).replace("Z", "")
                parsed = datetime.fromisoformat(raw)
                # Convert from IST (UTC+5:30) to UTC
                utc_dt = parsed - timedelta(hours=5, minutes=30)
                ends_at_iso = utc_dt.isoformat() + "Z"
            except Exception:
                return jsonify({"error": "Invalid end datetime format. Please select a valid date & time."}), 400
        else:
            # Default to 30 mins if none provided
            target_dt = now + timedelta(minutes=30)
            ends_at_iso = target_dt.isoformat() + "Z"

    row = SiteSetting.query.filter_by(key="maintenance").first()
    if not row:
        row = SiteSetting(key="maintenance")
        db.session.add(row)

    config = {
        "active": active,
        "message": message,
        "ends_at": ends_at_iso if active else None,
    }
    row.value = json.dumps(config)
    row.updated_at = datetime.utcnow()
    db.session.commit()

    # Invalidate in-memory status cache
    try:
        from app import invalidate_site_status_cache
        invalidate_site_status_cache()
    except Exception:
        pass

    # Format IST for response
    ends_at_ist = None
    if ends_at_iso:
        try:
            dt = datetime.fromisoformat(ends_at_iso.replace("Z", "+00:00")).replace(tzinfo=None)
            ist_dt = dt + timedelta(hours=5, minutes=30)
            ends_at_ist = ist_dt.strftime("%d %b %Y, %I:%M %p IST")
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "maintenance": {
            "active": active,
            "message": message,
            "ends_at": ends_at_iso if active else None,
            "ends_at_ist": ends_at_ist,
        }
    })
