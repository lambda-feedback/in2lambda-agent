"""The `in2lambda-agent` command."""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from in2lambda_agent import compare, gate, ocr, pair, routes, sweep, targets
from in2lambda_agent.mathpix import MathpixClient, MathpixError
from in2lambda_agent.model import ModelError, ModelUnavailable, choose_backend
from in2lambda_agent.settings import load_settings


def build_parser() -> argparse.ArgumentParser:
    """The command line as the design spec describes it.

    Returns:
        A parser with the `convert`, `corpus`, `targets`, `gate`, `compare` and
        `ui` subcommands.
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
    convert.add_argument(
        "--solutions",
        type=Path,
        default=None,
        help="The solutions document. Default: the file beside the document "
        "whose name is the document's with `_solutions` after it.",
    )
    filter_ = convert.add_mutually_exclusive_group()
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
    convert.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="Where to write the set's JSON folder and zip.",
    )
    convert.add_argument(
        "--cache",
        type=Path,
        default=ocr.DEFAULT_CACHE_DIR,
        help="Where the OCR of each PDF is kept.",
    )

    corpus_command = subcommands.add_parser(
        "corpus", help="Convert every set of a corpus and record what each sheet did."
    )
    corpus_command.add_argument("root", type=Path, help="The corpus directory.")
    corpus_command.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Folders under ROOT to run, defaulting to all of it.",
    )
    corpus_command.add_argument(
        "--suffix",
        action="append",
        dest="suffixes",
        metavar="SUFFIX",
        help="A file suffix to run, repeatable. Default: "
        f"{', '.join(sweep.DEFAULT_SUFFIXES)}.",
    )
    corpus_command.add_argument(
        "--results",
        type=Path,
        default=sweep.DEFAULT_RESULTS,
        help="Where to write the table, one row per sheet.",
    )
    corpus_command.add_argument(
        "--work",
        type=Path,
        default=sweep.DEFAULT_WORK_DIR,
        help="Where each set's filter and each sheet's zip are written; the "
        "corpus itself is never written to.",
    )
    corpus_command.add_argument(
        "--cache",
        type=Path,
        default=ocr.DEFAULT_CACHE_DIR,
        help="Where the OCR of each PDF is kept, so a sweep pointed at a cache "
        "another run filled converts nothing.",
    )

    against_export = subcommands.add_parser(
        "targets",
        help="Convert each target under ROOT and compare it with its export.",
    )
    against_export.add_argument(
        "root", type=Path, help="The directory the targets are under."
    )
    against_export.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Folders under ROOT to run, defaulting to all of them.",
    )
    against_export.add_argument(
        "--filters",
        type=Path,
        default=targets.DEFAULT_FILTER_DIR,
        help="The tree each target's filter and saved reply are kept in, "
        "mirroring the targets, with the fields the maintainer accepts a "
        f"difference in written in {targets.DIFFERS_NAME} beside them.",
    )
    against_export.add_argument(
        "--fresh",
        action="store_true",
        help="Read each document again rather than converting the saved reply, "
        "which is how a target is given a new reading of its pages.",
    )
    against_export.add_argument(
        "--out",
        type=Path,
        default=Path("out"),
        help="Where to write each target's set, under the target's own name.",
    )
    against_export.add_argument(
        "--cache",
        type=Path,
        default=ocr.DEFAULT_CACHE_DIR,
        help="Where the OCR of each PDF is kept.",
    )

    check = subcommands.add_parser(
        "gate",
        help="Replay every target under ROOT against its export, with no "
        "call that reads a document.",
    )
    check.add_argument("root", type=Path, help="The directory the targets are under.")
    check.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Folders under ROOT to run, defaulting to all of them.",
    )
    check.add_argument(
        "--filters",
        type=Path,
        required=True,
        help="The tree each target's saved filter and reply are read from. A "
        "target with neither saved is an error rather than a model call.",
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
        help="Where each target's set is written, under the system temp "
        "directory by default so the check writes nothing where it was run.",
    )

    against = subcommands.add_parser(
        "compare", help="Check a PDF's OCR against the pages it came from."
    )
    against.add_argument("pdf", type=Path, help="The PDF to convert and check.")
    against.add_argument(
        "--cache",
        type=Path,
        default=ocr.DEFAULT_CACHE_DIR,
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


def target_summary(results: Sequence[targets.Result]) -> int:
    """Prints the last line of a run over targets and returns its exit code.

    Args:
        results: One result per target, as `targets.run` and `gate.run` both
            return them.

    Returns:
        0 where at least one target ran, none failed and none differs from its
        export in a field the maintainer has not accepted. A root with no
        target under it is a mistyped path rather than a clean run, so an empty
        run fails like a new difference does.
    """
    new = sum(len(one.new) for one in results)
    failed = [one for one in results if one.error]
    print(
        f"{len(results)} target{'' if len(results) == 1 else 's'}, "
        f"{new} new difference{'' if new == 1 else 's'}"
        + (f", {len(failed)} did not run" if failed else "")
    )
    return 0 if results and not new and not failed else 1


def convert_command(args: argparse.Namespace) -> int:
    """Converts one document through both routes and prints the report.

    Args:
        args: The parsed arguments of `convert`.

    Returns:
        0 where the zip was written, and 1 where a conversion step failed. A
        flagged field does not change the code: the flags are what a person
        reads after the build, and no check blocks the write.
    """
    document = Path(args.document)
    if args.solutions is not None:
        # The user named the two documents, so the folder is not asked.
        solutions = args.solutions
    else:
        # Either half of a pair may be named, so the pairing goes both ways: name the
        # solutions document and the questions document beside it is what converts, and
        # the set is named after it. A solutions document with none beside it comes back
        # as the document itself, and converts on its own.
        document, solutions = pair.of(document)
    if solutions is not None:
        print(f"solutions {solutions}")
    elif pair.questions_stem(document) is None:
        # A pair whose two names share no stem, which is what the platform writes where
        # it puts the time of the download in each name, is a sheet whose solutions the
        # run did not find. A run that said nothing would read as a sheet with none,
        # and the set it writes holds an empty answer for every question.
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
    except (MathpixError, ModelUnavailable, ModelError, OSError, routes.BadReply) as error:
        # A document that is not there raises an OSError here, because the conversion
        # reads the file itself and in2lambda never sees the name. A reply that is not
        # a JSON list of questions raises BadReply.
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

    if args.command == "convert":
        return convert_command(args)

    if args.command == "corpus":
        rows = sweep.sweep(
            args.root,
            paths=args.paths,
            # Appended to, so the default cannot be the parser's: that would be
            # the default and whatever was named.
            suffixes=args.suffixes or sweep.DEFAULT_SUFFIXES,
            results=args.results,
            work=args.work,
            cache=args.cache,
            settings=load_settings(),
        )
        print(f"{len(rows)} sheets, written to {args.results}")
        # A sheet route B failed on built its set from route A, so the sheets
        # that built no set are what the exit code reports.
        return 0 if rows and all(row.built for row in rows) else 1

    if args.command == "targets":
        return target_summary(
            targets.run(
                args.root,
                paths=args.paths,
                filters=args.filters,
                out_dir=args.out,
                cache_dir=args.cache,
                settings=load_settings(),
                fresh=args.fresh,
            )
        )

    if args.command == "gate":
        # The directory is printed and is not deleted, so that the sets of a
        # target that differs can be read after the run.
        work = args.work or Path(tempfile.mkdtemp(prefix="in2lambda-agent-gate-"))
        print(f"work      {work}")
        return target_summary(
            gate.run(
                args.root,
                paths=args.paths,
                filters=args.filters,
                work=work,
                cache=args.cache,
                settings=load_settings(),
            )
        )

    if args.command == "compare":
        settings = load_settings()
        try:
            converted = ocr.ocr_pdf(
                args.pdf,
                cache_dir=Path(args.cache).resolve(),
                client=MathpixClient.from_settings(settings),
                fresh=args.fresh_ocr,
            )
            result = compare.compare(
                args.pdf,
                converted.markdown.read_text(encoding="utf-8"),
                choose_backend(settings),
            )
        except (MathpixError, ModelUnavailable, compare.RenderFailed) as error:
            print(f"in2lambda-agent: {error}", file=sys.stderr)
            return 1

        print(
            f"ocr       {'fresh pass' if converted.fresh else 'cached'} "
            f"{converted.markdown}"
        )
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

