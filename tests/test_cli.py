"""The command line the design spec describes."""

import getpass
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeMathpix

from in2lambda_agent import cli, compare, corpus, pipeline
from in2lambda_agent.cli import build_parser, main, reviewer_name
from in2lambda_agent.model import Usage
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


def test_corpus_defaults():
    args = build_parser().parse_args(["corpus", "ExampleContents"])

    assert args.command == "corpus"
    assert args.root == Path("ExampleContents")
    assert args.paths == []
    # Resolved where it is used: appending to a list the parser holds would run
    # the default suffixes as well as the named ones.
    assert args.suffixes is None
    assert args.replay is False
    assert args.rounds == 3
    assert args.results == Path("results.csv")
    assert args.work == Path(".in2lambda-agent/corpus")
    assert args.specs == Path("corpus-specs")


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
            "--replay",
            "--rounds",
            "1",
            "--results",
            "sweep.csv",
            "--work",
            "working",
            "--specs",
            "saved",
        ]
    )

    assert args.paths == [Path("Aero"), Path("MATE40002")]
    assert args.suffixes == ["tex", "md"]
    assert args.replay is True
    assert args.rounds == 1
    assert args.results == Path("sweep.csv")
    assert args.work == Path("working")
    assert args.specs == Path("saved")


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


def test_a_sweep_of_built_and_skipped_rows_is_a_sweep_that_worked(monkeypatch):
    # A corpus folder with a figure's tex source in it has a skipped row in
    # every sweep of it, and a file that is not a document is not a document
    # that failed: the exit code is the documents' and nothing else's.
    rows = [
        corpus.Row(source="tex/sheet.tex", set="tex", outcome="built"),
        corpus.Row(
            source="tex/figures/tunnel-potential.tex",
            set="tex/figures",
            outcome="skipped",
            reason="no \\begin{document}",
        ),
    ]
    monkeypatch.setattr(corpus, "sweep", lambda *args, **kwargs: rows)

    assert main(["corpus", "ExampleContents"]) == 0

    rows.append(
        corpus.Row(source="tex/sheet-2.tex", set="tex", outcome="build refused")
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

    assert main(["run", "sheet.md"]) == 0
    assert called["source"] == Path("sheet.md")


def test_a_verdict_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["review"])
