"""The end-to-end run: a markdown source in, a Lambda Feedback zip out."""

import zipfile
from pathlib import Path

from in2lambda_agent import pipeline
from in2lambda_agent.cli import main
from in2lambda_agent.settings import Settings

SOURCE = Path(__file__).parent / "fixtures" / "algorithmic.md"


def test_a_run_writes_a_zip_holding_the_questions_the_layout_found(tmp_path):
    result = pipeline.run(SOURCE, out_dir=tmp_path, settings=Settings())

    assert result.zip_path is not None
    assert result.zip_path.exists()
    # A source the layout finds nothing in still writes a zip, but one holding
    # set_set.json alone, so name the questions the fixture's headings give.
    assert zipfile.ZipFile(result.zip_path).namelist() == [
        "question_000_Question_1.json",
        "question_001_Question_2.json",
        "set_set.json",
    ]


def test_the_set_is_written_where_the_run_was_told_to(tmp_path, monkeypatch):
    working = tmp_path / "working"
    working.mkdir()
    monkeypatch.chdir(working)

    result = pipeline.run(SOURCE, out_dir=tmp_path / "out", settings=Settings())

    # The zip the build stage names is the one on disk, in the given directory.
    assert result.zip_path == tmp_path / "out" / "set.zip"
    assert result.zip_path.exists()
    assert list(working.iterdir()) == []


def test_the_stages_run_in_order(tmp_path):
    result = pipeline.run(SOURCE, out_dir=tmp_path, settings=Settings())

    assert [stage.name for stage in result.stages] == [
        "freeze",
        "spec",
        "layout",
        "validate",
        "review",
        "build",
    ]


def test_each_stub_names_what_it_waits_for_and_the_run_carries_on(tmp_path):
    result = pipeline.run(
        SOURCE, out_dir=tmp_path, settings=Settings(), review="sample", rounds=4
    )
    messages = {stage.name: stage.message for stage in result.stages}

    assert "in2lambda source add" in messages["freeze"]
    assert "in2lambda spec run" in messages["spec"]
    assert "in2lambda validate" in messages["validate"]
    assert "model stages" in messages["review"]
    assert "sample" in messages["review"] and "round limit 4" in messages["review"]
    # None of them stopped the run.
    assert result.zip_path is not None and result.zip_path.exists()


def test_a_spec_is_named_but_not_yet_run(tmp_path):
    result = pipeline.run(
        SOURCE, out_dir=tmp_path, settings=Settings(), spec=Path("sheet.yaml")
    )
    spec = next(stage for stage in result.stages if stage.name == "spec")

    assert "sheet.yaml" in spec.message
    assert pipeline.DEFAULT_LAYOUT in spec.message


def test_the_command_exits_zero_and_prints_a_line_per_stage(tmp_path, capsys):
    code = main(["run", str(SOURCE), "--out", str(tmp_path)])
    printed = capsys.readouterr().out.splitlines()

    assert code == 0
    assert [line.split()[0] for line in printed] == [
        "freeze",
        "spec",
        "layout",
        "validate",
        "review",
        "build",
    ]
    assert (tmp_path / "set.zip").exists()
