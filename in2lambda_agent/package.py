"""Every call the agent makes into in2lambda, in the agent's own shapes.

in2lambda does every deterministic step and every write, and this is the only
module that knows how those steps are called, so that the pipeline reads as the
design spec's diagram rather than as someone else's API.

The commands are the Python functions the `in2lambda` CLI itself calls, each
taking the path of the draft they act on rather than the working directory:

    in2lambda.source.add(files, start_over) -> Path   # the draft written
    in2lambda.source.show(draft) -> str               # numbered, with block ids
    in2lambda.draft.execute(
        in2lambda.draft.spec_command(spec, by, draft), draft) -> str
    in2lambda.draft.execute(
        {"command": name, "args": {...}, "by": by}, draft) -> str
    in2lambda.draft.report.validate(draft) -> list[Finding]
    in2lambda.draft.export.render(draft, output_dir) -> list[Path]
    in2lambda.draft.export.build(draft, output_dir) -> Path

A draft lives beside the file it was frozen from and is named after it:
`source add sheet.tex` writes `sheet.draft.json` next to it, and every command
after it is given that file. A spec is named relative to the directory the draft
is in, since that is what the draft's log records having run.
"""

import json
import re
import warnings
from dataclasses import dataclass, field
from os.path import relpath
from pathlib import Path
from typing import Any, Optional

import in2lambda.draft
import in2lambda.draft.export
import in2lambda.draft.report
import in2lambda.source
import in2lambda.spec
from in2lambda.source import SourceError

# Who ran the command, as the draft's log records it. The agent is one author
# whichever model wrote the spec it is running.
BY = "in2lambda-agent"

DRAFT_SUFFIX = in2lambda.source.DRAFT_SUFFIX
"""What a frozen source's draft is named with, beside the source itself."""

draft_of = in2lambda.source.draft_of
"""Where the draft of a document goes, for naming one without freezing it."""

ERROR = in2lambda.draft.report.ERROR
"""The level of a finding a build refuses over. Anything else it says and goes
on past, which is what makes a warnings-only report one to build."""

COMMANDS = (
    "mark ignore",
    "question add",
    "part add",
    "question solution",
    "field replace",
    "split block",
    "field set",
    "part solution",
)
"""The draft commands a report is fixed with, named as the log names them."""

RENDERED = re.compile(r"question_(\d+)_")
"""How in2lambda names the PDF it writes for a question: the question's place in
the set, counting from zero, so that a stack of them reads in order."""


class SpecRejected(ValueError):
    """in2lambda would not run a spec, and says why."""


class CommandRefused(ValueError):
    """in2lambda would not run a draft command, and says why."""


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
        dropped: One finding per ignored block whose lines hold an image, as
            `ignored_images` reports them. The set is built without those
            images, and nothing in2lambda checks says so.
    """

    layout: str
    blocks: int
    fields: dict[int, int] = field(default_factory=dict)
    ignored: int = 0
    unassigned: list[str] = field(default_factory=list)
    dropped: list["Finding"] = field(default_factory=list)

    def __str__(self) -> str:
        """The one line the coverage stage prints."""
        written = ", ".join(
            f"{count} fields at layer {layer}"
            for layer, count in sorted(self.fields.items())
        )
        left = ", ".join(self.unassigned) if self.unassigned else "none"
        line = (
            f"{self.layout}: {self.blocks} blocks, {written or 'no fields'}, "
            f"{self.ignored} ignored, {left} unassigned"
        )
        if self.dropped:
            images = ", ".join(_where(one.field, one.ranges) for one in self.dropped)
            plural = "image" if len(self.dropped) == 1 else "images"
            line += f"; {len(self.dropped)} {plural} dropped: {images}"
        return line


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
        check: Which check found it. `problem` is in2lambda's own validation of
            the set the draft describes — the maths delimiters, KaTeX, the
            images, the compile — reported against the field it is written in.
        level: `error` or `warning`. A build refuses over an error and says a
            warning and goes on past it.
        field: The block id or field key it is about, which is what a command
            fixing it names.
        ranges: The lines in question, as `[[start, end], ...]`.
        message: A sentence naming all of that, which is what the model is given.
    """

    check: str
    level: str
    field: str
    ranges: list[list[int]]
    message: str


