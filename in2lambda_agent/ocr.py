"""The OCR pass and its cache: a PDF is converted once per document.

The cache is keyed by the PDF's own bytes, so a second run over the same file
makes no Mathpix call. A fresh pass is a restart of the pipeline for that
document, and takes the whole cache entry with it.
"""

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from in2lambda_agent.mathpix import MathpixClient

SOURCE_NAME = "source.md"
MEDIA_NAME = "media"

DEFAULT_CACHE_DIR = Path(".in2lambda-agent")
"""Where the OCR of each PDF is kept, under the directory the user ran from."""


@dataclass
class OcrResult:
    """The markdown a PDF became, and the images beside it.

    The markdown refers to each image as `media/<name>`, which resolves from the
    folder source.md is in.
    """

    markdown: Path
    media: Path
    fresh: bool


def cached(pdf: Path, cache_dir: Path) -> Optional[OcrResult]:
    """The conversion already in the cache, or None where there is none.

    Asked before a client is built, so that a document whose OCR was fetched
    once runs again with no Mathpix credentials at all: a worktree, or a CI job
    on a fork, has the cache and not the account.

    Args:
        pdf: The PDF whose conversion is wanted.
        cache_dir: Holds one entry per document, named by the PDF's hash.

    Returns:
        Where the markdown and its media folder are, or None.
    """
    entry = Path(cache_dir) / _hash(pdf)
    markdown = entry / SOURCE_NAME
    if not markdown.exists():
        return None
    return OcrResult(markdown, entry / MEDIA_NAME, fresh=False)


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
    if not fresh and (hit := cached(pdf, cache_dir)) is not None:
        return hit

    entry = Path(cache_dir) / _hash(pdf)
    markdown = entry / SOURCE_NAME
    media = entry / MEDIA_NAME

    # A fresh pass restarts the conversion of this document, so the whole entry
    # is deleted: a file a later step keeps beside source.md belongs to the pass
    # that made it.
    shutil.rmtree(entry, ignore_errors=True)

    # Built beside the entry and renamed into place, so a pass that fails part
    # way through leaves nothing for the next run to mistake for a conversion.
    building = entry.with_name(f"{entry.name}.building")
    shutil.rmtree(building, ignore_errors=True)
    (building / MEDIA_NAME).mkdir(parents=True)
    try:
        # The media folder is written beside source.md because the client names
        # each image by this folder and the file in it, and in2lambda's export
        # resolves that reference from the folder holding the draft, which a
        # later stage writes beside source.md.
        text = client.convert(pdf, building / MEDIA_NAME)
        # Always UTF-8: OCR of a real sheet is full of non-ASCII, and the
        # platform encoding under a C or cp1252 locale would refuse it after
        # the conversion has been paid for.
        (building / SOURCE_NAME).write_text(text, encoding="utf-8")
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
