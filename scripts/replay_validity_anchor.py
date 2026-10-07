"""Offline replay: how is every extracted ``valid_to`` anchored to evidence?

Reads the ``facts.jsonl`` artifact exported by scripts/e2e_eval.py and
classifies each non-null ``valid_to`` with the SAME deterministic function
the online pipeline runs post-LLM (``date_is_quoted``), so the run16 figure
"5 quoted / 123 not quoted out of 128 facts carrying valid_to" can be
reproduced independently without any API key or database::

    ./.venv/Scripts/python scripts/replay_validity_anchor.py \\
        --in build/e2e_eval/run16-batch2

Offline limitation (stated honestly): the facts export contains the fact's
own quote but not the surrounding chunk text, so the two non-quoted classes
cannot be separated here — every not-quoted row is reported under one
bucket. The live extractor additionally distinguishes ``document_scope``
(the date appears verbatim elsewhere in the same chunk, e.g. a document
header "本文件有效期至 X") from ``calendar_derived`` (the date appears
nowhere; inferred from a period/edition label such as "Q3"). Both offline
buckets feed the non-blocking possibly_stale channel; only quoted expiry
reaches confirmed_stale.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from truthlayer.extraction.validity_anchor import date_is_quoted


def _load_facts(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return rows


def _sample(rows: list[dict], limit: int) -> None:
    for row in rows[:limit]:
        quote = (row.get("quote") or "").replace("\n", " ")
        if len(quote) > 80:
            quote = quote[:77] + "..."
        print(
            f"    - {row.get('subject')} / {row.get('predicate')} "
            f"valid_to={row.get('valid_to')} | quote: {quote}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--in",
        dest="run_dir",
        type=Path,
        default=Path("build/e2e_eval/run16-batch2"),
        help="e2e run directory containing facts.jsonl",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="example rows printed per bucket (default: 5)",
    )
    args = parser.parse_args()

    facts_path = args.run_dir / "facts.jsonl"
    if not facts_path.is_file():
        raise SystemExit(f"not found: {facts_path}")

    rows = _load_facts(facts_path)
    with_valid_to = [r for r in rows if r.get("valid_to")]
    buckets: Counter[str] = Counter()
    quoted_rows: list[dict] = []
    not_quoted_rows: list[dict] = []
    for row in with_valid_to:
        if date_is_quoted(row["valid_to"], row.get("quote") or ""):
            buckets["quoted"] += 1
            quoted_rows.append(row)
        else:
            buckets["not_quoted"] += 1
            not_quoted_rows.append(row)

    total = len(with_valid_to)
    print(f"run artifacts : {facts_path}")
    print(f"facts total   : {len(rows)}")
    print(f"with valid_to : {total}")
    if total:
        print(
            "  quoted      : "
            f"{buckets['quoted']} ({buckets['quoted'] / total:.1%}) "
            "-> expiry is deterministic (confirmed_stale)"
        )
        print(
            "  not quoted  : "
            f"{buckets['not_quoted']} ({buckets['not_quoted'] / total:.1%}) "
            "-> review-only (possibly_stale); document_scope vs "
            "calendar_derived needs chunk text, unavailable offline"
        )
    if args.samples:
        print(f"\nquoted samples ({len(quoted_rows)} total):")
        _sample(quoted_rows, args.samples)
        print(f"\nnot-quoted samples ({len(not_quoted_rows)} total):")
        _sample(not_quoted_rows, args.samples)


if __name__ == "__main__":
    main()
