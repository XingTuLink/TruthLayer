"""One-off dev helper: print tables / extensions / checks of the target DB.

Connection comes from TRUTHLAYER_DATABASE_URL only.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import psycopg


def main() -> None:
    raw = os.environ.get("TRUTHLAYER_DATABASE_URL", "").replace("+psycopg", "", 1)
    u = urlparse(raw)
    conn = psycopg.connect(
        host=u.hostname,
        port=u.port,
        dbname=u.path.lstrip("/"),
        user=u.username,
        password=u.password,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT extname, extversion FROM pg_extension WHERE extname = 'vector'"
            )
            row = cur.fetchone()
            print("extension vector:", row if row else "MISSING")

            cur.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "ORDER BY tablename"
            )
            tables = [r[0] for r in cur.fetchall()]
            print(f"{len(tables)} tables:")
            print(", ".join(tables))

            cur.execute(
                "SELECT conname FROM pg_constraint WHERE conname LIKE 'ck_%' "
                "ORDER BY conname"
            )
            print("check constraints:")
            for (name,) in cur.fetchall():
                print(" -", name)

            cur.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                "ORDER BY indexname"
            )
            print("indexes:", len(cur.fetchall()))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
