"""The sweep of a corpus through the two-route conversion, one row per sheet.

Written before the module. The fixture sets are the ones `test_routes` runs the
folder conversion over, and the replies are that test's, so a set of
`paired.md`, `paired_solutions.md` and `sheet.md` reports the counts recorded
there: 10 fields a sheet, 8 agreed and 1 adjudicated on `paired.md`.
"""

import csv
import json
import os
import shutil
from pathlib import Path

import pytest

from conftest import FakeBackend
from test_routes import PAIRED_DIRECT, SHEET_DIRECT

from in2lambda_agent import cli, routes, sweep
from in2lambda_agent.model import ModelError
from in2lambda_agent.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
SHEETS = ("paired.md", "paired_solutions.md", "sheet.md")
FILTER = (FIXTURES / "pair-filter.lua").read_text()
ADJUDICATION = json.dumps(
    [{"field": "q2.main_text", "choice": "A", "reason": "B carries the parts too"}]
)
EXAMPLES = Path(
    "/Users/peterbjohnson/code/lambdafeedback/in2lambda-agent/ExampleContents"
)
COURSES = ("MECH60014_Stress_analysis_3", "PHYS40002-Mechanics", "UCL_MechEng")

live = pytest.mark.skipif(
    not os.environ.get("IN2LAMBDA_LIVE"), reason="calls Mathpix and a model"
)
pandoc = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc")


def corpus(root: Path, *names: str) -> Path:
    """Writes one set folder per name, each holding the three fixture sheets."""
    for name in names:
        folder = root / name
        folder.mkdir(parents=True)
        for sheet in SHEETS:
            shutil.copy(FIXTURES / sheet, folder / sheet)
    return root


def replies(sets: int = 1) -> list[str]:
    """The model's answers for one set: the filter, and each sheet's call."""
    return [FILTER, json.dumps(PAIRED_DIRECT), ADJUDICATION, json.dumps(SHEET_DIRECT)] * sets


def table(path: Path) -> tuple[list[str], list[dict]]:
    """The header and the rows of a written table."""
    with Path(path).open(newline="", encoding="utf-8") as written:
        reader = csv.DictReader(written)
        return reader.fieldnames, list(reader)


# --- which folders are sets -------------------------------------------------------------


def test_a_folder_with_a_sheet_in_it_is_a_set_and_one_without_is_not(tmp_path):
    root = corpus(tmp_path / "corpus", "alpha", "beta/second")
    (root / "solutions-only").mkdir()
    shutil.copy(FIXTURES / "paired_solutions.md", root / "solutions-only")
    (root / "alpha" / "figures").mkdir()
    shutil.copy(FIXTURES / "ball.png", root / "alpha" / "figures")

    assert sweep.sets(root) == [root / "alpha", root / "beta" / "second"]


def test_only_the_named_paths_and_the_named_suffixes_run(tmp_path):
    root = corpus(tmp_path / "corpus", "alpha", "beta")

    assert sweep.sets(root, paths=[Path("beta")]) == [root / "beta"]
    # The fixture sheets are markdown, so a sweep of the tex files has no set.
    assert sweep.sets(root, suffixes=["tex"]) == []


# --- the row of one sheet ---------------------------------------------------------------


def test_the_row_counts_the_flags_and_the_fields_that_are_not_quotes():
    converted = routes.Converted(
        set=None,
        zip_path=Path("paired.zip"),
        reply=PAIRED_DIRECT,
        flags=[
            routes.Flag("q1.main_text", "a ball", "", routes.NOT_VERBATIM),
            routes.Flag("q2.p1.content", "A", "B", "two readings"),
        ],
        tokens=500,
        fields=10,
        agreed=8,
        defaulted=1,
        adjudicated=1,
    )

    row = sweep.row_of("alpha", "alpha/paired.md", converted, 2.5)

    assert row.set == "alpha" and row.sheet == "alpha/paired.md"
    assert (row.questions, row.parts, row.fields) == (2, 2, 10)
    assert (row.agreed, row.adjudicated) == (8, 1)
    assert (row.flagged, row.not_verbatim) == (2, 1)
    assert (row.tokens, row.seconds) == (500, 2.5)
    assert row.reason == "" and row.built


# --- the sweep ---------------------------------------------------------------------------