@dataclass
class Report:
    """What the checks found in a draft.

    `clean` is whether the draft can be built, not whether the checks found
    nothing: a report holding only warnings — a part whose solution is not on
    the sheet — is one in2lambda builds, saying each warning as it goes. So a
    warnings-only report is clean, `errors` is empty and `warnings` is not.
    """

    clean: bool
    errors: list[str]
    warnings: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def is_document(path: Path) -> bool:
    """Whether a file is a document of its own rather than input to one.

    A tex file with no `\\begin{document}` is a fragment: a TikZ source under a
    `figures/` folder, or a preamble a sheet inputs. Frozen and built it is a set
    of one question made of a drawing, which is not what the corpus holds it for.
    The other suffixes have no such marker, and every file of them is a document.
    """
    if path.suffix.lower() != ".tex":
        return True
    return r"\begin{document}" in path.read_text(encoding="utf-8", errors="replace")


def said(report: Report) -> str:
    """The validate line of a report nothing stops: the warnings, or nothing."""
    if not report.warnings:
        return "nothing to report"
    return "; ".join(report.warnings) + " — warnings, building"


def froze(draft: Path, solutions: Optional[str] = None) -> str:
    """The freeze line of a draft: the file, and what else was frozen into it.

    Args:
        draft: The draft that was written.
        solutions: The name of the solutions document, where the draft holds
            one as its second source.
    """
    if not solutions:
        return str(draft)
    return f"{draft}, with {solutions} as source 2"


def source_add(source: Path, *more: Path) -> Path:
    """Freezes one or more source documents into the draft beside the first.

    Always from the beginning: the agent's run owns the draft it writes, so a
    second run over the same file is a second run and not a continuation of
    the first one's fields.

    Args:
        source: The markdown, tex or docx file to freeze. The draft is named
            after it, and its blocks are `b1` onwards.
        more: Further documents to freeze into the same draft, in the order
            they are to be numbered: a sheet's solutions written as a file of
            their own. The blocks of the second source are `2/b1` onwards.

    Returns:
        The `FILE.draft.json` that was written, which every command below is
        given.

    Raises:
        SourceError: pandoc or panflute is missing, the files are not all in
            one directory, or a file cannot be read.
    """
    return in2lambda.source.add([str(source), *(str(one) for one in more)], True)


def source_show(draft: Path) -> str:
    """The frozen markdown, numbered, with block ids in the margin.

    Args:
        draft: The draft file.

    Returns:
        What the model is shown to write a spec from. A draft of two sources
        heads each with `Source N: NAME`, and the ids of the second source's
        blocks carry its number: `2/b1`.

    Raises:
        SourceError: there is no draft there, or its source has moved on.
    """
    return in2lambda.source.show(str(draft))


def spec_run(draft: Path, spec: Path) -> Coverage:
    """Runs a spec over a frozen source, filling the draft's layer 1 fields.

    Args:
        draft: The draft file.
        spec: The spec file, wherever it is kept.

    Returns:
        What the run made of the source.

    Raises:
        SpecRejected: the spec cannot be read, or one of its selectors claims
            lines another has already claimed.
    """
    # Relative to the draft, which is how the log names a file: an absolute
    # path would record this machine rather than the run.
    name = relpath(spec, Path(draft).parent)
    try:
        layout = in2lambda.spec.load(Path(spec).read_bytes()).layout
        in2lambda.draft.execute(
            in2lambda.draft.spec_command(name, BY, str(draft)), str(draft)
        )
    except SourceError as error:
        raise SpecRejected(str(error)) from None

    found = _frozen(draft)
    coverage = Coverage(layout=layout, blocks=blocks(draft))
    for key, written in found["fields"].items():
        if key.endswith(".ignore"):
            coverage.ignored += 1
        else:
            layer = written["layer"]
            coverage.fields[layer] = coverage.fields.get(layer, 0) + 1
    coverage.unassigned = [
        finding["field"] for finding in in2lambda.draft.report.uncovered(found)
    ]
    coverage.dropped = ignored_images(draft)
    return coverage


