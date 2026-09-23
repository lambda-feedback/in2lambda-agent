"""The command line the design spec describes."""

import argparse
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeBackend, FakeMathpix

from in2lambda_agent import cli, compare, gate, routes, sweep, targets
from in2lambda_agent.cli import build_parser, main
from in2lambda_agent.model import ModelUnavailable, Usage
from in2lambda_agent.settings import Settings


# --- convert ---------------------------------------------------------------


def test_convert_defaults():
    args = build_parser().parse_args(["convert", "sheet.pdf"])

    assert args.command == "convert"
    assert args.document == Path("sheet.pdf")
    assert args.solutions is None
    assert args.filter is None
    assert args.write_filter is False
    assert args.out == Path("out")
    assert args.cache == Path(".in2lambda-agent")


def test_convert_every_option():
    args = build_parser().parse_args(
        [
            "convert",
            "sheet.pdf",
            "--solutions",
            "sheet_solutions.pdf",
            "--filter",
            "set.lua",
            "--out",
            "somewhere",
            "--cache",
            "cached",
        ]
    )

    assert args.document == Path("sheet.pdf")
    assert args.solutions == Path("sheet_solutions.pdf")
    assert args.filter == Path("set.lua")
    assert args.out == Path("somewhere")
    assert args.cache == Path("cached")

    written = build_parser().parse_args(["convert", "sheet.pdf", "--write-filter"])
    assert written.write_filter is True
    assert written.filter is None


def test_a_filter_and_a_written_filter_together_are_refused():
    # One run has one route B filter: either the file named or the file written.
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["convert", "sheet.md", "--filter", "set.lua", "--write-filter"]
        )


def converted(zip_path=None, **counts):
    """What a monkeypatched `routes.convert` answers with."""
    return routes.Converted(
        set=None, zip_path=zip_path, flags=counts.pop("flags", []), reply=[], **counts
    )


def records(given, result):
    """A stand-in for `routes.convert` that records what it was given."""

    def record(document, solutions=None, **passed):
        given.update(passed, document=document, solutions=solutions)
        return result

    return record


@pytest.fixture
def backend(monkeypatch):
    """A backend the convert branch takes without reading the environment."""
    fake = FakeBackend()
    monkeypatch.setattr(cli, "choose_backend", lambda settings: fake)
    return fake


def test_convert_hands_the_document_and_the_options_to_the_route(
    tmp_path, backend, monkeypatch, capsys
):
    given = {}
    zip_path = tmp_path / "out" / "sheet.zip"
    monkeypatch.setattr(
        routes,
        "convert",
        records(given, converted(zip_path, fields=10, agreed=9, defaulted=1)),
    )

    code = main(
        [
            "convert",
            str(tmp_path / "sheet.md"),
            "--solutions",
            str(tmp_path / "sol.md"),
            "--filter",
            str(tmp_path / "set.lua"),
            "--out",
            str(tmp_path / "out"),
            "--cache",
            str(tmp_path / "cache"),
        ]
    )

    assert code == 0
    assert given["document"] == tmp_path / "sheet.md"
    assert given["solutions"] == tmp_path / "sol.md"
    assert given["lua"] == tmp_path / "set.lua"
    assert given["out_dir"] == tmp_path / "out"
    assert given["cache_dir"] == tmp_path / "cache"
    assert given["backend"] is backend
    assert given["name"] == "sheet"
    out = capsys.readouterr().out
    assert "10 fields, agreed 9, defaulted 1, adjudicated 0, flagged 0" in out
    assert f"build     {zip_path}" in out


def test_convert_takes_the_solutions_document_beside_the_document(
    tmp_path, backend, monkeypatch
):
    (tmp_path / "sheet.md").write_text("x")
    (tmp_path / "sheet_solutions.md").write_text("x")
    given = {}
    monkeypatch.setattr(routes, "convert", records(given, converted(tmp_path / "s.zip")))

    assert main(["convert", str(tmp_path / "sheet.md"), "--out", str(tmp_path)]) == 0
    assert given["solutions"] == tmp_path / "sheet_solutions.md"


