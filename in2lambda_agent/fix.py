"""Layers 3 and 4: the model answers a validation report, one round at a time.

`validate` hands back findings — a block in no field, a part nothing answers —
and each is answered by one of in2lambda's draft commands, which are the tools of
this call. The model chooses the command and the lines; in2lambda writes the
field, records the command in the draft's log and decides the layer, so a fix
leaves the same trail whoever asked for it.

Nothing here reads the `check` or the `level` a finding names. A finding is a
field, some lines and a sentence, and that is all a round is given: in2lambda's
own checks of the set — the maths delimiters, KaTeX, the images, the PDF compile
— arrive as findings of this same shape under the check `problem`, and a round
that switched on the check's name would have to be taught each one as it arrived.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from in2lambda_agent import package
from in2lambda_agent.model import Backend, Reply, Tool, ToolCall, Usage

LITERAL_MAX = 80
"""The most characters `literal` may type into a field.

A literal is a repair — a brace the OCR dropped, a marker the field cannot quote
as it stands — and a repair is short. Anything longer is the model writing what
the document does not say, which is the one thing a field must never hold, so
the cap is on the tool's schema and again in the runner: a backend that does not
enforce a schema still gets the refusal.
"""

SYSTEM = f"""\
You are fixing a draft of a question set. `in2lambda validate` has reported what \
is wrong with it, and you have the commands that change one. Answer everything \
the report names, then say in one line what you did.

A draft is fields — `q1.text`, `q1.p2.text`, `q1.solution` — filled in from a \
frozen source, which you are shown with every line numbered and every block's id \
in the margin. Each finding names the field or the block it is about.

Copy, do not type. A field is written by naming where its text is in the source: \
a block id, `b7`, or lines, `s13` or `s13:14`. That is what freezing the source \
was for, and it is the only way a field ends up saying what the document says. \
`literal` types a few characters out instead — a repair, for where the source \
spells the text wrongly and no range of it can be quoted as it stands. It takes \
at most {LITERAL_MAX} characters and a longer one is refused. It is never for \
writing a solution, a part or a question the document does not contain.

A finding no range of the source can answer is left as it is. A part whose \
solution is not on the sheet has no solution, and there is nothing in the source \
to give it: do not write one. It is reported as a warning, and the set is built \
with it. Say in your one-line reply which findings you left and why, and the run \
reports them.

The commands:

  mark_ignore        a block that belongs in no question: a heading, a page
                     number, a rubric. This is the answer to a block that is in
                     no field because nothing should take one from it.
  question_add       a question, from the lines holding its stem.
  part_add           a lettered part of a question that is written already.
  question_solution  a question's worked solution, wherever it is written. It
                     answers every part of that question that has none of its own.
  split_block        cuts a block in two at a line, so that each half can be
                     named: `b7` split at 14 becomes `b7a` and `b7b`. Use it when
                     one block holds two things — a question and its first part
                     run together, two solutions with no blank line between them.
  field_replace      changes wording inside a field that is written already, for
                     what no range of the source says correctly. The wording you
                     replace has to be in the field exactly once.

Lines that are already in a field cannot be put in another one. A command naming \
them is refused, and so is one naming a block that is not there; either way you \
are told why, so read it and try something else rather than running it again.

