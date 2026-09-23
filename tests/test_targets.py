"""Each target against the set Lambda Feedback exported from it.

Written before the module, over the ME2 fixtures: the direct-route reply of
2026-09-21 built into a zip stands in for a conversion, and the export beside it
is what the comparison holds it against. The live test runs the three real
targets and is skipped without the private corpus.
"""

import copy
import json
import os
import shutil
from pathlib import Path

import pytest

from conftest import FakeBackend

from in2lambda_agent import gate, routes, targets

ME2 = Path(__file__).parent / "fixtures" / "me2"
REPLY = json.loads((ME2 / "direct.json").read_text())
TARGETS = Path(
    "/Users/peterbjohnson/code/lambdafeedback/in2lambda-agent/ExampleContents/targets"
)

live = pytest.mark.skipif(
    not os.environ.get("IN2LAMBDA_LIVE"), reason="calls Mathpix and a model"
)


def make_target(root, name, *, questions="sheet.md", solutions="sheet_solutions.md"):
    """A target folder: the ME2 documents, and the ME2 export under its own name."""
    folder = Path(root) / name
    folder.mkdir(parents=True)
    (folder / questions).write_text((ME2 / "questions.md").read_text())
    if solutions:
        (folder / solutions).write_text((ME2 / "solutions.md").read_text())
    shutil.copytree(ME2 / "export", folder / "set_Introduction")
    return folder


def fake_convert(monkeypatch, flags=(), error=None, reply=None):
    """Stands in for a conversion: builds the fixture reply, records the call.

    A saved reply handed back is what route A answered, as `convert` uses it, so
    a second run over a target reads the first run's reply where it has one.
    """
    calls = []
    reply = REPLY if reply is None else reply

    def convert(document, solutions=None, **options):
        calls.append({"document": document, "solutions": solutions, **options})
        if error is not None:
            raise error
        answered = options.get("route_a") or reply
        built = routes.to_set(answered, name=options["name"])
        return routes.Converted(
            set=built,
            zip_path=routes.build(built, options["out_dir"]),
            flags=list(flags),
            reply=answered,
            route_a=answered,
            tokens=1200,
        )

    monkeypatch.setattr(targets.routes, "convert", convert)
    return calls


# --- finding the targets ----------------------------------------------------------------


def test_a_target_is_found_by_its_export_folder_at_any_depth(tmp_path):
    make_target(tmp_path, "ME2_Fluids_introduction")
    make_target(tmp_path, "EART40013_Mathematical_Methods_II/CW1")
    make_target(tmp_path, "EART40013_Mathematical_Methods_II/CW2")
    # A folder of sheets with no export is not a target.
    (tmp_path / "PHYS40002").mkdir()
    (tmp_path / "PHYS40002" / "Sheet_1.tex").write_text("x")

    found = targets.find(tmp_path)

    assert [one.name for one in found] == [
        "EART40013_Mathematical_Methods_II/CW1",
        "EART40013_Mathematical_Methods_II/CW2",
        "ME2_Fluids_introduction",
    ]
    assert found[2].questions == tmp_path / "ME2_Fluids_introduction" / "sheet.md"
    assert found[2].solutions == tmp_path / "ME2_Fluids_introduction" / "sheet_solutions.md"
    assert found[2].export == tmp_path / "ME2_Fluids_introduction" / "set_Introduction"
    assert [one.error for one in found] == [None, None, None]


def test_a_named_path_narrows_the_run_to_what_is_under_it(tmp_path):
    make_target(tmp_path, "ME2_Fluids_introduction")
    make_target(tmp_path, "EART40013_Mathematical_Methods_II/CW1")

    found = targets.find(tmp_path, [Path("EART40013_Mathematical_Methods_II")])

    assert [one.name for one in found] == ["EART40013_Mathematical_Methods_II/CW1"]


def test_a_target_whose_documents_cannot_be_read_is_an_error_not_a_raise(tmp_path):
    folder = tmp_path / "ME2_Fluids_introduction"
    (folder / "set_Introduction").mkdir(parents=True)
    make_target(tmp_path, "CW1")

    found = targets.find(tmp_path)

    assert [one.name for one in found] == ["CW1", "ME2_Fluids_introduction"]
    assert found[0].error is None
    assert "no questions document" in found[1].error


