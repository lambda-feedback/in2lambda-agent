"""The `in2lambda-agent` command."""

import argparse
from pathlib import Path
from typing import Optional, Sequence

from in2lambda_agent import pipeline
from in2lambda_agent.settings import load_settings


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
    run.add_argument("--spec", type=Path, help="A spec to run over SOURCE.")
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

    result = pipeline.run(
        args.source,
        out_dir=args.out,
        settings=load_settings(),
        spec=args.spec,
        review=args.review,
        rounds=args.rounds,
    )
    for stage in result.stages:
        print(f"{stage.name:<9} {stage.message}")

    return 0
