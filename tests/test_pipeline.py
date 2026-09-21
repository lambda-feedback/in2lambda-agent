"""The end-to-end run: a markdown or PDF source in, a Lambda Feedback zip out."""

import json
import os
import random
import shutil
import warnings
import zipfile
from pathlib import Path

import in2lambda.draft
import pytest
from conftest import PNG, FakeBackend, FakeMathpix
from in2lambda.validation.pdf import missing_tools

from in2lambda_agent import package, pair, pipeline
from in2lambda_agent.cli import main
from in2lambda_agent.model import ModelUnavailable
from in2lambda_agent.package import SourceError, SpecRejected
from in2lambda_agent.review import ReviewError
from in2lambda_agent.settings import Settings
from in2lambda_agent.spec import RECORD_NAME, SPEC_NAME, BadSpec

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = FIXTURES / "sheet.md"
SPEC = (FIXTURES / "sheet-spec.yaml").read_text()
PAIRED_SPEC = (FIXTURES / "paired-spec.yaml").read_text()
TEX_SPEC = (FIXTURES / "tex-sheet-spec.yaml").read_text()
FAULTY_SPEC = (FIXTURES / "faulty-spec.yaml").read_text()

# What a model would run over the faulty sheet: the merged block cut in two and
# each half quoted, the solution the spec's selector missed given to the question
# it answers, and then the brace the OCR dropped out of that solution put back.
# The three quotations are layer 3; the replacement is the layer 4 edit, which
# in2lambda marks on the field rather than moving where it came from.
#
# A run scripted with these passes `tries=1`: the spec loop stops at one spec,
# and the replies after it are the round's commands rather than another spec.
FIXES = [
    ("split_block", {"block": "b7", "at": 14}),
    ("question_add", {"text": "b7a"}),
    ("part_add", {"question": "q2", "text": "b7b"}),
    ("question_solution", {"question": "q2", "text": "b11"}),
    (
        "field_replace",
        {"field": "q2.solution", "old": r"\mathbf{B$", "new": r"\mathbf{B}$"},
    ),
]

# The same fixes over the sheet with an unanswerable part in it, where the extra
# part has pushed every block and line below it along: the merged block is b8 at
# lines 15-16, and the solution the selector missed is b12.
UNSOLVED_FIXES = [
    ("split_block", {"block": "b8", "at": 16}),
    ("question_add", {"text": "b8a"}),
    ("part_add", {"question": "q2", "text": "b8b"}),
    ("question_solution", {"question": "q2", "text": "b12"}),
    (
        "field_replace",
        {"field": "q2.solution", "old": r"\mathbf{B$", "new": r"\mathbf{B}$"},
    ),
]

# The same spec with no `part` selector, so it runs but leaves every lettered
# part in no field: a saved spec the checks have something to say about.
PARTLESS_SPEC = "\n".join(
    line for line in SPEC.splitlines() if not line.startswith("part:")
)

# And with no `solution` selector, which leaves the four solution paragraphs in
# no field: fewer blocks over than PARTLESS_SPEC leaves, so the spec loop keeps
# this one of the two.
SOLUTIONLESS_SPEC = (
    "\n".join(line for line in SPEC.splitlines() if not line.startswith("solution:"))
    + "\n"
)

# A spec whose question selector is keyed to the wording of the first sheet —
# "A ball", "A block" — and so covers it completely while leaving the second
# sheet's stem, "A car brakes...", in no field.
FIRST_SHEET_SPEC = SPEC.replace("text~'^[A-Z]'", "text~'^A b'")

# And one whose layout reads the solutions as a run of parts followed by a run
# of solutions. sheet-2.md holds one question, so in2lambda runs the spec over
# it; sheet.md holds two, where the layout writes question 2's solution twice,
# so in2lambda refuses the spec there.
SECOND_SHEET_SPEC = SPEC.replace("PartsSepSol", "PartPartSolSol")


def drafted(folder, name):
    """The draft a run over one sheet of a folder left, which is named after it."""
    return package.draft_of(folder / name)


@pytest.fixture
def sheets(tmp_path):
    """A document set: two sheets written the same way, in a folder of their own."""
    folder = tmp_path / "sheets"
    folder.mkdir()
    for name in ("sheet.md", "sheet-2.md"):
        shutil.copy(FIXTURES / name, folder / name)
    return folder


@pytest.fixture
def faulty(tmp_path):
    """A folder holding the sheet with three faults seeded in it, and nothing else.

    No spec beside it, so the run writes one — which keeps the rewrite a saved
    spec gets out of the way of what the fixing rounds do.
    """
    folder = tmp_path / "faulty"
    folder.mkdir()
    shutil.copy(FIXTURES / "faulty.md", folder / "faulty.md")
    return folder


@pytest.fixture
def unsolved(tmp_path):
    """The same sheet with a part the document never answers: q1 has a (c).

    Everything else about it is the faulty sheet, so a round has the two
    findings it can answer and one it cannot.
    """
    folder = tmp_path / "unsolved"
    folder.mkdir()
    shutil.copy(FIXTURES / "faulty-unsolved.md", folder / "faulty-unsolved.md")
    return folder


@pytest.fixture
def questions_only(tmp_path):
    """A sheet whose solutions are not on it, with the set's spec beside it.

    Nothing answers any of its four parts, so every check but `no-solution` is
    quiet and that one is a warning: the set is written with them said.
    """
    folder = tmp_path / "questions-only"
    folder.mkdir()
    shutil.copy(FIXTURES / "questions-only.md", folder / "questions-only.md")
    (folder / SPEC_NAME).write_text(SPEC)
    return folder


@pytest.fixture
def tex_sheets(tmp_path):
    """A set of tex sheets, which is the shape the corpus keeps its sets in."""
    folder = tmp_path / "tex"
    folder.mkdir()
    for name in ("tex-sheet.tex", "tex-sheet-2.tex"):
        shutil.copy(FIXTURES / name, folder / name)
    return folder


@pytest.fixture
def figures(tmp_path):
    """A sheet whose first question refers to an image, with the image beside it.

    The spec the set already has covers it: the reference sits in the same
    block as the question's text. The image is a real PNG rather than a few
    bytes named like one, because the checks now compile the set as the PDF
    generator does, and a file xelatex cannot load is an error of its own.
    """
    folder = tmp_path / "figures"
    (folder / "figures").mkdir(parents=True)
    shutil.copy(FIXTURES / "figure.md", folder / "figure.md")
    shutil.copy(FIXTURES / "ball.png", folder / "figures" / "ball.png")
    (folder / SPEC_NAME).write_text(SPEC)
    return folder


@pytest.fixture
def paired(tmp_path):
    """A sheet whose solutions are written as a file of their own beside it."""
    folder = tmp_path / "paired"
    folder.mkdir()
    for name in ("paired.md", "paired_solutions.md"):
        shutil.copy(FIXTURES / name, folder / name)
    return folder


@pytest.fixture
def marked(tmp_path):
    """A pair whose solutions file writes a marker above each group of solutions.

    A solutions document written to stand on its own repeats the question it is
    answering, so the `question` selector matches in the second source as well
    as the first. `marked_solutions.md` leaves question 1's part (b)
    unanswered, so the marker `Q2.` is what sends the solutions under it to
    question 2 rather than to question 1's remaining part.
    """
    folder = tmp_path / "marked"
    folder.mkdir()
    shutil.copy(FIXTURES / "paired.md", folder / "marked.md")
    shutil.copy(FIXTURES / "marked_solutions.md", folder / "marked_solutions.md")
    return folder


class PairedMathpix:
    """A Mathpix client answering with the fixture each PDF is named after.

    Each conversion holds a figure, as a scanned sheet does, and both call it
    `media/plot.png`: the pair of names that must not become one file when the
    solutions are copied beside the questions.
    """

    def __init__(self):
        self.calls: list[Path] = []

    def convert(self, pdf: Path, media_dir: Path) -> str:
        self.calls.append(Path(pdf))
        media_dir.mkdir(parents=True, exist_ok=True)
        (media_dir / "plot.png").write_bytes(PNG)
        lines = (FIXTURES / f"{Path(pdf).stem}.md").read_text().splitlines()
        # Inside the first paragraph, which is a field of the draft: an image of
        # a block the spec assigns to nothing is a coverage error, not a figure.
        first = next(
            number
            for number, line in enumerate(lines)
            if line and not line.startswith("#")
        )
        lines[first] += " ![a plot](media/plot.png)"
        return "\n".join(lines) + "\n"


def test_one_model_call_writes_the_sets_spec_and_the_run_builds(sheets, tmp_path):
    backend = FakeBackend(SPEC)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=backend,
    )

    assert len(backend.calls) == 1
    assert (sheets / SPEC_NAME).read_text() == SPEC
    assert result.zip_path is not None and result.zip_path.exists()
    # A zip was written, so there is nothing to say about why one was not.
    assert result.reason == ""


def test_the_zip_holds_what_the_spec_made_of_the_source(sheets, tmp_path):
    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(SPEC),
    )

    zip_file = zipfile.ZipFile(result.zip_path)
    assert zip_file.namelist() == [
        "question_000_Question_1.json",
        "question_001_Question_2.json",
        "set_set.json",
    ]
    question = json.loads(zip_file.read("question_000_Question_1.json"))
    # The parts with their markers stripped, and the solutions the layout
    # paired to them out of the separate solutions section.
    assert [part["content"] for part in question["parts"]] == [
        "Find the greatest height it reaches.",
        "Find its time of flight.",
    ]
    assert [part["workedSolution"]["content"] for part in question["parts"]] == [
        "$h = v^2 / 2g = 20.4\\,\\mathrm{m}$",
        "$t = 2v/g = 4.08\\,\\mathrm{s}$",
    ]


