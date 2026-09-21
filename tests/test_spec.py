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


def test_a_rewrite_carries_what_the_checks_found():
    backend = FakeBackend(SPEC)
    report = Report(clean=False, errors=["b4 (lines 7-7) is in no field."])

    write_spec("b1  1  # Sheet", backend, report)

    (_, prompt), = backend.calls
    assert "b4 (lines 7-7) is in no field." in prompt


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
