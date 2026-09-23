"""The answer boxes a part gets, proposed one part at a time and scored.

A part of an imported set has no box to type an answer into until something
writes one. What kind of box it is, and what the platform marks against, is not
in the document: the sheet says "find the mass", and the platform wants
`0.106 kg` under `comparePhysicalQuantities`. So after the routes have settled a
part, one small call reads that part's statement, its options and its final
answer, and proposes its boxes (`propose`): a kind, the text before the box, and
the answer in the machine form the platform reads. A part that asks for a
discussion gets none.

The kind decides the evaluation function (`KINDS`), which is never the model's to
choose, and a proposal the platform could not mark - a kind that is not one of
the three, a multiple choice that is not one true-or-false per option - is
refused with a reason rather than written (`_refusal`). `attach` does that for
every part of a reply and hands the refusals back for the caller to flag.

`to_response_area` writes a proposal through in2lambda's `ResponseArea`.
`areas_of` reads the boxes back out of a written zip or an exported folder, in
the platform's own shape, and `score` holds one against the other: same count
per part, same kind, and the same answer once units and symbols are normalised
(`same_answer`).
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from in2lambda.api.response_area import ResponseArea

from in2lambda_agent.model import Backend, Reply

KINDS = {
    "NUMERIC_UNITS": "comparePhysicalQuantities",
    "MATH_SINGLE_LINE": "symbolicEqual",
    "MULTIPLE_CHOICE": "arrayEqual",
}
"""The three kinds of box a set holds, and what marks each. A proposal names the
kind; the evaluation function is not a model's to choose."""

SYSTEM = (
    "You give a question part the answer boxes a learning platform marks it with. "
    "Answer with JSON only, no prose, no code fence."
)


def _prompt(content: str, options: list[str], answer: str) -> str:
    return (
        "Return a JSON array, one object per answer box the part needs, in the order a "
        'student fills them: {"kind": str, "pre_text": str, "answer": str}. '
        "kind is NUMERIC_UNITS for a number with units, MATH_SINGLE_LINE for an "
        "expression in symbols, MULTIPLE_CHOICE where the part lists options. "
        "pre_text is the short label shown before the box, as LaTeX between dollars - "
        '"$m=$" - or an empty string. answer is the correct answer written as the '
        'platform reads it: "0.106 kg" for a number with its units, '
        '"(pi/6)*rho*U**2*R**2" for an expression, with ** for powers and the names of '
        "Greek letters spelled out. For MULTIPLE_CHOICE, answer is instead a list of "
        "true or false, one for each option in the order given, true for the correct "
        "ones. Return one object per quantity the part asks for, and an empty array for "
        "a part that asks for a discussion, a sketch or a proof rather than an answer to "
        "type.\n\n"
        f"PART:\n\n{content}\n\n"
        + (
            "OPTIONS:\n\n" + "\n".join(f"{i}. {one}" for i, one in enumerate(options, 1)) + "\n\n"
            if options
            else ""
        )
        + f"FINAL ANSWER:\n\n{answer}"
    )


@dataclass
class Attached:
    """What a reply's parts were given.

    Attributes:
        proposals: The boxes by part key, `q1.p1`, for a part that got any.
        refused: Why a part's proposal was refused, by the same key, which the
            caller flags for a person.
        tokens: What the calls read and wrote.
    """

    proposals: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    refused: dict[str, list[str]] = field(default_factory=dict)
    tokens: int = 0


def _parse(text: str) -> tuple[list, Optional[str]]:
    """The JSON list a call was asked for, or why what came back is not one.

    The same shape as `routes._json`, except that a refused proposal is a flag on
    one part rather than a raise that loses the set, so this answers with the
    reason instead.
    """
    stripped = re.sub(r"^```(json)?\s*|\s*```$", "", text.strip())
    try:
        answered = json.loads(stripped)
    except json.JSONDecodeError as error:
        return [], f"the reply is not JSON: {error}"
    if not isinstance(answered, list):
        said = " ".join(stripped.split())[:60]
        return [], f"a reply is a JSON list of response areas, which {said!r} is not"
    return answered, None


