"""The `in2lambda-agent` command."""

import argparse
import getpass
import sys
from pathlib import Path
from typing import Optional, Sequence

from in2lambda_agent import pipeline
from in2lambda_agent.mathpix import MathpixError
from in2lambda_agent.model import ModelUnavailable
from in2lambda_agent.package import CommandRefused, SpecRejected
from in2lambda_agent.review import ReviewError
from in2lambda_agent.settings import load_settings
from in2lambda_agent.spec import BadSpec


def sample_count(given: str) -> int:
    """How many questions a sample shows, which is at least one.

    Args:
        given: What was typed after `--sample`.

    Returns:
        The count.

    Raises:
        ArgumentTypeError: it is below one. A review of no questions is not a
            review: it would stop the run, write a record nothing can answer,
            and never build.
    """
    count = int(given)
    if count < 1:
        raise argparse.ArgumentTypeError(
            f"a sample shows at least one question, not {count} — "
            "--review none is how a set is built without a review"
        )
    return count


def reviewer_name(given: Optional[str]) -> str:
    """Who the draft's log records an edit as being by.

    Asked only on the `review` branch, and never while the arguments are being
    parsed: a container with no passwd entry for its user — `--user 1001` with
    no LOGNAME set, which this repo's own image is run as — has no login name
    to give, and a run that does not touch `--by` should not care.

    Args:
        given: What was typed after `--by`, or None where nothing was.

    Returns:
        That name, or the login name, or `reviewer` where there is none.
    """
    if given is not None:
        return given
    try:
        return getpass.getuser()
    except (OSError, KeyError):
        # 3.13 and after raise OSError where there is no name to be had;
        # earlier versions raise KeyError.
        return "reviewer"


def build_parser() -> argparse.ArgumentParser:
    """The command line as the design spec describes it.

    Returns:
        A parser with the `run` and `review` subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="in2lambda-agent",
        description="Turns a source file into a Lambda Feedback set.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="Convert SOURCE into a set.")
    run.add_argument("source", type=Path, help="The question file to convert.")
    run.add_argument(
        "--spec",
        type=Path,
        help="The set's spec file: read if present, written if not.",
    )
    run.add_argument(
        "--review",
        choices=pipeline.REVIEW_MODES,
        default="none",
        help="How much of the set a reviewer sees before it is built.",
    )
    run.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="How many times the agent may try to fix validation errors.",
    )
    run.add_argument(
        "--sample",
        type=sample_count,
        default=3,
        help="How many questions a review in sample mode shows.",
    )
    run.add_argument(
        "--cache",
        type=Path,
        default=pipeline.DEFAULT_CACHE_DIR,
        help="Where the OCR of each PDF, and a waiting review, are kept.",
    )
    run.add_argument(
        "--fresh-ocr",
        action="store_true",
        help="Convert a PDF again even if it is already cached.",
    )
    run.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="Where to write the set's JSON folder and zip.",
    )

    review = subcommands.add_parser(
        "review", help="Answer the review a run stopped for."
    )
    verdicts = review.add_subparsers(dest="verdict", required=True)
    approve = verdicts.add_parser("approve", help="Accept one question as it is.")
    approve.add_argument("question", help="The question, by its key: q2.")
    reject = verdicts.add_parser(
        "reject", help="Send one question back with a note to fix it by."
    )
    reject.add_argument("question", help="The question, by its key: q2.")
    reject.add_argument(
        "--note", required=True, help="What is wrong with it, for the agent to fix."
    )
    edit = verdicts.add_parser("edit", help="Change the wording of one field.")
    edit.add_argument("field", help="The field to change, by its key: q1.text.")
    edit.add_argument("old", help="The wording to replace, which is in it once.")
    edit.add_argument("new", help="What to put there instead.")
    edit.add_argument(
        "--by",
        default=None,
        help=(
            "Who the reviewer is, as the draft's log records the edit. "
            "The login name by default, or `reviewer` where there is none."
        ),
    )
    for verdict in (approve, reject, edit):
        verdict.add_argument(
            "--cache",
            type=Path,
            default=pipeline.DEFAULT_CACHE_DIR,
            help="Where the run left the review.",
        )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs the command.

    Args:
        argv: The arguments, defaulting to the process's own.

    Returns:
        The exit code.
    """
    args = build_parser().parse_args(argv)

    try:
        if args.command == "run":
            result = pipeline.run(
                args.source,
                out_dir=args.out,
                settings=load_settings(),
                spec=args.spec,
                review=args.review,
                rounds=args.rounds,
                sample=args.sample,
                cache_dir=args.cache,
                fresh_ocr=args.fresh_ocr,
            )
        else:
            result = pipeline.resume(
                args.cache,
                verdict=args.verdict,
                settings=load_settings(),
                key=getattr(args, "question", None),
                note=getattr(args, "note", None),
                field=getattr(args, "field", None),
                old=getattr(args, "old", None),
                new=getattr(args, "new", None),
                by=reviewer_name(getattr(args, "by", None)),
            )
    except (
        MathpixError,
        ModelUnavailable,
        BadSpec,
        SpecRejected,
        ReviewError,
        CommandRefused,
    ) as error:
        # Missing credentials among them: the message names the variables, or
        # the login to run, or what a spec says that a spec cannot say, or the
        # question a review command names that is not under review.
        print(f"in2lambda-agent: {error}", file=sys.stderr)
        return 1

    for stage in result.stages:
        print(f"{stage.name:<9} {stage.message}")

    # A run that the checks found something in stops before the zip, and its
    # stage lines say what they found. A run with a question still to answer
    # has not failed: it is halfway through, and the review commands finish it.
    # A review nothing is left to answer and no zip came out of is a failure
    # like any other build that did not happen.
    waiting = result.review is not None and not result.review.done
    return 0 if result.zip_path or waiting else 1
