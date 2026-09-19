"""The OCR cache: one Mathpix call per document, and a fresh pass restarts it."""

import pytest

from in2lambda_agent.mathpix import MathpixError
from in2lambda_agent.ocr import ocr_pdf

from conftest import FakeMathpix


def test_the_first_pass_converts_and_lays_out_the_entry(pdf, tmp_path):
    client = FakeMathpix()

    result = ocr_pdf(pdf, cache_dir=tmp_path / "cache", client=client)

    assert result.fresh
    assert result.markdown.read_text() == client.markdown
    assert result.markdown.name == "source.md"
    assert (result.media / "plot.png").read_bytes() == b"PNG"
    assert result.media.parent == result.markdown.parent
    assert len(client.calls) == 1


def test_a_second_pass_over_the_same_pdf_makes_no_call(pdf, tmp_path):
    client = FakeMathpix()
    first = ocr_pdf(pdf, cache_dir=tmp_path / "cache", client=client)

    second = ocr_pdf(pdf, cache_dir=tmp_path / "cache", client=client)

    assert not second.fresh
    assert (second.markdown, second.media) == (first.markdown, first.media)
    assert len(client.calls) == 1


def test_a_fresh_pass_replaces_the_whole_entry(pdf, tmp_path):
    ocr_pdf(pdf, cache_dir=tmp_path / "cache", client=FakeMathpix())
    entry = next((tmp_path / "cache").iterdir())
    # Whatever a later stage comes to keep beside source.md goes with the pass
    # that made it: a fresh OCR restarts the pipeline for this document.
    (entry / "draft.json").write_text("{}")
    client = FakeMathpix(markdown="# Read again\n")

    result = ocr_pdf(pdf, cache_dir=tmp_path / "cache", client=client, fresh=True)

    assert result.fresh
    assert result.markdown.read_text() == "# Read again\n"
    assert not (entry / "draft.json").exists()
    assert len(client.calls) == 1


def test_a_failed_pass_leaves_no_entry(pdf, tmp_path):
    cache = tmp_path / "cache"

    with pytest.raises(MathpixError, match="page 3 is not a page"):
        ocr_pdf(pdf, cache_dir=cache, client=FakeMathpix(error="page 3 is not a page"))

    assert list(cache.iterdir()) == []


def test_each_document_gets_its_own_entry(pdf, tmp_path):
    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.4 a different sheet")
    client = FakeMathpix()

    first = ocr_pdf(pdf, cache_dir=tmp_path / "cache", client=client)
    second = ocr_pdf(other, cache_dir=tmp_path / "cache", client=client)

    assert first.markdown != second.markdown
    assert len(client.calls) == 2
