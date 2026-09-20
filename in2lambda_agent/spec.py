"""Layer 1: the YAML spec, written once per document set and iterated into shape.

A spec is selectors over the frozen source saying which blocks are questions,
which are parts and which are solutions, what to strip off the front of a field
and what to ignore, and which layout pairs the solutions up. `in2lambda spec
run` reads it; nothing here decides what a question is, and nothing here writes
a field.

The spec is saved beside the source, under the name every sheet in that folder
shares, because a document set is a folder of sheets written the same way: the
next one runs the saved spec with no model call. `--spec` names another file,
which is read if it is there and written if it is not.

A spec is the one piece of model output every sheet of a set reuses, so it is
written against what running it covers rather than blind. `iterate_spec` writes
one, runs it over this source and over another document of the set, reads the
coverage and the validation report back to the next call, and saves the spec
that left the fewest blocks unassigned and the fewest errors behind.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import yaml

from in2lambda_agent import package
from in2lambda_agent.fix import RoundResult
from in2lambda_agent.model import Backend, Reply, Usage
from in2lambda_agent.package import Coverage, Report

SPEC_NAME = "in2lambda-spec.yaml"
"""What the set's spec is called, beside the sources it is for."""

RECORD_NAME = "in2lambda-agent-runs.jsonl"
"""One line per run, beside the spec: what it covered and what it cost."""

LAYOUTS = ("PartsSepSol", "PartsOneSol", "PartSolPartSol", "PartPartSolSol")
"""The four ways a document writes its solutions, and all a spec may name."""

SYSTEM = """\
You write a spec for in2lambda: a small YAML file of selectors over a document \
already parsed into blocks. It is read by `in2lambda spec run`, which fills a \
draft's fields from it. Answer with the YAML and nothing else — no prose, no \
code fence.

A spec has these keys, and no others:

  question: which blocks hold a question's own text. Required.
  part:     which blocks hold one lettered part of a question.
  solution: which blocks hold a worked solution.
  ignore:   which blocks are none of those and are wanted in no field.
  strip:    a list of regexes taken off the front of every field's text.
  layout:   one of PartsSepSol, PartsOneSol, PartSolPartSol, PartPartSolSol.

A selector is an optional block type, then any number of constraints:

  Header level=2 text~'^Question'     type, then an exact and a regex constraint
  Para label~'^[0-9]'                 label is the block's first word
  after Header text=Solutions, Para   only blocks somewhere after such a header

The type is the pandoc element's own name: Header, Para, ListItem, Table,
BlockQuote, CodeBlock. Leave it out to match any block. `level` is a heading's
level, `text` is everything the block says, `label` its first word. Put single
quotes round any regex with a backslash in it.

Every block is tried against ignore, then question, then part, then solution,
whatever order the keys are written in, and is whatever the first of them says
it is. So the selectors must not overlap: if the solutions are paragraphs and
the question stems are paragraphs too, the question selector needs a constraint
that the solutions fail.

Three things about blocks to write selectors against:

  * One block fills one field. A question's text is the block holding its stem,
    not the heading above it — headings usually belong in `ignore`.
  * A lettered or numbered item — `(a) ...`, `a. ...`, `1. ...` — is a
    ListItem, and its marker is not part of the text a constraint matches. The
    marker is still in the field's value, so `strip` is what takes it off.
  * A block indented under a list item is inside it, not beside it: such a
    question and its parts are one block, and there is nothing to select.

The layout says which question or part a solution answers:

  PartsSepSol     every solution together at the end, in part order: each
                  question with parts is answered part by part, each question
                  without parts is answered once.
  PartsOneSol     one solution to the whole question.
  PartSolPartSol  each part answered where it stands.
  PartPartSolSol  a question's parts, then their solutions in the same order.

For a sheet whose questions are under `## Question n` headings, whose parts are
`(a)`/`(b)` items, and whose solutions are labelled `1(a)` under a `## \
Solutions` heading:

ignore:   Header
question: Para text~'^[A-Z]'
part:     ListItem
solution: after Header text=Solutions, Para
strip:    ['^\\([a-z]\\) ', '^\\d+\\([a-z]\\) ']
layout:   PartsSepSol

Every block of the source must end up in a field or be ignored: a block left
over is reported, and the run stops. Write selectors that account for all of
them.

You may be shown the spec you last wrote, what running it covered and what the
checks found in the draft it filled, and asked for a better one. The spec is
saved for the whole set, so a document of the set other than this one is shown
as well where the folder holds one. Change the selectors that left blocks over
and keep the ones that did not.\
"""


