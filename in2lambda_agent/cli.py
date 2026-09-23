"""The `in2lambda-agent` command."""

import argparse
import getpass
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from in2lambda_agent import compare, corpus, gate, pair, pipeline, routes
from in2lambda_agent.mathpix import MathpixClient, MathpixError
from in2lambda_agent.model import ModelError, ModelUnavailable, choose_backend
from in2lambda_agent.ocr import ocr_pdf
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


def try_count(given: str) -> int:
    """How many specs the agent may write, which is at least one.

    Args:
        given: What was typed after `--tries`.

    Returns:
        The count.

    Raises:
        ArgumentTypeError: it is below one. A run that may write no spec has
            none to run, and a set with no saved spec has nothing to reuse.
    """
    count = int(given)
    if count < 1:
        raise argparse.ArgumentTypeError(
            f"a run writes at least one spec, not {count}"
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


def _conversion_options(parser: argparse.ArgumentParser) -> None:
    """Adds the options of a conversion, which `convert` and `run` both take.

    Args:
        parser: The subcommand's parser.
    """
    parser.add_argument(
        "--solutions",
        type=Path,
        default=None,
        help="The solutions document. Default: the file beside the document "
        "whose name is the document's with `_solutions` after it.",
    )
    filter_ = parser.add_mutually_exclusive_group()
    filter_.add_argument(
        "--filter",
        type=Path,
        default=None,
        help="The Lua filter route B runs. Without one, route A converts the "
        "document alone and no field is compared.",
    )
    filter_.add_argument(
        "--write-filter",
        action="store_true",
        help="Write route B's filter for this document with a model call, and "
        "keep it at `OUT/filter.lua`.",
    )


def build_parser() -> argparse.ArgumentParser:
    """The command line as the design spec describes it.

    Returns:
        A parser with the `convert`, `run`, `review`, `corpus`, `gate`,
        `compare` and `ui` subcommands.
    """
    parser = argparse.ArgumentParser(
        prog="in2lambda-agent",
        description="Turns a source file into a Lambda Feedback set.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    convert = subcommands.add_parser(
        "convert", help="Convert DOCUMENT into a set through both routes."
    )
    convert.add_argument(
        "document",
        type=Path,
        help="The question file to convert: a PDF, markdown, tex or docx file.",
    )
    _conversion_options(convert)
    convert.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="Where to write the set's JSON folder and zip.",
    )
    convert.add_argument(
        "--cache",
        type=Path,
        default=pipeline.DEFAULT_CACHE_DIR,
        help="Where the OCR of each PDF is kept.",
    )

    run = subcommands.add_parser(
        "run", help="Convert SOURCE into a set; `convert` under the default route."
    )
    run.add_argument(
        "source",
        type=Path,
        help="The question file to convert. A solutions file beside it, named "
        "after it, is frozen with it.",
    )
    run.add_argument(
        "--route",
        choices=("direct", "spec"),
        default="direct",
        help="Which route converts the document: `direct` is the `convert` "
        "command, and `spec` writes a spec of selectors and runs it.",
    )
    _conversion_options(run)
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
        "--tries",
        type=try_count,
        default=3,
        help="How many specs the agent may write before keeping the best.",
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
        help="Run the saved specs and the documents' saved logs, making no "
        "model call.",
    )
    sweep.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="How many times the agent may try to fix validation errors.",
    )
    sweep.add_argument(
        "--tries",
        type=try_count,
        default=3,
        help="How many specs the agent may write before keeping the best.",
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
        help="The tree the sets' specs are kept in, mirroring the corpus, with "
        "each document's log of its fixing rounds beside its set's spec.",
    )
    sweep.add_argument(
        "--cache",
        type=Path,
        default=pipeline.DEFAULT_CACHE_DIR,
        help="Where the OCR of each PDF is kept, so a sweep pointed at a cache "
        "another run filled converts nothing.",
    )

    check = subcommands.add_parser(
        "gate", help="Replay the corpus the baseline names and check it against it."
    )
    check.add_argument("baseline", type=Path, help="The committed baseline file.")
    check.add_argument(
        "--record",
        action="store_true",
        help="Write this run's counts to the baseline instead of checking them.",
    )
    check.add_argument(
        "--cache",
        type=Path,
        default=gate.DEFAULT_CACHE_DIR,
        help="Where the OCR of each PDF is kept, shared between worktrees so "
        "that a conversion is paid for once.",
    )
    check.add_argument(
        "--work",
        type=Path,
        default=None,
        help="Where the folders are copied to be run, under the system temp "
        "directory by default so the check writes nothing where it was run.",
    )

    against = subcommands.add_parser(
        "compare", help="Check a PDF's OCR against the pages it came from."
    )
    against.add_argument("pdf", type=Path, help="The PDF to convert and check.")
    against.add_argument(
        "--cache",
        type=Path,
        default=pipeline.DEFAULT_CACHE_DIR,
        help="Where the OCR of each PDF is kept.",
    )
    against.add_argument(
        "--fresh-ocr",
        action="store_true",
        help="Convert the PDF again even if it is already cached.",
    )

    page = subcommands.add_parser(
        "ui", help="Serve the page for trying the agent, on this machine only."
    )
    page.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="The directory the source picker lists. Default: ExampleContents "
        "where there is one, and the current directory where there is not.",
    )
    page.add_argument("--port", type=int, default=8765, help="The port to listen on.")
    page.add_argument(
        "--no-open",
        action="store_true",
        help="Print the address and do not open the page in a browser.",
    )
    return parser


