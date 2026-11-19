#!/usr/bin/env python3
"""
Diagnostic utility to test database connectivity (especially Aiven PostgreSQL).

Usage:
    python test_db_connection.py
    python test_db_connection.py "<DATABASE_URL>"

Reads from:
    1. Command line argument if provided
    2. .env file (DATABASE_URL, POSTGRES_URL, AIVEN_DATABASE_URL, or discrete PG* vars)
    3. Environment variables
    4. Fallback: local SQLite (app.db)
"""

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, quote_plus

# Ensure terminal handles UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

BASE_DIR = Path(__file__).resolve().parent


def load_env():
    env_file = BASE_DIR / ".env"
    if env_file.is_file():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception as e:
            print(f"[Warning] Could not read .env: {e}")


def mask_url(url: str) -> str:
    """Mask password in connection URI for safe console logging."""
    try:
        parsed = urlparse(url)
        if parsed.password:
            netloc = f"{parsed.username}:******@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return parsed._replace(netloc=netloc).geturl()
    except Exception:
        pass
    return url


def resolve_database_url() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip().strip("'\"")

    load_env()

    raw = (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("AIVEN_DATABASE_URL")
        or ""
    ).strip().strip("'\"")

    if raw.startswith("DATABASE_URL="):
        raw = raw[len("DATABASE_URL="):].strip().strip("'\"")

    # Support discrete PG* environment variables
    if not raw and os.environ.get("PGHOST"):
        pghost = os.environ.get("PGHOST", "").strip()
        pgport = os.environ.get("PGPORT", "5432").strip()
        pguser = os.environ.get("PGUSER", "avnadmin").strip()
        pgpass = os.environ.get("PGPASSWORD", "").strip()
        pgdb = os.environ.get("PGDATABASE", "defaultdb").strip()
        pgssl = os.environ.get("PGSSLMODE", "require").strip()
        raw = f"postgresql://{quote_plus(pguser)}:{quote_plus(pgpass)}@{pghost}:{pgport}/{pgdb}?sslmode={pgssl}"

    if not raw:
        return f"sqlite:///{BASE_DIR / 'app.db'}"

    url = raw
    if url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://"):]
    elif url.startswith("postgresql://") and not url.startswith("postgresql+"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]

    if url.startswith("postgresql") and "localhost" not in url and "127.0.0.1" not in url:
        if "sslmode=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}sslmode=require"

    return url


def main():
    print("=" * 60)
    print("  Funk Files Database Diagnostic Tool (Aiven & Cloud Ready)")
    print("=" * 60)

    url = resolve_database_url()
    masked = mask_url(url)
    print(f"\n[1] Target Database: {masked}")

    # Check SSL configuration for remote PostgreSQL
    if url.startswith("postgresql") and "localhost" not in url and "127.0.0.1" not in url:
        if "sslmode=" in url:
            print("    ✓ SSL mode enforced in connection string.")
        else:
            print("    ⚠ Warning: sslmode parameter missing. Aiven requires sslmode=require.")

    # Test connection via SQLAlchemy
    print("\n[2] Connecting to database...")
    from sqlalchemy import create_engine, text, inspect

    engine_args = {"pool_pre_ping": True}
    if url.startswith("postgresql"):
        engine_args["connect_args"] = {"connect_timeout": 10}

    t0 = time.time()
    try:
        engine = create_engine(url, **engine_args)
        with engine.connect() as conn:
            ping_time = round((time.time() - t0) * 1000, 1)
            result = conn.execute(text("SELECT 1")).scalar()
            print(f"    ✓ Handshake successful! Ping latency: {ping_time}ms (SELECT 1 -> {result})")

            # Check DB version
            try:
                db_ver = conn.execute(text("SELECT version()")).scalar()
                print(f"    ✓ Database Engine: {db_ver.splitlines()[0]}")
            except Exception:
                try:
                    sqlite_ver = conn.execute(text("SELECT sqlite_version()")).scalar()
                    print(f"    ✓ SQLite Version: {sqlite_ver}")
                except Exception:
                    pass

            # Inspect tables
            print("\n[3] Inspecting schema tables...")
            inspector = inspect(engine)
            tables = inspector.get_table_names()
            expected_tables = [
                "users",
                "login_events",
                "file_history",
                "tickets",
                "site_settings",
                "processing_jobs",
                "error_logs",
            ]
            print(f"    Existing tables ({len(tables)}): {', '.join(tables) if tables else 'None yet'}")
            missing = [t for t in expected_tables if t not in tables]
            if missing:
                print(f"    ℹ Note: The following tables will be auto-created on app startup: {missing}")
            else:
                print("    ✓ All 7 expected tables are present and verified.")

        print("\n" + "=" * 60)
        print("  ✓ SUCCESS: Database connection is healthy and ready to use!")
        print("=" * 60)
        return 0

    except Exception as e:
        err_msg = str(e)
        print(f"\n[!] Connection FAILED: {err_msg}\n")
        print("=" * 60)
        print("  TROUBLESHOOTING TIPS FOR AIVEN POSTGRESQL:")
        print("=" * 60)
        if "no pg_hba.conf entry" in err_msg or "SSL" in err_msg or "ssl" in err_msg:
            print("  - SSL Error: Aiven strictly requires SSL. Ensure your URI ends with:")
            print("    ?sslmode=require")
        elif "password authentication failed" in err_msg:
            print("  - Password Error: Authentication failed for user 'avnadmin'.")
            print("    * Check if your password contains special characters like '@', ':', '#', or '%'.")
            print("    * These must be URL-encoded (e.g., '@' -> '%40', '#' -> '%23'),")
            print("      OR set discrete environment variables in .env:")
            print("      PGHOST=...")
            print("      PGPORT=...")
            print("      PGUSER=avnadmin")
            print("      PGPASSWORD=<your-plain-password>")
            print("      PGDATABASE=defaultdb")
        elif "could not translate host name" in err_msg or "Name or service not known" in err_msg:
            print("  - Host Error: Hostname could not be resolved.")
            print("    * Ensure you copied the full host (e.g. pg-xxxx-company.aivencloud.com).")
        elif "Connection refused" in err_msg or "timeout" in err_msg.lower():
            print("  - Network/Timeout Error:")
            print("    * In Aiven Console, ensure your service status is 'RUNNING' (not 'REBUILDING').")
            print("    * In Aiven Console -> Service Settings -> IP Filter, make sure 0.0.0.0/0 is allowed")
            print("      or your current outbound IP is in the allowlist.")
        else:
            print("  - Check that DATABASE_URL in .env matches the Service URI from Aiven Console.")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