def test_convert_named_by_its_solutions_document_converts_the_pair(
    tmp_path, backend, monkeypatch, capsys
):
    # Naming either half of a pair converts the pair, and the set is named after
    # the questions document.
    (tmp_path / "Worksheet_1.md").write_text("x")
    (tmp_path / "Worksheet_1_solutions.md").write_text("x")
    given = {}
    monkeypatch.setattr(routes, "convert", records(given, converted(tmp_path / "s.zip")))

    code = main(
        ["convert", str(tmp_path / "Worksheet_1_solutions.md"), "--out", str(tmp_path)]
    )

    assert code == 0
    assert given["document"] == tmp_path / "Worksheet_1.md"
    assert given["solutions"] == tmp_path / "Worksheet_1_solutions.md"
    assert given["name"] == "Worksheet_1"
    assert f"solutions {tmp_path / 'Worksheet_1_solutions.md'}" in capsys.readouterr().out


def test_convert_with_solutions_named_does_not_pair(tmp_path, backend, monkeypatch):
    # `--solutions` means what the user says, not what the folder holds.
    (tmp_path / "Worksheet_1.md").write_text("x")
    (tmp_path / "Worksheet_1_solutions.md").write_text("x")
    given = {}
    monkeypatch.setattr(routes, "convert", records(given, converted(tmp_path / "s.zip")))

    code = main(
        [
            "convert",
            str(tmp_path / "Worksheet_1_solutions.md"),
            "--solutions",
            str(tmp_path / "other.md"),
            "--out",
            str(tmp_path),
        ]
    )

    assert code == 0
    assert given["document"] == tmp_path / "Worksheet_1_solutions.md"
    assert given["solutions"] == tmp_path / "other.md"


@pytest.mark.parametrize("named", [False, True])
def test_convert_names_the_solutions_document_it_read(
    named, tmp_path, backend, monkeypatch, capsys
):
    # The document `--solutions` names and the document found beside the questions are
    # reported the same way: the reader sees which file the answers came from.
    solutions = tmp_path / "sheet_solutions.md"
    solutions.write_text("x")
    (tmp_path / "sheet.md").write_text("x")
    monkeypatch.setattr(routes, "convert", records({}, converted(tmp_path / "s.zip")))

    code = main(
        [
            "convert",
            str(tmp_path / "sheet.md"),
            "--out",
            str(tmp_path),
            *(["--solutions", str(solutions)] if named else []),
        ]
    )

    assert code == 0
    assert f"solutions {solutions}" in capsys.readouterr().out


def test_convert_says_where_it_found_no_solutions_document(
    tmp_path, backend, monkeypatch, capsys
):
    # A pair whose two names share no stem is a sheet whose solutions the run did not
    # find. A run that said nothing would read as a sheet with none.
    monkeypatch.setattr(routes, "convert", records({}, converted(tmp_path / "s.zip")))

    assert main(["convert", str(tmp_path / "sheet.pdf"), "--out", str(tmp_path)]) == 0
    assert (
        f"solutions none found beside {tmp_path / 'sheet.pdf'}; pass --solutions FILE"
        in capsys.readouterr().out
    )


def test_convert_says_nothing_of_the_solutions_of_a_solutions_document(
    tmp_path, backend, monkeypatch, capsys
):
    # A solutions document converted on its own is what the reader asked for, and there
    # is no file for `--solutions` to name.
    monkeypatch.setattr(routes, "convert", records({}, converted(tmp_path / "s.zip")))

    code = main(["convert", str(tmp_path / "sheet_solutions.md"), "--out", str(tmp_path)])

    assert code == 0
    assert "solutions" not in capsys.readouterr().out


