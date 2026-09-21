"""Layer 1: the YAML spec, written once per document set by one model call.

A spec is selectors over the frozen source saying which blocks are questions,
which are parts and which are solutions, what to strip off the front of a field
and what to ignore, and which layout pairs the solutions up. `in2lambda spec
run` reads it; nothing here decides what a question is, and nothing here writes
a field.

The spec is saved beside the source, under the name every sheet in that folder
shares, because a document set is a folder of sheets written the same way: the
next one runs the saved spec with no model call. `--spec` names another file,
which is read if it is there and written if it is not.
"""

import json
from pathlib import Path
from typing import Optional, Sequence

import yaml

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
them.\
"""


class BadSpec(ValueError):
    """What the model answered with is not a spec."""


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
    shown: str, backend: Backend, report: Optional[Report] = None
) -> tuple[str, Reply]:
    """Writes a spec for a source, in one model call with no tools.

    Args:
        shown: The numbered source with block ids, as `source show` prints it.
        backend: The backend to call, already known to be available.
        report: What the checks found about the draft a previous spec made,
            where this is the rewrite that follows a dirty validate.

    Returns:
        The spec, and the reply it came in.

    Raises:
        BadSpec: the reply is not YAML, is not a mapping, or names no layout
            or one that is not a layout.
    """
    prompt = f"Here is the source, one line each with its block id:\n\n{shown}\n"
    if report is not None:
        prompt += (
            "\nA previous spec for this set left the draft with this to answer "
            "for. Write a spec that does not:\n\n" + "\n".join(report.errors) + "\n"
        )
    reply = backend.call(SYSTEM, prompt)
    text = _unfenced(reply.text)
    _check(text)
    return text, reply


def record_run(
    path: Path,
    source: Path,
    *,
    reused: bool,
    coverage: Coverage,
    usage: Usage,
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
