"""Layer 1: where a set's spec lives, and the one call that writes it."""

import json
import shutil
from pathlib import Path

import pytest
from conftest import FakeBackend

from in2lambda_agent.model import Usage
from in2lambda_agent.package import Coverage, Finding, Report
from in2lambda_agent.spec import (
    SPEC_NAME,
    BadSpec,
    Previous,
    Second,
    SpecTry,
    iterate_spec,
    record_run,
    spec_path,
    write_spec,
)

FIXTURES = Path(__file__).parent / "fixtures"
SPEC = (FIXTURES / "sheet-spec.yaml").read_text()
IGNORES_THE_FIGURE = (FIXTURES / "figure-paragraph-spec.yaml").read_text()


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


def test_a_revision_shows_the_images_the_last_spec_ignored(tmp_path):
    backend = FakeBackend(SPEC)
    previous = Previous(
        text="question: Para\nlayout: PartsSepSol\n",
        coverage=Coverage(
            layout="PartsSepSol",
            blocks=9,
            fields={1: 5},
            ignored=4,
            dropped=[
                Finding(
                    check="coverage",
                    level="error",
                    field="b4",
                    ranges=[[7, 8]],
                    message="b4 (lines 7-8) holds an image and is marked ignore.",
                )
            ],
        ),
        report=Report(clean=True, errors=[]),
    )

    write_spec("b1  1  # Sheet", backend, previous)

    ((_, prompt),) = backend.calls
    # Under the one heading as the checks' own errors: the next call answers an
    # ignored figure the way it answers a block left in no field.
    assert "The checks then found:\n\nb4 (lines 7-8) holds an image" in prompt
    assert "fewer blocks unassigned, fewer images ignored" in prompt


def test_a_spec_that_ignores_a_figure_is_written_again_and_the_drop_reported(tmp_path):
    # Every try marks the figure's paragraph ignored, so no try scores zero and
    # the loop spends both its calls before keeping the first.
    folder = tmp_path / "figure-paragraph"
    (folder / "figures").mkdir(parents=True)
    shutil.copy(FIXTURES / "figure-paragraph.md", folder / "figure-paragraph.md")
    shutil.copy(FIXTURES / "ball.png", folder / "figures" / "ball.png")
    backend = FakeBackend(IGNORES_THE_FIGURE, IGNORES_THE_FIGURE)
    stages: list[tuple[str, str]] = []

    _, coverage, report, tries = iterate_spec(
        folder / "figure-paragraph.md",
        folder / SPEC_NAME,
        backend,
        tries=2,
        on_stage=lambda name, message: stages.append((name, message)),
    )

    assert len(backend.calls) == 2
    # The checks find nothing in either draft, so the dropped image is the whole
    # of the score.
    assert [(one.unassigned, one.errors, one.dropped) for one in tries] == [
        (0, 0, 1),
        (0, 0, 1),
    ]
    assert [one.score for one in tries] == [1, 1]
    # The revision names the image the first try dropped.
    assert "b4 (lines 7-8) holds an image and is marked ignore." in backend.calls[1][1]
    # The run goes on past the drop, and every coverage line names it.
    assert report.clean is True
    assert str(coverage).endswith("; 1 image dropped: b4 (lines 7-8)")
    assert [message for name, message in stages if name == "coverage"] == [
        str(coverage)
    ] * 3


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
            SpecTry(number=0, unassigned=2, errors=2, dropped=1),
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
        "dropped": 1,
        "second": None,
        "chosen": False,
    }
    assert (first["input_tokens"], first["output_tokens"]) == (900, 80)
    assert (first["second"], first["chosen"]) == (1, False)
    assert (second["try"], second["seconds"], second["chosen"]) == (2, 2.0, True)


def test_a_try_is_scored_on_its_dropped_images_as_well():
    one = SpecTry(number=1, unassigned=1, errors=2, dropped=3, second=4)

    assert one.score == 10
    assert SpecTry(number=1).score == 0


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
