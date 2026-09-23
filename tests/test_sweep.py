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

from in2lambda_agent import cli, ocr, routes, sweep
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

CREDENTIALS = Settings(mathpix_app_id="id", mathpix_api_key="key")
"""Enough to convert a PDF the cache holds: `markdown_of` builds the client
before it asks the cache, and a client is refused without them."""


def corpus(root: Path, *names: str) -> Path:
    """Writes one set folder per name, each holding the three fixture sheets."""
    for name in names:
        folder = root / name
        folder.mkdir(parents=True)
        for sheet in SHEETS:
            shutil.copy(FIXTURES / sheet, folder / sheet)
    return root


def cached_pdf(folder: Path, name: str, cache: Path, markdown: Path) -> Path:
    """A PDF sheet whose OCR the cache already holds, so Mathpix is not called."""
    pdf = folder / name
    pdf.write_bytes(b"%PDF-1.4 " + name.encode())
    entry = cache / ocr._hash(pdf)
    (entry / ocr.MEDIA_NAME).mkdir(parents=True)
    (entry / ocr.SOURCE_NAME).write_text(
        markdown.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return pdf


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


def test_a_tex_file_with_no_document_body_is_not_a_sheet(tmp_path):
    # A corpus folder of figures holds gnuplot and TikZ sources. Converting one
    # buys a filter call, a direct call and a set of no questions.
    root = tmp_path / "corpus"
    (root / "sheets").mkdir(parents=True)
    shutil.copy(FIXTURES / "tex-sheet.tex", root / "sheets")
    (root / "figures").mkdir()
    (root / "figures" / "plot.tex").write_text(
        "\\begingroup\n\\draw (0,0) -- (1,1);\n\\endgroup\n"
    )

    assert sweep.sets(root, suffixes=["tex"]) == [root / "sheets"]


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
    header, written = table(tmp_path / "results.csv")
    assert header == list(sweep.COLUMNS)
    assert [one["sheet"] for one in written] == [row.sheet for row in rows]
    assert written[0]["agreed"] == "8" and written[0]["not_verbatim"] == "0"
    assert sorted(path.relative_to(root).as_posix() for path in root.rglob("*")) == before


@pandoc
def test_the_set_filter_call_is_counted_on_its_first_sheet_and_nowhere_else(tmp_path, monkeypatch):
    root = corpus(tmp_path / "corpus", "alpha")
    backend = FakeBackend(*replies())
    # A clock of the test's own, so the seconds a row reports are known: the
    # filter call takes 2, the first sheet 3 and the second 4.
    ticks = iter([0, 2, 10, 13, 20, 24])
    monkeypatch.setattr(sweep.time, "monotonic", lambda: next(ticks))

    rows = sweep.sweep(
        root,
        results=tmp_path / "results.csv",
        work=tmp_path / "work",
        settings=Settings(),
        backend=backend,
    )

    # What FakeBackend records as usage: each prompt it read and each reply it
    # wrote. The set's four calls are the filter, paired.md's direct call, the
    # adjudication of the field the two routes read differently, and sheet.md's.
    cost = [len(prompt) for _, prompt in backend.calls]
    written = [FILTER, json.dumps(PAIRED_DIRECT), ADJUDICATION, json.dumps(SHEET_DIRECT)]
    filter_call = cost[0] + len(written[0])
    assert rows[0].tokens == cost[1] + len(written[1]) + cost[2] + len(written[2]) + filter_call
    assert rows[1].tokens == cost[3] + len(written[3])
    assert (rows[0].seconds, rows[1].seconds) == (5.0, 4.0)


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
def test_the_filter_is_written_from_the_first_sheet_pandoc_can_read(tmp_path):
    # pandoc cannot read a PDF, and one PDF at the head of a set would otherwise
    # deny route B to every sheet of the set, the ones pandoc reads among them.
    root = tmp_path / "corpus"
    folder = root / "alpha"
    folder.mkdir(parents=True)
    for sheet in ("paired.md", "paired_solutions.md"):
        shutil.copy(FIXTURES / sheet, folder / sheet)
    cached_pdf(folder, "a_sheet.pdf", tmp_path / "cache", FIXTURES / "sheet.md")
    backend = FakeBackend(
        FILTER, json.dumps(SHEET_DIRECT), json.dumps(PAIRED_DIRECT), ADJUDICATION
    )

    rows = sweep.sweep(
        root,
        suffixes=["md", "pdf"],
        results=tmp_path / "results.csv",
        work=tmp_path / "work",
        cache=tmp_path / "cache",
        settings=CREDENTIALS,
        backend=backend,
    )

    # The set's first call is the filter, and it was shown paired.md's blocks.
    assert "Tutorial Sheet 3" in backend.calls[0][1]
    assert [row.sheet for row in rows] == ["alpha/a_sheet.pdf", "alpha/paired.md"]
    # The PDF fails route B on its own; the sheet pandoc reads has its filter.
    assert rows[0].built and rows[0].reason.startswith("route B failed: ")
    assert rows[1].reason == "" and (rows[1].agreed, rows[1].adjudicated) == (8, 1)


def test_a_set_of_pdfs_alone_makes_no_filter_call_and_runs_route_a(tmp_path):
    root = tmp_path / "corpus"
    folder = root / "alpha"
    folder.mkdir(parents=True)
    cached_pdf(folder, "only.pdf", tmp_path / "cache", FIXTURES / "sheet.md")
    backend = FakeBackend(json.dumps(SHEET_DIRECT))

    rows = sweep.sweep(
        root,
        suffixes=["pdf"],
        results=tmp_path / "results.csv",
        work=tmp_path / "work",
        cache=tmp_path / "cache",
        settings=CREDENTIALS,
        backend=backend,
    )

    # The sheet's direct call and no filter call: pandoc has nothing to read.
    assert len(backend.calls) == 1
    assert rows[0].built and rows[0].fields == 10
    assert rows[0].reason == sweep.NO_FILTER_PDF


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
