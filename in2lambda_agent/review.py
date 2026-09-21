"""The review the design spec puts between a clean validate and the build.

A run in `sample` or `per-question` mode stops once the checks are quiet: it
renders the questions a reviewer is to see, writes this record beside the OCR
cache, and prints them. The reviewer answers from the command line — approve,
reject with a note, or edit a field — and each of those commands reads the
record back, so the two halves of the run are one run with a file between them.

The record holds everything the second half needs and the first half already
knew: where the draft is, what the run had cost so far, and what `record_run`
is to be told when the last question is approved. There is no reviewer state
anywhere else, and a run that is not in review builds without writing one.
"""

import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from in2lambda_agent.fix import RoundResult
from in2lambda_agent.model import ToolCall, Usage
from in2lambda_agent.package import Coverage, QuestionInfo
from in2lambda_agent.spec import Second, SpecTry

RECORD = "review.json"
"""What the pending review is called, in the run's cache directory."""


class ReviewError(ValueError):
    """A review command names something that is not waiting to be reviewed."""


@dataclass
class Question:
    """One question put to the reviewer, and what they said about it.

    Attributes:
        key: The question, as the draft's fields key it: `q2`.
        pdf: The PDF it was rendered to, or None where nothing rendered it.
        lines: The ranges of the frozen source its fields were copied from.
        status: `pending`, `approved` or `rejected`.
        note: What a rejection said, which is what the fixing round is asked.
    """

    key: str
    pdf: Optional[str] = None
    lines: list[list[int]] = field(default_factory=list)
    status: str = "pending"
    note: Optional[str] = None