# Each route's own options. `run` takes both sets, because argparse cannot know
# the route until it has parsed the line, so the run refuses an option of the
# route it is not taking rather than reading it and throwing it away.
_SPEC_ROUTE_OPTIONS = {
    "spec": "--spec",
    "review": "--review",
    "rounds": "--rounds",
    "tries": "--tries",
    "sample": "--sample",
    "fresh_ocr": "--fresh-ocr",
}
_DIRECT_ROUTE_OPTIONS = {
    "solutions": "--solutions",
    "filter": "--filter",
    "write_filter": "--write-filter",
}


def misplaced_option(args: argparse.Namespace) -> Optional[str]:
    """What is wrong where `run` was given an option of the other route.

    Args:
        args: The parsed arguments of `run`.

    Returns:
        What to print, naming the option and the route it belongs to, or None
        where every option given belongs to the route the run is taking.
    """
    if args.route == "direct":
        options, route, fix = _SPEC_ROUTE_OPTIONS, "spec", "add --route spec"
    else:
        options, route, fix = _DIRECT_ROUTE_OPTIONS, "direct", "drop --route spec"
    # An option counts as given where it is not the parser's default, which is
    # read back from the parser rather than repeated here.
    defaults = build_parser().parse_args(["run", str(args.source)])
    for dest, name in options.items():
        if getattr(args, dest) != getattr(defaults, dest):
            return f"{name} is an option of the {route} route; {fix}"
    return None


