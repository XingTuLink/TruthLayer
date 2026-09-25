"""Native parser tests: txt / md / csv (#24)."""

from __future__ import annotations

from pathlib import Path

from truthlayer.ingestion.parsers.csv_parser import CsvParser
from truthlayer.ingestion.parsers.markdown import MarkdownParser
from truthlayer.ingestion.parsers.text import TextParser


def test_txt_paragraph_grouping_and_offsets(tmp_path: Path) -> None:
    path = tmp_path / "p.txt"
    path.write_bytes("first paragraph\nstill first\n\nsecond one\n".encode("utf-8"))

    blocks = TextParser().parse(path)

    assert len(blocks) == 2
    assert blocks[0].text == "first paragraph\nstill first"
    assert blocks[0].paragraph == 0
    assert blocks[0].offset_start == 0
    assert blocks[1].text == "second one"
    # Offsets point into the decoded text and bracket the paragraph.
    full = path.read_text(encoding="utf-8")
    assert full[blocks[1].offset_start : blocks[1].offset_end] == "second one"


def test_txt_gbk_fallback(tmp_path: Path) -> None:
    path = tmp_path / "gbk.txt"
    path.write_bytes("标准价格99元".encode("gb18030"))

    blocks = TextParser().parse(path)

    assert blocks[0].text == "标准价格99元"


def test_markdown_paragraphs(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("# Title\n\nA paragraph.\n\n- item\n", encoding="utf-8")

    blocks = MarkdownParser().parse(path)

    texts = [block.text for block in blocks]
    assert "# Title" in texts
    assert "A paragraph." in texts
    assert "- item" in texts


def test_csv_header_value_pairs(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"
    path.write_text(
        "product,price,currency\nPro,99,CNY\nTeam,129,CNY\n",
        encoding="utf-8",
    )

    blocks = CsvParser().parse(path)

    assert len(blocks) == 2
    assert blocks[0].text == "product: Pro; price: 99; currency: CNY"
    assert blocks[0].row == 2
    assert blocks[1].row == 3


def test_csv_header_only(tmp_path: Path) -> None:
    path = tmp_path / "header.csv"
    path.write_text("a,b,c\n", encoding="utf-8")

    blocks = CsvParser().parse(path)

    assert len(blocks) == 1
    assert blocks[0].row == 1


def test_csv_empty_and_blank_rows(tmp_path: Path) -> None:
    path = tmp_path / "empty.csv"
    path.write_text("\n\n,,\n", encoding="utf-8")
    assert CsvParser().parse(path) == []
