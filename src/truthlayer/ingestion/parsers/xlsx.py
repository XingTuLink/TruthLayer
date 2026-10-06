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
                # Keep rows positionally (empty cells retained as "") so a
                # blank cell in the middle of a row cannot shift later columns
                # under the wrong header.
                rendered_rows: list[tuple[int, list[str]]] = []
                for row_index, row in enumerate(
                    worksheet.iter_rows(values_only=True), start=1
                ):
                    cells = [_render_cell(value) for value in row]
                    if any(cells):
                        rendered_rows.append((row_index, cells))

                if not rendered_rows:
                    continue

                # Real-world spreadsheets put a merged title banner, document
                # metadata and blank rows ABOVE the real table header. The
                # header is therefore the FIRST row with two or more populated
                # cells, not merely the first non-empty row. A sheet without
                # any such row is not a structured table and is skipped.
                header_position = next(
                    (
                        index
                        for index, (_, cells) in enumerate(rendered_rows)
                        if _filled_cell_count(cells) >= 2
                    ),
                    None,
                )
                if header_position is None:
                    # No multi-column row anywhere: a genuinely single-column
                    # (vertical key/value) sheet. Fall back to treating the
                    # first non-empty row as the key and following rows as
                    # values instead of dropping the sheet.
                    _emit_single_column_sheet(
                        worksheet.title, rendered_rows, blocks
                    )
                    continue

                # Preserve pre-header single-column rows (title / metadata) as
                # text blocks — they often carry effective/expiry dates.
                for row_index, cells in rendered_rows[:header_position]:
                    text = " ".join(cell for cell in cells if cell).strip()
                    if text:
                        blocks.append(
                            ParsedBlock(
                                text=text, sheet=worksheet.title, row=row_index
                            )
                        )

                header_row_index, header = rendered_rows[header_position]
                data_rows = rendered_rows[header_position + 1 :]

                if not data_rows:
                    blocks.append(
                        ParsedBlock(
                            text=" | ".join(cell for cell in header if cell),
                            sheet=worksheet.title,
                            row=header_row_index,
                        )
                    )
                    continue

                for row_index, cells in data_rows:
                    if _filled_cell_count(cells) >= 2:
                        # Align by ORIGINAL column position against the header.
                        pairs = [
                            f"{header[column]}: {cell}"
                            for column, cell in enumerate(cells)
                            if cell and column < len(header) and header[column]
                        ]
                        if pairs:
                            blocks.append(
                                ParsedBlock(
                                    text="; ".join(pairs),
                                    sheet=worksheet.title,
                                    row=row_index,
                                )
                            )
                    else:
                        # Trailing single-column note / footnote row.
                        text = " ".join(cell for cell in cells if cell).strip()
                        if text:
                            blocks.append(
                                ParsedBlock(
                                    text=text,
                                    sheet=worksheet.title,
                                    row=row_index,
                                )
                            )
        finally:
            workbook.close()
        return blocks


def _filled_cell_count(cells: list[str]) -> int:
    return sum(1 for cell in cells if cell)


def _emit_single_column_sheet(
    sheet_title: str,
    rendered_rows: list[tuple[int, list[str]]],
    blocks: list[ParsedBlock],
) -> None:
    """Render a sheet whose rows are all single-column as vertical key/value.

    The first non-empty row is the key/header; subsequent rows pair under it.
    """
    header_row_index, header_cells = rendered_rows[0]
    header = next((cell for cell in header_cells if cell), "")
    if len(rendered_rows) == 1:
        if header:
            blocks.append(
                ParsedBlock(text=header, sheet=sheet_title, row=header_row_index)
            )
        return
    for row_index, cells in rendered_rows[1:]:
        value = next((cell for cell in cells if cell), "")
        if not value:
            continue
        text = f"{header}: {value}" if header else value
        blocks.append(ParsedBlock(text=text, sheet=sheet_title, row=row_index))


def _render_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()
