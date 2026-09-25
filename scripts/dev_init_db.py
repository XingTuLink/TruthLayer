"""One-off dev helper: create the truthlayer database if missing.

Credentials are read from TRUTHLAYER_DATABASE_URL only; never hard-coded.
Safe to run repeatedly.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import psycopg


def _admin_url(database_url: str) -> str:
    parsed = urlparse(database_url.replace("+psycopg", "", 1))
    return urlunparse(parsed._replace(path="/postgres"))


def main() -> None:
    database_url = os.environ.get("TRUTHLAYER_DATABASE_URL")
    if not database_url:
        raise SystemExit("TRUTHLAYER_DATABASE_URL is not set")

    target = urlparse(database_url).path.lstrip("/")
    admin = psycopg.connect(_admin_url(database_url), autocommit=True)
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT version()")
            print("PG:", cur.fetchone()[0].split(",")[0])
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (target,)
            )
            if cur.fetchone():
                print(f"database {target}: already exists")
            else:
                cur.execute(f"CREATE DATABASE {target}")
                print(f"database {target}: created")
            cur.execute("SELECT extname FROM pg_extension")
            print("server extensions:", ", ".join(r[0] for r in cur.fetchall()))
    finally:
        admin.close()


if __name__ == "__main__":
    main()
