"""The OCR cache: one Mathpix call per document, and a fresh pass restarts it."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from in2lambda_agent.mathpix import MathpixError
from in2lambda_agent.ocr import ocr_pdf

from conftest import PNG, FakeMathpix


def test_the_first_pass_converts_and_lays_out_the_entry(pdf, tmp_path):
    client = FakeMathpix()

    result = ocr_pdf(pdf, cache_dir=tmp_path / "cache", client=client)

    assert result.fresh
    assert result.markdown.read_text() == client.markdown
    assert result.markdown.name == "source.md"
    assert (result.media / "plot.png").read_bytes() == PNG
    assert result.media.parent == result.markdown.parent
    assert len(client.calls) == 1


def test_the_image_reference_resolves_from_the_drafts_directory(pdf, tmp_path):
    result = ocr_pdf(pdf, cache_dir=tmp_path / "cache", client=FakeMathpix())

    # As in2lambda's export reads a reference out of a field: mathpix.IMAGE
    # matches the CDN URL before the download, not the reference replacing it.
    reference = re.search(r"!\[[^\]]*\]\(([^)]*)\)", result.markdown.read_text())[1]

    # in2lambda's export resolves the reference from the folder holding the
    # draft, which a later stage writes beside source.md.
    assert (result.markdown.parent / reference).read_bytes() == PNG


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


def test_the_markdown_is_written_as_utf8_under_any_locale(pdf, tmp_path):
    # In a subprocess because the locale's encoding is read once, when Python
    # starts. Under LC_ALL=C the platform encoding is ASCII, which would refuse
    # markdown like this one after the conversion has been paid for.
    program = (
        "import pathlib\n"
        "from in2lambda_agent.ocr import ocr_pdf\n"
        "class Client:\n"
        "    def convert(self, pdf, media_dir):\n"
        "        return '# \\u00c5ngstr\\u00f6m \\u00bd\\n'\n"
        f"print(ocr_pdf(pathlib.Path({str(pdf)!r}), "
        f"cache_dir=pathlib.Path({str(tmp_path / 'cache')!r}), client=Client()).markdown)"
    )
    environment = {
        **os.environ,
        "LC_ALL": "C",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
    }

    done = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        env=environment,
    )

    assert done.returncode == 0, done.stderr
    written = Path(done.stdout.strip()).read_bytes()
    assert written.decode("utf-8") == "# Ångström ½\n"


def test_each_document_gets_its_own_entry(pdf, tmp_path):
    other = tmp_path / "other.pdf"
    other.write_bytes(b"%PDF-1.4 a different sheet")
    client = FakeMathpix()

    first = ocr_pdf(pdf, cache_dir=tmp_path / "cache", client=client)
    second = ocr_pdf(other, cache_dir=tmp_path / "cache", client=client)

    assert first.markdown != second.markdown
    assert len(client.calls) == 2