def test_a_second_sheet_in_the_set_reuses_the_spec_with_no_call(sheets, tmp_path):
    backend = FakeBackend(SPEC)
    pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "first",
        settings=Settings(),
        backend=backend,
    )

    result = pipeline.run(
        sheets / "sheet-2.md",
        out_dir=tmp_path / "second",
        settings=Settings(),
        backend=backend,
    )
    spec = next(stage for stage in result.stages if stage.name == "spec")

    assert len(backend.calls) == 1
    assert spec.message == f"reused {sheets / SPEC_NAME}"
    assert result.zip_path.exists()


def test_a_tex_set_is_frozen_to_markdown_and_shares_one_spec(tex_sheets, tmp_path):
    backend = FakeBackend(TEX_SPEC)

    first = pipeline.run(
        tex_sheets / "tex-sheet.tex",
        out_dir=tmp_path / "first",
        settings=Settings(),
        backend=backend,
    )
    second = pipeline.run(
        tex_sheets / "tex-sheet-2.tex",
        out_dir=tmp_path / "second",
        settings=Settings(),
        backend=backend,
    )
    spec = next(stage for stage in second.stages if stage.name == "spec")

    # `source add` converts a tex sheet to markdown beside it and freezes that,
    # so a folder of tex sheets is a set of markdown sources by the time the
    # spec is written, and one spec does for all of them.
    assert (tex_sheets / "tex-sheet.md").exists()
    assert len(backend.calls) == 1
    assert spec.message == f"reused {tex_sheets / SPEC_NAME}"
    assert (first.coverage.fields, second.coverage.fields) == ({1: 2}, {1: 3})
    assert first.zip_path.exists() and second.zip_path.exists()


def test_the_coverage_is_reported_and_recorded_beside_the_spec(sheets, tmp_path):
    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(SPEC),
    )
    coverage = next(stage for stage in result.stages if stage.name == "coverage")

    assert result.coverage.layout == "PartsSepSol"
    assert result.coverage.unassigned == []
    # The sheet's fourteen blocks: four headings ignored, ten in fields.
    assert (result.coverage.blocks, result.coverage.fields) == (14, {1: 10})
    assert coverage.message == (
        "PartsSepSol: 14 blocks, 10 fields at layer 1, 4 ignored, none unassigned"
    )

    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    recorded = json.loads(line)
    assert recorded["layout"] == "PartsSepSol"
    assert (recorded["blocks"], recorded["fields"]) == (14, {"1": 10})
    assert recorded["unassigned"] == [] and recorded["reused"] is False
    assert recorded["output_tokens"] == result.usage.output_tokens > 0
    # A spec that covers its source is the whole run: nothing was left to fix.
    assert recorded["rounds"] == []


def test_a_named_spec_is_read_from_where_it_was_named(sheets, tmp_path):
    elsewhere = tmp_path / "mine.yaml"
    elsewhere.write_text(SPEC)
    backend = FakeBackend()

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        spec=elsewhere,
        backend=backend,
    )
    spec = next(stage for stage in result.stages if stage.name == "spec")

    assert backend.calls == []
    assert spec.message == f"reused {elsewhere}"
    assert not (sheets / SPEC_NAME).exists()
    assert result.zip_path.exists()


def test_a_named_spec_that_is_not_there_yet_is_written_there(sheets, tmp_path):
    elsewhere = tmp_path / "mine.yaml"

    pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        spec=elsewhere,
        backend=FakeBackend(SPEC),
    )

    assert elsewhere.read_text() == SPEC


def test_a_spec_in2lambda_will_not_run_stops_the_run_saying_why(sheets, tmp_path):
    backend = FakeBackend("question: NotAnElement\nlayout: PartsSepSol\n")

    with pytest.raises(SpecRejected, match="not a pandoc element"):
        pipeline.run(
            sheets / "sheet.md",
            out_dir=tmp_path / "out",
            settings=Settings(),
            backend=backend,
        )

    assert not (tmp_path / "out").exists()
    # And the set is not wedged: a spec in2lambda refused is not left for the
    # next run to read instead of making a call of its own.
    assert not (sheets / SPEC_NAME).exists()

    again = FakeBackend(SPEC)
    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=again,
    )

    assert len(again.calls) == 1
    assert result.zip_path.exists()


def test_a_rewrite_in2lambda_will_not_run_leaves_the_saved_spec_alone(
    sheets, tmp_path
):
    (sheets / SPEC_NAME).write_text(PARTLESS_SPEC)
    backend = FakeBackend("question: NotAnElement\nlayout: PartsSepSol\n")

    with pytest.raises(SpecRejected, match="not a pandoc element"):
        pipeline.run(
            sheets / "sheet.md",
            out_dir=tmp_path / "out",
            settings=Settings(),
            backend=backend,
        )

    # The spec the rewrite was meant to improve on still runs, whatever the
    # checks had to say about it; the one that does not run is gone.
    assert (sheets / SPEC_NAME).read_text() == PARTLESS_SPEC


def test_a_try_the_loop_never_chose_is_not_left_beside_the_sources(sheets, tmp_path):
    # The first call answers with a spec that runs but covers little, the second
    # with something that is not a spec at all. The set keeps the spec it had:
    # try 1 was written to the file, and no try was ever chosen.
    (sheets / SPEC_NAME).write_text(PARTLESS_SPEC)
    backend = FakeBackend(SOLUTIONLESS_SPEC, "I would rather not.")

    with pytest.raises(BadSpec):
        pipeline.run(
            sheets / "sheet.md",
            out_dir=tmp_path / "out",
            settings=Settings(),
            backend=backend,
        )

    assert len(backend.calls) == 2
    assert (sheets / SPEC_NAME).read_text() == PARTLESS_SPEC


def test_a_document_of_the_set_in2lambda_cannot_read_is_passed_over(
    sheets, tmp_path, monkeypatch
):
    # A folder holds files that are not documents — a Word lock file beside a
    # docx — and in2lambda refuses them. The run reports the file and converts
    # the source it was asked for.
    freeze = package.source_add

    def refuse(source):
        if Path(source).name == "sheet-2.md":
            raise SourceError("pandoc could not read sheet-2.md")
        return freeze(source)

    monkeypatch.setattr(package, "source_add", refuse)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(SPEC),
    )
    (over_set,) = [stage for stage in result.stages if stage.name == "set"]

    assert over_set.message == (
        "sheet-2.md cannot be read: pandoc could not read sheet-2.md"
    )
    assert result.zip_path is not None and result.zip_path.exists()
    # The try is then judged on this source alone, and the record says which
    # document the run passed over and why, rather than reading as a set of one.
    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    record = json.loads(line)
    (one,) = record["iterations"]
    assert one["second"] is None and one["chosen"] is True
    assert record["second"] == {
        "name": "sheet-2.md",
        "passed_over": "pandoc could not read sheet-2.md",
    }


def test_a_sheet_of_the_set_that_has_a_draft_of_its_own_is_left_alone(
    sheets, tmp_path
):
    # A run over sheet-2.md left the draft beside it, holding that sheet's
    # fields and the log of the commands that wrote them. Freezing sheet-2.md
    # to try a spec over it would write the draft again from the source and
    # delete both, so the loop runs each spec over a copy in the cache and the
    # draft beside the sheet is still the one that run wrote.
    elsewhere = tmp_path / "other-spec.yaml"
    elsewhere.write_text(SPEC)
    second = package.source_add(sheets / "sheet-2.md")
    package.spec_run(second, elsewhere)
    before = second.read_text()

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        cache_dir=tmp_path / "cache",
        backend=FakeBackend(SPEC),
    )
    (over_set,) = [stage for stage in result.stages if stage.name == "set"]

    assert second.read_text() == before
    assert (tmp_path / "cache" / "second" / "sheet-2.md").is_file()
    assert result.zip_path is not None and result.zip_path.exists()
    # The sheet is evidence about the spec all the same: the copy is run over,
    # the line names the sheet, and the try carries what it left there.
    assert over_set.message.startswith("sheet-2.md: ")
    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    record = json.loads(line)
    (one,) = record["iterations"]
    assert one["second"] == 0
    assert record["second"] == {"name": "sheet-2.md", "passed_over": None}


def test_a_saved_spec_the_checks_fault_is_written_again_once(sheets, tmp_path):
    (sheets / SPEC_NAME).write_text(PARTLESS_SPEC)
    backend = FakeBackend(SPEC)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=backend,
    )
    names = [stage.name for stage in result.stages]
    first, second = (stage for stage in result.stages if stage.name == "spec")

    assert len(backend.calls) == 1
    # The report the first pass left is what the second was asked to answer.
    assert "is in no field" in backend.calls[0][1]
    assert first.message.startswith("reused ")
    assert second.message.startswith(f"wrote {sheets / SPEC_NAME}")
    assert names.count("validate") == 2
    assert (sheets / SPEC_NAME).read_text() == SPEC
    assert result.zip_path.exists()


def test_a_fresh_spec_the_checks_fault_stops_the_run_with_no_zip(sheets, tmp_path):
    backend = FakeBackend(PARTLESS_SPEC)

    # No rounds, so the report is where the run ends: what the rounds make of a
    # report is below.
    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        rounds=0,
        tries=1,
        backend=backend,
    )
    stages = {stage.name: stage.message for stage in result.stages}

    assert len(backend.calls) == 1
    assert "is in no field and not marked ignore" in stages["validate"]
    # The blocks left over are named by block id, as the coverage line and the
    # record say they are: the sheet's four lettered parts, with the two
    # solutions that had no part to pair with.
    assert result.coverage.unassigned == ["b4", "b5", "b8", "b9", "b13", "b14"]
    assert "b4, b5, b8, b9, b13, b14 unassigned" in stages["coverage"]
    assert result.zip_path is None
    assert result.clean is False
    assert not (tmp_path / "out").exists()


