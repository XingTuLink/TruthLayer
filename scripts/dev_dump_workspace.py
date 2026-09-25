"""One-off dev helper: dump persisted documents / groups / chunks.

Usage: set TRUTHLAYER_DATABASE_URL, then pass a workspace name.
Connection credentials come only from the environment.
"""

from __future__ import annotations

import os
import sys
from urllib.parse import urlparse

import psycopg


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: dev_dump_workspace.py <workspace-name>")
    workspace_name = sys.argv[1]

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
                "SELECT id, name FROM workspaces WHERE name = %s",
                (workspace_name,),
            )
            row = cur.fetchone()
            if not row:
                print(f"workspace not found: {workspace_name}")
                return
            workspace_id, _ = row

            print("== document_groups ==")
            cur.execute(
                "SELECT id, name FROM document_groups "
                "WHERE workspace_id = %s ORDER BY name",
                (workspace_id,),
            )
            groups = {gid: name for gid, name in cur.fetchall()}
            for gid, name in groups.items():
                print(f"  {name}  ({gid})")

            print("== documents ==")
            cur.execute(
                """
                SELECT filename, status, authority_score, file_hash,
                       version_label, document_group_id, previous_version_id,
                       (SELECT count(*) FROM chunks c WHERE c.document_id = d.id)
                FROM documents d
                WHERE workspace_id = %s
                ORDER BY filename
                """,
                (workspace_id,),
            )
            docs = cur.fetchall()
            for (
                rel,
                status,
                authority,
                fhash,
                label,
                group_id,
                prev_id,
                n_chunks,
            ) in docs:
                prev = ""
                if prev_id is not None:
                    cur.execute(
                        "SELECT filename FROM documents WHERE id = %s",
                        (prev_id,),
                    )
                    prev_row = cur.fetchone()
                    prev = f"  -> supersedes: {prev_row[0] if prev_row else prev_id}"
                group = f", group={groups[group_id]}" if group_id else ""
                print(
                    f"  [{status}] {rel}  auth={authority}, "
                    f"chunks={n_chunks}, label={label or '-'}{group}{prev}"
                )
                print(f"      sha256={fhash[:16]}...")

            print("== chunk samples (first 60 chars each) ==")
            cur.execute(
                """
                SELECT d.filename, c.chunk_index, c.token_count,
                       left(c.text, 60)
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE d.workspace_id = %s
                ORDER BY d.filename, c.chunk_index
                """,
                (workspace_id,),
            )
            for rel, idx, tokens, snippet in cur.fetchall():
                one_line = snippet.replace("\n", " ")
                print(f"  {rel}#chunk{idx} ({tokens} tok): {one_line}...")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
