"""What the package module reads back out of a draft, and what it cannot yet."""

import json
import shutil
from pathlib import Path

import in2lambda.draft
import pytest

from in2lambda_agent import package

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def draft(tmp_path):
    """A frozen sheet with its spec run over it: ten fields, all layer 1."""
    folder = tmp_path / "sheets"
    folder.mkdir()
    shutil.copy(FIXTURES / "sheet.md", folder / "sheet.md")
    spec = FIXTURES / "sheet-spec.yaml"
    written = package.source_add(folder / "sheet.md")
    package.spec_run(written, spec)
    return written


def test_the_questions_are_numbered_with_the_lines_they_came_from(draft):
    found = package.questions(draft)

    assert list(found) == ["q1", "q2"]
    # The question's own text, its two parts and their two solutions.
    assert found["q1"].ranges == [[5, 5], [7, 7], [9, 9], [21, 21], [23, 23]]
    assert found["q1"].layer == 1


def test_a_field_written_past_the_spec_raises_its_questions_layer(draft):
    package.command(
        draft,
        "field replace",
        {"field": "q2.text", "old": "A block", "new": "A crate"},
    )

    found = package.questions(draft)

    # Layer 4, the typed-out edit, is what sample review picks on; the question
    # nothing touched is still the spec's.
    assert found["q2"].layer == 4
    assert found["q1"].layer == 1


def test_a_field_typed_out_has_no_lines_of_the_source_behind_it(draft):
    package.command(
        draft, "question add", {"literal": "Show that the field is solenoidal."}
    )
    written = json.loads(draft.read_text())["fields"]["q3.text"]

    # What in2lambda records for a field no range of the source backs: the key
    # is there and empty rather than absent. `questions` reads it with a
    # default all the same, so a version that leaves it out reads the same way.
    assert (written["layer"], written["edited"]) == (4, True)
    assert written["ranges"] == []
    assert package.questions(draft)["q3"] == package.QuestionInfo("q3", 4, [])


def test_a_reviewers_edit_is_logged_under_their_name(draft):
    package.command(
        draft,
        "field replace",
        {"field": "q1.text", "old": "ball", "new": "stone"},
        by="ada",
    )
    fields = json.loads(draft.read_text())["fields"]

    assert package.command_log(draft)[-1]["by"] == "ada"
    assert fields["q1.text"]["edited"] is True
    assert "stone" in fields["q1.text"]["value"]


def test_a_part_with_no_solution_is_a_warning_the_report_is_still_clean_for(
    tmp_path,
):
    # The same set's spec over a sheet whose solutions are not on it: nothing
    # the checks find stops a build, so the report is clean with four warnings
    # in it rather than four errors.
    folder = tmp_path / "questions-only"
    folder.mkdir()
    shutil.copy(FIXTURES / "questions-only.md", folder / "questions-only.md")
    written = package.source_add(folder / "questions-only.md")
    package.spec_run(written, FIXTURES / "sheet-spec.yaml")

    report = package.validate(written)

    assert (report.clean, report.errors) == (True, [])
    assert len(report.warnings) == 4
    assert all(one.level == "warning" for one in report.findings)
    assert all(one.check == "no-solution" for one in report.findings)
    assert report.warnings == [one.message for one in report.findings]


def test_the_frozen_source_is_named_from_the_draft(draft):
    assert package.frozen_source(draft).name == "sheet.md"
    assert package.frozen_source(draft).is_file()


def test_rendering_says_that_in2lambda_has_no_render_yet(draft, tmp_path):
    with pytest.raises(package.RenderUnavailable, match="in2lambda render"):
        package.render(draft, tmp_path / "render")


def test_rendering_names_the_pdf_written_for_each_question(
    draft, tmp_path, monkeypatch
):
    # What `in2lambda render` will do when it is there, so that the review
    # reads the same either way.
    monkeypatch.setattr(
        in2lambda.draft,
        "render",
        lambda directory, out: {"q1": f"{out}/q1.pdf"},
        raising=False,
    )

    assert package.render(draft, tmp_path / "render") == {
        "q1": tmp_path / "render" / "q1.pdf"
    }