def test_a_root_that_is_the_target_itself_is_refused_naming_the_root_to_pass(tmp_path):
    folder = make_target(tmp_path, "EART40013_Mathematical_Methods_II/CW2")

    (found,) = targets.find(folder)

    # Its name from that root is `.`, and a filter and a differs.txt kept under
    # that name are not the ones the target has: it is refused rather than
    # converted against a filter tree it would write over the top of.
    assert found.name == "CW2"
    assert "is a target itself" in found.error
    assert f"targets {folder.parent} CW2" in found.error


def test_a_folder_holding_two_exports_is_one_target_and_an_error(tmp_path):
    folder = make_target(tmp_path, "ME2_Fluids_introduction")
    (folder / "set_Second_half").mkdir()

    (found,) = targets.find(tmp_path)

    assert found.name == "ME2_Fluids_introduction"
    assert "set_Introduction" in found.error and "set_Second_half" in found.error


# --- one target -------------------------------------------------------------------------


def test_the_comparison_reports_every_difference_from_the_export(tmp_path, monkeypatch):
    calls = fake_convert(monkeypatch)
    make_target(tmp_path / "corpus", "ME2")
    (target,) = targets.find(tmp_path / "corpus")

    result = targets.run_one(
        target,
        filters=tmp_path / "filters",
        out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache",
        backend=FakeBackend("-- filter"),
    )

    # The reply was converted under the export's own name, so the zip the
    # comparison reads is the set Lambda Feedback would have exported.
    assert calls[0]["name"] == "Introduction"
    assert calls[0]["document"] == target.questions
    assert calls[0]["solutions"] == target.solutions
    # The fixture reply is the model's reading of the same pages, so the two
    # sets differ in wording over most of the questions.
    assert len(result.differences) == 21
    assert result.new == result.differences
    assert result.known == []
    assert result.error is None
    assert result.tokens == 1200


@pytest.mark.parametrize(
    "line, key",
    [
        ('Question 1 "", main text: the agent says \'a\'', "q1.main_text"),
        ('Question 12 "", part (c), worked solution: the agent', "q12.p3.worked_solution"),
        ('Question 2 "Towing a submarine", part (a), text: the', "q2.p1.text"),
        ('Question 3 "": the export wrote this question and the agent did not', "q3"),
        ('Question 3 "", part (b): the export wrote this part', "q3.p2"),
    ],
)
def test_the_key_of_a_difference_is_the_field_it_names(line, key):
    assert targets.field_key(line) == key


def test_a_differs_file_holds_a_key_a_line_and_a_reason_after_the_hash(tmp_path):
    path = tmp_path / targets.DIFFERS_NAME
    path.write_text(
        "# Accepted 2026-09-23.\n"
        "q1.main_text  # the export keeps the platform's own spacing\n"
        "\n"
        "q2.p1.worked_solution\n"
    )

    assert targets.accepted(path) == ["q1.main_text", "q2.p1.worked_solution"]
    assert targets.accepted(tmp_path / "nothing-here.txt") == []


def test_a_key_the_maintainer_accepted_is_known_and_not_new(tmp_path, monkeypatch):
    fake_convert(monkeypatch)
    make_target(tmp_path / "corpus", "ME2")
    filters = tmp_path / "filters"
    (target,) = targets.find(tmp_path / "corpus")
    first = targets.run_one(
        target, filters=filters, out_dir=tmp_path / "out", cache_dir=tmp_path / "cache",
        backend=FakeBackend("-- filter"),
    )

    # Every field that differs accepted, and one key more that no field of this
    # run differs in, which is neither known nor new.
    (filters / "ME2" / targets.DIFFERS_NAME).write_text(
        "".join(f"{targets.field_key(line)}  # t40\n" for line in first.differences)
        + "q9.main_text  # t41 a question this sheet no longer has\n"
    )
    again = targets.run_one(
        target, filters=filters, out_dir=tmp_path / "out", cache_dir=tmp_path / "cache",
        backend=FakeBackend("-- filter"),
    )

    assert again.new == []
    assert again.known == first.differences
    assert again.agreed == ["q9.main_text"]


