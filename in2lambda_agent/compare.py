"""One call: does the OCR markdown say what the pages say?

Every check in the pipeline reads the markdown alone, so a misread symbol that
still renders — `\\nabla` for `\\partial`, a lost superscript, a digit that
changed — passes all of them. The only thing that can catch it is the page, so
this renders the PDF's pages and sends them to the model beside the markdown,
asking what differs.

This is the design spec's open question tried one way, not a pipeline stage:
nothing calls it but the `compare` command, and what it found over the corpus is
written up in docs/ocr-comparison.md.

The pages are rendered with pdftoppm, which is poppler's, and go through
`Backend.call` like any other call, so the comparison runs on whichever backend
the settings chose — including the Claude Code login, which is what development
has. All the pages go in one call with the whole markdown, rather than a page at
a time: Mathpix returns one markdown document with no page boundaries in it, so
there is no page's markdown to pair a page image with.
"""

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from in2lambda_agent.model import Backend, ModelUnavailable, Usage

# What a page is rendered at. Enough for a subscript to be legible, small
# enough that a four-page sheet is a few hundred KB of PNG.
PAGE_DPI = 150

SYSTEM = """\
You are given the pages of a question sheet as images, in order, and the \
markdown an OCR made of them. Find every place the markdown says something the \
pages do not.

Look for: a symbol read as another symbol, a lost or invented subscript or \
superscript, a dropped brace or bracket, a changed digit or sign, a line of a \
page missing from the markdown, and text in the markdown that is on no page.

Ignore everything about form rather than content: headings, line breaks, \
spacing, list markers, which LaTeX command was used for a symbol the page also \
shows, `$...$` against `\\(...\\)`, and images written as links.

Answer with a JSON array and nothing else. One object per difference:

  [{"page": 1, "ocr": "\\\\nabla \\\\times B", \
"page_shows": "\\\\nabla \\\\cdot B", "note": "curl read as divergence"}]

`ocr` is what the markdown says, quoted exactly and briefly; `page_shows` is \
what the page shows instead. Answer `[]` if the markdown says what the pages \
say.\
"""

# The first JSON array in the reply, past any prose the model wrapped it in.
ARRAY = re.compile(r"\[.*\]", re.DOTALL)


class RenderFailed(RuntimeError):
    """The PDF's pages could not be rendered to images."""


@dataclass
class Finding:
    """One place the markdown and the page differ, as the model reported it."""

    page: str = ""
    ocr: str = ""
    page_shows: str = ""
    note: str = ""


@dataclass
class Comparison:
    """What one call found, what it cost, and what it actually said.

    `raw` is kept because this is an experiment: a reply that parsed to nothing
    is a result about the method, and the write-up needs to quote it.
    """

    findings: list[Finding] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw: str = ""
    pages: int = 0


def render_pages(pdf: Path, *, dpi: int = PAGE_DPI) -> list[bytes]:
    """Renders every page of a PDF to a PNG.

    Args:
        pdf: The PDF to render.
        dpi: What to render it at.

    Returns:
        One PNG per page, in page order.

    Raises:
        RenderFailed: If pdftoppm is not installed, or will not read the PDF.
    """
    if shutil.which("pdftoppm") is None:
        raise RenderFailed(
            "install poppler for pdftoppm, which renders a PDF's pages"
        )
    with tempfile.TemporaryDirectory() as into:
        done = subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-png", str(pdf), f"{into}/page"],
            capture_output=True,
            text=True,
        )
        if done.returncode != 0:
            raise RenderFailed(
                f"pdftoppm could not render {pdf}: {done.stderr.strip()}"
            )
        # Named page-1, page-2, … with the number padded to the page count's
        # width, so sorting the names is page order.
        return [one.read_bytes() for one in sorted(Path(into).glob("page-*.png"))]


def parse_findings(text: str) -> list[Finding]:
    """The findings out of a reply.

    Args:
        text: What the model answered with.

    Returns:
        One `Finding` per object in the reply's JSON array, and none at all
        where there is no array, it does not parse, or it holds no objects —
        `raw` carries the reply either way, so nothing is lost by not raising.
    """
    match = ARRAY.search(text)
    if match is None:
        return []
    try:
        loaded = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [
        Finding(
            page=str(one.get("page", "")),
            ocr=str(one.get("ocr", "")),
            page_shows=str(one.get("page_shows", "")),
            note=str(one.get("note", "")),
        )
        for one in loaded
        if isinstance(one, dict)
    ]


def compare(
    pdf: Path, markdown: str, backend: Backend, *, dpi: int = PAGE_DPI
) -> Comparison:
    """Asks a model what the OCR markdown says that the PDF's pages do not.

    Args:
        pdf: The PDF the markdown was made from, whose pages are rendered.
        markdown: What the OCR made of it.
        backend: The backend to call.
        dpi: What to render the pages at.

    Returns:
        The findings, what the call cost, the reply it came in, and how many
        pages went with it.

    Raises:
        ModelUnavailable: If the backend has no credential or login.
        RenderFailed: If the pages cannot be rendered.
    """
    if (reason := backend.unavailable()) is not None:
        raise ModelUnavailable(reason)

    pages = render_pages(pdf, dpi=dpi)
    prompt = (
        f"Those are the {len(pages)} pages of {Path(pdf).name}, in order. Here "
        f"is the markdown the OCR made of them:\n\n{markdown}\n"
    )
    reply = backend.call(SYSTEM, prompt, images=pages)
    return Comparison(
        findings=parse_findings(reply.text),
        usage=reply.usage,
        raw=reply.text,
        pages=len(pages),
    )
