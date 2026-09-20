"""The end-to-end run: a markdown or PDF source in, a Lambda Feedback zip out."""

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from conftest import FakeBackend, FakeMathpix

from in2lambda_agent import package, pipeline
from in2lambda_agent.cli import main
from in2lambda_agent.model import ModelUnavailable
from in2lambda_agent.package import SpecRejected
from in2lambda_agent.settings import Settings
from in2lambda_agent.spec import RECORD_NAME, SPEC_NAME

FIXTURES = Path(__file__).parent / "fixtures"
SOURCE = FIXTURES / "sheet.md"
SPEC = (FIXTURES / "sheet-spec.yaml").read_text()
TEX_SPEC = (FIXTURES / "tex-sheet-spec.yaml").read_text()
FAULTY_SPEC = (FIXTURES / "faulty-spec.yaml").read_text()

# What a model would run over the faulty sheet: the merged block cut in two and
# each half quoted, and the two solutions the spec's selector missed given to the
# questions they answer. Every one of them copies, so every field is layer 3.
FIXES = [
    ("split_block", {"block": "b7", "at": 14}),
    ("question_add", {"text": "b7a"}),
    ("part_add", {"question": "q2", "text": "b7b"}),
    ("question_solution", {"question": "q1", "text": "b10"}),
    ("question_solution", {"question": "q2", "text": "b11"}),
]

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


def test_the_rounds_fix_what_the_checks_found_and_the_run_builds(faulty, tmp_path):
    backend = FakeBackend(FAULTY_SPEC, FIXES)

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
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
        "question solution q1, question solution q2), "
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
        backend=backend,
    )
    second = backend.calls[2][1]

    assert [one.number for one in result.rounds] == [1, 2]
    assert [one.left for one in result.rounds] == [4, 0]
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
        backend=FakeBackend(FAULTY_SPEC, FIXES),
    )
    log = package.command_log(faulty)
    fields = json.loads((faulty / package.DRAFT).read_text())["fields"]

    # The spec, and then every fix after it, all recorded by in2lambda as it
    # applied them: replaying this log rebuilds the draft with no model in it.
    assert [entry["command"] for entry in log] == [
        "spec run",
        "split block",
        "question add",
        "part add",
        "question solution",
        "question solution",
    ]
    assert {entry["by"] for entry in log} == {package.BY}
    # Every fix copied a range of the source, so every field it wrote is layer 3
    # and none of them is marked as edited.
    written = ["q2.text", "q2.p1.text", "q1.solution", "q2.solution"]
    assert [fields[key]["layer"] for key in written] == [3, 3, 3, 3]
    assert not any(fields[key]["edited"] for key in written)
    # And the fields the spec wrote are still layer 1.
    assert fields["q1.text"]["layer"] == 1


def test_the_record_says_what_each_round_cost(faulty, tmp_path):
    pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
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
        backend=backend,
    )
    refused = result.rounds[0].commands[0]

    assert "was refused" in refused.result and "no block b99" in refused.result
    # The refusal is what the model was told, and it went on to fix the draft.
    assert result.zip_path is not None and result.zip_path.exists()
    assert "b99" not in str(package.command_log(faulty))


def test_a_run_the_rounds_cannot_fix_stops_at_the_limit_with_no_zip(faulty, tmp_path):
    # A model that answers without running a command: nothing is fixed, and the
    # rounds run out on the same report they started with.
    backend = FakeBackend(FAULTY_SPEC, [], [], [])

    result = pipeline.run(
        faulty / "faulty.md",
        out_dir=tmp_path / "out",
        settings=Settings(),
        backend=backend,
    )
    last = result.stages[-1]

    assert [stage.name for stage in result.stages].count("fix") == 3
    assert [one.left for one in result.rounds] == [4, 4, 4]
    assert last.name == "validate"
    assert "is in no field and not marked ignore" in last.message
    assert last.message.endswith("— round limit 3 reached, no zip")
    assert result.zip_path is None
    assert not (tmp_path / "out").exists()


def test_the_rounds_running_out_prints_its_stages_and_exits_one(
    faulty, tmp_path, monkeypatch, capsys
):
    backend = FakeBackend(FAULTY_SPEC, [], [], [])
    monkeypatch.setattr(pipeline, "choose_backend", lambda settings: backend)

    code = main(["run", str(faulty / "faulty.md"), "--out", str(tmp_path / "out")])
    printed = capsys.readouterr().out

    assert code == 1
    assert [line.split()[0] for line in printed.splitlines()].count("fix") == 3
    assert "round limit 3 reached, no zip" in printed
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