def test_an_accepted_field_stays_known_however_the_run_words_it(tmp_path, monkeypatch):
    # What a differs.txt accepts is the field, not the sentence: route A is a
    # model call, and a model does not word a field the same way twice.
    fake_convert(monkeypatch)
    make_target(tmp_path / "corpus", "ME2")
    filters = tmp_path / "filters"
    (target,) = targets.find(tmp_path / "corpus")
    first = targets.run_one(
        target, filters=filters, out_dir=tmp_path / "out", cache_dir=tmp_path / "cache",
        backend=FakeBackend("-- filter"),
    )
    (filters / "ME2" / targets.DIFFERS_NAME).write_text(
        "".join(f"{targets.field_key(line)}  # t40\n" for line in first.differences)
    )

    # The first question's main text, which differs from the export either way,
    # read a sentence longer this time.
    reworded = copy.deepcopy(REPLY)
    reworded[0]["main_text"] += " Take $g$ as $9.81\\,\\mathrm{m/s^2}$."
    fake_convert(monkeypatch, reply=reworded)
    again = targets.run_one(
        target, filters=filters, out_dir=tmp_path / "out", cache_dir=tmp_path / "cache",
        backend=FakeBackend("-- filter"), fresh=True,
    )

    assert again.differences != first.differences
    assert again.new == []
    assert again.agreed == []


def test_route_as_reply_is_saved_and_read_back_unless_a_fresh_one_is_asked_for(
    tmp_path, monkeypatch
):
    # A target's conversion is repeatable because route A's reply is: the model
    # is called for it once, and a fresh call is a deliberate act.
    calls = fake_convert(monkeypatch)
    make_target(tmp_path / "corpus", "ME2")
    filters = tmp_path / "filters"
    (target,) = targets.find(tmp_path / "corpus")
    ran = dict(
        filters=filters, out_dir=tmp_path / "out", cache_dir=tmp_path / "cache",
        backend=FakeBackend("-- filter"),
    )

    targets.run_one(target, **ran)
    saved = filters / "ME2" / targets.REPLY_NAME
    assert calls[0]["route_a"] is None
    assert json.loads(saved.read_text()) == REPLY

    targets.run_one(target, **ran)
    assert calls[1]["route_a"] == REPLY

    targets.run_one(target, **ran, fresh=True)
    assert calls[2]["route_a"] is None


def test_the_filter_is_written_once_and_read_after_that(tmp_path, monkeypatch):
    calls = fake_convert(monkeypatch)
    make_target(tmp_path / "corpus", "ME2")
    (target,) = targets.find(tmp_path / "corpus")
    filters = tmp_path / "filters"
    backend = FakeBackend("function Pandoc(doc) end")

    targets.run_one(
        target, filters=filters, out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache", backend=backend,
    )
    lua = filters / "ME2" / targets.FILTER_NAME
    assert lua.read_text() == "function Pandoc(doc) end"
    assert calls[0]["lua"] == lua
    assert len(backend.calls) == 1

    # The second run reads the saved filter, so a target costs one call ever.
    targets.run_one(
        target, filters=filters, out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache", backend=backend,
    )
    assert len(backend.calls) == 1
    assert calls[1]["lua"] == lua


def test_a_scanned_target_converts_with_no_filter(tmp_path, monkeypatch):
    # Pandoc cannot read a PDF, so there is no structure to write a filter from
    # and no call to make: route A converts the pages' markdown alone.
    calls = fake_convert(monkeypatch)
    make_target(
        tmp_path / "corpus", "ME2",
        questions="sheet.pdf", solutions="sheet_solutions.pdf",
    )
    (target,) = targets.find(tmp_path / "corpus")
    backend = FakeBackend()

    targets.run_one(
        target, filters=tmp_path / "filters", out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache", backend=backend,
    )

    assert calls[0]["lua"] is None
    assert backend.calls == []
    assert not (tmp_path / "filters" / "ME2" / targets.FILTER_NAME).exists()


