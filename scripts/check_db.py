"""Preflight check for the configured database (SQLite, Supabase, Neon, local Postgres).

Usage:  python scripts/check_db.py

Verifies the URL parses, the server is reachable, the driver is installed, and
reports whether Alembic migrations have been applied.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import inspect, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.db.session import _is_transaction_pooler, get_engine  # noqa: E402


def redact(url: str) -> str:
    """Hide the password in a connection URL before printing it."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:****@{host}"


def main() -> int:
    url = settings.database_url
    print(f"URL     : {redact(url)}")

    if url.startswith("sqlite"):
        kind = "SQLite (local dev)"
    elif _is_transaction_pooler(url):
        kind = "Postgres via TRANSACTION pooler (prepared statements disabled)"
    else:
        kind = "Postgres (direct / session pooler)"
    print(f"Mode    : {kind}")

    # Common Supabase footguns, caught before the confusing driver error appears.
    if "supabase" in url:
        if "+psycopg" not in url:
            print("\nERROR: missing driver prefix. Use 'postgresql+psycopg://', not "
                  "'postgresql://' — Supabase's copy button omits it.")
            return 1
        if "db." in url and ".supabase.co" in url:
            print("\nWARNING: that looks like the IPv6-only DIRECT endpoint. If this "
                  "hangs or fails DNS, switch to the Session pooler (port 5432).")
        if "sslmode" not in url:
            print("\nWARNING: no sslmode in URL; append '?sslmode=require'.")

    try:
        engine = get_engine()
        with engine.connect() as conn:
            if url.startswith("sqlite"):
                print("Server  : sqlite")
            else:
                version = conn.execute(text("SHOW server_version")).scalar()
                print(f"Server  : PostgreSQL {version}")
            conn.execute(text("SELECT 1"))
        print("Connect : OK")
    except ModuleNotFoundError as e:
        print(f"\nERROR: driver not installed ({e}). Run: pip install 'psycopg[binary]'")
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"\nERROR: could not connect -> {type(e).__name__}: {str(e)[:400]}")
        return 1

    # Schema state
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    expected = set(Base.metadata.tables)
    missing = expected - existing

    print(f"Tables  : {len(expected - missing)}/{len(expected)} present")
    if missing:
        print(f"          missing: {sorted(missing)}")
        print("\nRun migrations:  python -m alembic upgrade head")
        return 1
    if "alembic_version" in existing:
        with engine.connect() as conn:
            rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        print(f"Revision: {rev}")
    print("\nDatabase is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