def ignored_images(draft: Path) -> list[Finding]:
    """The blocks a spec marked ignore whose lines hold an image.

    A figure belongs to the question or part it illustrates. Mathpix writes a
    figure as one paragraph, the image line and then its caption, so an `ignore`
    selector matching the caption's `Figure n:` marks the image ignored and the
    set is built without it. in2lambda's checks report nothing about an ignored
    block, so the agent reads the ignored blocks back and reports each image.

    Args:
        draft: The draft file, after a spec has been run over it.

    Returns:
        One finding per ignored block whose lines hold a markdown image, in the
        order the blocks appear in the sources. The message follows the wording
        of in2lambda's own coverage findings, so that a spec-writing prompt
        reads the same for either finding. A source whose bytes are not text —
        a docx, frozen as itself — has no such block to report.
    """
    found = _frozen(draft)
    read: dict[int, Optional[list[str]]] = {}

    def source_lines(number: int) -> Optional[list[str]]:
        """The lines of one source, read the first time a block of it is ignored."""
        if number not in read:
            path = Path(draft).parent / found["sources"][number]["source"]
            read[number] = _source_lines(path)
        return read[number]

    dropped = []
    for key, written in found["fields"].items():
        if not key.endswith(".ignore"):
            continue
        block = key[: -len(".ignore")]
        # `2/b3` is the second source's block; `b3` is the first source's.
        number, _, _ = block.rpartition("/")
        lines = source_lines(int(number) - 1 if number else 0)
        if lines is None:
            continue
        ranges = written["ranges"]
        held = "\n".join("\n".join(lines[start - 1 : end]) for start, end in ranges)
        if "![" in held:
            dropped.append(
                Finding(
                    check="coverage",
                    level=ERROR,
                    field=block,
                    ranges=ranges,
                    message=f"{_where(block, ranges)} holds an image and is "
                    "marked ignore.",
                )
            )
    return sorted(dropped, key=lambda one: (one.field.rpartition("/")[0], one.ranges))


def _where(field: str, ranges: list[list[int]]) -> str:
    """A block and the lines it covers, as a finding's message names one.

    Returns `b10 (lines 29-30)`, which is how in2lambda's own findings name one.
    """
    covered = ", ".join(f"{start}-{end}" for start, end in ranges)
    return f"{field} (lines {covered})"


def _source_lines(path: Path) -> Optional[list[str]]:
    """A frozen source read as text, or None where its bytes are not text.

    A docx is frozen as itself and is a zip, and a tex sheet of a real set need
    not be UTF-8, so a source is decoded the way `is_document` decodes one and
    a source holding a NUL byte is left alone: it has no line of markdown to
    find an image in.
    """
    raw = path.read_bytes()
    if b"\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace").splitlines()


def blocks(draft: Path) -> int:
    """How many blocks a draft's frozen source has.

    A spec run reports this count in its coverage. in2lambda runs no spec it
    refuses, so a caller reporting how many blocks a refused spec left in no
    field counts every block of the source.

    Args:
        draft: The draft file.

    Returns:
        The count.
    """
    return sum(len(one["blocks"]) for one in _frozen(draft)["sources"])


def _frozen(draft: Path) -> dict[str, Any]:
    """A draft read off disk, as in2lambda writes one."""
    return json.loads(Path(draft).read_text())


