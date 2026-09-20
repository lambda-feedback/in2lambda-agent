"""The end-to-end run: a markdown or PDF source in, a Lambda Feedback zip out."""

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from conftest import FakeBackend, FakeMathpix

from in2lambda_agent import pipeline
from in2lambda_agent.cli import main
from in2lambda_agent.model import ModelUnavailable
from in2lambda_agent.package import SpecRejected
from in2lambda_agent.settings import Settings
from in2lambda_agent.spec import RECORD_NAME, SPEC_NAME

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = FIXTURES / "sheet.md"
SPEC = (FIXTURES / "sheet-spec.yaml").read_text()
TEX_SPEC = (FIXTURES / "tex-sheet-spec.yaml").read_text()

# The same spec with no `part` selector, so it runs but leaves every lettered
# part in no field: a saved spec the checks have something to say about.
PARTLESS_SPEC = "\n".join(
    line for line in SPEC.splitlines() if not line.startswith("part:")
)


@pytest.fixture
def sheets(tmp_path):
    """A document set: two sheets written the same way, in a folder of their own."""
    folder = tmp_path / "sheets"
    folder.mkdir()
    for name in ("sheet.md", "sheet-2.md"):
        shutil.copy(FIXTURES / name, folder / name)
    return folder


@pytest.fixture
def tex_sheets(tmp_path):
    """A set of tex sheets, which is the shape the corpus keeps its sets in."""
    folder = tmp_path / "tex"
    folder.mkdir()
    for name in ("tex-sheet.tex", "tex-sheet-2.tex"):
        shutil.copy(FIXTURES / name, folder / name)
    return folder


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

    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
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
        "review",
        "build",
    ]


def test_review_still_names_the_model_stages_it_waits_for(sheets, tmp_path):
    result = pipeline.run(
        sheets / "sheet.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        review="sample",
        rounds=4,
        backend=FakeBackend(SPEC),
    )
    review = next(stage for stage in result.stages if stage.name == "review")

    assert "model stages" in review.message
    assert "sample" in review.message and "round limit 4" in review.message


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
    assert list(working.iterdir()) == []


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
