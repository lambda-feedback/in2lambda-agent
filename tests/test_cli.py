"""The command line the design spec describes."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import FakeMathpix

from in2lambda_agent import cli, compare
from in2lambda_agent.cli import build_parser
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


@pytest.mark.parametrize("mode", ["none", "sample", "per-question"])
def test_review_modes(mode):
    assert build_parser().parse_args(["run", "s.md", "--review", mode]).review == mode


def test_an_unknown_review_mode_is_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "s.md", "--review", "everything"])


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


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
