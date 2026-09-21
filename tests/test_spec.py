"""Layer 1: where a set's spec lives, and the one call that writes it."""

import json
from pathlib import Path

import pytest
from conftest import FakeBackend

from in2lambda_agent.model import Usage
from in2lambda_agent.package import Coverage, Report
from in2lambda_agent.spec import (
    SPEC_NAME,
    BadSpec,
    Previous,
    Second,
    SpecTry,
    record_run,
    spec_path,
    write_spec,
)

SPEC = (Path(__file__).parent / "fixtures" / "sheet-spec.yaml").read_text()


def test_the_sets_spec_is_beside_the_source(tmp_path):
    assert spec_path(tmp_path / "sheets" / "sheet.md") == (
        tmp_path / "sheets" / SPEC_NAME
    )


def test_every_sheet_in_one_folder_shares_a_spec(tmp_path):
    first = spec_path(tmp_path / "sheet.md")
    second = spec_path(tmp_path / "sheet-2.md")

    assert first == second


def test_a_named_spec_overrides_the_sets_own(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert spec_path(Path("sheets/sheet.md"), Path("mine.yaml")) == (
        tmp_path / "mine.yaml"
    )


def test_the_prompt_carries_the_numbered_source_as_it_was_shown():
    backend = FakeBackend(SPEC)
    shown = "b1   1  # Tutorial Sheet 1\n     2\nb2   3  A ball is thrown."

    text, reply = write_spec(shown, backend)

    (_, prompt), = backend.calls
    assert shown in prompt
    assert text == SPEC
    assert reply.usage.output_tokens > 0


def test_a_file_of_solutions_alone_is_said_to_hold_no_questions():
    backend = FakeBackend(SPEC, SPEC)

    write_spec("b1  1  ## Solutions", backend, solutions_only=True)
    write_spec("b1  1  ## Question 1", backend)

    lone, paired = (prompt for _, prompt in backend.calls)
    assert "This document holds solutions and no questions" in lone
    assert "is the question here" in lone
    assert "holds solutions and no questions" not in paired


def test_a_revision_carries_the_last_spec_and_what_running_it_covered():
    backend = FakeBackend(SPEC)
    previous = Previous(
        text="question: Para\nlayout: PartsSepSol\n",
        coverage=Coverage(
            layout="PartsSepSol", blocks=14, fields={1: 9}, ignored=4,
            unassigned=["b4"],
        ),
        report=Report(clean=False, errors=["b4 (lines 7-7) is in no field."]),
        second=Coverage(layout="PartsSepSol", blocks=9, unassigned=["b5", "b6"]),
        second_name="sheet-2.md",
    )

    write_spec("b1  1  # Sheet", backend, previous)

    (_, prompt), = backend.calls
    assert "question: Para\nlayout: PartsSepSol\n" in prompt
    assert "b4 unassigned" in prompt
    assert "b4 (lines 7-7) is in no field." in prompt
    assert "over sheet-2.md, another document of this set, left b5, b6" in prompt


def test_a_revision_of_a_spec_that_covered_the_set_says_so():
    backend = FakeBackend(SPEC)
    previous = Previous(
        text="question: Para\nlayout: PartsSepSol\n",
        second=Coverage(layout="PartsSepSol", blocks=9),
        second_name="sheet-2.md",
    )

    write_spec("b1  1  # Sheet", backend, previous)

    (_, prompt), = backend.calls
    assert "left no blocks in no field" in prompt


def test_a_spec_in_a_code_fence_is_unwrapped():
    backend = FakeBackend(f"```yaml\n{SPEC}```\n")

    text, _ = write_spec("b1  1  # Sheet", backend)

    assert text == SPEC


def test_a_reply_that_is_not_a_mapping_is_refused():
    backend = FakeBackend("I am afraid I cannot write a spec for this.\n")

    with pytest.raises(BadSpec, match="a mapping of selectors"):
        write_spec("b1  1  # Sheet", backend)


def test_a_reply_that_is_not_yaml_is_refused():
    backend = FakeBackend("question: Para\n  layout: 'unclosed\n")

    with pytest.raises(BadSpec, match="not YAML"):
        write_spec("b1  1  # Sheet", backend)


def test_a_spec_naming_no_layout_is_refused_by_name():
    backend = FakeBackend("question: Para\n")

    with pytest.raises(BadSpec, match="names no layout"):
        write_spec("b1  1  # Sheet", backend)


def test_a_spec_naming_something_that_is_not_a_layout_is_refused_by_name():
    backend = FakeBackend("question: Para\nlayout: PartsThenSols\n")

    with pytest.raises(BadSpec, match="'PartsThenSols' is not a layout"):
        write_spec("b1  1  # Sheet", backend)


def test_each_run_appends_one_line_saying_what_the_spec_covered(tmp_path):
    record = tmp_path / "runs.jsonl"
    coverage = Coverage(
        layout="PartsSepSol",
        blocks=14,
        fields={1: 10},
        ignored=4,
        unassigned=["b13"],
    )

    record_run(
        record,
        Path("sheet.md"),
        reused=False,
        coverage=coverage,
        usage=Usage(input_tokens=900, output_tokens=80, seconds=2.5),
    )
    record_run(
        record,
        Path("sheet-2.md"),
        reused=True,
        coverage=coverage,
        usage=Usage(),
    )

    first, second = (json.loads(line) for line in record.read_text().splitlines())
    assert first["source"] == "sheet.md"
    assert first["reused"] is False
    assert first["layout"] == "PartsSepSol"
    assert (first["blocks"], first["fields"], first["ignored"]) == (14, {"1": 10}, 4)
    assert first["unassigned"] == ["b13"]
    assert (first["input_tokens"], first["output_tokens"]) == (900, 80)
    assert second["reused"] is True and second["output_tokens"] == 0
    # A run that reused the set's spec wrote none, so it iterated over nothing.
    assert first["iterations"] == [] and second["iterations"] == []


def test_the_record_says_what_each_spec_the_run_wrote_covered_and_cost(tmp_path):
    record = tmp_path / "runs.jsonl"
    coverage = Coverage(layout="PartsSepSol", blocks=14, fields={1: 10})

    record_run(
        record,
        Path("sheet.md"),
        reused=False,
        coverage=coverage,
        usage=Usage(input_tokens=900, output_tokens=80, seconds=2.5),
        tries=[
            SpecTry(number=0, unassigned=2, errors=2),
            SpecTry(
                number=1,
                usage=Usage(input_tokens=900, output_tokens=80, seconds=2.5),
                unassigned=0,
                errors=0,
                second=1,
            ),
            SpecTry(
                number=2,
                usage=Usage(input_tokens=950, output_tokens=70, seconds=2.0),
                chosen=True,
            ),
        ],
    )

    (line,) = record.read_text().splitlines()
    saved, first, second = json.loads(line)["iterations"]

    # The saved spec the rewrite started from, which cost no call of its own.
    assert saved == {
        "try": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "seconds": 0.0,
        "unassigned": 2,
        "errors": 2,
        "second": None,
        "chosen": False,
    }
    assert (first["input_tokens"], first["output_tokens"]) == (900, 80)
    assert (first["second"], first["chosen"]) == (1, False)
    assert (second["try"], second["seconds"], second["chosen"]) == (2, 2.0, True)


def test_the_record_names_the_other_document_of_the_set(tmp_path):
    record = tmp_path / "runs.jsonl"
    coverage = Coverage(layout="PartsSepSol", blocks=14, fields={1: 10})

    for second in (
        Second("sheet-2.md", path=tmp_path / "second" / "sheet-2.md"),
        Second("sheet-2.pdf", passed_over="converting it takes an OCR call"),
        None,
    ):
        record_run(
            record,
            Path("sheet.md"),
            reused=False,
            coverage=coverage,
            usage=Usage(),
            second=second,
        )

    over, passed, alone = [json.loads(line) for line in record.read_text().splitlines()]

    assert over["second"] == {"name": "sheet-2.md", "passed_over": None}
    assert passed["second"] == {
        "name": "sheet-2.pdf",
        "passed_over": "converting it takes an OCR call",
    }
    # Null is the folder holding no other document, and nothing else.
    assert alone["second"] is None
