"""What the package module reads back out of a draft, and what it cannot yet."""

import json
import shutil
from pathlib import Path

import in2lambda.draft
import pytest

from in2lambda_agent import package

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def draft_dir(tmp_path):
    """A frozen sheet with its spec run over it: ten fields, all layer 1."""
    folder = tmp_path / "sheets"
    folder.mkdir()
    shutil.copy(FIXTURES / "sheet.md", folder / "sheet.md")
    spec = FIXTURES / "sheet-spec.yaml"
    directory = package.source_add(folder / "sheet.md")
    package.spec_run(directory, spec)
    return directory


def test_the_questions_are_numbered_with_the_lines_they_came_from(draft_dir):
    found = package.questions(draft_dir)

    assert list(found) == ["q1", "q2"]
    # The question's own text, its two parts and their two solutions.
    assert found["q1"].ranges == [[5, 5], [7, 7], [9, 9], [21, 21], [23, 23]]
    assert found["q1"].layer == 1


def test_a_field_written_past_the_spec_raises_its_questions_layer(draft_dir):
    package.command(
        draft_dir,
        "field replace",
        {"field": "q2.text", "old": "A block", "new": "A crate"},
    )

    found = package.questions(draft_dir)

    # Layer 4, the typed-out edit, is what sample review picks on; the question
    # nothing touched is still the spec's.
    assert found["q2"].layer == 4
    assert found["q1"].layer == 1


def test_a_reviewers_edit_is_logged_under_their_name(draft_dir):
    package.command(
        draft_dir,
        "field replace",
        {"field": "q1.text", "old": "ball", "new": "stone"},
        by="ada",
    )
    fields = json.loads((draft_dir / package.DRAFT).read_text())["fields"]

    assert package.command_log(draft_dir)[-1]["by"] == "ada"
    assert fields["q1.text"]["edited"] is True
    assert "stone" in fields["q1.text"]["value"]


def test_the_frozen_source_is_named_from_the_draft(draft_dir):
    assert package.frozen_source(draft_dir).name == "sheet.md"
    assert package.frozen_source(draft_dir).is_file()


def test_rendering_says_that_in2lambda_has_no_render_yet(draft_dir, tmp_path):
    with pytest.raises(package.RenderUnavailable, match="in2lambda render"):
        package.render(draft_dir, tmp_path / "render")


def test_rendering_names_the_pdf_written_for_each_question(
    draft_dir, tmp_path, monkeypatch
):
    # What `in2lambda render` will do when it is there, so that the review
    # reads the same either way.
    monkeypatch.setattr(
        in2lambda.draft,
        "render",
        lambda directory, out: {"q1": f"{out}/q1.pdf"},
        raising=False,
    )

    assert package.render(draft_dir, tmp_path / "render") == {
        "q1": tmp_path / "render" / "q1.pdf"
    }
