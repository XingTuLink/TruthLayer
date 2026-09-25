"""CSV parser — native Python csv module.

Each data row becomes one block whose text is "Header: value; ..." so the
column semantics survive into extraction (important for price lists).
Row provenance is carried by ``row`` on ParsedBlock.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

from truthlayer.ingestion.parsers._encoding import decode_bytes
from truthlayer.providers.parser_protocol import ParsedBlock


class CsvParser:
    extensions = frozenset({".csv"})

    def parse(self, path: Path) -> list[ParsedBlock]:
        text = decode_bytes(Path(path).read_bytes())
        rows = list(csv.reader(io.StringIO(text)))
        rows = [[cell.strip() for cell in row] for row in rows]
        rows = [row for row in rows if any(cell for cell in row)]

        blocks: list[ParsedBlock] = []
        if not rows:
            return blocks

        header = [cell or f"col_{i + 1}" for i, cell in enumerate(rows[0])]

        if len(rows) == 1:
            # Header-only file: emit the header row positionally.
            blocks.append(ParsedBlock(text=" | ".join(header), row=1))
            return blocks

        for index, row in enumerate(rows[1:], start=2):
            pairs = [
                f"{header[i]}: {cell}"
                for i, cell in enumerate(row)
                if cell and i < len(header)
            ]
            if pairs:
                blocks.append(ParsedBlock(text="; ".join(pairs), row=index))
        return blocks