def _refusal(proposal: Any, options: list[str]) -> Optional[str]:
    """Why a proposal cannot be written, or None where it can.

    A box the platform cannot mark is worse than no box: the student types an
    answer into it and is marked wrong whatever they type.
    """
    if not isinstance(proposal, dict):
        return f"a response area is an object, which {proposal!r} is not"
    kind = proposal.get("kind")
    if kind not in KINDS:
        return f"{kind!r} is not one of {', '.join(KINDS)}"
    answer = proposal.get("answer")
    if kind == "MULTIPLE_CHOICE":
        if (
            not isinstance(answer, list)
            or len(answer) != len(options)
            or not all(isinstance(one, bool) for one in answer)
            or not any(answer)
        ):
            return (
                f"a multiple-choice answer is true or false for each of the "
                f"{len(options)} options, with a true among them, which {answer!r} is not"
            )
    elif not isinstance(answer, str) or not answer.strip():
        return f"an answer is text the platform marks, which {answer!r} is not"
    return None


def propose(
    content: str, options: list[str], answer: str, backend: Backend
) -> tuple[list[dict[str, Any]], list[str], Reply]:
    """One call: the boxes proposed for one part, what was refused, and the call.

    Args:
        content: The part's statement.
        options: The choices of a multiple-choice part; empty otherwise.
        answer: The part's final answer as the solutions document gives it.
        backend: What to call.

    Returns:
        The proposals that can be written, as `{"kind", "pre_text", "answer"}`;
        the reason each refused proposal was refused, which is a flag for the
        caller; and the call, for its usage.
    """
    reply = backend.call(SYSTEM, _prompt(content, options, answer))
    answered, problem = _parse(reply.text)
    if problem is not None:
        return [], [problem], reply
    proposals, refused = [], []
    for one in answered:
        reason = _refusal(one, options)
        if reason is not None:
            refused.append(reason)
            continue
        proposals.append(
            {
                "kind": one["kind"],
                "pre_text": str(one.get("pre_text") or ""),
                "answer": one["answer"],
            }
        )
    return proposals, refused, reply


def attach(reply: list[dict[str, Any]], backend: Backend) -> Attached:
    """A call for every part of a reply with something to answer.

    A part with no statement, no options and no answer is a part the document
    left empty, and nothing can be proposed for it, so it is not asked about.
    """
    attached = Attached()
    for i, question in enumerate(reply, 1):
        for j, part in enumerate(question.get("parts", []), 1):
            content = part.get("content") or ""
            options = list(part.get("options") or [])
            answer = part.get("answer") or ""
            if not (content.strip() or options or answer.strip()):
                continue
            proposals, refused, call = propose(content, options, answer, backend)
            attached.tokens += call.usage.input_tokens + call.usage.output_tokens
            key = f"q{i}.p{j}"
            if proposals:
                attached.proposals[key] = proposals
            if refused:
                attached.refused[key] = refused
    return attached


def to_response_area(proposal: dict[str, Any], options: list[str]) -> ResponseArea:
    """A proposal as in2lambda's `ResponseArea`, which writes the platform's JSON."""
    kind = proposal["kind"]
    area = ResponseArea(
        response_type=kind,
        answer=proposal["answer"],
        evaluation_function=KINDS[kind],
        pre_text=proposal.get("pre_text", ""),
    )
    if kind == "MULTIPLE_CHOICE":
        area.config = {
            "single": sum(bool(one) for one in area.answer) == 1,
            "options": list(options),
            "randomise": False,
        }
    return area


# --- what a set says ---------------------------------------------------------------------


def _questions(built: Path) -> list[dict[str, Any]]:
    """Every question of a written zip or an exported folder, as the JSON has it."""
    built = Path(built)
    if built.is_dir():
        texts = [one.read_text(encoding="utf-8") for one in sorted(built.glob("question_*.json"))]
    else:
        with zipfile.ZipFile(built) as archive:
            texts = [
                archive.read(name).decode("utf-8")
                for name in sorted(archive.namelist())
                if name.startswith("question_")
            ]
    return sorted((json.loads(one) for one in texts), key=lambda q: q.get("orderNumber", 0))


def areas_of(built: Path) -> dict[str, list[tuple[str, Any]]]:
    """The boxes of a set by part key, as the platform reads them.

    The zip and the export are read as JSON rather than through in2lambda's own
    model, so what is scored is what Lambda Feedback will load.

    Args:
        built: A zip the agent wrote, or a `set_*` folder the platform exported.

    Returns:
        `{"q1.p1": [(kind, answer)]}`, each part's boxes in the order the
        platform numbers them. A part with no box is left out.
    """
    found: dict[str, list[tuple[str, Any]]] = {}
    for i, question in enumerate(_questions(built), 1):
        parts = sorted(question.get("parts", []), key=lambda p: p.get("orderNumber", 0))
        for j, part in enumerate(parts, 1):
            boxes = sorted(
                part.get("responseAreas", []), key=lambda a: a.get("orderNumber", 0)
            )
            if boxes:
                found[f"q{i}.p{j}"] = [
                    (
                        box["response"]["responseInput"]["responseType"],
                        box["response"]["responseInput"]["answer"],
                    )
                    for box in boxes
                ]
    return found


