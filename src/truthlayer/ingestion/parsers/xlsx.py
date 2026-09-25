"""XLSX parser — openpyxl (read-only, cached formula values)."""

from __future__ import annotations

from pathlib import Path

from truthlayer.domain.errors import ParserError
from truthlayer.providers.parser_protocol import ParsedBlock


class XlsxParser:
    extensions = frozenset({".xlsx"})

    def parse(self, path: Path) -> list[ParsedBlock]:
        try:
            import openpyxl
        except ImportError as exc:
            raise ParserError(
                "xlsx support requires the parsers extra: "
                'pip install "truthlayer[parsers]"'
            ) from exc

        try:
            workbook = openpyxl.load_workbook(
                filename=path, read_only=True, data_only=True
            )
        except Exception as exc:
            raise ParserError(f"failed to open xlsx {path}: {exc}") from exc

        blocks: list[ParsedBlock] = []
        try:
            for worksheet in workbook.worksheets:
                non_empty_rows: list[tuple[int, list[str]]] = []
                for row_index, row in enumerate(
                    worksheet.iter_rows(values_only=True), start=1
                ):
                    cells = [_render_cell(value) for value in row]
                    cells = [cell for cell in cells if cell]
                    if cells:
                        non_empty_rows.append((row_index, cells))

                if not non_empty_rows:
                    continue

                header_row_index, header_cells = non_empty_rows[0]
                header = header_cells

                if len(non_empty_rows) == 1:
                    blocks.append(
                        ParsedBlock(
                            text=" | ".join(header),
                            sheet=worksheet.title,
                            row=header_row_index,
                        )
                    )
                    continue

                for row_index, cells in non_empty_rows[1:]:
                    pairs = [
                        f"{header[i]}: {cell}"
                        for i, cell in enumerate(cells)
                        if i < len(header) and cell
                    ]
                    if pairs:
                        blocks.append(
                            ParsedBlock(
                                text="; ".join(pairs),
                                sheet=worksheet.title,
                                row=row_index,
                            )
                        )
        finally:
            workbook.close()
        return blocks


def _render_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