def test_the_spec_is_written_again_against_what_running_the_last_one_covered(
    sheets, tmp_path
):
    # Three specs, none of them clean: the second leaves four blocks over where
    # the first and third leave six, so it is the one the set keeps.
    backend = FakeBackend(PARTLESS_SPEC, SOLUTIONLESS_SPEC, PARTLESS_SPEC)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        rounds=0,
        backend=backend,
    )
    kept = [stage for stage in result.stages if stage.name == "spec"][-1]
    fields = json.loads(drafted(sheets, "sheet.md").read_text())["fields"]

    assert len(backend.calls) == 3
    # What the first spec covered is in the second call's prompt, by the block
    # ids the coverage line names and the sentences the checks wrote.
    assert "b4, b5, b8, b9, b13, b14 unassigned" in backend.calls[1][1]
    assert "b4 (lines 7-7) is in no field" in backend.calls[1][1]
    assert "sheet-2.md, another document of this set" in backend.calls[1][1]
    assert kept.message == "kept try 2 of 3"
    # The spec beside the sources, the coverage the run carries and the draft on
    # disk are all the second try's, which the third try wrote over and the loop
    # ran again.
    assert (sheets / SPEC_NAME).read_text() == SOLUTIONLESS_SPEC
    assert result.coverage.unassigned == ["b11", "b12", "b13", "b14"]
    assert "q1.p1.text" in fields

    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    iterations = json.loads(line)["iterations"]
    assert [one["try"] for one in iterations] == [1, 2, 3]
    assert all(one["input_tokens"] > 0 and one["seconds"] > 0 for one in iterations)
    assert [one["unassigned"] for one in iterations] == [6, 4, 6]
    assert [one["chosen"] for one in iterations] == [False, True, False]


def test_a_rewrite_reads_the_other_sheet_of_a_set_whose_sheets_all_have_drafts(
    sheets, tmp_path
):
    # The sweep's third sheet: every other sheet of the set has been run and has
    # a draft beside it, and the saved spec faults this one. The rewrite is the
    # call this ticket added try 0 for, so try 0 is run over the other sheet as
    # well and the first call reads what the saved spec left there.
    elsewhere = tmp_path / "other-spec.yaml"
    elsewhere.write_text(SPEC)
    second = package.source_add(sheets / "sheet-2.md")
    package.spec_run(second, elsewhere)
    before = second.read_text()
    (sheets / SPEC_NAME).write_text(PARTLESS_SPEC)
    backend = FakeBackend(FIRST_SHEET_SPEC, SPEC)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        cache_dir=tmp_path / "cache",
        tries=2,
        backend=backend,
    )

    # The saved spec leaves b4, b5 and b8 of sheet-2.md in no field, and the
    # first call is asked for a spec that does not.
    assert "another document of this set, left b4, b5, b8" in backend.calls[0][1]
    assert (sheets / SPEC_NAME).read_text() == SPEC
    assert second.read_text() == before
    assert result.zip_path is not None and result.zip_path.exists()

    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    record = json.loads(line)
    # Try 0 is the saved spec, try 1 covers this sheet alone and try 2 the set.
    assert [one["try"] for one in record["iterations"]] == [0, 1, 2]
    assert [one["second"] for one in record["iterations"]] == [3, 5, 0]
    assert record["second"] == {"name": "sheet-2.md", "passed_over": None}


def test_a_spec_in2lambda_refuses_over_the_other_sheet_is_scored_on_that(
    sheets, tmp_path
):
    # The first spec covers this sheet, and in2lambda refuses it over the other
    # sheet. in2lambda reads the other sheet, so the copy of it stays and the
    # next try is run over it as well, and the refused try scores as leaving
    # every block of it in no field.
    backend = FakeBackend(SECOND_SHEET_SPEC, SPEC)

    result = pipeline.run(
        sheets / "sheet-2.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        cache_dir=tmp_path / "cache",
        tries=2,
        backend=backend,
    )
    refused, ran = [stage for stage in result.stages if stage.name == "set"]

    assert refused.message.startswith("sheet.md: in2lambda refused the spec: ")
    assert ran.message.startswith("sheet.md: ")
    assert (sheets / SPEC_NAME).read_text() == SPEC
    assert result.zip_path is not None and result.zip_path.exists()

    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    record = json.loads(line)
    # The refused try covers this sheet completely, so the other sheet is what
    # carried the loop on to the try that covers both sheets. The refused spec
    # wrote no field in sheet.md, so it left all 14 of its blocks in no field.
    assert [one["unassigned"] for one in record["iterations"]] == [0, 0]
    assert [one["second"] for one in record["iterations"]] == [14, 0]
    assert [one["chosen"] for one in record["iterations"]] == [False, True]
    # The spec the run kept ran over the document, so the record does not say
    # the document was passed over.
    assert record["second"] == {"name": "sheet.md", "passed_over": None}


def test_a_refusal_that_went_with_a_try_the_run_threw_away_is_not_recorded(
    sheets, tmp_path
):
    # The second spec covers this sheet and in2lambda refuses it over the other
    # sheet, so the run keeps the first, which ran over both. What the record
    # says became of the other sheet is what the kept spec made of it, not what
    # the try after it did.
    backend = FakeBackend(SOLUTIONLESS_SPEC, SECOND_SHEET_SPEC)

    result = pipeline.run(
        sheets / "sheet-2.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        cache_dir=tmp_path / "cache",
        rounds=0,
        tries=2,
        backend=backend,
    )
    ran, refused = [stage for stage in result.stages if stage.name == "set"]
    kept = [stage for stage in result.stages if stage.name == "spec"][-1]

    # The stage lines are the log of the loop, so both tries are still in them.
    assert ran.message.startswith("sheet.md: PartsSepSol")
    assert refused.message.startswith("sheet.md: in2lambda refused the spec: ")
    assert kept.message == "kept try 1 of 2"
    assert (sheets / SPEC_NAME).read_text() == SOLUTIONLESS_SPEC

    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    record = json.loads(line)
    assert [one["chosen"] for one in record["iterations"]] == [True, False]
    assert record["second"] == {"name": "sheet.md", "passed_over": None}


def test_the_sheets_own_solutions_file_is_no_other_document_of_the_set(
    paired, tmp_path
):
    # The solutions file is the second source of this run's own draft, so it is
    # not another document for a spec to be judged over, and the folder holds
    # nothing else.
    result = pipeline.run(
        paired / "paired.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        cache_dir=tmp_path / "cache",
        backend=FakeBackend(PAIRED_SPEC),
    )

    assert not [stage for stage in result.stages if stage.name == "set"]
    assert not (tmp_path / "cache" / "second").exists()
    (line,) = (paired / RECORD_NAME).read_text().splitlines()
    assert json.loads(line)["second"] is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root may read any file")
def test_a_document_of_the_set_the_run_cannot_copy_is_passed_over(sheets, tmp_path):
    # The copy is what keeps the spec loop off the set's own drafts, so a sheet
    # the OS will not let the run copy is one to pass over, as one in2lambda
    # cannot read is, rather than one that ends the run with no spec written.
    (sheets / "sheet-2.md").chmod(0o000)
    try:
        result = pipeline.run(
            sheets / "sheet.md",
            out_dir=tmp_path / "out",
            settings=Settings(),
            cache_dir=tmp_path / "cache",
            backend=FakeBackend(SPEC),
        )
    finally:
        (sheets / "sheet-2.md").chmod(0o644)
    (over_set,) = [stage for stage in result.stages if stage.name == "set"]

    assert over_set.message.startswith("sheet-2.md passed over: copying it failed: ")
    assert result.zip_path is not None and result.zip_path.exists()
    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    record = json.loads(line)
    assert record["second"]["name"] == "sheet-2.md"
    assert record["second"]["passed_over"].startswith("copying it failed: ")
    (one,) = record["iterations"]
    assert one["second"] is None


def test_a_pdf_beside_a_pdf_source_is_passed_over_and_the_record_says_why(
    pdf, tmp_path
):
    (tmp_path / "sheet-2.pdf").write_bytes(b"%PDF-1.4 the next sheet")

    result = pipeline.run(
        pdf,
        out_dir=tmp_path / "out",
        settings=Settings(),
        cache_dir=tmp_path / "cache",
        mathpix=FakeMathpix(markdown=SOURCE.read_text()),
        backend=FakeBackend(SPEC),
    )
    (over_set,) = [stage for stage in result.stages if stage.name == "set"]

    assert over_set.message == (
        "sheet-2.pdf passed over: converting it takes an OCR call, and the "
        "spec loop makes no call but the model's"
    )
    (line,) = (tmp_path / RECORD_NAME).read_text().splitlines()
    record = json.loads(line)
    assert record["second"]["name"] == "sheet-2.pdf"
    assert record["second"]["passed_over"].startswith("converting it takes")
    (one,) = record["iterations"]
    assert one["second"] is None


def test_a_saved_spec_the_checks_fault_is_the_try_the_rewrite_improves_on(
    sheets, tmp_path
):
    (sheets / SPEC_NAME).write_text(PARTLESS_SPEC)
    backend = FakeBackend(SPEC)

    pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=backend,
    )

    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    saved, written = json.loads(line)["iterations"]

    # The saved spec is try 0: it is what the call was asked to improve on, and
    # it cost no call of its own.
    assert (saved["try"], saved["unassigned"], saved["chosen"]) == (0, 6, False)
    assert saved["input_tokens"] == saved["output_tokens"] == 0
    assert (written["try"], written["unassigned"], written["chosen"]) == (1, 0, True)
    assert written["output_tokens"] > 0


