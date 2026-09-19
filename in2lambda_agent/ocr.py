"""The OCR pass and its cache: a PDF is converted once per document.

The cache is keyed by the PDF's own bytes, so a second run over the same file
makes no Mathpix call. A fresh pass is a restart of the pipeline for that
document, and takes the whole cache entry with it.
"""

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from in2lambda_agent.mathpix import MathpixClient

SOURCE_NAME = "source.md"
MEDIA_NAME = "media"


@dataclass
class OcrResult:
    """The markdown a PDF became, and the images beside it."""

    markdown: Path
    media: Path
    fresh: bool


def ocr_pdf(
    pdf: Path, *, cache_dir: Path, client: MathpixClient, fresh: bool = False
) -> OcrResult:
    """Converts a PDF to markdown, or returns the conversion already cached.

    Args:
        pdf: The PDF to convert.
        cache_dir: Holds one entry per document, named by the PDF's hash.
        client: The Mathpix client to convert with.
        fresh: Convert again even if the document is cached.

    Returns:
        Where the markdown and its media folder are, and whether Mathpix ran.

    Raises:
        MathpixError: If the conversion fails; the entry is left absent.
    """
    entry = Path(cache_dir) / _hash(pdf)
    markdown = entry / SOURCE_NAME
    media = entry / MEDIA_NAME
    if markdown.exists() and not fresh:
        return OcrResult(markdown, media, fresh=False)

    # A fresh pass restarts the pipeline for this document, so the whole entry
    # goes: anything a later stage comes to keep beside source.md — a draft, a
    # spec run, a report — belongs to the pass that made it, not to this one.
    shutil.rmtree(entry, ignore_errors=True)

    # Built beside the entry and renamed into place, so a pass that fails part
    # way through leaves nothing for the next run to mistake for a conversion.
    building = entry.with_name(f"{entry.name}.building")
    shutil.rmtree(building, ignore_errors=True)
    (building / MEDIA_NAME).mkdir(parents=True)
    try:
        text = client.convert(pdf, building / MEDIA_NAME)
        (building / SOURCE_NAME).write_text(text)
    except BaseException:
        shutil.rmtree(building, ignore_errors=True)
        raise
    building.rename(entry)

    return OcrResult(markdown, media, fresh=True)


def _hash(pdf: Path) -> str:
    """The sha256 of the PDF's bytes, which names its cache entry."""
    digest = hashlib.sha256()
    with Path(pdf).open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()
