"""Reading and writing the synthetic extracts in each client's file format.

Every format writes and reads back the same ``(headers, rows)``; rows are lists of
strings (``.xlsx`` cells are written as text so dates and IDs keep the client's format).
``.xlsx`` output is made byte-for-byte deterministic: fixed workbook properties and fixed
zip entry timestamps, so the manifest's SHA-256 values are stable.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

FIXED_TIME = datetime(2026, 11, 1, 0, 0, 0)  # for .xlsx properties and zip entries


@dataclass(frozen=True)
class FileFormat:
    kind: str = "csv"  # "csv" (any delimiter) or "xlsx"
    delimiter: str = ","
    encoding: str = "utf-8"
    bom: bool = False
    preamble: tuple[str, ...] = ()  # lines before the header (header offset)
    footer_total: bool = False  # a "TOTAL" row after the data
    extension: str = ".csv"

    @property
    def header_line(self) -> int:
        """1-based physical line (or sheet row) of the header."""
        return len(self.preamble) + 1

    @property
    def footer_lines(self) -> int:
        return 1 if self.footer_total else 0

    def describe(self) -> dict[str, Any]:
        out = asdict(self)
        out["preamble"] = list(self.preamble)
        out["header_line"] = self.header_line
        out["footer_lines"] = self.footer_lines
        return out


def write(fmt: FileFormat, headers: Sequence[str], rows: Sequence[Sequence[str]],
          footer: Sequence[str] | None = None) -> bytes:  # fmt: skip
    if fmt.kind == "xlsx":
        return _write_xlsx(fmt, headers, rows, footer)
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=fmt.delimiter, lineterminator="\n")
    for line in fmt.preamble:
        buf.write(line + "\n")
    writer.writerow(headers)
    writer.writerows(rows)
    if footer is not None:
        writer.writerow(footer)
    data = buf.getvalue().encode(fmt.encoding)
    return (b"\xef\xbb\xbf" + data) if fmt.bom else data


def read(fmt: FileFormat, data: bytes) -> tuple[list[str], list[list[str]]]:
    """``(headers, data rows)``: preamble and footer removed."""
    if fmt.kind == "xlsx":
        table = _read_xlsx(data)
    else:
        if fmt.bom:
            if not data.startswith(b"\xef\xbb\xbf"):
                raise ValueError("expected a UTF-8 byte-order mark")
            data = data[3:]
        text = data.decode(fmt.encoding)
        table = list(csv.reader(io.StringIO(text, newline=""), delimiter=fmt.delimiter))
    body = table[len(fmt.preamble) :]
    headers, rows = body[0], body[1:]
    if fmt.footer_total:
        if not rows or rows[-1][0] != "TOTAL":
            raise ValueError("expected a TOTAL footer row")
        rows = rows[:-1]
    return headers, rows


def _write_xlsx(fmt: FileFormat, headers: Sequence[str], rows: Sequence[Sequence[str]],
                footer: Sequence[str] | None) -> bytes:  # fmt: skip
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None  # noqa: S101 - a new workbook always has one sheet
    ws.title = "Claims"
    for line in fmt.preamble:
        ws.append([line])
    for record in [list(headers), *map(list, rows), *([list(footer)] if footer else [])]:
        ws.append(record)
        for cell in ws[ws.max_row]:
            cell.number_format = "@"  # text: keep dates, IDs and amounts exactly as written
    wb.properties.creator = "ai-portfolio synthetic data"
    wb.properties.created = FIXED_TIME
    wb.properties.modified = FIXED_TIME
    wb.properties.lastModifiedBy = None
    raw = io.BytesIO()
    wb.save(raw)
    return _fix_zip_times(raw.getvalue())


def _fix_zip_times(data: bytes) -> bytes:
    """Rewrite the .xlsx zip with fixed entry timestamps, and pin ``dcterms:modified``
    (openpyxl stamps the save time there whatever the workbook properties say)."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:  # fmt: skip
        for info in src.infolist():
            entry = zipfile.ZipInfo(info.filename, date_time=FIXED_TIME.timetuple()[:6])
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            content = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                content = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
                                 rb"\g<1>" + FIXED_TIME.strftime("%Y-%m-%dT%H:%M:%SZ").encode()
                                 + rb"\g<2>", content)  # fmt: skip
            dst.writestr(entry, content)
    return out.getvalue()


def _read_xlsx(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True)
    ws = wb.worksheets[0]
    table = [["" if v is None else str(v) for v in row] for row in ws.iter_rows(values_only=True)]
    wb.close()
    return table
