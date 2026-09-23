"""The command line the design spec describes."""

import getpass
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeBackend, FakeMathpix

from in2lambda_agent import cli, compare, gate, pipeline, routes, sweep
from in2lambda_agent.cli import build_parser, main, reviewer_name
from in2lambda_agent.model import ModelUnavailable, Usage
from in2lambda_agent.settings import Settings


def test_defaults():
    args = build_parser().parse_args(["run", "sheet.md"])

    assert args.source == Path("sheet.md")
    assert args.spec is None
    assert args.review == "none"
    assert args.rounds == 3
    assert args.out == Path("out")
    assert args.cache == Path(".in2lambda-agent")
    assert args.fresh_ocr is False
    assert args.sample == 3
    assert args.tries == 3


def test_every_option():
    args = build_parser().parse_args(
        [
            "run",
            "sheet.md",
            "--spec",
            "sheet.yaml",
            "--review",
            "per-question",
            "--rounds",
            "5",
            "--tries",
            "2",
            "--sample",
            "2",
            "--cache",
            "cached",
            "--fresh-ocr",
            "--out",
            "somewhere",
        ]
    )

    assert args.spec == Path("sheet.yaml")
    assert args.review == "per-question"
    assert args.rounds == 5
    assert args.out == Path("somewhere")
    assert args.cache == Path("cached")
    assert args.fresh_ocr is True
    assert args.sample == 2
    assert args.tries == 2


@pytest.mark.parametrize("mode", ["none", "sample", "per-question"])
def test_review_modes(mode):
    assert build_parser().parse_args(["run", "s.md", "--review", mode]).review == mode


def test_an_unknown_review_mode_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "s.md", "--review", "everything"])


@pytest.mark.parametrize("count", ["0", "-1"])
def test_a_sample_of_no_questions_is_refused(count, capsys):
    # It would stop the run, write a record with nothing in it to approve, and
    # never build: there would be no way on from there but to delete the record.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "sheet.md", "--sample", count])

    assert "at least one question" in capsys.readouterr().err


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


@pytest.mark.parametrize("command", ["convert", "run"])
def test_a_filter_and_a_written_filter_together_are_refused(command):
    # One run has one route B filter: either the file named or the file written.
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            [command, "sheet.md", "--filter", "set.lua", "--write-filter"]
        )


def test_run_converts_through_both_routes_unless_the_spec_route_is_asked_for():
    assert build_parser().parse_args(["run", "sheet.md"]).route == "direct"
    assert build_parser().parse_args(["run", "s.md", "--route", "spec"]).route == "spec"
    assert build_parser().parse_args(["run", "s.md", "--solutions", "s2.md"]).solutions


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