@dataclass
class Review:
    """A review waiting to be answered, and the run it stopped in the middle of.

    Attributes:
        mode: `sample` or `per-question`.
        count: How many questions sample mode was to show.
        source: The file the run converted, as the record names it.
        spec: The set's spec file, whose folder the run record is in.
        out_dir: Where the zip goes once every question is approved.
        limit: The run's round limit, which a rejection's fixing gets again.
        draft: The draft file the run left.
        frozen: The source the draft was frozen from, for reading against.
        reused: Whether the spec was the saved one, for the run record.
        coverage: What the spec run made of the source, for the run record.
        questions: Each question put to the reviewer, in order.
        errors: What the checks last found, empty where they found nothing.
            A reviewer's rounds and edits reach the draft between one command
            and the next, so this is how the listing says that the draft as it
            stands cannot be built.
        rejections: Every rejection, in the order they were made.
        edits: Every field the reviewer changed by hand, and who they were.
        usage: What the run's model calls have cost so far.
        tries: What each spec the run wrote covered and cost, for the run record.
        second: The other document of the set the specs were run over, or the
            one the run passed over, for the run record.
        rounds: What each fixing round has done so far, the reviewer's among them.
    """

    mode: str
    count: int
    source: str
    spec: str
    out_dir: str
    limit: int
    draft: str
    frozen: str
    reused: bool
    coverage: Coverage
    questions: list[Question] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    tries: list[SpecTry] = field(default_factory=list)
    second: Optional[Second] = None
    rounds: list[RoundResult] = field(default_factory=list)

    @property
    def done(self) -> bool:
        """Whether every question put to the reviewer has been approved."""
        return all(question.status == "approved" for question in self.questions)

    def question(self, key: str) -> Question:
        """The question a review command names.

        Args:
            key: What the reviewer called it.

        Returns:
            That question of this review.

        Raises:
            ReviewError: no question of that name is under review, and the
                message names the ones that are.
        """
        for question in self.questions:
            if question.key == key:
                return question
        listed = ", ".join(one.key for one in self.questions)
        raise ReviewError(f"{key} is not under review. This review has {listed}.")

    def listing(self) -> str:
        """The questions, one line each, as the reviewer reads them."""
        return "\n".join(
            f"  {question.key} {question.status}: "
            f"{question.pdf or 'not rendered'}, "
            f"{self.frozen} lines {_lines(question.lines)}"
            + (f" — {question.note}" if question.note else "")
            for question in self.questions
        )

    def to_json(self) -> dict[str, Any]:
        """What the run's record line says about the review.

        Returns:
            The mode, every question with its verdict and note, and what the
            reviewer's rejections and edits came to.
        """
        return {
            "mode": self.mode,
            "questions": [
                {"key": one.key, "status": one.status, "note": one.note}
                for one in self.questions
            ],
            "rejections": self.rejections,
            "edits": self.edits,
        }

    def save(self, path: Path) -> None:
        """Writes the record where a review command will read it.

        Args:
            path: The file to write, whose directory is made if it is not there.
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        state = {
            **asdict(self),
            "tries": [_try_json(one) for one in self.tries],
            # `asdict` writes the copy the specs were run over as a Path, which
            # json refuses, and the copy is of no use to the command that
            # answers the review.
            "second": None if self.second is None else self.second.to_json(),
            "rounds": [_round_json(one) for one in self.rounds],
        }
        Path(path).write_text(json.dumps(state, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Review":
        """Reads back a review a run left waiting.

        Args:
            path: The file `save` wrote.

        Returns:
            The review, with what the run had spent on it.

        Raises:
            ReviewError: there is no review waiting there, or what is there is
                not one: a record cut short by a run that died writing it, or
                one an older version of the agent left behind.
        """
        path = Path(path)
        if not path.is_file():
            raise ReviewError(
                f"No review is waiting in {path}. A run with --review sample "
                "or per-question writes one there when the checks come clean."
            )
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            coverage = state["coverage"]
            return cls(
                **{
                    **state,
                    "coverage": Coverage(
                        layout=coverage["layout"],
                        blocks=coverage["blocks"],
                        # JSON has no integer keys, and the layers are numbers
                        # everywhere else they are read.
                        fields={int(k): v for k, v in coverage["fields"].items()},
                        ignored=coverage["ignored"],
                        unassigned=coverage["unassigned"],
                    ),
                    "questions": [Question(**one) for one in state["questions"]],
                    "usage": Usage(**state["usage"]),
                    "tries": [_try_from(one) for one in state["tries"]],
                    "second": (
                        None
                        if state["second"] is None
                        else Second.from_json(state["second"])
                    ),
                    "rounds": [_round_from(one) for one in state["rounds"]],
                }
            )
        except (ValueError, TypeError, KeyError) as error:
            raise ReviewError(
                f"{path} is not a review this run can read ({error}). Delete it "
                "and run again with --review sample or per-question."
            ) from None


def choose(
    infos: dict[str, QuestionInfo],
    mode: str,
    count: int,
    rng: Optional[random.Random] = None,
) -> list[str]:
    """Which questions the reviewer is shown.

    Args:
        infos: Every question of the draft, as `package.questions` reads them.
        mode: `sample` or `per-question`.
        count: How many a sample is at most.
        rng: What picks the rest of a sample, so that a test can fix it.

    Returns:
        The keys to review, in the order they are put to the reviewer: in a
        sample the questions something past the spec wrote come first, since
        those are the ones a reviewer is there for.
    """
    if mode == "per-question":
        return list(infos)
    risky = [key for key, info in infos.items() if info.layer >= 3]
    rest = [key for key, info in infos.items() if info.layer < 3]
    chosen = risky[:count]
    if len(chosen) < count:
        picked = (rng or random.Random()).sample(
            rest, min(count - len(chosen), len(rest))
        )
        chosen += [key for key in rest if key in set(picked)]
    return chosen


def _lines(ranges: Sequence[Sequence[int]]) -> str:
    """The lines of a question, as the listing names them."""
    if not ranges:
        return "none"
    return ", ".join(f"{start}-{end}" for start, end in ranges)


def _try_json(one: SpecTry) -> dict[str, Any]:
    """One spec the run wrote, as the record keeps it between the two commands."""
    return {
        "number": one.number,
        "usage": asdict(one.usage),
        "unassigned": one.unassigned,
        "errors": one.errors,
        "dropped": one.dropped,
        "second": one.second,
        "chosen": one.chosen,
    }


def _try_from(saved: dict[str, Any]) -> SpecTry:
    """One spec the run wrote, back out of the record for the run's own record."""
    return SpecTry(
        number=saved["number"],
        usage=Usage(**saved["usage"]),
        unassigned=saved["unassigned"],
        errors=saved["errors"],
        dropped=saved["dropped"],
        second=saved["second"],
        chosen=saved["chosen"],
    )


def _round_json(one: RoundResult) -> dict[str, Any]:
    """One fixing round, as the record keeps it between the two commands."""
    return {
        "number": one.number,
        "commands": [asdict(call) for call in one.commands],
        "usage": asdict(one.usage),
        "left": one.left,
    }


def _round_from(saved: dict[str, Any]) -> RoundResult:
    """One fixing round, back out of the record for the run's own record."""
    return RoundResult(
        number=saved["number"],
        commands=[ToolCall(**call) for call in saved["commands"]],
        usage=Usage(**saved["usage"]),
        left=saved["left"],
    )