def test_a_spec_that_covers_this_sheet_alone_does_not_stop_the_loop(sheets, tmp_path):
    # The first spec covers sheet.md completely and leaves sheet-2.md's stem in
    # no field. The spec is saved for the whole set, so that is not a spec to
    # stop at: the second call is made, and covers both.
    backend = FakeBackend(FIRST_SHEET_SPEC, SPEC)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=2,
        backend=backend,
    )
    over_set = [stage for stage in result.stages if stage.name == "set"]

    assert len(backend.calls) == 2
    assert over_set[0].message.startswith("sheet-2.md: ")
    # Nothing of the second sheet is covered: its stem is in no field, and its
    # parts and solutions have no question to belong to.
    assert "b3, b4, b5, b7, b8 unassigned" in over_set[0].message
    assert (sheets / SPEC_NAME).read_text() == SPEC
    assert result.zip_path is not None and result.zip_path.exists()

    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    iterations = json.loads(line)["iterations"]
    assert [one["unassigned"] for one in iterations] == [0, 0]
    assert [one["second"] for one in iterations] == [5, 0]
    assert [one["chosen"] for one in iterations] == [False, True]


def test_a_sheet_with_no_other_document_beside_it_is_judged_on_its_own(
    faulty, tmp_path
):
    backend = FakeBackend(FAULTY_SPEC, FIXES)

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=1,
        backend=backend,
    )

    assert not [stage for stage in result.stages if stage.name == "set"]
    (line,) = (faulty / RECORD_NAME).read_text().splitlines()
    record = json.loads(line)
    (one,) = record["iterations"]
    assert one["second"] is None and one["chosen"] is True
    # Null here is the folder holding no other document, which is what a reader
    # of the record can tell it from a document the run passed over by.
    assert record["second"] is None


def test_the_rounds_fix_what_the_checks_found_and_the_run_builds(faulty, tmp_path):
    backend = FakeBackend(FAULTY_SPEC, FIXES)

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=1,
        backend=backend,
    )
    fixed = next(stage for stage in result.stages if stage.name == "fix")

    # One call to write the spec, one round to answer what it left over.
    assert len(backend.calls) == 2
    assert [stage.name for stage in result.stages] == [
        "ocr",
        "freeze",
        "spec",
        "coverage",
        "validate",
        "fix",
        "validate",
        "review",
        "build",
    ]
    assert fixed.message.startswith(
        "round 1: 5 commands (split block b7, question add b7a, part add q2, "
        "question solution q2, field replace q2.solution), "
    )
    assert result.stages[-3].message == "nothing to report"
    assert result.zip_path is not None and result.zip_path.exists()


def test_each_round_answers_what_the_one_before_it_left(faulty, tmp_path):
    # The same fixes, spread over two rounds: the second is given the report the
    # first left behind, and the source with the block the first split in it.
    backend = FakeBackend(FAULTY_SPEC, FIXES[:2], FIXES[2:])

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=1,
        backend=backend,
    )
    second = backend.calls[2][1]

    assert [one.number for one in result.rounds] == [1, 2]
    assert [one.left for one in result.rounds] == [2, 0]
    # The half of b7 still in no field, under the id the first round's split
    # gave it, which is not an id the first round was shown.
    assert "b7b (lines 14-14) is in no field" in second
    assert "b7b" in second.split("in2lambda validate reports")[0]
    assert result.zip_path is not None and result.zip_path.exists()


def test_every_fix_is_in_the_drafts_log_with_the_layer_it_wrote(faulty, tmp_path):
    pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=1,
        backend=FakeBackend(FAULTY_SPEC, FIXES),
    )
    log = package.command_log(drafted(faulty, "faulty.md"))
    fields = json.loads(drafted(faulty, "faulty.md").read_text())["fields"]

    # The spec, and then every fix after it, all recorded by in2lambda as it
    # applied them: replaying this log rebuilds the draft with no model in it.
    assert [entry["command"] for entry in log] == [
        "spec run",
        "split block",
        "question add",
        "part add",
        "question solution",
        "field replace",
    ]
    assert {entry["by"] for entry in log} == {package.BY}
    # Each of those quoted a range of the source, so the fields they wrote are
    # layer 3: what they say is what the source says, and can be shown against it.
    quoted = ["q2.text", "q2.p1.text", "q2.solution"]
    assert [fields[key]["layer"] for key in quoted] == [3, 3, 3]
    assert not any(fields[key]["edited"] for key in quoted[:2])
    # The replacement is the layer 4 work: it leaves the field quoting the lines
    # it came from and marks it as no longer saying what they say, which is how
    # the brace the OCR dropped ends up repaired in what is built.
    assert fields["q2.solution"]["edited"] is True
    assert r"\mathbf{B}$" in fields["q2.solution"]["value"]
    # And the fields the spec wrote, which nothing touched, are still layer 1.
    assert fields["q1.text"]["layer"] == 1 and not fields["q1.text"]["edited"]


def test_the_record_says_what_each_round_cost(faulty, tmp_path):
    pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=1,
        backend=FakeBackend(FAULTY_SPEC, FIXES),
    )

    (line,) = (faulty / RECORD_NAME).read_text().splitlines()
    (round_one,) = json.loads(line)["rounds"]

    assert round_one["round"] == 1
    assert round_one["input_tokens"] > 0 and round_one["output_tokens"] > 0
    assert round_one["seconds"] > 0
    assert round_one["commands"] == [name for name, _ in FIXES]
    # Nothing left for a second round, which is why there was not one.
    assert round_one["left"] == 0


def test_a_command_in2lambda_refuses_is_answered_rather_than_ending_the_run(
    faulty, tmp_path
):
    backend = FakeBackend(FAULTY_SPEC, [("mark_ignore", {"block": "b99"})] + FIXES)

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=1,
        backend=backend,
    )
    refused = result.rounds[0].commands[0]

    assert "was refused" in refused.result and "no block b99" in refused.result
    # The refusal is what the model was told, and it went on to fix the draft.
    assert result.zip_path is not None and result.zip_path.exists()
    assert "b99" not in str(package.command_log(drafted(faulty, "faulty.md")))


def test_a_part_whose_solution_is_not_on_the_sheet_is_reported_not_written(
    unsolved, tmp_path
):
    # The round answers the two findings the source can answer and leaves the
    # one it cannot: q1's third part has no solution anywhere in the document,
    # and there is nothing to quote for it.
    backend = FakeBackend(FAULTY_SPEC, UNSOLVED_FIXES)

    result = pipeline.run(
        unsolved / "faulty-unsolved.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=1,
        backend=backend,
    )
    checked = [stage for stage in result.stages if stage.name == "validate"][-1]
    fields = json.loads(drafted(unsolved, "faulty-unsolved.md").read_text())["fields"]

    assert [stage.name for stage in result.stages].count("fix") == 1
    # A part nothing answers is a warning, not an error: it is said and the set
    # is written anyway, since half the sheets there are keep their solutions
    # somewhere else or have none.
    assert "q1.p3" in checked.message and "has no solution" in checked.message
    assert checked.message.endswith("— warnings, building")
    # Nothing was typed into the gap, and nothing in the log could have been.
    assert "q1.p3.solution" not in fields
    assert not any(
        "literal" in entry["args"]
        for entry in package.command_log(drafted(unsolved, "faulty-unsolved.md"))
    )
    assert result.clean is True
    assert result.zip_path is not None and result.zip_path.exists()
    # What the build went past, for the table a sweep writes to carry.
    assert "q1.p3" in result.reason and "has no solution" in result.reason


def test_a_solution_the_model_types_out_is_refused_and_the_finding_stays(
    unsolved, tmp_path
):
    invented = "The ball rises, slows and falls back along the same line. " * 4
    backend = FakeBackend(
        FAULTY_SPEC,
        [("question_solution", {"question": "q1", "literal": invented})]
        + UNSOLVED_FIXES,
    )

    result = pipeline.run(
        unsolved / "faulty-unsolved.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=1,
        backend=backend,
    )
    typed = result.rounds[0].commands[0]

    assert "was refused" in typed.result and "may be typed" in typed.result
    # The refusal never reached in2lambda, so the draft was built by the
    # quotations alone and the gap it could not answer is reported rather than
    # filled: the set is written with the warning, not with an invented answer.
    assert not any(
        "literal" in entry["args"]
        for entry in package.command_log(drafted(unsolved, "faulty-unsolved.md"))
    )
    checked = [stage for stage in result.stages if stage.name == "validate"][-1]
    assert checked.message.endswith("— warnings, building")
    assert "q1.p3" in result.reason and "has no solution" in result.reason


def test_a_questions_only_sheet_builds_with_its_warnings_in_the_reason(
    questions_only, tmp_path
):
    # A sheet with no solutions on it at all, which is half the sets there are:
    # every part is a warning, nothing is an error, and the set is written.
    result = pipeline.run(
        questions_only / "questions-only.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        rounds=0,
    )
    checked = [stage for stage in result.stages if stage.name == "validate"][-1]

    # No round was spent on them, and the saved spec was not written again:
    # there is nothing here a second spec would cover any better.
    assert (result.rounds, result.reused, result.clean) == ([], True, True)
    assert result.zip_path is not None and result.zip_path.exists()
    assert checked.message.count("has no solution") == 4
    assert checked.message.endswith("— warnings, building")
    assert result.reason.count("has no solution") == 4


def test_the_warnings_in2lambda_says_as_it_builds_reach_no_one_twice(
    questions_only, tmp_path, capsys
):
    # in2lambda warns about each of the four unanswered parts as it builds, and
    # Python would print each one to stderr with the line of in2lambda that
    # raised it. The validate stage line lists the same four, so the run prints
    # its stage lines and nothing else.
    with warnings.catch_warnings(record=True) as escaped:
        warnings.simplefilter("always")
        result = pipeline.run(
            questions_only / "questions-only.md",
            out_dir=tmp_path / "out",
            settings=Settings(),
            rounds=0,
        )

    assert escaped == []
    printed = capsys.readouterr().err
    assert "UserWarning" not in printed and "beartype" not in printed
    built = [stage for stage in result.stages if stage.name == "build"]
    assert not any(stage.message.startswith("warning: ") for stage in built)
    assert built[-1].message == str(result.zip_path)