class BadSpec(ValueError):
    """What the model answered with is not a spec."""


@dataclass
class SpecTry:
    """One spec the agent wrote, and what running it made of the set.

    Attributes:
        number: 0 for the saved spec a rewrite starts from, then 1 for the first
            call, 2 for the second.
        usage: What the call cost, all zeroes for try 0.
        unassigned: How many blocks the spec left in no field and not ignored.
        errors: How many errors the checks then found in the draft it filled.
        second: How many blocks it left over in another document of the set, or
            None where the folder holds no other document.
        chosen: Whether this is the spec the run saved and went on with.
    """

    number: int
    usage: Usage = field(default_factory=Usage)
    unassigned: int = 0
    errors: int = 0
    second: Optional[int] = None
    chosen: bool = False

    @property
    def score(self) -> int:
        """What the tries are ranked by, the lowest winning.

        A block in no field is an error of the report as well as a line of the
        coverage, so it counts twice. That is the same double for every try and
        does not change the order they come in.
        """
        return self.unassigned + self.errors + (self.second or 0)


@dataclass
class Previous:
    """A spec that has been run, as the call revising it is shown it.

    Attributes:
        text: The spec itself.
        coverage: What running it made of this source.
        report: What the checks found in the draft it filled.
        second: What running it made of another document of the set, or None
            where the folder holds no other document.
        second_name: That document's file name.
    """

    text: str
    coverage: Optional[Coverage] = None
    report: Optional[Report] = None
    second: Optional[Coverage] = None
    second_name: str = ""


def spec_path(source: Path, spec: Optional[Path] = None) -> Path:
    """Where this source's set keeps its spec.

    Args:
        source: The file the user asked to convert, before any OCR: the set is
            the folder that file is in, not the folder its markdown ended up in.
        spec: A spec named on the command line, which overrides the set's own.

    Returns:
        The file to read the spec from, and to write it to if it is not there.
    """
    if spec is not None:
        return Path(spec).resolve()
    return Path(source).resolve().parent / SPEC_NAME


def write_spec(
    shown: str, backend: Backend, previous: Optional[Previous] = None
) -> tuple[str, Reply]:
    """Writes a spec for a source, in one model call with no tools.

    Args:
        shown: The numbered source with block ids, as `source show` prints it.
        backend: The backend to call, already known to be available.
        previous: The spec run before this call and what running it covered,
            where this call is a revision of that spec.

    Returns:
        The spec, and the reply it came in.

    Raises:
        BadSpec: the reply is not YAML, is not a mapping, or names no layout
            or one that is not a layout.
    """
    prompt = f"Here is the source, one line each with its block id:\n\n{shown}\n"
    if previous is not None:
        prompt += _revision(previous)
    reply = backend.call(SYSTEM, prompt)
    text = _unfenced(reply.text)
    _check(text)
    return text, reply


def _revision(previous: Previous) -> str:
    """The last spec and what running it covered, as the next call is shown them."""
    said = [f"\nYour last spec for this set was:\n\n{previous.text}"]
    if previous.coverage is not None:
        said.append(f"\nRunning it over this source covered:\n\n{previous.coverage}\n")
    if previous.report is not None and previous.report.errors:
        said.append(
            "\nThe checks then found:\n\n" + "\n".join(previous.report.errors) + "\n"
        )
    if previous.second is not None:
        left = ", ".join(previous.second.unassigned) or "no blocks"
        said.append(
            f"\nRunning it over {previous.second_name}, another document of this "
            f"set, left {left} in no field.\n"
        )
    said.append(
        "\nWrite a spec that leaves fewer blocks unassigned and fewer errors "
        "behind, over this source and over the rest of the set.\n"
    )
    return "".join(said)


