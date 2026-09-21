"""What the package module reads back out of a draft, and what it cannot yet."""

import json
import shutil
import warnings
from pathlib import Path

import in2lambda.draft.export
import pytest
from in2lambda.source import ConversionToolsMissing
from in2lambda.validation.pdf import missing_tools

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


def test_the_fix_log_is_the_commands_after_the_spec_run(draft):
    package.command(
        draft, "field replace", {"field": "q1.text", "old": "ball", "new": "stone"}
    )

    assert package.command_log(draft)[0]["command"] == "spec run"
    assert [one["command"] for one in package.fix_log(draft)] == ["field replace"]


def test_a_saved_log_replays_in_the_order_it_was_saved(draft, tmp_path):
    saved = [
        {
            "command": "field replace",
            "args": {"field": "q1.text", "old": "ball", "new": "stone"},
            "by": package.BY,
        },
        {
            "command": "field replace",
            "args": {"field": "q1.text", "old": "stone", "new": "brick"},
            "by": package.BY,
        },
    ]
    again = package.source_add(package.frozen_source(draft))
    package.spec_run(again, FIXTURES / "sheet-spec.yaml")

    assert package.replay(again, saved) == 2
    assert "brick" in package.field_value(again, "q1.text")


def test_a_refused_command_names_its_place_in_the_log(draft):
    saved = [
        {
            "command": "field replace",
            "args": {"field": "q1.text", "old": "ball", "new": "stone"},
            "by": package.BY,
        },
        {"command": "mark ignore", "args": {"block": "b99"}, "by": package.BY},
        {
            "command": "field replace",
            "args": {"field": "q1.text", "old": "stone", "new": "brick"},
            "by": package.BY,
        },
    ]

    with pytest.raises(package.CommandRefused, match="command 2 of 3, mark ignore"):
        package.replay(draft, saved)

    # The command before the refusal was applied, and the one after it was not.
    assert "stone" in package.field_value(draft, "q1.text")


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


def beside_its_image(tmp_path, name: str) -> Path:
    """A sheet copied into a folder of its own, beside the image it refers to."""
    folder = tmp_path / Path(name).stem
    (folder / "figures").mkdir(parents=True)
    shutil.copy(FIXTURES / name, folder / name)
    shutil.copy(FIXTURES / "ball.png", folder / "figures" / "ball.png")
    return folder / name


def test_a_block_marked_ignore_whose_lines_hold_an_image_is_reported(tmp_path):
    # The spec marks the figure's paragraph ignored by matching its caption.
    # Nothing in2lambda checks says so, and the set built from the draft holds
    # no image.
    written = package.source_add(beside_its_image(tmp_path, "figure-paragraph.md"))
    package.spec_run(written, FIXTURES / "figure-paragraph-spec.yaml")

    (dropped,) = package.ignored_images(written)

    assert (dropped.check, dropped.level) == ("coverage", package.ERROR)
    assert (dropped.field, dropped.ranges) == ("b4", [[7, 8]])
    assert dropped.message == "b4 (lines 7-8) holds an image and is marked ignore."


def test_an_image_inside_a_question_is_nothing_to_report(tmp_path):
    written = package.source_add(beside_its_image(tmp_path, "figure.md"))
    package.spec_run(written, FIXTURES / "sheet-spec.yaml")

    assert package.ignored_images(written) == []


def test_a_source_whose_bytes_are_not_text_has_no_ignored_image_to_read(tmp_path):
    # A docx source is frozen as itself, so the file beside the draft is a zip.
    # Reading it for a `![` is not what it is for, and must not end the run.
    written = package.source_add(beside_its_image(tmp_path, "figure-paragraph.md"))
    package.spec_run(written, FIXTURES / "figure-paragraph-spec.yaml")
    package.frozen_source(written).write_bytes((FIXTURES / "ball.png").read_bytes())

    assert package.ignored_images(written) == []


def test_the_coverage_line_names_the_images_the_spec_dropped(tmp_path):
    written = package.source_add(beside_its_image(tmp_path, "figure-paragraph.md"))

    coverage = package.spec_run(written, FIXTURES / "figure-paragraph-spec.yaml")

    assert str(coverage).endswith("; 1 image dropped: b4 (lines 7-8)")


def test_the_coverage_line_of_a_spec_that_dropped_none_is_unchanged(tmp_path):
    written = package.source_add(beside_its_image(tmp_path, "figure.md"))

    coverage = package.spec_run(written, FIXTURES / "sheet-spec.yaml")

    assert str(coverage).endswith("4 ignored, none unassigned")