def test_a_warning_the_validate_line_does_not_list_gets_a_stage_line(
    tmp_path, monkeypatch
):
    # Everything in2lambda warns inside a build is handed back, including a
    # warning no finding of the report accounts for. One of those is a line of
    # its own, before the zip.
    monkeypatch.setattr(
        package,
        "build",
        lambda draft, out_dir: package.Built(
            out_dir / "set.zip", ["q1.p1 has no solution", "something new"]
        ),
    )
    result = pipeline.RunResult()

    pipeline._build(
        tmp_path / "sheet.draft.json",
        tmp_path / "out",
        result,
        ["q1.p1 has no solution"],
    )

    assert [(stage.name, stage.message) for stage in result.stages] == [
        ("build", "warning: something new"),
        ("build", str(tmp_path / "out" / "set.zip")),
    ]


@pytest.mark.skipif(
    shutil.which("node") is None or bool(missing_tools()),
    reason="the set checks need Node for KaTeX and pandoc and xelatex to compile",
)
def test_a_problem_the_set_checks_find_reaches_the_next_round(faulty, tmp_path):
    # The first round quotes the solution the spec missed and stops there,
    # leaving the brace the OCR dropped out of it. That is in2lambda's own
    # validation of the set rather than one of the draft's own checks, and it
    # reaches the next round as a finding like any other.
    backend = FakeBackend(FAULTY_SPEC, FIXES[:-1], FIXES[-1:])

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        rounds=2,
        tries=1,
        backend=backend,
    )
    _, second_round = backend.calls[2]

    named = [
        line
        for line in second_round.splitlines()
        if line.startswith("- error problem q2.solution ")
    ]
    assert named and any(r"\mathbf{B" in line for line in named)
    assert len(result.rounds) == 2
    assert result.zip_path is not None and result.zip_path.exists()


def test_a_round_that_answers_nothing_ends_the_run_with_what_it_left(
    faulty, tmp_path
):
    # A model that answers without running a command: the report the round was
    # given is the report it left, so there is nothing for another round to do.
    backend = FakeBackend(FAULTY_SPEC, [])

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        tries=1,
        backend=backend,
    )
    last = result.stages[-1]

    assert [stage.name for stage in result.stages].count("fix") == 1
    assert [one.left for one in result.rounds] == [2]
    assert last.name == "validate"
    assert "is in no field and not marked ignore" in last.message
    assert last.message.endswith("— left by round 1, no zip")
    assert result.zip_path is None
    assert not (tmp_path / "out").exists()


def test_a_field_the_round_tried_to_write_ends_the_run_naming_it(faulty, tmp_path):
    # The round splits b7, so there is a finding no round was given before and
    # the run would otherwise go on. Its other command answers a finding by
    # writing a field, which the rounds have no command for: the run ends there
    # naming the field, whatever the limit allows.
    backend = FakeBackend(
        FAULTY_SPEC,
        [
            ("split_block", {"block": "b7", "at": 14}),
            ("field_replace", {"field": "q1.text", "old": "", "new": "A ball."}),
        ],
    )

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        rounds=3,
        tries=1,
        backend=backend,
    )
    last = result.stages[-1]

    assert [stage.name for stage in result.stages].count("fix") == 1
    assert last.name == "validate"
    assert "q1.text" in last.message
    assert "cannot be repaired by the loop" in last.message
    assert last.message.endswith("left by round 1, no zip")
    assert result.zip_path is None
    # And the field is as the spec wrote it: a refused command writes nothing.
    assert "A ball is thrown" in package.field_value(
        drafted(faulty, "faulty.md"), "q1.text"
    )


def test_a_run_still_making_progress_stops_at_the_limit_with_no_zip(faulty, tmp_path):
    # The split leaves b7b, which no round was given before: there is more for a
    # round to do, and it is the limit rather than the report that stops the run.
    backend = FakeBackend(FAULTY_SPEC, FIXES[:2])

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        rounds=1,
        tries=1,
        backend=backend,
    )
    last = result.stages[-1]

    assert [stage.name for stage in result.stages].count("fix") == 1
    assert "b7b" in last.message
    assert last.message.endswith("— round limit 1 reached, no zip")
    assert result.zip_path is None
    # The reason is one finding, not the joined line the stage printed, and not
    # the limit that stopped the rounds: it is what the draft is still faulted for.
    assert result.reason == package.validate(result.draft).errors[0]
    assert result.reason in last.message and "round limit" not in result.reason
    assert not (tmp_path / "out").exists()


def test_the_rounds_running_out_prints_its_stages_and_exits_one(
    faulty, tmp_path, monkeypatch, capsys
):
    backend = FakeBackend(FAULTY_SPEC, FIXES[:2])
    monkeypatch.setattr(pipeline, "choose_backend", lambda settings: backend)

    code = main(
        [
            "run",
            str(faulty / "faulty.md"),
            "--rounds",
            "1",
            "--tries",
            "1",
            "--out",
            str(tmp_path / "out"),
        ]
    )
    printed = capsys.readouterr().out

    assert code == 1
    assert [line.split()[0] for line in printed.splitlines()].count("fix") == 1
    assert "round limit 1 reached, no zip" in printed
    assert not (tmp_path / "out").exists()


def test_no_rounds_leaves_a_saved_spec_alone_and_stops(sheets, tmp_path):
    (sheets / SPEC_NAME).write_text(PARTLESS_SPEC)
    backend = FakeBackend(SPEC)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        rounds=0,
        backend=backend,
    )

    assert backend.calls == []
    assert result.zip_path is None
    assert (sheets / SPEC_NAME).read_text() == PARTLESS_SPEC


def test_a_saved_spec_needs_no_backend(sheets, tmp_path):
    (sheets / SPEC_NAME).write_text(SPEC)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(reason="no login"),
    )

    assert result.zip_path.exists()
    assert result.usage.output_tokens == 0


def test_writing_a_spec_without_a_backend_says_what_to_do(sheets, tmp_path):
    with pytest.raises(ModelUnavailable, match="run claude login"):
        pipeline.run(
            sheets / "sheet.md",
            out_dir=tmp_path / "out",
            settings=Settings(),
            backend=FakeBackend(reason="run claude login"),
        )


def test_the_stages_run_in_order(sheets, tmp_path):
    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(SPEC),
    )

    assert [stage.name for stage in result.stages] == [
        "ocr",
        "freeze",
        "spec",
        "coverage",
        "validate",
        # What the spec covered of the set's other sheet, which is the last
        # thing the choice between two specs is made on.
        "set",
        "review",
        "build",
    ]


def reviewed(sheets, tmp_path, mode="sample", **given):
    """A run stopped for review, with its cache and out directories under tmp."""
    (sheets / SPEC_NAME).write_text(SPEC)
    return pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        review=mode,
        cache_dir=tmp_path / "cache",
        rng=random.Random(0),
        backend=FakeBackend(),
        **given,
    )


def test_a_review_stops_the_run_with_the_questions_listed_and_no_zip(
    sheets, tmp_path
):
    result = reviewed(sheets, tmp_path)
    stages = {stage.name: stage.message for stage in result.stages}

    assert [stage.name for stage in result.stages][-3:] == [
        "validate",
        "render",
        "review",
    ]
    # Each question by its key, the PDF where one was rendered, and the lines
    # of the frozen source it was built from.
    assert f"q1 pending: not rendered, {sheets / 'sheet.md'} lines 5-5, 7-7" in (
        stages["review"]
    )
    assert "in2lambda render is not there yet" in stages["render"]
    assert "in2lambda-agent review approve Q" in stages["review"]
    # Nothing built, and no run recorded: the run is not over.
    assert result.zip_path is None
    assert not (tmp_path / "out" / "set.zip").exists()
    assert not (sheets / RECORD_NAME).exists()
    assert json.loads((tmp_path / "cache" / "review.json").read_text())["mode"] == (
        "sample"
    )


def test_a_review_of_a_question_a_literal_wrote_lists_the_lines_it_has(
    faulty, tmp_path
):
    # The faulty sheet again, except that the round found the solution's line
    # too mangled to quote: it marked that block as belonging nowhere and typed
    # the repair out instead. So q2.solution is layer 4 with no range of the
    # source behind it, which is the field the listing has to read past.
    TYPED_FIXES = [
        ("split_block", {"block": "b7", "at": 14}),
        ("question_add", {"text": "b7a"}),
        ("part_add", {"question": "q2", "text": "b7b"}),
        ("mark_ignore", {"block": "b11"}),
        (
            "question_solution",
            {
                "question": "q2",
                "literal": r"Write $\mathbf{B}$ in components and differentiate.",
            },
        ),
    ]

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        review="sample",
        cache_dir=tmp_path / "cache",
        rng=random.Random(0),
        tries=1,
        backend=FakeBackend(FAULTY_SPEC, TYPED_FIXES),
    )
    stages = {stage.name: stage.message for stage in result.stages}
    written = json.loads(drafted(faulty, "faulty.md").read_text())["fields"]

    assert (written["q2.solution"]["layer"], written["q2.solution"]["edited"]) == (
        4,
        True,
    )
    assert written["q2.solution"]["ranges"] == []
    # The run reaches the reviewer rather than the field with no ranges in it
    # stopping the listing, and q2 is named by the lines its other fields do
    # have — the typed one adds none.
    assert package.questions(drafted(faulty, "faulty.md"))["q2"].ranges == [
        [13, 13],
        [14, 14],
    ]
    assert "q2 pending: not rendered" in stages["review"]
    assert "lines 13-13, 14-14" in stages["review"]
    assert result.zip_path is None


