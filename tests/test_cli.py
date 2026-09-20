"""The command line the design spec describes."""

from pathlib import Path

import pytest

from in2lambda_agent.cli import build_parser


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


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])