@pytest.mark.parametrize("command", ["convert", "run"])
def test_convert_named_by_its_solutions_document_converts_the_pair(
    command, tmp_path, backend, monkeypatch, capsys
):
    # Naming either half of a pair converts the pair, and the set is named after the
    # questions document, as the spec route has always done.
    (tmp_path / "Worksheet_1.md").write_text("x")
    (tmp_path / "Worksheet_1_solutions.md").write_text("x")
    given = {}
    monkeypatch.setattr(routes, "convert", records(given, converted(tmp_path / "s.zip")))

    code = main(
        [command, str(tmp_path / "Worksheet_1_solutions.md"), "--out", str(tmp_path)]
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


def test_run_without_a_route_converts_the_document(tmp_path, backend, monkeypatch):
    given = {}
    monkeypatch.setattr(routes, "convert", records(given, converted(tmp_path / "s.zip")))
    monkeypatch.setattr(
        pipeline, "run", lambda *a, **k: pytest.fail("the spec route ran")
    )

    assert main(["run", str(tmp_path / "sheet.md"), "--out", str(tmp_path)]) == 0
    assert given["document"] == tmp_path / "sheet.md"


def test_the_written_filter_is_kept_in_the_out_directory(
    tmp_path, backend, monkeypatch
):
    given = {}
    monkeypatch.setattr(routes, "convert", records(given, converted(tmp_path / "s.zip")))
    monkeypatch.setattr(
        routes, "write_filter", lambda document, solutions, backend: ("-- lua", None)
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


@pytest.mark.parametrize(
    "given, message",
    [
        (
            ["--review", "per-question"],
            "--review is an option of the spec route; add --route spec",
        ),
        (
            ["--spec", "set.yaml"],
            "--spec is an option of the spec route; add --route spec",
        ),
        (
            ["--route", "spec", "--write-filter"],
            "--write-filter is an option of the direct route; drop --route spec",
        ),
        (
            ["--route", "spec", "--solutions", "sol.md"],
            "--solutions is an option of the direct route; drop --route spec",
        ),
    ],
)
def test_an_option_of_the_other_route_is_refused(given, message, monkeypatch, capsys):
    # Under the route it does not belong to the option would be parsed and
    # thrown away: a saved spec ignored and written again by two model calls, a
    # review never stopped for, a filter never written. So the run says so, and
    # says so before it has paid for anything.
    monkeypatch.setattr(
        cli, "choose_backend", lambda settings: pytest.fail("a model was asked for")
    )
    monkeypatch.setattr(
        pipeline, "run", lambda *a, **k: pytest.fail("the spec route ran")
    )

    code = main(["run", "sheet.md", *given])

    assert code == 1
    assert capsys.readouterr().err.strip() == f"in2lambda-agent: {message}"


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


def test_the_corpus_cache_is_handed_to_the_sweep(monkeypatch):
    given = {}

    def record(*args, **kwargs):
        given.update(kwargs)
        return []

    monkeypatch.setattr(sweep, "sweep", record)

    main(["corpus", "ExampleContents", "--cache", "cached"])

    assert given["cache"] == Path("cached")


def test_gate_defaults():
    args = build_parser().parse_args(["gate", "gate-baseline.json"])

    assert args.command == "gate"
    assert args.baseline == Path("gate-baseline.json")
    assert args.record is False
    assert args.cache == Path.home() / ".cache" / "in2lambda-agent"
    # Chosen when the command runs, so that two runs do not share a directory.
    assert args.work is None


def test_gate_every_option():
    args = build_parser().parse_args(
        [
            "gate",
            "saved.json",
            "--record",
            "--cache",
            "cached",
            "--work",
            "working",
        ]
    )

    assert args.record is True
    assert args.cache == Path("cached")
    assert args.work == Path("working")


def test_a_gate_that_passes_exits_zero(tmp_path, monkeypatch, capsys):
    path = written_baseline(tmp_path)
    report = gate.Report(folders={"tex": gate.Summary(built=2, recorded=2)})
    monkeypatch.setattr(gate, "run", lambda *args, **kwargs: report)

    code = main(["gate", str(path)])

    assert code == 0
    assert "tex" in capsys.readouterr().out


def test_a_gate_that_fails_exits_one_and_says_what_the_folder_built(
    tmp_path, monkeypatch, capsys
):
    path = written_baseline(tmp_path)
    summary = gate.Summary(built=1, counts={"faulted": 1}, recorded=2)
    monkeypatch.setattr(
        gate, "run", lambda *args, **kwargs: gate.Report(folders={"tex": summary})
    )

    code = main(["gate", str(path)])

    assert code == 1
    assert "tex" in capsys.readouterr().out


def test_recording_writes_the_baseline_and_exits_zero(tmp_path, monkeypatch):
    path = written_baseline(tmp_path)

    def record(baseline, **kwargs):
        baseline.folders["tex"].built = 2
        return gate.Report(folders={"tex": gate.Summary(built=2, recorded=2)})

    monkeypatch.setattr(gate, "run", record)

    code = main(["gate", str(path), "--record"])

    assert code == 0
    assert gate.read_baseline(path).folders["tex"].built == 2


def written_baseline(tmp_path):
    """A baseline file on disk, for the gate command to read."""
    path = tmp_path / "baseline.json"
    gate.write_baseline(
        gate.Baseline(
            specs=Path("corpus-specs"),
            folders={"tex": gate.Folder(root=tmp_path / "corpus", suffixes=["tex"])},
        ),
        path,
    )
    return path


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


def test_approving_a_question():
    args = build_parser().parse_args(["review", "approve", "q2", "--cache", "cached"])

    assert (args.command, args.verdict, args.question) == ("review", "approve", "q2")
    assert args.cache == Path("cached")


def test_rejecting_a_question_carries_a_note():
    args = build_parser().parse_args(
        ["review", "reject", "q2", "--note", "part (b) is missing"]
    )

    assert (args.verdict, args.question, args.note) == (
        "reject",
        "q2",
        "part (b) is missing",
    )
    assert args.cache == Path(".in2lambda-agent")


def test_a_rejection_without_a_note_is_refused():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["review", "reject", "q2"])


def test_an_edit_names_the_field_the_wording_and_the_reviewer():
    args = build_parser().parse_args(
        ["review", "edit", "q1.text", "m/s", "m/s^2", "--by", "ada"]
    )

    assert (args.verdict, args.field, args.old, args.new) == (
        "edit",
        "q1.text",
        "m/s",
        "m/s^2",
    )
    assert args.by == "ada"


def test_an_edit_is_by_whoever_is_logged_in_unless_they_say():
    args = build_parser().parse_args(["review", "edit", "q1.text", "a", "b"])

    # Nothing is asked of the system while the arguments are being parsed: the
    # name is resolved on the review branch and nowhere else.
    assert args.by is None
    assert reviewer_name(args.by) == getpass.getuser()
    assert reviewer_name("ada") == "ada"


def test_an_edit_is_by_the_reviewer_where_there_is_no_login_name(monkeypatch):
    monkeypatch.setattr(
        getpass, "getuser", lambda: (_ for _ in ()).throw(OSError("no passwd entry"))
    )

    assert reviewer_name(None) == "reviewer"


def test_a_run_parses_where_there_is_no_login_name(monkeypatch, tmp_path):
    # A container started with `--user 1001` and no LOGNAME: `run` never wants
    # a reviewer's name, so it must not be asked for one to get to the parser.
    monkeypatch.setattr(
        getpass, "getuser", lambda: (_ for _ in ()).throw(OSError("no passwd entry"))
    )
    called = {}

    def record(source, **given):
        called["source"] = source
        return pipeline.RunResult(zip_path=tmp_path / "set.zip")

    monkeypatch.setattr(pipeline, "run", record)

    assert main(["run", "sheet.md", "--route", "spec"]) == 0
    assert called["source"] == Path("sheet.md")


def test_how_many_specs_may_be_written_reaches_the_run(monkeypatch, tmp_path):
    given = {}

    def record(source, **passed):
        given.update(passed)
        return pipeline.RunResult(zip_path=tmp_path / "set.zip")

    monkeypatch.setattr(pipeline, "run", record)

    assert main(["run", "sheet.md", "--route", "spec", "--tries", "5"]) == 0
    assert given["tries"] == 5


@pytest.mark.parametrize("count", ["0", "-1"])
def test_a_run_that_may_write_no_spec_is_refused(count, capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "s.md", "--tries", count])

    assert "at least one spec" in capsys.readouterr().err


def test_a_verdict_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["review"])


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