@pandoc
def test_a_sweep_writes_one_filter_a_set_and_one_row_a_sheet(tmp_path):
    root = corpus(tmp_path / "corpus", "alpha", "beta")
    before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
    backend = FakeBackend(*replies(sets=2))

    rows = sweep.sweep(
        root,
        results=tmp_path / "results.csv",
        work=tmp_path / "work",
        settings=Settings(),
        backend=backend,
    )

    assert [(row.set, row.sheet) for row in rows] == [
        ("alpha", "alpha/paired.md"),
        ("alpha", "alpha/sheet.md"),
        ("beta", "beta/paired.md"),
        ("beta", "beta/sheet.md"),
    ]
    assert [(row.questions, row.parts, row.fields) for row in rows] == [(2, 2, 10)] * 4
    assert [(row.agreed, row.adjudicated, row.flagged) for row in rows] == [
        (8, 1, 0),
        (10, 0, 0),
        (8, 1, 0),
        (10, 0, 0),
    ]
    assert all(row.reason == "" and row.built for row in rows)
    # One filter call a set, written under the set's own folder, and read by
    # both sheets of that set.
    for name in ("alpha", "beta"):
        written_filter = (tmp_path / "work" / name / "filter.lua").read_text()
        assert written_filter == FILTER.strip()
    assert (tmp_path / "work" / "alpha" / "paired" / "paired.zip").is_file()
    assert (tmp_path / "work" / "beta" / "sheet" / "sheet.zip").is_file()
    # The filter call's tokens are the first sheet's, so each set's first row
    # reports more tokens than its second.
    assert rows[0].tokens > rows[1].tokens > 0
    header, written = table(tmp_path / "results.csv")
    assert header == list(sweep.COLUMNS)
    assert [one["sheet"] for one in written] == [row.sheet for row in rows]
    assert written[0]["agreed"] == "8" and written[0]["not_verbatim"] == "0"
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == before


@pandoc
def test_a_sheet_that_raises_is_a_row_and_the_next_sheet_still_runs(tmp_path):
    root = corpus(tmp_path / "corpus", "alpha")
    backend = FakeBackend(
        FILTER, ModelError("the provider stopped the call"), json.dumps(SHEET_DIRECT)
    )

    rows = sweep.sweep(
        root,
        results=tmp_path / "results.csv",
        work=tmp_path / "work",
        settings=Settings(),
        backend=backend,
    )

    assert [row.sheet for row in rows] == ["alpha/paired.md", "alpha/sheet.md"]
    assert rows[0].reason == "no set: the provider stopped the call"
    assert not rows[0].built
    assert (rows[0].questions, rows[0].fields) == (0, 0)
    assert rows[1].built and rows[1].fields == 10
    assert [one["sheet"] for one in table(tmp_path / "results.csv")[1]] == [
        "alpha/paired.md",
        "alpha/sheet.md",
    ]


@pandoc
def test_a_set_whose_filter_call_fails_converts_every_sheet_through_route_a(tmp_path):
    root = corpus(tmp_path / "corpus", "alpha")
    # No filter, so route B does not run and no field is adjudicated: the two
    # direct calls are the whole of the set's calls.
    backend = FakeBackend(
        ModelError("the provider stopped the call"),
        json.dumps(PAIRED_DIRECT),
        json.dumps(SHEET_DIRECT),
    )

    rows = sweep.sweep(
        root,
        results=tmp_path / "results.csv",
        work=tmp_path / "work",
        settings=Settings(),
        backend=backend,
    )

    assert [row.reason for row in rows] == [
        "no filter: the provider stopped the call"
    ] * 2
    assert all(row.built and row.fields == 10 for row in rows)
    assert [(row.agreed, row.adjudicated) for row in rows] == [(0, 0), (0, 0)]


@pandoc
def test_a_filter_pandoc_refuses_leaves_a_row_built_from_route_a(tmp_path):
    root = corpus(tmp_path / "corpus", "alpha")
    backend = FakeBackend(
        "this is not a filter\n", json.dumps(PAIRED_DIRECT), json.dumps(SHEET_DIRECT)
    )

    rows = sweep.sweep(
        root,
        results=tmp_path / "results.csv",
        work=tmp_path / "work",
        settings=Settings(),
        backend=backend,
    )

    assert all(row.built and row.fields == 10 for row in rows)
    assert all(row.reason.startswith("route B failed: ") for row in rows)


# --- live ----------------------------------------------------------------------------------


@live
@pytest.mark.skipif(not EXAMPLES.is_dir(), reason="private corpus")
def test_the_three_course_folders_sweep_from_the_command_line(tmp_path, capsys):
    # The ticket's run: the three folders of ExampleContents the gate replays,
    # PDFs included, reading the OCR the gate's cache holds.
    results = tmp_path / "results.csv"

    code = cli.main(
        [
            "corpus",
            str(EXAMPLES),
            *COURSES,
            "--suffix", "tex", "--suffix", "md", "--suffix", "docx", "--suffix", "pdf",
            "--results", str(results),
            "--work", str(tmp_path / "work"),
            "--cache", str(Path.home() / ".cache" / "in2lambda-agent"),
        ]
    )

    printed = capsys.readouterr().out
    print("\n" + printed)
    print(results.read_text(encoding="utf-8"))
    header, written = table(results)
    assert header == list(sweep.COLUMNS)
    assert {one["set"].split("/")[0] for one in written} == set(COURSES)
    assert code in (0, 1)
