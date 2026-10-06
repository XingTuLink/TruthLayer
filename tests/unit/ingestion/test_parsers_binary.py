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


def test_xlsx_table_with_banner_rows_above_header(tmp_path: Path) -> None:
    """Real pricing sheets put merged title/metadata/blank rows above header.

    Regression: the parser used to treat the first non-empty single-cell
    banner row as the header, truncating every data row to its first column
    and silently dropping product names and prices.
    """
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "pricing_2025"
    worksheet.append(["产品服务价目表（2025版）"])  # merged banner
    worksheet.append(["文件编号：X-001 ｜ 生效日期：2025年1月1日 ｜ 有效期至：2025年12月31日"])
    worksheet.append([None])  # blank separator
    worksheet.append(["产品编码", "产品/服务名称", "计价单位", "2025年价格（元）", "备注"])
    worksheet.append(["CS-STD", "云客服标准版", "元/年·企业", 9800, None])
    worksheet.append(["CS-PRO", "云客服专业版", "元/年·企业", 22800, "含7×24小时响应"])
    worksheet.append([None])
    worksheet.append(["说明：本价目表到期后按2026版执行。"])  # trailing footnote
    path = tmp_path / "pricing_2025.xlsx"
    workbook.save(path)

    blocks = XlsxParser().parse(path)
    texts = [b.text for b in blocks]

    # Banner / metadata / footnote single-column rows are preserved verbatim.
    assert any("产品服务价目表（2025版）" in t for t in texts)
    assert any("有效期至：2025年12月31日" in t for t in texts)
    assert any("本价目表到期后按2026版执行" in t for t in texts)

    # Data rows keep ALL columns aligned under the located header.
    standard = next(t for t in texts if t.startswith("产品编码: CS-STD"))
    assert "产品/服务名称: 云客服标准版" in standard
    assert "计价单位: 元/年·企业" in standard
    assert "2025年价格（元）: 9800" in standard
    pro = next(t for t in texts if t.startswith("产品编码: CS-PRO"))
    assert "2025年价格（元）: 22800" in pro
    assert "备注: 含7×24小时响应" in pro


def test_xlsx_blank_middle_cell_does_not_shift_columns(tmp_path: Path) -> None:
    """An empty cell in the middle must not shift later values left."""
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.append(["编码", "名称", "单位", "价格", "备注"])
    worksheet.append(["A1", "标准版", None, 9800, "默认"])  # 单位 blank
    path = tmp_path / "alignment.xlsx"
    workbook.save(path)

    blocks = XlsxParser().parse(path)
    assert len(blocks) == 1
    assert "编码: A1" in blocks[0].text
    assert "名称: 标准版" in blocks[0].text
    assert "价格: 9800" in blocks[0].text
    assert "备注: 默认" in blocks[0].text
    assert "单位:" not in blocks[0].text  # blank middle cell omitted, not misfilled


def test_corrupt_pdf_raises_parser_error(tmp_path: Path) -> None:
    from truthlayer.domain.errors import ParserError

    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a real pdf")

    with pytest.raises(ParserError):
        PdfParser().parse(path)