def test_a_sample_shows_a_few_questions_and_per_question_shows_them_all(
    sheets, tmp_path
):
    sample = reviewed(sheets, tmp_path, sample=1)
    every = reviewed(sheets, tmp_path, mode="per-question")

    assert len(sample.review.questions) == 1
    assert [one.key for one in every.review.questions] == ["q1", "q2"]


def test_the_review_names_the_pdf_a_render_wrote(sheets, tmp_path, monkeypatch):
    monkeypatch.setattr(
        in2lambda.draft,
        "render",
        lambda directory, out: {"q1": f"{out}/q1.pdf", "q2": f"{out}/q2.pdf"},
        raising=False,
    )

    result = reviewed(sheets, tmp_path)
    stages = {stage.name: stage.message for stage in result.stages}

    assert stages["render"] == f"2 questions to {tmp_path / 'out' / 'render'}"
    assert str(tmp_path / "out" / "render" / "q1.pdf") in stages["review"]


def test_approving_every_question_builds_the_set_and_records_the_review(
    sheets, tmp_path
):
    waiting = reviewed(sheets, tmp_path)

    for question in list(waiting.review.questions):
        result = pipeline.resume(
            tmp_path / "cache",
            verdict="approve",
            key=question.key,
            settings=Settings(),
        )

    assert result.zip_path is not None and result.zip_path.exists()
    # The review is answered, so the record of it goes, and the run's line is
    # written with what the reviewer said in it.
    assert not (tmp_path / "cache" / "review.json").exists()
    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    recorded = json.loads(line)["review"]
    assert recorded["mode"] == "sample"
    assert [one["status"] for one in recorded["questions"]] == ["approved"] * 2
    assert recorded["rejections"] == [] and recorded["edits"] == []


def test_the_line_an_approval_writes_names_the_other_document_of_the_set(
    sheets, tmp_path
):
    # The spec loop ran before the review, so the line the last approval writes
    # says which document of the set the specs were run over, as the line a run
    # without a review writes does.
    waiting = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        review="sample",
        cache_dir=tmp_path / "cache",
        rng=random.Random(0),
        backend=FakeBackend(SPEC),
    )

    for question in list(waiting.review.questions):
        pipeline.resume(
            tmp_path / "cache",
            verdict="approve",
            key=question.key,
            settings=Settings(),
        )

    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    record = json.loads(line)
    assert record["second"] == {"name": "sheet-2.md", "passed_over": None}
    assert [one["second"] for one in record["iterations"]] == [0]


def test_a_review_is_answered_from_wherever_the_reviewer_is(
    sheets, tmp_path, monkeypatch
):
    (sheets / SPEC_NAME).write_text(SPEC)
    monkeypatch.chdir(sheets)
    waiting = pipeline.run(
        Path("sheet.md"),
        out_dir=tmp_path / "out",
        settings=Settings(),
        review="per-question",
        cache_dir=tmp_path / "cache",
        backend=FakeBackend(),
    )
    # The review commands are another process, run from wherever the reviewer
    # happens to be, so the record's paths cannot mean the run's directory.
    monkeypatch.chdir(tmp_path)

    for question in list(waiting.review.questions):
        result = pipeline.resume(
            tmp_path / "cache",
            verdict="approve",
            key=question.key,
            settings=Settings(),
        )

    assert result.zip_path is not None and result.zip_path.exists()
    assert (sheets / RECORD_NAME).is_file()


def test_a_question_that_is_not_under_review_is_refused(sheets, tmp_path):
    reviewed(sheets, tmp_path)

    with pytest.raises(ReviewError, match="q9 is not under review"):
        pipeline.resume(
            tmp_path / "cache", verdict="approve", key="q9", settings=Settings()
        )


def test_a_rejection_goes_back_to_the_fix_loop_with_the_note(sheets, tmp_path):
    waiting = reviewed(sheets, tmp_path)
    backend = FakeBackend(
        [("field_replace", {"field": "q2.p2.text", "old": "least", "new": "smallest"})]
    )

    result = pipeline.resume(
        tmp_path / "cache",
        verdict="reject",
        key="q2",
        note="part (b) asks for the smallest coefficient",
        settings=Settings(),
        backend=backend,
    )
    stages = [stage.name for stage in result.stages]

    # Exactly one round, with the note in what the model was asked, and the
    # checks run again after it.
    assert len(backend.calls) == 1
    assert "The reviewer rejected q2: part (b) asks" in backend.calls[0][1]
    assert stages.count("fix") == 1
    assert [one.number for one in result.rounds] == [1]
    assert result.stages[stages.index("fix") + 1].message == "nothing to report"
    # And q2 is back in front of the reviewer, with the note saying why.
    assert result.zip_path is None
    saved = pipeline.Review.load(tmp_path / "cache" / "review.json")
    assert saved.question("q2").status == "pending"
    assert saved.rejections == [
        {"key": "q2", "note": "part (b) asks for the smallest coefficient"}
    ]
    assert "smallest coefficient" in saved.listing()
    assert waiting.review.question("q2").lines == saved.question("q2").lines


def test_a_rejection_with_no_rounds_left_says_so_rather_than_doing_nothing(
    sheets, tmp_path
):
    reviewed(sheets, tmp_path, rounds=0)
    logged = package.command_log(drafted(sheets, "sheet.md"))

    # No backend, and none to be had: a run with no rounds in it never asks for
    # one, so a machine with no key can still record what the reviewer said.
    result = pipeline.resume(
        tmp_path / "cache",
        verdict="reject",
        key="q2",
        note="part (b) is wrong",
        settings=Settings(),
    )
    stages = {stage.name: stage.message for stage in result.stages}

    assert "no rounds left" in stages["fix"]
    # Nothing was run, so the draft is as it was and q2 comes back unchanged —
    # but the note is in the record, so it says the reviewer objected and why.
    assert package.command_log(drafted(sheets, "sheet.md")) == logged
    saved = pipeline.Review.load(tmp_path / "cache" / "review.json")
    assert saved.question("q2").status == "pending"
    assert saved.rejections == [{"key": "q2", "note": "part (b) is wrong"}]
    assert "part (b) is wrong" in stages["review"]


def test_a_reviewers_edit_is_logged_as_theirs_and_leaves_the_question_waiting(
    sheets, tmp_path
):
    reviewed(sheets, tmp_path)

    result = pipeline.resume(
        tmp_path / "cache",
        verdict="edit",
        field="q1.text",
        old="ball",
        new="stone",
        by="ada",
        settings=Settings(),
    )
    fields = json.loads(drafted(sheets, "sheet.md").read_text())["fields"]

    assert package.command_log(drafted(sheets, "sheet.md"))[-1] == {
        "command": "field replace",
        "args": {"field": "q1.text", "old": "ball", "new": "stone"},
        "by": "ada",
    }
    assert fields["q1.text"]["edited"] is True and "stone" in fields["q1.text"]["value"]
    assert result.zip_path is None
    saved = pipeline.Review.load(tmp_path / "cache" / "review.json")
    assert saved.edits == [{"field": "q1.text", "by": "ada"}]
    assert saved.question("q1").status == "pending"


# An edit that leaves a field holding nothing, which is the shortest way for a
# reviewer to put a draft the checks fault in front of the next command.
EMPTIES = {
    "field": "q1.text",
    "old": r"A ball is thrown straight up at $20\,\mathrm{m/s}$.",
    "new": " ",
}


def test_an_edit_that_faults_the_draft_says_so_in_the_listing(sheets, tmp_path):
    reviewed(sheets, tmp_path)

    result = pipeline.resume(
        tmp_path / "cache", verdict="edit", **EMPTIES, by="ada", settings=Settings()
    )

    assert "q1.text (lines 5-5) is empty." in result.stages[-1].message
    assert "the checks fault the draft" in result.stages[-1].message
    saved = pipeline.Review.load(tmp_path / "cache" / "review.json")
    assert saved.errors == ["q1.text (lines 5-5) is empty."]


def test_a_rejection_the_rounds_cannot_answer_leaves_the_fault_in_the_listing(
    sheets, tmp_path
):
    reviewed(sheets, tmp_path, rounds=1)
    # A round that makes things worse rather than better: the checks were quiet
    # when the reviewer was asked, and are not when they answer. The round
    # repairs wording, since a round may not replace the whole of a field as the
    # reviewer's own edit does, and the repair drops a closing $.
    backend = FakeBackend(
        [("field_replace", {"field": "q1.text", "old": r"m/s}$.", "new": "m/s}."})]
    )

    result = pipeline.resume(
        tmp_path / "cache",
        verdict="reject",
        key="q2",
        note="part (b) answers the wrong question",
        settings=Settings(),
        backend=backend,
    )

    assert "the checks fault the draft" in result.stages[-1].message
    saved = pipeline.Review.load(tmp_path / "cache" / "review.json")
    assert "q1.text (lines 5-5): unclosed inline $ ... $" in saved.errors
    assert saved.question("q2").status == "pending"


def test_approving_a_draft_the_checks_fault_refuses_to_build_it(sheets, tmp_path):
    reviewed(sheets, tmp_path)
    pipeline.resume(
        tmp_path / "cache", verdict="edit", **EMPTIES, by="ada", settings=Settings()
    )

    for key in ("q1", "q2"):
        result = pipeline.resume(
            tmp_path / "cache", verdict="approve", key=key, settings=Settings()
        )
    checked = [stage for stage in result.stages if stage.name == "validate"][-1]

    # Every question approved, and still no zip: the design spec builds only
    # after validate returns clean, and the last line says what it found.
    assert checked.message == "q1.text (lines 5-5) is empty."
    assert result.zip_path is None
    assert not (tmp_path / "out" / "set.zip").exists()
    assert "build" not in [stage.name for stage in result.stages]
    # And the run is not over: the review is there to answer again, and no run
    # record claims a set was made.
    assert (tmp_path / "cache" / "review.json").exists()
    assert not (sheets / RECORD_NAME).exists()