def command(draft: Path, name: str, args: dict[str, Any], by: str = BY) -> str:
    """Runs one draft command, which is how every fix reaches a draft.

    in2lambda writes the field, records the command in the draft's log as it
    applies it, and decides which layer the field came from — 3 for text copied
    out of the frozen source, 4 for text typed out. So a fix leaves its whole
    record without the agent keeping one of its own, and this is the only place
    that knows what a log entry looks like.

    Args:
        draft: The draft file.
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
            {"command": name, "args": args, "by": by}, str(draft)
        )
    except SourceError as error:
        raise CommandRefused(str(error)) from None


def field_value(draft: Path, key: str) -> Optional[str]:
    """The text a draft holds for one field.

    Args:
        draft: The draft file.
        key: The field's key: `q1.text`.

    Returns:
        The field's text, or None where the draft holds no field of that key.
        A block marked `ignore` is a field whose value is `true` rather than
        text, and it has no text to return either.
    """
    written = _frozen(draft)["fields"].get(key)
    value = None if written is None else written["value"]
    return value if isinstance(value, str) else None


def command_log(draft: Path) -> list[dict[str, Any]]:
    """Every command the draft records having been built by, in the order they ran.

    Args:
        draft: The draft file.

    Returns:
        One entry per command, each `{"command", "args", "by"}`.
    """
    return _frozen(draft)["log"]


def questions(draft: Path) -> dict[str, QuestionInfo]:
    """The questions a draft holds, in the order they are numbered.

    A question is its fields — `q2.text`, `q2.p1.solution` — so this is what
    reading the draft's fields by their keys says about each one: where in the
    source it was copied from, and whether anything past the spec wrote it.

    Args:
        draft: The draft file.

    Returns:
        One entry per question, keyed by `q1`, `q2`.
    """
    found: dict[str, QuestionInfo] = {}
    for key, written in _frozen(draft)["fields"].items():
        name = key.split(".")[0]
        if not name.startswith("q"):
            # A block marked ignore: `b3.ignore`, or `2/b3.ignore` for a block
            # of a second frozen source, which is in no question either. What a
            # block id looks like is in2lambda's, so this asks what the key is
            # not rather than what it is.
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


def frozen_source(draft: Path) -> Path:
    """The file the draft was frozen from, which the reviewer reads it against.

    Args:
        draft: The draft file.

    Returns:
        The first source, named from the directory the draft is in as the draft
        itself names it. A draft the agent wrote has only the one.
    """
    return Path(draft).parent / _frozen(draft)["sources"][0]["source"]


def render(draft: Path, out_dir: Path) -> dict[str, Path]:
    """Writes each question of a draft as a PDF, for a reviewer to read.

    Args:
        draft: The draft file.
        out_dir: Where to write the PDFs.

    Returns:
        The PDF written for each question, keyed as `questions` keys it. A
        question the compiler gave up on while the rest rendered has no PDF and
        is not in it, which is why the key comes from the file's name rather
        than from its place in the list.

    Raises:
        CommandRefused: the pages cannot be compiled — pandoc or xelatex is
            missing, or in2lambda says why.
    """
    try:
        written = in2lambda.draft.export.render(str(draft), str(out_dir))
    except SourceError as error:
        raise CommandRefused(str(error)) from None
    keyed = {}
    for path in written:
        numbered = RENDERED.match(Path(path).name)
        if numbered:
            keyed[f"q{int(numbered.group(1)) + 1}"] = Path(path)
    return keyed


def layers(draft: Path) -> dict[str, int]:
    """How many fields each layer wrote, and how many of them were edited.

    The design spec's test plan asks per document for the share of fields from
    each layer. Counts rather than shares: they diff cleanly between two runs,
    and a share is one division away from them.

    Args:
        draft: The draft file.

    Returns:
        `{"layer1": n, ..., "layer4": n, "edited": n}`, always all five keys.
        A block marked `ignore` is not a field and is in none of them, which is
        what `Coverage.fields` counts too.
    """
    fields = _frozen(draft)["fields"]
    counted = {f"layer{number}": 0 for number in (1, 2, 3, 4)}
    counted["edited"] = 0
    for key, written in fields.items():
        if key.endswith(".ignore"):
            continue
        counted[f"layer{written['layer']}"] += 1
        counted["edited"] += bool(written["edited"])
    return counted


def validate(draft: Path) -> Report:
    """Checks a draft over and writes the report into it, as `build` requires.

    Args:
        draft: The draft file.

    Returns:
        Whether the draft can be built, and what the checks found. Only an error
        stops a build: a report holding warnings alone is clean, and the build
        says each of them and writes the set anyway.

    Raises:
        SourceError: there is no draft there, or its source has moved on.
    """
    # Named field by field rather than passed through, so that a check in2lambda
    # grows later arrives here as a finding of the shape the fixer already reads.
    findings = [
        Finding(
            found["check"],
            found["level"],
            found["field"],
            found["ranges"],
            found["message"],
        )
        for found in in2lambda.draft.report.validate(str(draft))
    ]
    # An error is the only thing a build refuses over, so it is the only thing
    # `clean` asks about: the warnings are said and gone past.
    errors = [one.message for one in findings if one.level == ERROR]
    return Report(
        clean=not errors,
        errors=errors,
        warnings=[one.message for one in findings if one.level != ERROR],
        findings=findings,
    )


@dataclass
class Built:
    """A set in2lambda wrote, and what it warned as it wrote one.

    Attributes:
        zip_path: The zip that was written.
        warnings: What in2lambda warned while building, in the order it warned
            each one, as the message alone.
    """

    zip_path: Path
    warnings: list[str]


def build(draft: Path, out_dir: Path) -> Built:
    """Writes a validated draft out as a Lambda Feedback set.

    in2lambda reports a warning-level finding through `warnings.warn`, which
    Python prints to stderr with the line of in2lambda that raised it. This
    function records each warning and returns it with the zip, so that the
    caller decides what a reader sees.

    Args:
        draft: The draft file.
        out_dir: Where to write the set's folder and its zip.

    Returns:
        The zip that was written and the warnings in2lambda said as it wrote
        one. A report holding only warnings is one the build proceeds past, so
        a draft with a part nothing answers still builds.

    Raises:
        BuildRefused: the checks found an error in the draft, or it refers to an
            image that is not beside it.
    """
    try:
        with warnings.catch_warnings(record=True) as said:
            # A warning Python has shown once is not shown again by default,
            # and a long-running harness builds more than one draft.
            warnings.simplefilter("always")
            zip_path = in2lambda.draft.export.build(str(draft), str(out_dir))
    except SourceError as error:
        raise BuildRefused(str(error)) from None
    return Built(zip_path, [str(one.message) for one in said])
