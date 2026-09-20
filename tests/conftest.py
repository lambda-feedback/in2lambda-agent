"""What more than one test file needs. No test makes an HTTP request."""

from pathlib import Path

import pytest

from in2lambda_agent.mathpix import MathpixError
from in2lambda_agent.model import Reply, Usage


class FakeBackend:
    """A model backend that answers from a list instead of calling a model."""

    name = "fake"

    def __init__(self, *replies, reason=None):
        self.replies = list(replies)
        self.reason = reason
        self.calls: list[tuple[str, str]] = []

    def unavailable(self):
        return self.reason

    def call(self, system, prompt, tools=()):
        self.calls.append((system, prompt))
        text = self.replies.pop(0)
        # Something non-zero, so that a test can tell a run that called from
        # one that did not by what it recorded.
        return Reply(
            text=text,
            usage=Usage(input_tokens=len(prompt), output_tokens=len(text), seconds=0.5),
            backend=self.name,
            model="fake-model",
        )


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
