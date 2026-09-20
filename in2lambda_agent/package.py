"""Every call the agent makes into in2lambda, in the agent's own shapes.

in2lambda does every deterministic step and every write, and this is the only
module that knows how those steps are called, so that the pipeline reads as the
design spec's diagram rather than as someone else's API.

The commands are the Python functions the `in2lambda` CLI itself calls, each
taking the directory its `draft.json` is in rather than the working directory:

    in2lambda.source.add(file, start_over) -> Path   # the draft.json written
    in2lambda.source.show(directory) -> str          # numbered, with block ids
    in2lambda.draft.execute(
        in2lambda.draft.spec_command(spec, by, directory), directory) -> str
    in2lambda.draft.report.validate(directory) -> list[Finding]
    in2lambda.draft.export.build(directory, output_dir) -> Path

A draft lives beside the file it was frozen from: `source add` writes
`draft.json` next to the source, and every command after it is given that
directory. A spec is named relative to the same directory, since that is what
the draft's log records having run.
"""

import json
from dataclasses import dataclass, field
from os.path import relpath
from pathlib import Path

import in2lambda.draft
import in2lambda.draft.export
import in2lambda.draft.report
import in2lambda.source
import in2lambda.spec
from in2lambda.source import SourceError

# Who ran the command, as the draft's log records it. The agent is one author
# whichever model wrote the spec it is running.
BY = "in2lambda-agent"

DRAFT = in2lambda.source.DRAFT
"""What a frozen source is written to, in the directory every command is given."""


class SpecRejected(ValueError):
    """in2lambda would not run a spec, and says why."""


@dataclass
class Coverage:
    """What a spec run made of the source, which is what a spec is judged on.

    Attributes:
        layout: The layout the spec chose, which pairs solutions to parts.
        blocks: How many blocks the frozen source has.
        fields: How many fields each layer wrote, keyed by layer number.
        ignored: How many blocks the spec's `ignore` selector matched.
        unassigned: The ids of blocks in no field and not ignored.
    """

    layout: str
    blocks: int
    fields: dict[int, int] = field(default_factory=dict)
    ignored: int = 0
    unassigned: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        """The one line the coverage stage prints."""
        written = ", ".join(
            f"{count} fields at layer {layer}"
            for layer, count in sorted(self.fields.items())
        )
        left = ", ".join(self.unassigned) if self.unassigned else "none"
        return (
            f"{self.layout}: {self.blocks} blocks, {written or 'no fields'}, "
            f"{self.ignored} ignored, {left} unassigned"
        )


@dataclass
class Report:
    """What the checks found in a draft."""

    clean: bool
    errors: list[str]


def source_add(source: Path) -> Path:
    """Freezes a source document and returns the directory its draft is in.

    Always from the beginning: the agent's run owns the draft it writes, so a
    second run over the same file is a second run and not a continuation of
    the first one's fields.

    Args:
        source: The markdown, tex or docx file to freeze.

    Returns:
        The directory holding the `draft.json` that was written, which every
        command below is given.

    Raises:
        SourceError: pandoc or panflute is missing, or the file cannot be read.
    """
    return in2lambda.source.add(str(source), True).parent


def source_show(draft_dir: Path) -> str:
    """The frozen markdown, numbered, with block ids in the margin.

    Args:
        draft_dir: Where the `draft.json` is.

    Returns:
        What the model is shown to write a spec from.

    Raises:
        SourceError: there is no draft there, or its source has moved on.
    """
    return in2lambda.source.show(str(draft_dir))


def spec_run(draft_dir: Path, spec: Path) -> Coverage:
    """Runs a spec over a frozen source, filling the draft's layer 1 fields.

    Args:
        draft_dir: Where the `draft.json` is.
        spec: The spec file, wherever it is kept.

    Returns:
        What the run made of the source.

    Raises:
        SpecRejected: the spec cannot be read, or one of its selectors claims
            lines another has already claimed.
    """
    # Relative to the draft, which is how the log names a file: an absolute
    # path would record this machine rather than the run.
    name = relpath(spec, draft_dir)
    try:
        layout = in2lambda.spec.load(Path(spec).read_bytes()).layout
        in2lambda.draft.execute(
            in2lambda.draft.spec_command(name, BY, str(draft_dir)), str(draft_dir)
        )
    except SourceError as error:
        raise SpecRejected(str(error)) from None

    draft = json.loads((draft_dir / in2lambda.source.DRAFT).read_text())
    coverage = Coverage(layout=layout, blocks=len(draft["blocks"]))
    for key, written in draft["fields"].items():
        if key.endswith(".ignore"):
            coverage.ignored += 1
        else:
            layer = written["layer"]
            coverage.fields[layer] = coverage.fields.get(layer, 0) + 1
    coverage.unassigned = [
        finding["field"] for finding in in2lambda.draft.report.uncovered(draft)
    ]
    return coverage


def validate(draft_dir: Path) -> Report:
    """Checks a draft over and writes the report into it, as `build` requires.

    Args:
        draft_dir: Where the `draft.json` is.

    Returns:
        Whether the checks found nothing, and what they found where they did.

    Raises:
        SourceError: there is no draft there, or its source has moved on.
    """
    findings = in2lambda.draft.report.validate(str(draft_dir))
    return Report(clean=not findings, errors=[f["message"] for f in findings])


def build(draft_dir: Path, out_dir: Path) -> Path:
    """Writes a validated draft out as a Lambda Feedback set.

    Args:
        draft_dir: Where the `draft.json` is.
        out_dir: Where to write the set's folder and its zip.

    Returns:
        The zip that was written.

    Raises:
        SourceError: the draft has not validated clean, or refers to an image
            that is not beside it.
    """
    return in2lambda.draft.export.build(str(draft_dir), str(out_dir))
