"""Binary parser tests: pdf / docx / xlsx (#24).

Requires the parsers extra (PyMuPDF / python-docx / openpyxl).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf")
docx = pytest.importorskip("docx")
openpyxl = pytest.importorskip("openpyxl")

from truthlayer.ingestion.parsers.docx import DocxParser  # noqa: E402
from truthlayer.ingestion.parsers.pdf import PdfParser  # noqa: E402
from truthlayer.ingestion.parsers.xlsx import XlsxParser  # noqa: E402


def test_pdf_page_provenance(tmp_path: Path) -> None:
    path = tmp_path / "policy.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Standard price is 99 yuan.")
    document.save(path)
    document.close()

    blocks = PdfParser().parse(path)

    assert blocks
    assert blocks[0].page == 1
    assert "99" in blocks[0].text
    assert blocks[0].paragraph is not None


def test_docx_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "policy.docx"
    document = docx.Document()
    document.add_paragraph("Policy version 2026.")
    document.add_paragraph("The price is 129.")
    document.save(path)

    blocks = DocxParser().parse(path)

    assert [block.text for block in blocks] == [
        "Policy version 2026.",
        "The price is 129.",
    ]
    assert [block.paragraph for block in blocks] == [0, 1]


def test_docx_tables_become_blocks(tmp_path: Path) -> None:
    path = tmp_path / "table.docx"
    document = docx.Document()
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Product"
    table.cell(0, 1).text = "Price"
    document.save(path)

    blocks = DocxParser().parse(path)

    assert any("Product | Price" in block.text for block in blocks)


def test_xlsx_header_value_pairs_and_sheet(tmp_path: Path) -> None:
    path = tmp_path / "prices.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "pricing"
    worksheet.append(["product", "price"])
    worksheet.append(["Pro", 99])
    worksheet.append(["Team", 129])
    workbook.save(path)

    blocks = XlsxParser().parse(path)

    assert len(blocks) == 2
    assert blocks[0].sheet == "pricing"
    assert blocks[0].row == 2
    assert blocks[0].text == "product: Pro; price: 99"
    assert blocks[1].text == "product: Team; price: 129"


def test_xlsx_integer_float_rendering(tmp_path: Path) -> None:
    path = tmp_path / "ints.xlsx"
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["n"])
    worksheet.append([129.0])
    workbook.save(path)

    blocks = XlsxParser().parse(path)

    assert blocks[0].text == "n: 129"


def test_corrupt_pdf_raises_parser_error(tmp_path: Path) -> None:
    from truthlayer.domain.errors import ParserError

    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a real pdf")

    with pytest.raises(ParserError):
        PdfParser().parse(path)
