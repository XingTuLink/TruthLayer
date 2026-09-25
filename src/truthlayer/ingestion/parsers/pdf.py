"""PDF parser — PyMuPDF.

Text blocks are read per page with page numbers and block numbers, which
is enough provenance for Evidence (#24).
"""

from __future__ import annotations

from pathlib import Path

from truthlayer.domain.errors import ParserError
from truthlayer.providers.parser_protocol import ParsedBlock


class PdfParser:
    extensions = frozenset({".pdf"})

    def parse(self, path: Path) -> list[ParsedBlock]:
        try:
            import pymupdf
        except ImportError as exc:
            raise ParserError(
                "pdf support requires the parsers extra: "
                'pip install "truthlayer[parsers]"'
            ) from exc

        try:
            document = pymupdf.open(path)
        except Exception as exc:
            raise ParserError(f"failed to open pdf {path}: {exc}") from exc

        blocks: list[ParsedBlock] = []
        try:
            for page_number, page in enumerate(document, start=1):
                raw_blocks = page.get_text("blocks") or []
                page_blocks = [
                    (block_info[5], block_info[4].strip())
                    for block_info in raw_blocks
                    if block_info[6] == 0 and block_info[4].strip()
                ]
                if not page_blocks:
                    # Scanned/image page or unusual layout: fall back to text.
                    full_text = page.get_text("text").strip()
                    if full_text:
                        blocks.append(
                            ParsedBlock(text=full_text, page=page_number)
                        )
                    continue
                for block_number, text in page_blocks:
                    blocks.append(
                        ParsedBlock(
                            text=text,
                            page=page_number,
                            paragraph=int(block_number),
                        )
                    )
        finally:
            document.close()
        return blocks
