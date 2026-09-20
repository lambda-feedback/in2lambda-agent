"""The `in2lambda-agent` command."""

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from in2lambda_agent import pipeline
from in2lambda_agent.mathpix import MathpixError
from in2lambda_agent.model import ModelUnavailable
from in2lambda_agent.package import SpecRejected
from in2lambda_agent.settings import load_settings
from in2lambda_agent.spec import BadSpec


def build_parser() -> argparse.ArgumentParser:
    """The command line as the design spec describes it.

    Returns:
        A parser with the `run` subcommand.
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
        default=1,
        help="How many times the agent may try to fix validation errors.",
    )
    run.add_argument(
        "--cache",
        type=Path,
        default=pipeline.DEFAULT_CACHE_DIR,
        help="Where the OCR of each PDF is kept.",
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
        result = pipeline.run(
            args.source,
            out_dir=args.out,
            settings=load_settings(),
            spec=args.spec,
            review=args.review,
            rounds=args.rounds,
            cache_dir=args.cache,
            fresh_ocr=args.fresh_ocr,
        )
    except (MathpixError, ModelUnavailable, BadSpec, SpecRejected) as error:
        # Missing credentials among them: the message names the variables, or
        # the login to run, or what a spec says that a spec cannot say.
        print(f"in2lambda-agent: {error}", file=sys.stderr)
        return 1

    for stage in result.stages:
        print(f"{stage.name:<9} {stage.message}")

    # A run that the checks found something in stops before the zip, and its
    # stage lines say what they found.
    return 0 if result.zip_path else 1
