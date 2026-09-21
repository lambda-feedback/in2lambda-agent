"""What more than one test file needs. No test makes an HTTP request."""

from pathlib import Path

import pytest

from in2lambda_agent.mathpix import MathpixError
from in2lambda_agent.model import Reply, ToolCall, Usage

# A real PNG rather than a few bytes named like one: the set checks compile the
# set as the PDF generator does, and xelatex refuses a file it cannot load.
PNG = (Path(__file__).parent / "fixtures" / "ball.png").read_bytes()


class FakeBackend:
    """A model backend that answers from a list instead of calling a model.

    A reply is the text to answer with, or a list of `(tool name, arguments)`
    for a call that uses its tools: the named tools are run in the order given,
    against whatever they were built over, exactly as a real backend's loop runs
    them. That is what scripts a fixing round without a model in it. A reply
    that is an exception is raised, which scripts a call that does not finish.
    """

    name = "fake"

    def __init__(self, *replies, reason=None):
        self.replies = list(replies)
        self.reason = reason
        self.calls: list[tuple[str, str]] = []
        self.images: list[list[bytes]] = []

    def unavailable(self):
        return self.reason

    def call(self, system, prompt, tools=(), images=()):
        self.calls.append((system, prompt))
        self.images.append(list(images))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        made = []
        if isinstance(reply, list):
            by_name = {one.name: one for one in tools}
            for name, arguments in reply:
                result = by_name[name].run(dict(arguments))
                made.append(ToolCall(name, arguments, result))
            reply = "done"
        # Something non-zero, so that a test can tell a run that called from
        # one that did not by what it recorded.
        return Reply(
            text=reply,
            usage=Usage(
                input_tokens=len(prompt), output_tokens=len(reply), seconds=0.5
            ),
            calls=made,
            backend=self.name,
            model="fake-model",
        )


class FakeMathpix:
    """A Mathpix client that records its calls instead of making requests."""

    def __init__(self, markdown="# Sheet\n\n![a plot](media/plot.png)\n", error=None):
        self.markdown = markdown
        self.error = error
        self.calls: list[Path] = []

    def convert(self, pdf: Path, media_dir: Path) -> str:
        self.calls.append(Path(pdf))
        if self.error:
            raise MathpixError(self.error)
        media_dir.mkdir(parents=True, exist_ok=True)
        # Named in the default markdown as the real client names it: media_dir's
        # own folder, then the file in it.
        (media_dir / "plot.png").write_bytes(PNG)
        return self.markdown


@pytest.fixture
def pdf(tmp_path):
    """A file the agent treats as a PDF; only its bytes and suffix matter."""
    source = tmp_path / "sheet.pdf"
    source.write_bytes(b"%PDF-1.4 not really a PDF")
    return source