def test_a_second_source_is_frozen_into_the_same_draft(tmp_path):
    folder = tmp_path / "sheets"
    folder.mkdir()
    for name in ("paired.md", "paired_solutions.md"):
        shutil.copy(FIXTURES / name, folder / name)

    written = package.source_add(folder / "paired.md", folder / "paired_solutions.md")

    assert written == folder / "paired.draft.json"
    frozen = json.loads(written.read_text())["sources"]
    assert [one["source"] for one in frozen] == ["paired.md", "paired_solutions.md"]
    # The second source's blocks carry its number, which is how a command and a
    # spec-writing prompt address them.
    assert all(one["id"].startswith("2/") for one in frozen[1]["blocks"])


def test_the_warnings_a_build_says_are_returned_rather_than_printed(tmp_path):
    # The same sheet without its solutions: in2lambda builds it and warns about
    # each unanswered part as it goes. Those are the messages the validate line
    # already lists, which is why the pipeline prints none of them again.
    folder = tmp_path / "questions-only"
    folder.mkdir()
    shutil.copy(FIXTURES / "questions-only.md", folder / "questions-only.md")
    written = package.source_add(folder / "questions-only.md")
    package.spec_run(written, FIXTURES / "sheet-spec.yaml")
    report = package.validate(written)

    with warnings.catch_warnings(record=True) as escaped:
        warnings.simplefilter("always")
        built = package.build(written, tmp_path / "out")

    assert escaped == []
    assert built.zip_path.is_file()
    assert built.warnings == report.warnings


def test_the_frozen_source_is_named_from_the_draft(draft):
    assert package.frozen_source(draft).name == "sheet.md"
    assert package.frozen_source(draft).is_file()


def test_rendering_names_the_pdf_written_for_each_question(
    draft, tmp_path, monkeypatch
):
    out = tmp_path / "render"
    # in2lambda numbers each file with the question's place in the set, counting
    # from zero, which is what the key comes from: a question the compiler gave
    # up on is left out of the list, so counting the list would number the rest
    # wrongly.
    monkeypatch.setattr(
        in2lambda.draft.export,
        "render",
        lambda written, directory: [
            Path(directory) / "question_001_Question_2.pdf",
            Path(directory) / "question_000_Question_1.pdf",
        ],
    )

    assert package.render(draft, out) == {
        "q1": out / "question_000_Question_1.pdf",
        "q2": out / "question_001_Question_2.pdf",
    }


def test_rendering_that_cannot_compile_is_refused_with_in2lambdas_reason(
    draft, tmp_path, monkeypatch
):
    def missing(written, directory):
        raise ConversionToolsMissing("Rendering questions needs xelatex.")

    monkeypatch.setattr(in2lambda.draft.export, "render", missing)

    with pytest.raises(package.CommandRefused, match="needs xelatex"):
        package.render(draft, tmp_path / "render")


@pytest.mark.skipif(bool(missing_tools()), reason="needs pandoc and xelatex")
def test_rendering_writes_a_pdf_for_each_question_of_the_draft(draft, tmp_path):
    rendered = package.render(draft, tmp_path / "render")

    assert list(rendered) == ["q1", "q2"]
    assert all(path.parent == tmp_path / "render" for path in rendered.values())
    assert all(path.is_file() for path in rendered.values())


@pytest.mark.skipif(bool(missing_tools()), reason="needs pandoc and xelatex")
def test_a_question_the_compiler_gives_up_on_leaves_the_rest_keyed_as_they_were(
    draft, tmp_path
):
    # TeX stops on a file it cannot find before it has typeset anything, so q1
    # has no page while q2 still does. in2lambda leaves it out of the list it
    # returns and names the files it did write after the question's place in
    # the set, which is what the key must come from: counting the list would
    # hand q2's page to q1 and leave q2 reading `not rendered`.
    package.command(
        draft,
        "field replace",
        {
            "field": "q1.text",
            "old": r"A ball is thrown straight up at $20\,\mathrm{m/s}$.",
            "new": r"\input{no-such-file-at-all}",
        },
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        rendered = package.render(draft, tmp_path / "render")

    assert rendered == {"q2": tmp_path / "render" / "question_001_Question_2.pdf"}
    assert rendered["q2"].is_file()
    assert not (tmp_path / "render" / "question_000_Question_1.pdf").exists()
