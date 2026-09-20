"""The `in2lambda-agent` command."""

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

from in2lambda_agent import corpus, pipeline
from in2lambda_agent.mathpix import MathpixError
from in2lambda_agent.model import ModelUnavailable
from in2lambda_agent.package import SpecRejected
from in2lambda_agent.settings import load_settings
from in2lambda_agent.spec import BadSpec


def build_parser() -> argparse.ArgumentParser:
    """The command line as the design spec describes it.

    Returns:
        A parser with the `run` and `corpus` subcommands.
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

    sweep = subcommands.add_parser(
        "corpus", help="Run every document of a corpus and record what each did."
    )
    sweep.add_argument("root", type=Path, help="The corpus directory.")
    sweep.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Folders under ROOT to run, defaulting to all of it.",
    )
    sweep.add_argument(
        "--suffix",
        action="append",
        dest="suffixes",
        metavar="SUFFIX",
        help="A file suffix to run, repeatable. Default: "
        f"{', '.join(corpus.DEFAULT_SUFFIXES)}.",
    )
    sweep.add_argument(
        "--replay",
        action="store_true",
        help="Run the saved specs and nothing else, making no model call.",
    )
    sweep.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="How many times the agent may try to fix validation errors.",
    )
    sweep.add_argument(
        "--results",
        type=Path,
        default=corpus.DEFAULT_RESULTS,
        help="Where to write the table, one row per document.",
    )
    sweep.add_argument(
        "--work",
        type=Path,
        default=corpus.DEFAULT_WORK_DIR,
        help="Where each set's folder is copied to be run; the corpus itself "
        "is never written to.",
    )
    sweep.add_argument(
        "--specs",
        type=Path,
        default=corpus.DEFAULT_SPEC_DIR,
        help="The tree the sets' specs are kept in, mirroring the corpus.",
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

    if args.command == "corpus":
        rows = corpus.sweep(
            args.root,
            paths=args.paths,
            # Appended to, so the default cannot be the parser's: that would be
            # the default and whatever was named.
            suffixes=args.suffixes or corpus.DEFAULT_SUFFIXES,
            results=args.results,
            work=args.work,
            specs=args.specs,
            replay=args.replay,
            rounds=args.rounds,
            settings=load_settings(),
        )
        print(f"{len(rows)} documents, written to {args.results}")
        return 0 if rows and all(row.outcome == "built" for row in rows) else 1

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