@pytest.mark.parametrize("missing", ["document", "solutions"])
def test_convert_names_a_file_that_is_not_there(missing, tmp_path, backend, capsys):
    # This route reads each file itself, so in2lambda never sees the name and never
    # complains about it.
    if missing == "solutions":
        (tmp_path / "sheet.md").write_text("# Question 1\n")

    code = main(
        [
            "convert",
            str(tmp_path / "sheet.md"),
            "--solutions",
            str(tmp_path / "sol.md"),
            "--out",
            str(tmp_path),
        ]
    )
    printed = capsys.readouterr()

    assert code == 1
    assert printed.err.startswith("in2lambda-agent: ")
    assert ("sol.md" if missing == "solutions" else "sheet.md") in printed.err


def test_the_written_filter_is_kept_in_the_out_directory(
    tmp_path, backend, monkeypatch
):
    given = {}
    monkeypatch.setattr(routes, "convert", records(given, converted(tmp_path / "s.zip")))
    monkeypatch.setattr(
        routes, "write_filter", lambda document, solutions, backend, **_: ("-- lua", None)
    )

    code = main(
        [
            "convert",
            str(tmp_path / "sheet.md"),
            "--write-filter",
            "--out",
            str(tmp_path / "out"),
        ]
    )

    assert code == 0
    assert (tmp_path / "out" / "filter.lua").read_text() == "-- lua"
    assert given["lua"] == tmp_path / "out" / "filter.lua"


def test_a_flagged_field_does_not_stop_the_build(tmp_path, backend, monkeypatch, capsys):
    # The zip is written whatever the flags say; a person reads the flags after it.
    flag = routes.Flag("q2.p1.worked_solution", "a", "", routes.STRAY_MINUS)
    monkeypatch.setattr(
        routes, "convert", records({}, converted(tmp_path / "s.zip", flags=[flag]))
    )

    code = main(["convert", str(tmp_path / "sheet.md"), "--out", str(tmp_path)])

    assert code == 0
    assert "flag      q2.p1.worked_solution:" in capsys.readouterr().out


