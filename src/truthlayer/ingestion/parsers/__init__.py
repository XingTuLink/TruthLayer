"""Parser registry (#24).

Heavy libraries (PyMuPDF / python-docx / openpyxl) are imported lazily
inside the parser's ``parse`` method, so importing the registry stays cheap
and a missing optional dependency only fails when that format is used.
"""

from __future__ import annotations

from pathlib import Path

from truthlayer.domain.errors import ParserError
from truthlayer.ingestion.parsers.csv_parser import CsvParser
from truthlayer.ingestion.parsers.docx import DocxParser
from truthlayer.ingestion.parsers.markdown import MarkdownParser
from truthlayer.ingestion.parsers.pdf import PdfParser
from truthlayer.ingestion.parsers.text import TextParser
from truthlayer.ingestion.parsers.xlsx import XlsxParser

_PARSERS = (
    TextParser(),
    MarkdownParser(),
    CsvParser(),
    XlsxParser(),
    DocxParser(),
    PdfParser(),
)

PARSERS_BY_EXTENSION: dict[str, object] = {
    ext: parser for parser in _PARSERS for ext in parser.extensions
}

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(PARSERS_BY_EXTENSION)


def get_parser(path: str | Path):
    """Return the parser for a file extension or raise ParserError."""
    extension = Path(path).suffix.lower()
    parser = PARSERS_BY_EXTENSION.get(extension)
    if parser is None:
        raise ParserError(
            f"unsupported file type {extension or '(none)'}: {path}. "
            f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    return parser
