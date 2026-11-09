from datetime import datetime
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash
from sqlalchemy import Index, text

db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_login = db.Column(db.DateTime, nullable=True, index=True)
    login_count = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    # Relationships
    login_events = db.relationship(
        "LoginEvent",
        backref="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
        order_by="LoginEvent.logged_in_at.desc()"
    )
    history_entries = db.relationship(
        "FileHistory",
        backref="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
        order_by="FileHistory.created_at.desc()"
    )
    tickets = db.relationship(
        "Ticket",
        backref="user",
        cascade="all, delete-orphan",
        lazy="dynamic",
        order_by="Ticket.created_at.desc()"
    )

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email or "—",
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "last_login": self.last_login.strftime("%Y-%m-%d %H:%M") if self.last_login else "Never",
            "login_count": self.login_count,
            "is_active": self.is_active,
        }


class LoginEvent(db.Model):
    __tablename__ = "login_events"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    logged_in_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else "Unknown",
            "ip": self.ip or "—",
            "user_agent": self.user_agent or "—",
            "logged_in_at": self.logged_in_at.strftime("%Y-%m-%d %H:%M:%S") if self.logged_in_at else "",
        }


class FileHistory(db.Model):
    __tablename__ = "file_history"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    tool_name = db.Column(db.String(128), nullable=False)
    output_name = db.Column(db.String(255), nullable=False)
    output_path = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)


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

    __table_args__ = (
        Index("idx_tickets_status_created", "status", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else "Unknown",
            "email": self.user.email if self.user and self.user.email else "—",
            "type": self.type,
            "subject": self.subject,
            "message": self.message,
            "page_url": self.page_url or "",
            "user_agent": self.user_agent or "",
            "screenshot_path": self.screenshot_path or "",
            "has_screenshot": bool(self.screenshot_path),
            "status": self.status,
            "admin_note": self.admin_note or "",
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M") if self.created_at else "",
            "updated_at": self.updated_at.strftime("%Y-%m-%d %H:%M") if self.updated_at else "",
        }


class SiteSetting(db.Model):
    __tablename__ = "site_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=False, default="")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "updated_at": self.updated_at.isoformat() if self.updated_at else "",
        }


class ProcessingJob(db.Model):
    __tablename__ = "processing_jobs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    tool = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(20), default="processing", nullable=False, index=True)  # processing, success, failed
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    duration_ms = db.Column(db.Integer, nullable=True)
    input_size_bytes = db.Column(db.Integer, nullable=True)
    error_id = db.Column(db.Integer, db.ForeignKey("error_logs.id", ondelete="SET NULL"), nullable=True)

    user = db.relationship("User", foreign_keys=[user_id], backref=db.backref("processing_jobs", lazy="dynamic"))
    error_log = db.relationship("ErrorLog", foreign_keys=[error_id])

    __table_args__ = (
        Index("idx_jobs_tool_started", "tool", "started_at"),
        Index("idx_jobs_status_started", "status", "started_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.user.username if self.user else "Guest",
            "tool": self.tool,
            "status": self.status,
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S") if self.started_at else "",
            "finished_at": self.finished_at.strftime("%Y-%m-%d %H:%M:%S") if self.finished_at else "In progress",
            "duration_ms": self.duration_ms or 0,
            "input_size_bytes": self.input_size_bytes or 0,
            "error_id": self.error_id,
        }


class ErrorLog(db.Model):
    __tablename__ = "error_logs"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    username_snapshot = db.Column(db.String(80), nullable=True)
    tool = db.Column(db.String(64), nullable=True, index=True)
    error_type = db.Column(db.String(40), nullable=False, index=True)
    error_message = db.Column(db.String(500), nullable=True)
    traceback = db.Column(db.String(4000), nullable=True)
    http_status = db.Column(db.Integer, nullable=True)
    route = db.Column(db.String(255), nullable=True)
    ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    status = db.Column(db.String(20), default="new", nullable=False, index=True)  # new, investigating, fixed
    admin_note = db.Column(db.Text, nullable=True)

    user = db.relationship("User", foreign_keys=[user_id])

    __table_args__ = (
        Index("idx_errors_created_type", "created_at", "error_type"),
        Index("idx_errors_status_created", "status", "created_at"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username_snapshot or (self.user.username if self.user else "Guest"),
            "email": self.user.email if self.user and self.user.email else "—",
            "tool": self.tool or "system",
            "error_type": self.error_type,
            "error_message": self.error_message or "",
            "traceback": self.traceback or "",
            "http_status": self.http_status or 500,
            "route": self.route or "",
            "ip": self.ip or "—",
            "user_agent": self.user_agent or "—",
            "created_at": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else "",
            "status": self.status,
            "admin_note": self.admin_note or "",
        }


def init_or_migrate_db(app):
    """
    Ensures all tables, columns, and indexes exist.
    Safe to run on every startup (idempotent for SQLite and PostgreSQL).
    """
    with app.app_context():
        db.create_all()
        try:
            with db.engine.connect() as conn:
                dialect = db.engine.dialect.name
                if dialect == "sqlite":
                    # Check columns on users table
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
                                conn.rollback()
                    # Safe index creation
                    indexes = [
                        "CREATE INDEX IF NOT EXISTS idx_users_last_login ON users(last_login)",
                        "CREATE INDEX IF NOT EXISTS idx_login_events_logged ON login_events(logged_in_at)",
                        "CREATE INDEX IF NOT EXISTS idx_tickets_status_created ON tickets(status, created_at)",
                        "CREATE INDEX IF NOT EXISTS idx_errors_created_type ON error_logs(created_at, error_type)",
                        "CREATE INDEX IF NOT EXISTS idx_errors_status_created ON error_logs(status, created_at)",
                        "CREATE INDEX IF NOT EXISTS idx_jobs_tool_started ON processing_jobs(tool, started_at)",
                        "CREATE INDEX IF NOT EXISTS idx_jobs_status_started ON processing_jobs(status, started_at)",
                    ]
                    for idx_sql in indexes:
                        try:
                            conn.execute(text(idx_sql))
                            conn.commit()
                        except Exception:
                            conn.rollback()

                elif dialect == "postgresql":
                    statements = [
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(120)",
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_login TIMESTAMP",
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS login_count INTEGER DEFAULT 0",
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
                        "CREATE INDEX IF NOT EXISTS idx_users_last_login ON users(last_login)",
                        "CREATE INDEX IF NOT EXISTS idx_login_events_logged ON login_events(logged_in_at)",
                        "CREATE INDEX IF NOT EXISTS idx_tickets_status_created ON tickets(status, created_at)",
                        "CREATE INDEX IF NOT EXISTS idx_errors_created_type ON error_logs(created_at, error_type)",
                        "CREATE INDEX IF NOT EXISTS idx_errors_status_created ON error_logs(status, created_at)",
                        "CREATE INDEX IF NOT EXISTS idx_jobs_tool_started ON processing_jobs(tool, started_at)",
                        "CREATE INDEX IF NOT EXISTS idx_jobs_status_started ON processing_jobs(status, started_at)",
                    ]
                    for stmt in statements:
                        try:
                            conn.execute(text(stmt))
                            conn.commit()
                        except Exception:
                            conn.rollback()
        except Exception as e:
            print(f"[Warning] DB index/column check: {e}", flush=True)