def test_convert_without_a_backend_says_what_to_set(tmp_path, backend, monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise ModelUnavailable("set ANTHROPIC_API_KEY, or run `claude login`")

    monkeypatch.setattr(routes, "convert", unavailable)

    code = main(["convert", str(tmp_path / "sheet.md"), "--out", str(tmp_path)])
    printed = capsys.readouterr()

    assert code == 1
    assert "claude login" in printed.err
    # Nothing was converted, so there is no report.
    assert "fields" not in printed.out and "build" not in printed.out


def test_convert_reports_what_pandoc_said(tmp_path, backend, monkeypatch, capsys):
    def refuses(*args, **kwargs):
        raise subprocess.CalledProcessError(
            43, "pandoc", stderr=b"Error at line 3 column 1\n"
        )

    monkeypatch.setattr(routes, "convert", refuses)

    code = main(["convert", str(tmp_path / "sheet.tex"), "--out", str(tmp_path)])

    assert code == 1
    assert "Error at line 3 column 1" in capsys.readouterr().err


@pytest.mark.parametrize(
    "answer", ["I cannot convert this sheet.", '{"title": "A ball", "parts": []}']
)
def test_convert_reports_a_reply_that_is_not_a_list_of_questions(
    answer, tmp_path, monkeypatch, capsys
):
    # Route A is asked for a JSON list. A model that answers with a sentence, and an
    # answer cut short at the output-token limit, both arrive as text no step below
    # route A reads. The run names the fault, as it does for pandoc and for Mathpix.
    (tmp_path / "sheet.md").write_text("# Question 1\n\nFind the height.\n")
    monkeypatch.setattr(cli, "choose_backend", lambda settings: FakeBackend(answer))

    code = main(["convert", str(tmp_path / "sheet.md"), "--out", str(tmp_path / "out")])
    printed = capsys.readouterr()

    assert code == 1
    assert printed.err.startswith("in2lambda-agent: ")
    assert "fields" not in printed.out and "build" not in printed.out


def test_corpus_defaults():
    args = build_parser().parse_args(["corpus", "ExampleContents"])

    assert args.command == "corpus"
    assert args.root == Path("ExampleContents")
    assert args.paths == []
    # Resolved where it is used: appending to a list the parser holds would run
    # the default suffixes as well as the named ones.
    assert args.suffixes is None
    assert args.results == Path("results.csv")
    assert args.work == Path(".in2lambda-agent/corpus")
    assert args.cache == Path(".in2lambda-agent")


def test_corpus_every_option():
    args = build_parser().parse_args(
        [
            "corpus",
            "ExampleContents",
            "Aero",
            "MATE40002",
            "--suffix",
            "tex",
            "--suffix",
            "md",
            "--results",
            "sweep.csv",
            "--work",
            "working",
            "--cache",
            "cached",
        ]
    )

    assert args.paths == [Path("Aero"), Path("MATE40002")]
    assert args.suffixes == ["tex", "md"]
    assert args.results == Path("sweep.csv")
    assert args.work == Path("working")
    assert args.cache == Path("cached")


def test_targets_defaults():
    args = build_parser().parse_args(["targets", "ExampleContents/targets"])

    assert args.command == "targets"
    assert args.root == Path("ExampleContents/targets")
    assert args.paths == []
    assert args.filters == Path("targets")
    assert args.out == Path("out")
    assert args.cache == Path(".in2lambda-agent")
    assert args.fresh is False


def test_targets_every_option():
    args = build_parser().parse_args(
        [
            "targets",
            "ExampleContents/targets",
            "EART40013_Mathematical_Methods_II",
            "--filters",
            "saved",
            "--out",
            "built",
            "--cache",
            "cached",
            "--fresh",
        ]
    )

    assert args.paths == [Path("EART40013_Mathematical_Methods_II")]
    assert args.filters == Path("saved")
    assert args.out == Path("built")
    assert args.cache == Path("cached")
    assert args.fresh is True


def test_a_targets_run_prints_its_report_and_passes_with_no_new_difference(
    monkeypatch, capsys
):
    given = {}

    def record(root, **kwargs):
        given.update(root=root, **kwargs)
        return [
            targets.Result(
                name="ME2", differences=["Question 1 \"\": a"],
                known=["Question 1 \"\": a"], flags=2,
            )
        ]

    monkeypatch.setattr(targets, "run", record)

    code = main(["targets", "ExampleContents/targets", "--cache", "cached"])

    assert code == 0
    assert given["root"] == Path("ExampleContents/targets")
    assert given["cache_dir"] == Path("cached")
    assert given["fresh"] is False
    # `run` printed the target's own report as it went; this is the total.
    assert capsys.readouterr().out == "1 target, 0 new differences\n"


@pytest.mark.parametrize(
    "result, total",
    [
        (
            targets.Result(name="ME2", differences=["a"], new=["a"]),
            "1 target, 1 new difference\n",
        ),
        (
            targets.Result(name="ME2", error="set MATHPIX_APP_ID"),
            "1 target, 0 new differences, 1 did not run\n",
        ),
    ],
)
def test_a_new_difference_or_a_target_that_failed_fails_the_run(
    monkeypatch, result, total, capsys
):
    monkeypatch.setattr(targets, "run", lambda *args, **kwargs: [result])

    assert main(["targets", "ExampleContents/targets"]) == 1
    assert capsys.readouterr().out == total


def test_a_run_over_no_target_at_all_fails(monkeypatch):
    # A root with no `set_*` folder under it is a mistyped path, not a clean run.
    monkeypatch.setattr(targets, "run", lambda *args, **kwargs: [])

    assert main(["targets", "ExampleContents/targets"]) == 1


def test_the_corpus_cache_is_handed_to_the_sweep(monkeypatch):
    given = {}

    def record(*args, **kwargs):
        given.update(kwargs)
        return []

    monkeypatch.setattr(sweep, "sweep", record)

    main(["corpus", "ExampleContents", "--cache", "cached"])

    assert given["cache"] == Path("cached")


def test_gate_defaults():
    args = build_parser().parse_args(
        ["gate", "ci-corpus/targets", "--filters", "ci-corpus/filters"]
    )

    assert args.command == "gate"
    assert args.root == Path("ci-corpus/targets")
    assert args.paths == []
    assert args.filters == Path("ci-corpus/filters")
    assert args.cache == Path.home() / ".cache" / "in2lambda-agent"
    # Chosen when the command runs, so that two runs do not share a directory.
    assert args.work is None


def test_a_gate_with_no_filter_tree_is_refused():
    # There is nothing saved to replay without one, and a gate that wrote what
    # is missing would be making the calls it exists not to make.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["gate", "ci-corpus/targets"])


def test_gate_every_option():
    args = build_parser().parse_args(
        [
            "gate",
            "ci-corpus/targets",
            "sheet",
            "--filters",
            "saved",
            "--cache",
            "cached",
            "--work",
            "working",
        ]
    )

    assert args.paths == [Path("sheet")]
    assert args.filters == Path("saved")
    assert args.cache == Path("cached")
    assert args.work == Path("working")


def test_a_gate_with_no_new_difference_passes_and_says_where_it_worked(
    monkeypatch, capsys
):
    given = {}

    def record(root, **kwargs):
        given.update(root=root, **kwargs)
        return [targets.Result(name="sheet")]

    monkeypatch.setattr(gate, "run", record)

    code = main(["gate", "ci-corpus/targets", "--filters", "ci-corpus/filters"])
    printed = capsys.readouterr().out

    assert code == 0
    assert given["root"] == Path("ci-corpus/targets")
    assert given["filters"] == Path("ci-corpus/filters")
    assert given["cache"] == gate.DEFAULT_CACHE_DIR
    # The directory is printed and not deleted, so the sets can be read after.
    assert printed.startswith("work      ")
    assert printed.endswith("1 target, 0 new differences\n")


def test_a_target_the_gate_could_not_replay_fails_the_run(monkeypatch, capsys):
    monkeypatch.setattr(
        gate,
        "run",
        lambda *args, **kwargs: [
            targets.Result(name="sheet", error="reply.json is not saved")
        ],
    )

    assert main(["gate", "ci-corpus/targets", "--filters", "saved"]) == 1
    assert "1 did not run" in capsys.readouterr().out


def test_compare_defaults():
    args = build_parser().parse_args(["compare", "sheet.pdf"])

    assert args.command == "compare"
    assert args.pdf == Path("sheet.pdf")
    assert args.cache == Path(".in2lambda-agent")
    assert args.fresh_ocr is False


def test_compare_prints_a_line_per_finding_and_converts_once(
    tmp_path, pdf, monkeypatch, capsys
):
    mathpix = FakeMathpix(markdown="# Sheet\n\n$\\mathrm{m/s$\n")
    monkeypatch.setattr(
        cli, "MathpixClient", SimpleNamespace(from_settings=lambda settings: mathpix)
    )
    seen = []

    def fake_compare(pdf, markdown, backend):
        seen.append(markdown)
        return compare.Comparison(
            findings=[compare.Finding("1", "\\mathrm{m/s", "\\mathrm{m/s}", "brace")],
            usage=Usage(input_tokens=4200, output_tokens=90, seconds=3.0),
            pages=2,
        )

    monkeypatch.setattr(compare, "compare", fake_compare)
    argv = ["compare", str(pdf), "--cache", str(tmp_path / "cache")]

    assert cli.main(argv) == 0
    assert cli.main(argv) == 0

    out = capsys.readouterr().out
    assert "p1: \\mathrm{m/s → \\mathrm{m/s}  brace" in out
    assert "1 findings over 2 pages, 4290 tokens, 3.0s" in out
    # The second run read the cached conversion: one Mathpix call, two compares.
    assert len(mathpix.calls) == 1
    assert seen == ["# Sheet\n\n$\\mathrm{m/s$\n"] * 2


def test_compare_prints_the_reply_when_nothing_parsed_out_of_it(
    tmp_path, pdf, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli,
        "MathpixClient",
        SimpleNamespace(from_settings=lambda settings: FakeMathpix()),
    )
    monkeypatch.setattr(
        compare,
        "compare",
        lambda pdf, markdown, backend: compare.Comparison(
            raw="I could not read the third page.", pages=1
        ),
    )

    assert cli.main(["compare", str(pdf), "--cache", str(tmp_path / "cache")]) == 0

    out = capsys.readouterr().out
    assert "reply     I could not read the third page." in out
    assert "0 findings over 1 pages" in out


def test_compare_without_a_backend_says_what_to_set(tmp_path, pdf, monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "MathpixClient",
        SimpleNamespace(from_settings=lambda settings: FakeMathpix()),
    )
    monkeypatch.setattr(cli, "load_settings", lambda: Settings())
    # No key and no Claude Code on the path: nothing can make the call.
    monkeypatch.setattr("shutil.which", lambda name: None)

    code = cli.main(["compare", str(pdf), "--cache", str(tmp_path / "cache")])

    assert code == 1
    assert "claude login" in capsys.readouterr().err


def test_a_sweep_is_a_sweep_that_worked_where_every_sheet_built_a_set(monkeypatch):
    # A sheet route B failed on still built its set from route A, so the exit
    # code reports the sheets that built nothing and nothing else.
    rows = [
        sweep.Row(set="tex", sheet="tex/sheet.tex"),
        sweep.Row(
            set="tex",
            sheet="tex/sheet-2.tex",
            reason="route B failed: Error running filter",
        ),
    ]
    monkeypatch.setattr(sweep, "sweep", lambda *args, **kwargs: rows)

    assert main(["corpus", "ExampleContents"]) == 0

    rows.append(
        sweep.Row(set="tex", sheet="tex/sheet-3.tex", reason="no set: ModelError")
    )
    assert main(["corpus", "ExampleContents"]) == 1


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_the_commands_are_the_ones_the_agent_has():
    # The spec route's `run` and `review` are gone, and the parser is where a
    # user finds that out.
    action = next(
        one
        for one in build_parser()._actions
        if isinstance(one, argparse._SubParsersAction)
    )

    assert list(action.choices) == [
        "convert",
        "corpus",
        "targets",
        "gate",
        "compare",
        "ui",
    ]


@pytest.mark.parametrize("gone", ["run", "review"])
def test_the_spec_routes_commands_are_refused(gone):
    with pytest.raises(SystemExit):
        build_parser().parse_args([gone, "sheet.md"])


def test_ui_defaults_and_every_option():
    defaults = build_parser().parse_args(["ui"])
    given = build_parser().parse_args(
        ["ui", "--corpus", "sheets", "--port", "9000", "--no-open"]
    )

    assert (defaults.corpus, defaults.port, defaults.no_open) == (None, 8765, False)
    assert (given.corpus, given.port, given.no_open) == (Path("sheets"), 9000, True)


def test_ui_serves_the_page_with_what_was_asked_for(monkeypatch):
    served = []
    server = SimpleNamespace(
        serve=lambda corpus, port, open_browser: served.append(
            (corpus, port, open_browser)
        )
    )
    # Both of them: `from in2lambda_agent.ui import server` reads the attribute
    # of the package where the ui extra is installed, and sys.modules where it
    # is not.
    monkeypatch.setattr("in2lambda_agent.ui.server", server, raising=False)
    monkeypatch.setitem(sys.modules, "in2lambda_agent.ui.server", server)

    code = main(["ui", "--corpus", "sheets", "--port", "9000", "--no-open"])

    assert code == 0
    assert served == [(Path("sheets"), 9000, False)]


def test_ui_without_the_extra_says_what_to_install(monkeypatch, capsys):
    # A None entry in sys.modules raises ImportError, which is what an import
    # of Starlette raises where the ui extra is not installed.
    monkeypatch.delattr("in2lambda_agent.ui.server", raising=False)
    monkeypatch.setitem(sys.modules, "in2lambda_agent.ui.server", None)

    code = main(["ui"])
    printed = capsys.readouterr()

    assert code == 1
    assert "poetry install --extras ui" in printed.err
    assert printed.out == ""