def test_a_refused_build_is_a_stage_line_and_the_review_stays(
    sheets, tmp_path, monkeypatch
):
    reviewed(sheets, tmp_path)
    # The checks pass and in2lambda still will not write the set out.
    monkeypatch.setattr(
        package,
        "build",
        lambda draft, out_dir: (_ for _ in ()).throw(
            package.BuildRefused("figures/ball.png is not beside the draft")
        ),
    )

    for key in ("q1", "q2"):
        result = pipeline.resume(
            tmp_path / "cache", verdict="approve", key=key, settings=Settings()
        )
    build = [stage for stage in result.stages if stage.name == "build"][-1]

    assert build.message == "refused: figures/ball.png is not beside the draft"
    assert result.zip_path is None
    assert (tmp_path / "cache" / "review.json").exists()


def test_what_a_rejection_cost_is_in_the_run_record(sheets, tmp_path):
    reviewed(sheets, tmp_path)
    pipeline.resume(
        tmp_path / "cache",
        verdict="reject",
        key="q1",
        note="the greatest height is (a), not the stem",
        settings=Settings(),
        backend=FakeBackend([]),
    )
    for key in ("q1", "q2"):
        pipeline.resume(
            tmp_path / "cache", verdict="approve", key=key, settings=Settings()
        )

    (line,) = (sheets / RECORD_NAME).read_text().splitlines()
    recorded = json.loads(line)

    # The round the rejection caused is one of the run's rounds, and what it
    # cost is in the run's total.
    assert [one["round"] for one in recorded["rounds"]] == [1]
    assert recorded["output_tokens"] > 0
    assert recorded["review"]["rejections"][0]["key"] == "q1"


def test_a_review_mode_none_builds_at_once(sheets, tmp_path):
    result = reviewed(sheets, tmp_path, mode="none")
    review = next(stage for stage in result.stages if stage.name == "review")

    assert review.message == "not asked for (mode none)"
    assert result.review is None
    assert result.zip_path.exists()
    assert not (tmp_path / "cache" / "review.json").exists()


def test_the_set_is_written_where_the_run_was_told_to(sheets, tmp_path, monkeypatch):
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(SPEC),
    )

    # The zip the build stage names is the one on disk, in the given directory.
    assert result.zip_path == tmp_path / "out" / "set.zip"
    assert result.zip_path.exists()
    # The cache is the one thing the run writes under the working directory,
    # since `--cache` defaults to a folder there, and it holds the copy of the
    # set's other sheet each spec was run over.
    assert [one.name for one in working.iterdir()] == [".in2lambda-agent"]
    assert (working / ".in2lambda-agent" / "second" / "sheet-2.md").is_file()


def test_a_relative_out_dir_is_resolved_against_the_working_directory(
    sheets, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=Path("out"),
        settings=Settings(),
        backend=FakeBackend(SPEC),
    )

    assert result.zip_path == tmp_path / "out" / "set.zip"
    assert result.zip_path.exists()


def test_a_markdown_source_needs_no_ocr(sheets, tmp_path):
    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(SPEC),
    )
    ocr = next(stage for stage in result.stages if stage.name == "ocr")

    assert ocr.message == "not needed for sheet.md"


def test_a_pdf_is_converted_and_the_run_carries_on_from_the_markdown(pdf, tmp_path):
    client = FakeMathpix(markdown=SOURCE.read_text())

    result = pipeline.run(
        pdf,
        out_dir=tmp_path / "out",
        settings=Settings(),
        cache_dir=tmp_path / "cache",
        mathpix=client,
        backend=FakeBackend(SPEC),
    )
    stages = {stage.name: stage.message for stage in result.stages}

    assert "fresh pass" in stages["ocr"] and "source.md" in stages["ocr"]
    # The freeze read the OCR's markdown, not the PDF; the spec is kept with
    # the PDF, which is the document set, rather than in the cache entry.
    assert "cache" in stages["freeze"]
    assert stages["spec"].startswith(f"wrote {tmp_path / SPEC_NAME}")
    assert result.zip_path.exists()
    assert len(client.calls) == 1


def test_a_second_run_over_the_same_pdf_uses_the_cache(pdf, tmp_path):
    client = FakeMathpix(markdown=SOURCE.read_text())
    backend = FakeBackend(SPEC)
    for _ in range(2):
        result = pipeline.run(
            pdf,
            out_dir=tmp_path / "out",
            settings=Settings(),
            cache_dir=tmp_path / "cache",
            mathpix=client,
            backend=backend,
        )
    ocr = next(stage for stage in result.stages if stage.name == "ocr")

    assert ocr.message.startswith("cached ")
    assert len(client.calls) == 1
    assert len(backend.calls) == 1