def iterate_spec(
    frozen: Path,
    saved: Path,
    backend: Backend,
    *,
    tries: int,
    second: Optional[Path] = None,
    previous: Optional[Previous] = None,
) -> tuple[Path, Coverage, Report, list[SpecTry], list[tuple[str, str]]]:
    """Writes the set's spec up to `tries` times and saves the best of them.

    Each call after the first is shown the spec before it, the coverage line,
    the errors the checks found and the blocks the spec left over in another
    document of the set. The loop stops at a spec that leaves no block
    unassigned and no error behind, since a further call has nothing to improve.

    Args:
        frozen: The markdown, tex or docx file each spec is run over.
        saved: The set's spec file, which every try writes and the chosen spec
            is left in.
        backend: The backend to call, already known to be available.
        tries: How many specs may be written.
        second: Another document of the set, run to say whether a spec covers
            the set rather than this one sheet of it.
        previous: The saved spec and what running it covered, where this loop
            is the rewrite of a spec the checks faulted. It is recorded as try
            0 and is what the first call is asked to improve on; the spec it
            names is being replaced, so it is not one of the tries chosen from.

    Returns:
        The draft the chosen spec filled, what that spec covered, what the
        checks found in the draft, what each try did, and one `(stage, message)`
        pair per line for the run to print.

    Raises:
        BadSpec: what the model answered with is not a spec.
        SpecRejected: in2lambda will not run a spec this loop wrote, and the
            spec the set had before it is put back.
        SourceError: in2lambda cannot freeze or check a source.
    """
    made: list[SpecTry] = []
    if previous is not None:
        made.append(
            SpecTry(
                number=0,
                unassigned=len(previous.coverage.unassigned),
                errors=len(previous.report.errors),
            )
        )
    # What the set's spec said before this loop wrote over it: a spec in2lambda
    # refuses is put back, because one left beside the sources is read by every
    # later run over the set, which then makes no call and fails in the same
    # place until someone deletes the file by hand.
    replaced = saved.read_text(encoding="utf-8") if saved.is_file() else None

    stages: list[tuple[str, str]] = []
    best: Optional[tuple[SpecTry, str]] = None
    try:
        for number in range(1, tries + 1):
            draft = package.source_add(frozen)
            stages.append(("freeze", str(draft)))
            text, reply = write_spec(package.source_show(draft), backend, previous)
            saved.write_text(text, encoding="utf-8")
            tokens = reply.usage.input_tokens + reply.usage.output_tokens
            stages.append(
                (
                    "spec",
                    f"wrote {saved} via {reply.backend}, {tokens} tokens, "
                    f"{reply.usage.seconds:.1f}s (try {number} of {tries})",
                )
            )
            coverage, report = _run(draft, saved, stages)
            over_second = None
            if second is not None:
                over_second = package.spec_run(package.source_add(second), saved)
                stages.append(("set", f"{second.name}: {over_second}"))
            one = SpecTry(
                number=number,
                usage=reply.usage,
                unassigned=len(coverage.unassigned),
                errors=len(report.errors),
                second=None if over_second is None else len(over_second.unassigned),
            )
            made.append(one)
            if best is None or one.score < best[0].score:
                best = (one, text)
            if one.score == 0:
                break
            previous = Previous(
                text=text,
                coverage=coverage,
                report=report,
                second=over_second,
                second_name=second.name if second is not None else "",
            )
    except package.SpecRejected:
        if replaced is None:
            saved.unlink()
        else:
            saved.write_text(replaced, encoding="utf-8")
        raise

    chosen, text = best
    chosen.chosen = True
    if len([one for one in made if one.number]) > 1:
        stages.append(("spec", f"kept try {chosen.number} of {tries}"))
    if chosen.number != made[-1].number:
        # A later try covered the set less well, so the chosen spec is written
        # and run again: the draft the run goes on with is the one that spec
        # filled, not the one the last try left.
        saved.write_text(text, encoding="utf-8")
        draft = package.source_add(frozen)
        stages.append(("freeze", str(draft)))
        coverage, report = _run(draft, saved, stages)
    return draft, coverage, report, made, stages


