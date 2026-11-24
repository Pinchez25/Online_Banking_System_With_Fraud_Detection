"""CSV and PDF rendering for customer documents (history exports, statements)."""
from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator, Sequence

from django.utils import timezone
from fpdf import FPDF
from fpdf.fonts import FontFace

BANK_NAME = "Kwetu Bank"


class _EchoBuffer:
    def write(self, value):
        return value


def csv_lines(headers: Sequence[str], rows: Iterable[Sequence[str]]) -> Iterator[str]:
    """CSV text line by line, for a StreamingHttpResponse."""
    writer = csv.writer(_EchoBuffer())
    yield writer.writerow(headers)
    for row in rows:
        yield writer.writerow(row)


def _latin1(value) -> str:
    # The built-in PDF fonts cover Latin-1 only.
    return str(value).encode("latin-1", "replace").decode("latin-1")


class _Document(FPDF):
    def __init__(self, title: str):
        super().__init__(orientation="landscape", format="A4")
        self.document_title = title
        self.set_auto_page_break(auto=True, margin=15)
        self.set_title(title)
        self.set_creator(BANK_NAME)

    def header(self):
        self.set_font("helvetica", "B", 14)
        self.cell(0, 8, _latin1(f"{BANK_NAME} · {self.document_title}"), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("helvetica", size=8)
        self.set_text_color(120)
        generated = timezone.now().strftime("%d %b %Y %H:%M")
        self.cell(0, 6, _latin1(f"Generated {generated}  ·  Page {self.page_no()}/{{nb}}"), align="R")


def render_pdf(*, title: str, details: Sequence[tuple[str, str]], headers: Sequence[str],
               rows: Iterable[Sequence[str]], numeric_columns: Sequence[int] = (), col_widths: Sequence[int] = (),
               note: str = "") -> bytes:
    pdf = _Document(title)
    pdf.add_page()

    pdf.set_font("helvetica", size=9)
    for label, value in details:
        pdf.set_font(style="B")
        pdf.cell(45, 6, _latin1(label))
        pdf.set_font(style="")
        pdf.cell(0, 6, _latin1(value), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    align = tuple("RIGHT" if i in numeric_columns else "LEFT" for i in range(len(headers)))
    with pdf.table(col_widths=tuple(col_widths) or None, text_align=align, line_height=5.5,
                   headings_style=FontFace(emphasis="BOLD", fill_color=(235, 238, 242)),
                   cell_fill_color=(248, 249, 251), cell_fill_mode="ROWS") as table:
        heading = table.row()
        for header in headers:
            heading.cell(_latin1(header))
        for values in rows:
            row = table.row()
            for value in values:
                row.cell(_latin1(value))

    if note:
        pdf.ln(3)
        pdf.set_font(style="I", size=8)
        pdf.multi_cell(0, 5, _latin1(note))
    return bytes(pdf.output())