def test_a_pdf_without_credentials_exits_one_naming_the_variables(
    pdf, tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MATHPIX_APP_ID", raising=False)
    monkeypatch.delenv("MATHPIX_API_KEY", raising=False)

    code = main(["run", str(pdf), "--out", str(tmp_path / "out")])
    printed = capsys.readouterr()

    assert code == 1
    assert "MATHPIX_APP_ID and MATHPIX_API_KEY" in printed.err
    assert printed.out == ""


def test_the_command_exits_zero_and_prints_a_line_per_stage(sheets, tmp_path, capsys):
    (sheets / SPEC_NAME).write_text(SPEC)

    code = main(["run", str(sheets / "sheet.md"), "--out", str(tmp_path / "out")])
    printed = capsys.readouterr().out.splitlines()

    assert code == 0
    assert [line.split()[0] for line in printed] == [
        "ocr",
        "freeze",
        "spec",
        "coverage",
        "validate",
        "review",
        "build",
    ]
    assert (tmp_path / "out" / "set.zip").exists()


def test_a_review_run_and_its_approvals_exit_zero_and_build(sheets, tmp_path, capsys):
    (sheets / SPEC_NAME).write_text(SPEC)
    where = ["--cache", str(tmp_path / "cache")]
    out = ["--out", str(tmp_path / "out")]

    stopped = main(["run", str(sheets / "sheet.md"), "--review", "sample", *where, *out])
    printed = capsys.readouterr().out

    # Waiting for a reviewer is not a failure, and nothing is built yet.
    assert stopped == 0
    assert "q1 pending" in printed and "q2 pending" in printed
    assert not (tmp_path / "out" / "set.zip").exists()

    assert main(["review", "approve", "q1", *where]) == 0
    assert main(["review", "approve", "q2", *where]) == 0

    assert (tmp_path / "out" / "set.zip").exists()
    assert "build" in capsys.readouterr().out


def test_approving_a_draft_the_checks_fault_exits_one_saying_what_they_found(
    sheets, tmp_path, capsys
):
    (sheets / SPEC_NAME).write_text(SPEC)
    where = ["--cache", str(tmp_path / "cache")]
    out = ["--out", str(tmp_path / "out")]

    main(["run", str(sheets / "sheet.md"), "--review", "sample", *where, *out])
    main(["review", "edit", EMPTIES["field"], EMPTIES["old"], EMPTIES["new"], *where])
    capsys.readouterr()

    assert main(["review", "approve", "q1", *where]) == 0
    # Nothing left to answer and nothing built, which is a failure like any
    # other build that did not happen.
    assert main(["review", "approve", "q2", *where]) == 1
    assert "q1.text (lines 5-5) is empty." in capsys.readouterr().out
    assert not (tmp_path / "out" / "set.zip").exists()


def test_a_review_command_with_no_review_waiting_exits_one(tmp_path, capsys):
    code = main(["review", "approve", "q1", "--cache", str(tmp_path / "cache")])
    printed = capsys.readouterr()

    assert code == 1
    assert "No review is waiting" in printed.err
    assert printed.out == ""


def test_the_default_out_is_the_working_directorys_out(sheets, tmp_path, monkeypatch):
    (sheets / SPEC_NAME).write_text(SPEC)
    monkeypatch.chdir(tmp_path)

    assert main(["run", str(sheets / "sheet.md")]) == 0
    assert (tmp_path / "out" / "set.zip").exists()


def test_a_run_the_checks_fault_prints_its_stages_and_exits_one(
    sheets, tmp_path, capsys
):
    (sheets / SPEC_NAME).write_text(PARTLESS_SPEC)

    code = main(
        ["run", str(sheets / "sheet.md"), "--rounds", "0", "--out", str(tmp_path)]
    )
    printed = capsys.readouterr()

    assert code == 1
    assert "is in no field and not marked ignore" in printed.out
    assert not (tmp_path / "set.zip").exists()


def test_a_run_with_no_backend_exits_one_naming_what_to_do(
    sheets, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(
        pipeline, "choose_backend", lambda settings: FakeBackend(reason="run claude login")
    )

    code = main(["run", str(sheets / "sheet.md"), "--out", str(tmp_path / "out")])
    printed = capsys.readouterr()

    assert code == 1
    assert "run claude login" in printed.err
    assert printed.out == ""


def test_a_build_in2lambda_refuses_ends_in_one_stage_line_with_no_zip(
    figures, tmp_path, monkeypatch
):
    out_dir = tmp_path / "out"
    # The checks pass and in2lambda still will not write the set out. Stubbed
    # because the refusals it has today — a missing image among them — are
    # findings of the report now, which is a faulted draft and not this.
    monkeypatch.setattr(
        package,
        "build",
        lambda draft, out: (_ for _ in ()).throw(
            package.BuildRefused("figures/ball.png is not beside the draft")
        ),
    )

    result = pipeline.run(
        figures / "figure.md", out_dir=out_dir, settings=Settings()
    )
    build = result.stages[-1]

    assert build.name == "build"
    assert build.message.startswith("refused: ")
    assert "figures/ball.png" in build.message
    assert result.zip_path is None
    # The checks came clean and the export refused: no zip, but nothing faulted,
    # which is what a run of many documents has to tell apart.
    assert result.clean is True
    # And why, without the stage line's prefix, for a table to be read on its own.
    assert result.reason == build.message.removeprefix("refused: ")
    assert "figures/ball.png" in result.reason
    assert not list(out_dir.glob("*.zip"))
    # The run still ends the way any other does, with its record beside the spec.
    assert (figures / RECORD_NAME).is_file()


def test_a_refused_build_prints_its_stages_and_exits_one(
    figures, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(
        package,
        "build",
        lambda draft, out: (_ for _ in ()).throw(
            package.BuildRefused("figures/ball.png is not beside the draft")
        ),
    )

    code = main(["run", str(figures / "figure.md"), "--out", str(tmp_path / "out")])
    printed = capsys.readouterr()

    assert code == 1
    assert [line.split()[0] for line in printed.out.splitlines()] == [
        "ocr",
        "freeze",
        "spec",
        "coverage",
        "validate",
        "review",
        "build",
    ]
    assert "refused:" in printed.out and "figures/ball.png" in printed.out
    assert printed.err == ""
    assert not (tmp_path / "out" / "set.zip").exists()


def test_a_source_beside_its_figures_builds_with_the_images_in_media(
    figures, tmp_path
):
    result = pipeline.run(
        figures / "figure.md", out_dir=tmp_path / "out", settings=Settings()
    )

    assert result.stages[-1].message == str(result.zip_path)
    assert "media/ball.png" in zipfile.ZipFile(result.zip_path).namelist()


def test_a_sheet_and_its_solutions_are_frozen_into_one_draft(paired, tmp_path):
    result = pipeline.run(
        paired / "paired.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(PAIRED_SPEC),
    )
    frozen = json.loads(result.draft.read_text())

    assert result.draft == drafted(paired, "paired.md")
    assert [one["source"] for one in frozen["sources"]] == [
        "paired.md",
        "paired_solutions.md",
    ]
    # Every solution was quoted out of the second source, and every question's
    # own text out of the first.
    assert {
        key: field.get("source", 1)
        for key, field in frozen["fields"].items()
        if not key.endswith(".ignore")
    } == {
        "q1.text": 1,
        "q1.p1.text": 1,
        "q1.p2.text": 1,
        "q2.text": 1,
        "q2.p1.text": 1,
        "q2.p2.text": 1,
        "q1.p1.solution": 2,
        "q1.p2.solution": 2,
        "q2.p1.solution": 2,
        "q2.p2.solution": 2,
    }
    assert result.zip_path is not None and result.zip_path.exists()


def test_the_zip_quotes_the_part_solutions_from_the_solutions_file(paired, tmp_path):
    result = pipeline.run(
        paired / "paired.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(PAIRED_SPEC),
    )

    question = json.loads(
        zipfile.ZipFile(result.zip_path).read("question_000_Question_1.json")
    )
    assert [part["workedSolution"]["content"] for part in question["parts"]] == [
        "$\\omega = v / r$",
        "$E = \\tfrac{3}{4} m v^2$",
    ]


def test_a_marker_in_the_solutions_file_writes_no_question(marked, tmp_path):
    result = pipeline.run(
        marked / "marked.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(PAIRED_SPEC),
    )
    fields = json.loads(result.draft.read_text())["fields"]

    # `Q1.` and `Q2.` match the `question` selector in the second source. The
    # draft holds the two questions the first source states, and neither marker
    # is a field.
    assert sorted(key for key in fields if key.endswith(".text")) == [
        "q1.p1.text",
        "q1.p2.text",
        "q1.text",
        "q2.p1.text",
        "q2.p2.text",
        "q2.text",
    ]
    # Each marker is ignored, so its text reaches no question of the set.
    assert fields["2/b3.ignore"]["value"] is True
    assert fields["2/b5.ignore"]["value"] is True
    # Each marker sends the solutions below it to its own question, so question
    # 2's solutions answer question 2's parts. Question 1's part (b) is
    # unanswered, which is a warning the build runs after.
    assert {
        key: field["source"] for key, field in fields.items() if ".solution" in key
    } == {
        "q1.p1.solution": 2,
        "q2.p1.solution": 2,
        "q2.p2.solution": 2,
    }
    assert result.zip_path is not None and result.zip_path.exists()


def test_the_spec_prompt_shows_both_sources(paired, tmp_path):
    backend = FakeBackend(PAIRED_SPEC)

    pipeline.run(
        paired / "paired.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=backend,
    )
    (_, prompt) = backend.calls[0]

    assert "whose blocks are `2/b1` onwards" in prompt
    assert "Source 1: paired.md" in prompt
    assert "Source 2: paired_solutions.md" in prompt
    assert "2/b3   5  1(a)" in prompt


def test_naming_the_solutions_file_runs_the_questions_file(paired, tmp_path):
    result = pipeline.run(
        paired / "paired_solutions.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(PAIRED_SPEC),
    )
    freeze = next(stage for stage in result.stages if stage.name == "freeze")

    assert result.draft == drafted(paired, "paired.md")
    assert freeze.message == (
        f"{drafted(paired, 'paired.md')}, with paired_solutions.md as source 2"
    )
    # The record is the questions file's, so the two names are one run.
    (line,) = (paired / RECORD_NAME).read_text().splitlines()
    assert json.loads(line)["source"] == str(paired / "paired.md")
    assert result.zip_path.exists()


def test_solutions_with_no_questions_beside_them_stop_the_run(tmp_path):
    folder = tmp_path / "lone"
    folder.mkdir()
    shutil.copy(FIXTURES / "paired_solutions.md", folder / "paired_solutions.md")

    with pytest.raises(
        pair.SolutionsWithoutQuestions,
        match="solutions without questions: nothing named paired.md",
    ):
        pipeline.run(
            folder / "paired_solutions.md",
            out_dir=tmp_path / "out",
            settings=Settings(),
            backend=FakeBackend(PAIRED_SPEC),
        )


def test_a_lone_solutions_file_exits_one_saying_so(tmp_path, capsys):
    folder = tmp_path / "lone"
    folder.mkdir()
    shutil.copy(FIXTURES / "paired_solutions.md", folder / "paired_solutions.md")

    code = main(
        ["run", str(folder / "paired_solutions.md"), "--out", str(tmp_path / "out")]
    )
    printed = capsys.readouterr()

    assert code == 1
    assert "solutions without questions" in printed.err
    assert printed.out == ""


def test_a_pair_of_pdfs_is_converted_and_frozen_into_one_draft(tmp_path):
    for name in ("paired.pdf", "paired_solutions.pdf"):
        (tmp_path / name).write_bytes(f"%PDF-1.4 {name}".encode())
    client = PairedMathpix()

    result = pipeline.run(
        tmp_path / "paired.pdf",
        out_dir=tmp_path / "out",
        settings=Settings(),
        cache_dir=tmp_path / "cache",
        mathpix=client,
        backend=FakeBackend(PAIRED_SPEC),
    )
    frozen = json.loads(result.draft.read_text())

    assert [one.name for one in client.calls] == [
        "paired.pdf",
        "paired_solutions.pdf",
    ]
    # Each PDF has a cache entry of its own, and a draft holds the documents of
    # one directory, so the solutions markdown is copied beside the questions.
    assert [one["source"] for one in frozen["sources"]] == [
        "source.md",
        "paired_solutions.md",
    ]
    # The images came with it, and under a name of their own: both conversions
    # call their figure media/plot.png, and the set holds each of them.
    assert result.zip_path.exists()
    assert [
        one
        for one in zipfile.ZipFile(result.zip_path).namelist()
        if one.startswith("media/")
    ] == ["media/plot.png", "media/question_000_Question_1_0001.png"]


def test_a_pdf_with_a_figure_builds_with_the_image_in_media(pdf, tmp_path):
    # As the OCR leaves a converted PDF: the image under the cache entry's
    # media/, and the markdown referring to it by that folder and its name.
    markdown = (FIXTURES / "figure.md").read_text().replace("figures/ball", "media/plot")

    result = pipeline.run(
        pdf,
        out_dir=tmp_path / "out",
        settings=Settings(),
        cache_dir=tmp_path / "cache",
        mathpix=FakeMathpix(markdown=markdown),
        backend=FakeBackend(SPEC),
    )

    assert result.zip_path is not None
    assert "media/plot.png" in zipfile.ZipFile(result.zip_path).namelist()


def test_on_stage_is_called_with_each_stage_of_a_run(sheets, tmp_path):
    (sheets / SPEC_NAME).write_text(SPEC)
    watched = []

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=FakeBackend(),
        # The name and the message, rather than the stage itself: the run's own
        # list holds those objects, so comparing the two lists of them would
        # hold whatever was appended and prove nothing.
        on_stage=lambda stage: watched.append((stage.name, stage.message)),
    )

    assert watched == [(stage.name, stage.message) for stage in result.stages]
    assert watched[0][0] == "ocr"


def test_on_stage_is_called_with_the_stages_of_a_rejection(sheets, tmp_path):
    reviewed(sheets, tmp_path)
    watched = []

    result = pipeline.resume(
        tmp_path / "cache",
        verdict="reject",
        key="q2",
        note="part (b) asks for the smallest coefficient",
        settings=Settings(),
        backend=FakeBackend(
            [("field_replace", {"field": "q2.p2.text", "old": "least", "new": "small"})]
        ),
        on_stage=lambda stage: watched.append((stage.name, stage.message)),
    )

    assert watched == [(stage.name, stage.message) for stage in result.stages]
    assert "fix" in [name for name, _ in watched]