_QUANTITY = re.compile(r"\s*([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*(.*)", re.S)


def _quantity(text: str) -> Optional[tuple[float, str]]:
    """A number with units as its value and its unit, or None where it has no number."""
    match = _QUANTITY.fullmatch(text)
    if match is None:
        return None
    # `m^(-3)` and `m^-3` are the same unit; the platform writes either.
    unit = re.sub(r"\^\(([^)]*)\)", r"^\1", match.group(2))
    return float(match.group(1)), "".join(unit.split())


# A bracket round a product is only spelling where taking it away leaves the
# same expression: `rho*(U**2)*R` is `rho*U**2*R`. After a division or under a
# power it is not - `2/(a*b)` is not `2/a*b`, and `(U*R)**2` is not `U*R**2` -
# so a bracket the character before or after binds tighter than `*` stays.
_BRACKETED = re.compile(r"(?<!/)(?<!\*\*)\(([A-Za-z0-9_.*]+)\)(?!\*\*)")


def _symbols(text: str) -> str:
    """An expression as compared: `^` is `**`, and a bracket round one factor is not one."""
    folded = "".join(text.split()).replace("^", "**")
    while True:
        once = _BRACKETED.sub(r"\1", folded)
        if once == folded:
            return folded
        folded = once


def same_answer(kind: str, made: Any, wanted: Any) -> bool:
    """Whether two answers of one kind are the same answer.

    A number is the same within half a percent, so that a rounded answer counts;
    its unit is compared as written, so kilograms are not grams. An expression is
    compared with its spacing and its brackets round single factors dropped. A
    multiple choice is the same when the same options are true.
    """
    if kind == "MULTIPLE_CHOICE":
        return isinstance(made, list) and isinstance(wanted, list) and list(made) == list(wanted)
    if not isinstance(made, str) or not isinstance(wanted, str):
        return False
    if kind == "NUMERIC_UNITS":
        ours, theirs = _quantity(made), _quantity(wanted)
        if ours is None or theirs is None:
            return False
        return ours[1] == theirs[1] and abs(ours[0] - theirs[0]) <= 0.005 * abs(theirs[0])
    return _symbols(made) == _symbols(wanted)


@dataclass
class Score:
    """How many of an export's boxes the agent made, and which it did not.

    Attributes:
        matches: The export's boxes the agent made the same way.
        total: How many the export holds, which is what a run is scored out of.
        misses: One line per box that differs, naming the part, the box's place
            in it, and what each side says.
    """

    matches: int = 0
    total: int = 0
    misses: list[str] = field(default_factory=list)


def _said(box: Optional[tuple[str, Any]]) -> str:
    return "nothing" if box is None else f"{box[0]} {box[1]!r}"


def _order(key: str) -> tuple[int, int]:
    numbers = re.findall(r"\d+", key)
    return int(numbers[0]), int(numbers[1])


def score(
    made: dict[str, list[tuple[str, Any]]], wanted: dict[str, list[tuple[str, Any]]]
) -> Score:
    """The agent's boxes against an export's, part by part and place by place.

    A box matches when the part and the place in it are the same, the kind is the
    same, and the answer is the same once normalised. A part given more boxes
    than the export gives it misses on the extra ones, which is what stops a run
    scoring by writing a box everywhere.
    """
    scored = Score(total=sum(len(boxes) for boxes in wanted.values()))
    for key in sorted(set(made) | set(wanted), key=_order):
        ours, theirs = made.get(key, []), wanted.get(key, [])
        for n in range(max(len(ours), len(theirs))):
            box = ours[n] if n < len(ours) else None
            want = theirs[n] if n < len(theirs) else None
            if box is not None and want is not None and box[0] == want[0] and same_answer(want[0], box[1], want[1]):
                scored.matches += 1
            else:
                scored.misses.append(
                    f"{key}[{n + 1}]: wanted {_said(want)}, made {_said(box)}"
                )
    return scored
