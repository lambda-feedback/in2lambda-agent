"""The command line the design spec describes."""

import getpass
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

    assert args.by == getpass.getuser()


def test_a_verdict_is_required():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["review"])