def convert_command(args: argparse.Namespace) -> int:
    """Converts one document through both routes and prints the report.

    Args:
        args: The parsed arguments of `convert`, or of `run` under the direct
            route, which takes the same options.

    Returns:
        0 where the zip was written, and 1 where a conversion step failed. A
        flagged field does not change the code: the flags are what a person
        reads after the build, and no check blocks the write.
    """
    # `run SOURCE` converts the same document, under the other name.
    document = Path(getattr(args, "document", None) or args.source)
    solutions = args.solutions or pair.solutions_beside(document)
    if solutions is None and pair.questions_stem(document) is None:
        # A pair whose two documents do not share a stem, which is what the platform
        # writes where it puts a timestamp in each name, is not a sheet with no
        # solutions; a run that said nothing about it would read as one.
        print(f"solutions none found beside {document}; pass --solutions FILE")
    out_dir = Path(args.out)
    settings = load_settings()
    backend = choose_backend(settings)
    lua = args.filter
    try:
        if args.write_filter:
            out_dir.mkdir(parents=True, exist_ok=True)
            lua = out_dir / "filter.lua"
            lua.write_text(
                routes.write_filter(document, solutions, backend)[0], encoding="utf-8"
            )
        result = routes.convert(
            document,
            solutions,
            out_dir=out_dir,
            cache_dir=args.cache,
            backend=backend,
            settings=settings,
            lua=lua,
            name=document.stem,
        )
    except (MathpixError, ModelUnavailable, ModelError) as error:
        print(f"in2lambda-agent: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        # pandoc read the document, or ran the filter, and refused. Its own
        # message names the line; the exit status alone names nothing.
        stderr = (error.stderr or b"").decode("utf-8", "replace").strip()
        print(f"in2lambda-agent: {stderr or error}", file=sys.stderr)
        return 1

    for line in result.report():
        print(line)
    return 0 if result.zip_path else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Runs the command.

    Args:
        argv: The arguments, defaulting to the process's own.

    Returns:
        The exit code.
    """
    args = build_parser().parse_args(argv)

    if args.command == "run":
        wrong = misplaced_option(args)
        if wrong:
            print(f"in2lambda-agent: {wrong}", file=sys.stderr)
            return 1

    if args.command == "convert" or (args.command == "run" and args.route == "direct"):
        return convert_command(args)

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
            tries=args.tries,
            cache=args.cache,
            settings=load_settings(),
        )
        print(f"{len(rows)} documents, written to {args.results}")
        # A file that is not a document is not a document that failed, so a
        # figure's tex source among the rows does not make the sweep one.
        succeeded = {"built", "skipped"}
        return 0 if rows and all(row.outcome in succeeded for row in rows) else 1

    if args.command == "gate":
        baseline = gate.read_baseline(args.baseline)
        # The directory is printed and is not deleted, so that the drafts of a
        # folder that failed can be read after the run.
        work = args.work or Path(tempfile.mkdtemp(prefix="in2lambda-agent-gate-"))
        print(f"work      {work}")
        report = gate.run(
            baseline,
            record=args.record,
            cache=args.cache,
            work=work,
            settings=load_settings(),
        )
        for name, summary in report.folders.items():
            print(gate.folder_line(name, summary))
        if args.record:
            gate.write_baseline(baseline, args.baseline)
            print(f"recorded  {args.baseline}")
            return 0
        return 1 if report.failed else 0

    if args.command == "compare":
        settings = load_settings()
        try:
            ocr = ocr_pdf(
                args.pdf,
                cache_dir=Path(args.cache).resolve(),
                client=MathpixClient.from_settings(settings),
                fresh=args.fresh_ocr,
            )
            result = compare.compare(
                args.pdf,
                ocr.markdown.read_text(encoding="utf-8"),
                choose_backend(settings),
            )
        except (MathpixError, ModelUnavailable, compare.RenderFailed) as error:
            print(f"in2lambda-agent: {error}", file=sys.stderr)
            return 1

        print(f"ocr       {'fresh pass' if ocr.fresh else 'cached'} {ocr.markdown}")
        if result.raw and not result.findings:
            # No findings is either a page the markdown matches or a reply the
            # parser could not read, and from here the two look the same. So
            # print what was said rather than leave a reply that listed
            # differences showing as "0 findings" and nothing else.
            print(f"reply     {result.raw}")
        for finding in result.findings:
            print(
                f"p{finding.page}: {finding.ocr} → {finding.page_shows}  "
                f"{finding.note}"
            )
        tokens = result.usage.input_tokens + result.usage.output_tokens
        print(
            f"{len(result.findings)} findings over {result.pages} pages, "
            f"{tokens} tokens, {result.usage.seconds:.1f}s"
        )
        # An experiment reports what it found; whether a finding should stop a
        # run is what the write-up decides, so nothing here exits 1 over one.
        return 0

    if args.command == "ui":
        try:
            from in2lambda_agent.ui import server
        except ImportError:
            # Starlette and uvicorn are the `ui` extra, which a plain install
            # leaves out.
            print(
                "in2lambda-agent: the ui command needs Starlette and uvicorn. "
                "Install them with `poetry install --extras ui`.",
                file=sys.stderr,
            )
            return 1
        server.serve(args.corpus, args.port, open_browser=not args.no_open)
        return 0

    try:
        if args.command == "run":
            result = pipeline.run(
                args.source,
                out_dir=args.out,
                settings=load_settings(),
                spec=args.spec,
                review=args.review,
                rounds=args.rounds,
                tries=args.tries,
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
        ModelError,
        BadSpec,
        SpecRejected,
        ReviewError,
        CommandRefused,
    ) as error:
        # Missing credentials among them: the message names the variables, or
        # the login to run, or what the provider said stopped a call, or what a
        # spec says that a spec cannot say, or the question a review command
        # names that is not under review.
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