def _run(
    draft: Path, saved: Path, stages: list[tuple[str, str]]
) -> tuple[Coverage, Report]:
    """Runs one spec over one draft and appends the two lines it prints."""
    coverage = package.spec_run(draft, saved)
    stages.append(("coverage", str(coverage)))
    report = package.validate(draft)
    stages.append(
        ("validate", package.said(report) if report.clean else "; ".join(report.errors))
    )
    return coverage, report


def record_run(
    path: Path,
    source: Path,
    *,
    reused: bool,
    coverage: Coverage,
    usage: Usage,
    tries: Sequence[SpecTry] = (),
    rounds: Sequence[RoundResult] = (),
    review: Optional[dict] = None,
) -> None:
    """Appends one line about a run to the set's record.

    The design spec's test plan is run over the corpus and reads these: what
    share of a document a spec covers, whether the set's spec was reused, what
    the model calls cost, and how many rounds of fixing the checks took — or how
    many they took without ever coming clean.

    Args:
        path: The record file, beside the spec.
        source: The file the run converted.
        reused: Whether the spec was the saved one rather than a new call.
        coverage: What the spec run made of the source.
        usage: What the run's model calls cost, all zeroes where there were none.
        tries: What each spec the run wrote covered and cost, in order, and
            empty where the run reused the set's saved spec.
        rounds: What each round of fixing did, in order, and empty where the
            draft came clean out of the spec alone.
        review: What a reviewer made of the set, as `Review.to_json` says it,
            and absent where the run was not reviewed. The design spec's test
            plan counts the rejections a mode drew, which is how a document set
            earns its way to mode none.
    """
    line = {
        "source": str(source),
        "reused": reused,
        "layout": coverage.layout,
        "blocks": coverage.blocks,
        "fields": coverage.fields,
        "ignored": coverage.ignored,
        "unassigned": coverage.unassigned,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "seconds": round(usage.seconds, 3),
        "iterations": [
            {
                "try": one.number,
                "input_tokens": one.usage.input_tokens,
                "output_tokens": one.usage.output_tokens,
                "seconds": round(one.usage.seconds, 3),
                "unassigned": one.unassigned,
                "errors": one.errors,
                "second": one.second,
                "chosen": one.chosen,
            }
            for one in tries
        ],
        "rounds": [
            {
                "round": one.number,
                "input_tokens": one.usage.input_tokens,
                "output_tokens": one.usage.output_tokens,
                "seconds": round(one.usage.seconds, 3),
                "commands": [call.name for call in one.commands],
                "left": one.left,
            }
            for one in rounds
        ],
    }
    if review is not None:
        line["review"] = review
    with Path(path).open("a", encoding="utf-8") as record:
        record.write(json.dumps(line) + "\n")


def _unfenced(text: str) -> str:
    """The spec out of a reply, past a code fence the model wrapped it in.

    Asking for the YAML alone is not the same as getting it, and a fence is the
    one thing a model adds often enough to be worth taking off rather than
    refusing over.
    """
    lines = text.strip().splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
    return "\n".join(lines).strip() + "\n"


def _check(text: str) -> None:
    """Refuses a spec that in2lambda would refuse, saying which part of it.

    Only what is cheap to say better here: the layout is the field the agent
    chooses and in2lambda cannot suggest an alternative for, and a reply that
    is not a mapping at all is a reply that is not a spec. Everything else a
    spec can get wrong is `spec run`'s to report, against the line it is on.
    """
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise BadSpec(f"The spec is not YAML: {error}") from None
    if not isinstance(loaded, dict):
        raise BadSpec(f"A spec is a mapping of selectors, which {text!r} is not.")
    if "layout" not in loaded:
        raise BadSpec(
            "The spec names no layout, and a layout is what pairs a solution "
            f"to the part it answers. One of {', '.join(LAYOUTS)}."
        )
    if loaded["layout"] not in LAYOUTS:
        raise BadSpec(
            f"{loaded['layout']!r} is not a layout. One of {', '.join(LAYOUTS)}."
        )
