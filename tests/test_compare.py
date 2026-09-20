"""The one call that checks OCR markdown against the pages it came from.

No test makes a request: the backend is the fake one. The pages are really
rendered, from a two-page PDF written here, so what goes to the backend is what
pdftoppm made of it.
"""

import json

import pytest
from conftest import FakeBackend

from in2lambda_agent.compare import (
    Finding,
    RenderFailed,
    compare,
    parse_findings,
    render_pages,
)
from in2lambda_agent.model import ModelUnavailable

FINDINGS = [
    {
        "page": 1,
        "ocr": "\\mathrm{m/s",
        "page_shows": "\\mathrm{m/s}",
        "note": "dropped brace",
    }
]

# Two blank pages. Poppler reconstructs the missing xref, and nothing here
# cares what is on a page — only that a page becomes a PNG.
TWO_PAGES = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R 4 0 R]/Count 2>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj
4 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj
trailer<</Root 1 0 R>>
"""


@pytest.fixture
def sheet(tmp_path):
    """A PDF with two pages that pdftoppm will render."""
    pdf = tmp_path / "sheet.pdf"
    pdf.write_bytes(TWO_PAGES)
    return pdf


def test_a_bare_array_parses():
    findings = parse_findings(json.dumps(FINDINGS))

    (one,) = findings
    assert (one.page, one.ocr) == ("1", "\\mathrm{m/s")
    assert one.page_shows == "\\mathrm{m/s}"
    assert one.note == "dropped brace"


def test_an_array_wrapped_in_prose_parses():
    text = f"I found one difference:\n\n{json.dumps(FINDINGS)}\n\nThat is all."

    (one,) = parse_findings(text)

    assert one.ocr == "\\mathrm{m/s"


def test_prose_with_a_bracket_in_it_after_the_array_is_not_swallowed():
    text = (
        f"{json.dumps(FINDINGS)}\n\n"
        "I ignored the [a] [b] list markers, as instructed."
    )

    (one,) = parse_findings(text)

    assert one.ocr == "\\mathrm{m/s"


def test_an_empty_array_is_no_findings():
    assert parse_findings("[]") == []


def test_a_reply_with_no_array_in_it_is_no_findings():
    assert parse_findings("The markdown matches the page.") == []


def test_a_reply_whose_array_is_not_json_is_no_findings():
    assert parse_findings("[{page: 1, ocr: 'no quotes'}]") == []


def test_every_page_is_rendered_in_order(sheet):
    pages = render_pages(sheet, dpi=50)

    assert len(pages) == 2
    assert all(one.startswith(b"\x89PNG") for one in pages)


def test_a_pdf_that_will_not_render_says_so(tmp_path):
    not_a_pdf = tmp_path / "sheet.pdf"
    not_a_pdf.write_bytes(b"%PDF-1.4 not really a PDF")

    with pytest.raises(RenderFailed, match="could not render"):
        render_pages(not_a_pdf)


def test_without_pdftoppm_it_says_what_to_install(sheet, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(RenderFailed, match="poppler"):
        render_pages(sheet)


def test_the_pages_go_with_the_markdown_in_one_call(sheet):
    backend = FakeBackend(json.dumps(FINDINGS))

    result = compare(sheet, "# Sheet\n\n$\\mathrm{m/s$\n", backend, dpi=50)

    (system, prompt), = backend.calls
    (images,) = backend.images
    assert len(images) == 2 and result.pages == 2
    assert all(one.startswith(b"\x89PNG") for one in images)
    assert "$\\mathrm{m/s$" in prompt
    assert system.startswith("You are given the pages")


def test_the_findings_the_usage_and_the_reply_come_back(sheet):
    backend = FakeBackend(json.dumps(FINDINGS))

    result = compare(sheet, "# Sheet\n", backend, dpi=50)

    assert result.findings == [
        Finding("1", "\\mathrm{m/s", "\\mathrm{m/s}", "dropped brace")
    ]
    assert result.usage.output_tokens > 0
    assert result.raw == json.dumps(FINDINGS)


def test_a_reply_that_parsed_to_nothing_still_carries_what_was_said(sheet):
    backend = FakeBackend("I cannot read these pages.")

    result = compare(sheet, "# Sheet\n", backend, dpi=50)

    assert result.findings == []
    assert result.raw == "I cannot read these pages."


def test_a_backend_that_cannot_run_says_what_to_set(sheet):
    backend = FakeBackend(reason="set ANTHROPIC_API_KEY to use the anthropic backend")

    with pytest.raises(ModelUnavailable, match="ANTHROPIC_API_KEY"):
        compare(sheet, "# Sheet\n", backend)
