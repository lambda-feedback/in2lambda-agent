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
    in2lambda.draft.execute(
        {"command": name, "args": {...}, "by": by}, directory) -> str
    in2lambda.draft.report.validate(directory) -> list[Finding]
    in2lambda.draft.render(directory, out_dir) -> dict  # not there yet
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
from typing import Any

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

COMMANDS = (
    "mark ignore",
    "question add",
    "part add",
    "question solution",
    "field replace",
    "split block",
)
"""The draft commands a report is fixed with, named as the log names them."""


class SpecRejected(ValueError):
    """in2lambda would not run a spec, and says why."""


class CommandRefused(ValueError):
    """in2lambda would not run a draft command, and says why."""


class RenderUnavailable(RuntimeError):
    """in2lambda has no render command yet, so there are no pages to show."""


class BuildRefused(ValueError):
    """in2lambda would not write a validated draft out, and says why."""


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
class QuestionInfo:
    """One question of a draft, as a reviewer is given it.

    Attributes:
        key: The question, by the key of its fields: `q2`.
        layer: The highest layer any of its fields came from, counting an
            edited field as layer 4. 3 or 4 means something other than the
            spec wrote part of it, which is what sample review looks at first.
        ranges: Every range of the frozen source its fields were copied from,
            in order, so that the reviewer can read the question against it.
    """

    key: str
    layer: int
    ranges: list[list[int]]


@dataclass
class Finding:
    """One thing the checks found, as the report writes it.

    Attributes:
        check: Which check found it.
        field: The block id or field key it is about, which is what a command
            fixing it names.
        ranges: The lines in question, as `[[start, end], ...]`.
        message: A sentence naming all of that, which is what the model is given.
    """

    check: str
    field: str
    ranges: list[list[int]]
    message: str


@dataclass
class Report:
    """What the checks found in a draft."""

    clean: bool
    errors: list[str]
    findings: list[Finding] = field(default_factory=list)


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


def command(
    draft_dir: Path, name: str, args: dict[str, Any], by: str = BY
) -> str:
    """Runs one draft command, which is how every fix reaches a draft.

    in2lambda writes the field, records the command in the draft's log as it
    applies it, and decides which layer the field came from — 3 for text copied
    out of the frozen source, 4 for text typed out. So a fix leaves its whole
    record without the agent keeping one of its own, and this is the only place
    that knows what a log entry looks like.

    Args:
        draft_dir: Where the `draft.json` is.
        name: One of COMMANDS.
        args: The command's arguments, as the log records them.
        by: Who asked for it, as the draft's log records it: the agent, or the
            reviewer whose own edit this is.

    Returns:
        What the command wrote: the key of the field, or the ids a split made.

    Raises:
        CommandRefused: in2lambda would not run it — a block that is not there,
            lines another field has taken, wording that is not in the field once.
    """
    try:
        return in2lambda.draft.execute(
            {"command": name, "args": args, "by": by}, str(draft_dir)
        )
    except SourceError as error:
        raise CommandRefused(str(error)) from None


def command_log(draft_dir: Path) -> list[dict[str, Any]]:
    """Every command the draft records having been built by, in the order they ran.

    Args:
        draft_dir: Where the `draft.json` is.

    Returns:
        One entry per command, each `{"command", "args", "by"}`.
    """
    return json.loads((draft_dir / DRAFT).read_text())["log"]


def questions(draft_dir: Path) -> dict[str, QuestionInfo]:
    """The questions a draft holds, in the order they are numbered.

    A question is its fields — `q2.text`, `q2.p1.solution` — so this is what
    reading the draft's fields by their keys says about each one: where in the
    source it was copied from, and whether anything past the spec wrote it.

    Args:
        draft_dir: Where the `draft.json` is.

    Returns:
        One entry per question, keyed by `q1`, `q2`.
    """
    draft = json.loads((draft_dir / DRAFT).read_text())
    found: dict[str, QuestionInfo] = {}
    for key, written in draft["fields"].items():
        name = key.split(".")[0]
        if not name.startswith("q"):
            # A block marked ignore: `b3.ignore`, which is in no question.
            continue
        info = found.setdefault(name, QuestionInfo(name, 0, []))
        # An edit is layer 4 work whatever layer wrote the field first:
        # in2lambda leaves a replaced field quoting the lines it came from and
        # marks it as no longer saying what they say, which is the design
        # spec's layer 1 field carrying a layer 4 edit.
        info.layer = max(info.layer, 4 if written["edited"] else written["layer"])
        # A field a `literal` typed out is layer 4 with no source behind it, so
        # it has no ranges to add: the listing's `none` is what it comes to.
        info.ranges.extend(written.get("ranges") or [])
    for info in found.values():
        info.ranges.sort()
    return {key: found[key] for key in sorted(found, key=lambda key: int(key[1:]))}


