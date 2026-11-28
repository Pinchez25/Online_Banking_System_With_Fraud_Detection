import re

import pytest

from bank.services import documents


@pytest.fixture(autouse=True)
def bank_name(monkeypatch):
    monkeypatch.setattr(documents, "institution_name", lambda: "Test Bank")


def test_csv_lines_stream_a_header_then_rows():
    lines = list(documents.csv_lines(("A", "B"), [("1", "x,y"), ("2", 'say "hi"')]))
    assert lines == ["A,B\r\n", '1,"x,y"\r\n', '2,"say ""hi"""\r\n']


def test_pdf_renders_details_rows_and_a_note():
    pdf = documents.render_pdf(title="Statement", details=[("Account", "0100")], headers=("Date", "Amount"),
                               rows=[("2026-11-02", "1.00")] * 120, numeric_columns=(1,), note="Truncated.")
    assert pdf.startswith(b"%PDF")
    assert int(re.search(rb"/Count (\d+)", pdf).group(1)) > 1


def test_pdf_tolerates_text_outside_latin_1():
    assert documents.render_pdf(title="Statement · 🚀", details=[("Holder", "Zoë 李")], headers=("A",),
                                rows=[("✓",)]).startswith(b"%PDF")
