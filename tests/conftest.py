"""What more than one test file needs. No test makes an HTTP request."""

from pathlib import Path

import pytest

from in2lambda_agent.mathpix import MathpixError


class FakeMathpix:
    """A Mathpix client that records its calls instead of making requests."""

    def __init__(self, markdown="# Sheet\n\n![a plot](plot.png)\n", error=None):
        self.markdown = markdown
        self.error = error
        self.calls: list[Path] = []

    def convert(self, pdf: Path, media_dir: Path) -> str:
        self.calls.append(Path(pdf))
        if self.error:
            raise MathpixError(self.error)
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / "plot.png").write_bytes(b"PNG")
        return self.markdown


@pytest.fixture
def pdf(tmp_path):
    """A file the agent treats as a PDF; only its bytes and suffix matter."""
    source = tmp_path / "sheet.pdf"
    source.write_bytes(b"%PDF-1.4 not really a PDF")
    return source
