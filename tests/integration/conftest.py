"""Database isolation for integration tests.

Integration tests never touch the development database. When
TRUTHLAYER_DATABASE_URL is set, tests run against a throwaway database
named ``<dbname>_it`` (overridable via TRUTHLAYER_TEST_DATABASE),
which is recreated at the start of the session and dropped afterwards.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from psycopg import sql


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    raw = os.environ.get("TRUTHLAYER_DATABASE_URL")
    if not raw:
        pytest.skip(
            "TRUTHLAYER_DATABASE_URL not set; skipping DB integration test"
        )

    parsed = urlparse(raw)
    source_db = parsed.path.lstrip("/")
    test_db = os.environ.get("TRUTHLAYER_TEST_DATABASE", f"{source_db}_it")

    admin_dsn = urlunparse(parsed._replace(scheme="postgresql", path="/postgres"))
    test_url = urlunparse(parsed._replace(path=f"/{test_db}"))

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (test_db,),
            )
            cur.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(test_db)
                )
            )
            cur.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(test_db))
            )

    try:
        yield test_url
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            with admin.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (test_db,),
                )
                cur.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {}").format(
                        sql.Identifier(test_db)
                    )
                )