def test_a_target_whose_export_cannot_be_read_is_an_error_and_the_next_one_runs(
    tmp_path, monkeypatch
):
    # Half of an export copied into the corpus: the folder is there, so the
    # target is found and its conversion is paid for, and the comparison is
    # what fails. It is this target's line like any other.
    fake_convert(monkeypatch)
    half = make_target(tmp_path / "corpus", "ME2")
    shutil.rmtree(half / "set_Introduction")
    (half / "set_Introduction").mkdir()
    make_target(tmp_path / "corpus", "CW1")

    results = targets.run(
        tmp_path / "corpus", filters=tmp_path / "filters", out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache", backend=FakeBackend("-- a", "-- b"),
    )

    assert [one.name for one in results] == ["CW1", "ME2"]
    assert results[0].error is None and results[0].new
    assert results[1].error


def test_a_target_that_failed_is_a_line_of_its_own_and_the_next_one_runs(tmp_path, monkeypatch):
    from in2lambda_agent.mathpix import MathpixError

    fake_convert(monkeypatch, error=MathpixError("set MATHPIX_APP_ID"))
    make_target(tmp_path / "corpus", "ME2")
    make_target(tmp_path / "corpus", "CW1")

    results = targets.run(
        tmp_path / "corpus", filters=tmp_path / "filters", out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache", backend=FakeBackend("-- a", "-- b"),
    )

    assert [one.name for one in results] == ["CW1", "ME2"]
    assert [one.error for one in results] == ["set MATHPIX_APP_ID"] * 2
    assert [one.new for one in results] == [[], []]


# --- the report ---------------------------------------------------------------------------


def test_the_report_names_each_difference_and_counts_them():
    result = targets.Result(
        name="ME2",
        differences=["Question 1 \"\": a", "Question 2 \"\": b"],
        known=["Question 1 \"\": a"],
        new=["Question 2 \"\": b"],
        agreed=["q3.p1.text"],
        flags=2,
    )

    assert result.report() == [
        'differs   ME2: Question 2 "": b',
        'known     ME2: Question 1 "": a',
        "agrees    ME2: q3.p1.text now agrees, remove the line",
        "ME2: 2 differ, 1 known, 1 new, 2 flagged",
    ]


def test_the_report_of_a_target_that_failed_says_what_stopped_it():
    result = targets.Result(name="CW1", error="set MATHPIX_APP_ID")

    assert result.report() == ["error     CW1: set MATHPIX_APP_ID"]


# --- live -----------------------------------------------------------------------------------


@live
@pytest.mark.skipif(not TARGETS.is_dir(), reason="private corpus")
def test_every_target_reports_its_known_differences_and_no_other(tmp_path, capsys):
    # The ticket's run: the three targets, their saved filters, their accepted
    # differences, and no difference beside them.
    results = targets.run(
        TARGETS,
        filters=targets.DEFAULT_FILTER_DIR,
        out_dir=tmp_path / "out",
        # The cache the gate shares between worktrees: a second OCR pass of the
        # same PDF is paid for again and reads it a little differently, which
        # is a difference in the run and not in the agent.
        cache_dir=gate.DEFAULT_CACHE_DIR,
    )
    print("\n" + capsys.readouterr().out)

    assert [one.name for one in results] == [
        "EART40013_Mathematical_Methods_II/CW1",
        "EART40013_Mathematical_Methods_II/CW2",
        "ME2_Fluids_introduction",
    ]
    assert [one.error for one in results] == [None, None, None]
    assert [one.new for one in results] == [[], [], []]


@live
@pytest.mark.skipif(not TARGETS.is_dir(), reason="private corpus")
def test_the_same_run_twice_reports_the_same_fields(tmp_path, capsys):
    # The saved reply is what makes the run above a check rather than a reading:
    # route A is not called again, so the second run compares the same set.
    ran = dict(filters=targets.DEFAULT_FILTER_DIR, cache_dir=gate.DEFAULT_CACHE_DIR)
    first = targets.run(TARGETS, out_dir=tmp_path / "first", **ran)
    again = targets.run(TARGETS, out_dir=tmp_path / "again", **ran)
    print("\n" + capsys.readouterr().out)

    assert [one.new for one in again] == [[], [], []]
    assert [len(one.differences) for one in again] == [
        len(one.differences) for one in first
    ]