A reviewer who has read a question against the document may send a note as well. \
Answer it with the same commands, and take what it says about the draft over what \
the checks say.\
"""

_BLOCK = {
    "type": "string",
    "description": "A block id, as the margin of the source shows it: b7, b7a.",
}

_QUESTION = {
    "type": "string",
    "description": "The question to add to, by the key of its text: q2.",
}

# Every command that fills a field takes one or the other of these, and in2lambda
# refuses both at once, so neither is required and the model is told which to use.
_WHERE = {
    "text": {
        "type": "string",
        "description": (
            "Where the text is in the frozen source: a block id, b7, or lines, "
            "s13 or s13:14. Give this or literal, not both."
        ),
    },
    "literal": {
        "type": "string",
        "maxLength": LITERAL_MAX,
        "description": (
            "A few characters, typed out, where the source spells the text "
            f"wrongly and no range of it can be quoted. At most {LITERAL_MAX} "
            "characters, and never content the document does not hold; what it "
            "writes is recorded as edited."
        ),
    },
}

_DESCRIPTIONS = {
    "mark ignore": "Mark one block of the source as belonging in no question.",
    "question add": "Add a question, from the lines holding its stem.",
    "part add": "Add a lettered part to a question that is written already.",
    "question solution": "Give a question the worked solution written for it.",
    "field replace": "Change one piece of wording inside a field already written.",
    "split block": "Cut one block in two at a line, so each half can be named.",
}

_PARAMETERS: dict[str, dict[str, Any]] = {
    "mark ignore": {
        "type": "object",
        "properties": {"block": _BLOCK},
        "required": ["block"],
    },
    "question add": {"type": "object", "properties": _WHERE, "required": []},
    "part add": {
        "type": "object",
        "properties": {"question": _QUESTION, **_WHERE},
        "required": ["question"],
    },
    "question solution": {
        "type": "object",
        "properties": {"question": _QUESTION, **_WHERE},
        "required": ["question"],
    },
    "field replace": {
        "type": "object",
        "properties": {
            "field": {
                "type": "string",
                "description": "The field to change, by its key: q1.text.",
            },
            "old": {
                "type": "string",
                "description": "The wording to replace, which is in the field once.",
            },
            "new": {"type": "string", "description": "What to put there instead."},
            "regex": {
                "type": "boolean",
                "description": "Read `old` as a regular expression rather than text.",
            },
        },
        "required": ["field", "old", "new"],
    },
    "split block": {
        "type": "object",
        "properties": {
            "block": _BLOCK,
            "at": {
                "type": "integer",
                "description": "The first line of the second half.",
            },
        },
        "required": ["block", "at"],
    },
}


@dataclass
class RoundResult:
    """What one round of fixing did, for the run's record.

    Attributes:
        number: Which round it was, from 1.
        commands: The commands the model ran, and what each was told back.
        usage: What the round's one model call cost.
        left: How many findings the checks still had once the commands had run.
    """

    number: int
    commands: list[ToolCall]
    usage: Usage
    left: int


def tools(draft: Path) -> list[Tool]:
    """The draft commands, as the tools of one model call.

    Args:
        draft: The draft file the commands change.

    Returns:
        One tool per command in `package.COMMANDS`, each running it against that
        draft. Tools are named with underscores, which is the only shape a name
        may have on the wire; the command keeps the name the log records.
    """
    return [
        Tool(
            name=name.replace(" ", "_"),
            description=_DESCRIPTIONS[name],
            parameters=_PARAMETERS[name],
            run=_runner(draft, name),
        )
        for name in package.COMMANDS
    ]


def fix_round(
    draft: Path,
    shown: str,
    report: package.Report,
    backend: Backend,
    instruction: Optional[str] = None,
) -> Reply:
    """One round: the report and the source to the model, its commands to the draft.

    Args:
        draft: The draft file.
        shown: The numbered source with block ids, as `source show` prints it,
            read again each round so that a split made last round is in it.
        report: What the checks found, which is what this round is to answer.
        backend: The backend to call, already known to be available.
        instruction: A reviewer's note about the draft, which this round is to
            answer as well as the report — and by itself where a reviewer
            rejected a question the checks had nothing to say about.

    Returns:
        The reply, whose tool calls are the commands the draft now records. The
        draft is changed by the tools as the model runs them, so a round that
        answers nothing leaves it exactly as it was.
    """
    findings = "\n".join(
        # The field and the lines are named before the sentence as well as in it.
        # Every message in2lambda writes today quotes both, but a check it grows
        # later need not, and a round has to be able to act on a finding by
        # itself: what to name in the command, and which lines it is about.
        f"- {finding.level} {finding.check} {finding.field}"
        f"{_lines(finding.ranges)}: {finding.message}"
        for finding in report.findings
    )
    prompt = f"Here is the source, one line each with its block id:\n\n{shown}\n\n"
    if findings:
        prompt += (
            "in2lambda validate reports this about the draft written from it:"
            f"\n\n{findings}\n"
        )
    else:
        prompt += "in2lambda validate has nothing to report about the draft.\n"
    if instruction is not None:
        prompt += f"\nA reviewer has read the draft and says this:\n\n{instruction}\n"
    return backend.call(SYSTEM, prompt, tools(draft))


def summary(calls: Sequence[ToolCall]) -> str:
    """The commands of one round, as the stage line names them.

    Args:
        calls: What the model ran, in order.

    Returns:
        How many there were and which, each with what it was about.
    """
    if not calls:
        return "no commands"
    named = ", ".join(
        f"{call.name.replace('_', ' ')} {_subject(call)}" for call in calls
    )
    return f"{len(calls)} command{'' if len(calls) == 1 else 's'} ({named})"


def _lines(ranges: list[list[int]]) -> str:
    """The lines a finding is about, or "" where it is about no line at all."""
    if not ranges:
        return ""
    return " lines " + ", ".join(f"{start}-{end}" for start, end in ranges)


def _runner(draft: Path, name: str) -> Callable[[dict[str, Any]], str]:
    """What one tool does when the model calls it.

    A refusal is something for the model to read and work round — the block it
    named is not there, the lines it wants are in a field already — so it comes
    back as the tool's result. Raising would end the call, and with it the round
    and every fix the model had left to make.

    A `literal` over LITERAL_MAX is refused here as well as by the schema, and
    for the same reason as any other refusal: nothing is written, nothing is
    logged, and the model is told why. Only a string is measured: a backend that
    sends `null`, or a number, is not typing anything too long, and in2lambda
    already has something to say about an argument of the wrong shape.
    """

    def run(arguments: dict[str, Any]) -> str:
        typed = arguments.get("literal")
        if isinstance(typed, str) and len(typed) > LITERAL_MAX:
            return (
                f"{name} was refused: literal is {len(typed)} characters, and at "
                f"most {LITERAL_MAX} may be typed — a field's text is copied from "
                f"the source by block or line range, and what the source does not "
                f"hold is left as a finding"
            )
        try:
            return f"{name} wrote {package.command(draft, name, arguments)}"
        except package.CommandRefused as refused:
            return f"{name} was refused: {refused}"

    return run


def _subject(call: ToolCall) -> str:
    """What one command was about, for the stage line: a block, question or field."""
    for name in ("block", "question", "field", "text", "literal"):
        if name in call.arguments:
            return str(call.arguments[name])
    return ""