def frozen_source(draft_dir: Path) -> Path:
    """The file the draft was frozen from, which the reviewer reads it against.

    Args:
        draft_dir: Where the `draft.json` is.

    Returns:
        The source file, named from the directory the draft is in as the draft
        itself names it.
    """
    return draft_dir / json.loads((draft_dir / DRAFT).read_text())["source"]


def render(draft_dir: Path, out_dir: Path) -> dict[str, Path]:
    """Writes each question of a draft as a PDF, for a reviewer to read.

    Args:
        draft_dir: Where the `draft.json` is.
        out_dir: Where to write the PDFs.

    Returns:
        The PDF written for each question, keyed as `questions` keys it.

    Raises:
        RenderUnavailable: in2lambda has no render command yet.
        CommandRefused: the draft cannot be rendered, and in2lambda says why.
    """
    renderer = getattr(in2lambda.draft, "render", None)
    if renderer is None:
        raise RenderUnavailable(
            "in2lambda render is not there yet, so the review names each "
            "question by the lines of the source it was built from instead"
        )
    try:
        written = renderer(str(draft_dir), str(out_dir))
    except SourceError as error:
        raise CommandRefused(str(error)) from None
    return {key: Path(path) for key, path in written.items()}


def layers(draft_dir: Path) -> dict[str, int]:
    """How many fields each layer wrote, and how many of them were edited.

    The design spec's test plan asks per document for the share of fields from
    each layer. Counts rather than shares: they diff cleanly between two runs,
    and a share is one division away from them.

    Args:
        draft_dir: Where the `draft.json` is.

    Returns:
        `{"layer1": n, ..., "layer4": n, "edited": n}`, always all five keys.
        A block marked `ignore` is not a field and is in none of them, which is
        what `Coverage.fields` counts too.
    """
    fields = json.loads((draft_dir / DRAFT).read_text())["fields"]
    counted = {f"layer{number}": 0 for number in (1, 2, 3, 4)}
    counted["edited"] = 0
    for key, written in fields.items():
        if key.endswith(".ignore"):
            continue
        counted[f"layer{written['layer']}"] += 1
        counted["edited"] += bool(written["edited"])
    return counted


def validate(draft_dir: Path) -> Report:
    """Checks a draft over and writes the report into it, as `build` requires.

    Args:
        draft_dir: Where the `draft.json` is.

    Returns:
        Whether the checks found nothing, and what they found where they did.

    Raises:
        SourceError: there is no draft there, or its source has moved on.
    """
    # Named field by field rather than passed through, so that a check in2lambda
    # grows later arrives here as a finding of the shape the fixer already reads.
    findings = in2lambda.draft.report.validate(str(draft_dir))
    return Report(
        clean=not findings,
        errors=[found["message"] for found in findings],
        findings=[
            Finding(
                found["check"], found["field"], found["ranges"], found["message"]
            )
            for found in findings
        ],
    )


def build(draft_dir: Path, out_dir: Path) -> Path:
    """Writes a validated draft out as a Lambda Feedback set.

    Args:
        draft_dir: Where the `draft.json` is.
        out_dir: Where to write the set's folder and its zip.

    Returns:
        The zip that was written.

    Raises:
        BuildRefused: the draft has not validated clean, or refers to an image
            that is not beside it.
    """
    try:
        return in2lambda.draft.export.build(str(draft_dir), str(out_dir))
    except SourceError as error:
        raise BuildRefused(str(error)) from None
