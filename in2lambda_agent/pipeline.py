"""The run: source in, Lambda Feedback zip out.

Only the layout and build stages do anything yet. The rest wait on in2lambda
commands being built (`source add`, `spec run`, `validate`) or on the agent's own
model stages; each of those says what it waits for and lets the run carry on, so
the end-to-end path works today with no model call.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from in2lambda.api.set import Set
from in2lambda.main import runner

from in2lambda_agent.settings import Settings

# Used until `in2lambda spec run` exists and a spec can choose for itself.
DEFAULT_LAYOUT = "PartsSepSol"

REVIEW_MODES = ("none", "sample", "per-question")


@dataclass
class StageResult:
    """What one stage did, as one line of output."""

    name: str
    message: str


@dataclass
class RunResult:
    """What a run did, in order, and the zip it wrote."""

    stages: list[StageResult] = field(default_factory=list)
    zip_path: Optional[Path] = None


def run(
    source: Path,
    *,
    out_dir: Path,
    settings: Settings,
    spec: Optional[Path] = None,
    review: str = "none",
    rounds: int = 1,
) -> RunResult:
    """Drives in2lambda over one source file.

    Args:
        source: The question file to convert.
        out_dir: Where in2lambda writes the set's JSON folder and zip.
        settings: The environment the run has available.
        spec: An optional spec file, for when `in2lambda spec run` exists.
        review: One of REVIEW_MODES.
        rounds: The round limit, N in the design spec.

    Returns:
        Each stage's line and the zip that was written.
    """
    result = RunResult()

    result.stages.append(StageResult("freeze", "waiting for in2lambda source add"))

    spec_note = f"ignoring {spec}, " if spec else ""
    result.stages.append(
        StageResult(
            "spec",
            f"waiting for in2lambda spec run; {spec_note}"
            f"using the {DEFAULT_LAYOUT} layout",
        )
    )

    question_set: Set = runner(str(source), DEFAULT_LAYOUT)
    result.stages.append(
        StageResult(
            "layout", f"{DEFAULT_LAYOUT}: {len(question_set.questions)} questions"
        )
    )

    result.stages.append(StageResult("validate", "waiting for in2lambda validate"))

    result.stages.append(
        StageResult(
            "review",
            f"waiting for the model stages (mode {review}, round limit {rounds})",
        )
    )

    # Moves to `in2lambda build` once that command exists. in2lambda writes into
    # the directory but does not create it, and --out defaults to ./out.
    out_dir.mkdir(parents=True, exist_ok=True)
    question_set.to_json(str(out_dir))
    result.zip_path = out_dir / f"{question_set._name}.zip"
    result.stages.append(StageResult("build", str(result.zip_path)))

    return result
