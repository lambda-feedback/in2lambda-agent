"""The answer boxes a part gets, and how they are scored against an export.

Written before the module, over the ME2 fixtures: the export's own response
areas are read back from the platform's JSON, written into a set through
in2lambda, and scored against themselves, so the round trip is tested without a
model in it. What a model proposes is scripted with a fake backend, one reply a
part. The live test converts the ME2 pair and reports what it matched.
"""

import json
import os
import zipfile
from pathlib import Path

import pytest

from conftest import FakeBackend

from in2lambda_agent import gate, response_areas, routes, targets
from in2lambda_agent.settings import Settings

ME2 = Path(__file__).parent / "fixtures" / "me2"
REPLY = json.loads((ME2 / "direct.json").read_text())
EXPORT = ME2 / "export"
ME2_TARGET = Path(
    "/Users/peterbjohnson/code/lambdafeedback/in2lambda-agent/ExampleContents/targets/"
    "ME2_Fluids_introduction"
)
OUT = Path(__file__).parent.parent / "out"

live = pytest.mark.skipif(
    not os.environ.get("IN2LAMBDA_LIVE"), reason="calls Mathpix and a model"
)


def proposals_of(found):
    """The areas of an export as proposals a part's call could have made."""
    return {
        key: [{"kind": kind, "pre_text": "", "answer": answer} for kind, answer in boxes]
        for key, boxes in found.items()
    }


# --- one part's call --------------------------------------------------------------------


def test_a_part_with_a_number_for_an_answer_is_a_numeric_box():
    backend = FakeBackend(
        json.dumps([{"kind": "NUMERIC_UNITS", "pre_text": "$m=$", "answer": "0.106 kg"}])
    )

    proposed, refused, reply = response_areas.propose(
        "What mass does the scale read?", [], "$m = 0.106\\,\\mathrm{kg}$", backend
    )

    assert refused == []
    assert proposed == [
        {"kind": "NUMERIC_UNITS", "pre_text": "$m=$", "answer": "0.106 kg"}
    ]
    area = response_areas.to_response_area(proposed[0], [])
    assert area.response_type == "NUMERIC_UNITS"
    assert area.evaluation_function == "comparePhysicalQuantities"
    assert area.answer == "0.106 kg"
    assert area.pre_text == "$m=$"
    assert area.config is None
    # The prompt carries the part, so that the box is proposed for what it asks.
    assert "What mass does the scale read?" in backend.calls[0][1]


def test_a_part_with_a_symbolic_answer_is_a_maths_box():
    backend = FakeBackend(
        json.dumps(
            [
                {
                    "kind": "MATH_SINGLE_LINE",
                    "pre_text": "$F=$",
                    "answer": "(pi/6)*rho*U**2*R**2",
                }
            ]
        )
    )

    proposed, refused, _ = response_areas.propose(
        "Find the drag force.", [], "$F = \\pi \\rho U^2 R^2 / 6$", backend
    )

    assert refused == []
    area = response_areas.to_response_area(proposed[0], [])
    assert area.response_type == "MATH_SINGLE_LINE"
    assert area.evaluation_function == "symbolicEqual"
    assert area.answer == "(pi/6)*rho*U**2*R**2"


def test_a_multiple_choice_part_is_one_true_or_false_per_option():
    options = ["Yes", "No"]
    backend = FakeBackend(
        json.dumps([{"kind": "MULTIPLE_CHOICE", "pre_text": "", "answer": [True, False]}])
    )

    proposed, refused, _ = response_areas.propose(
        "Is the continuum assumption valid?", options, "Yes", backend
    )

    assert refused == []
    area = response_areas.to_response_area(proposed[0], options)
    assert area.response_type == "MULTIPLE_CHOICE"
    assert area.evaluation_function == "arrayEqual"
    assert area.answer == [True, False]
    assert area.config == {"single": True, "options": options, "randomise": False}


def test_a_part_with_more_than_one_correct_option_is_not_a_single_choice():
    options = ["A", "B", "C"]
    backend = FakeBackend(
        json.dumps(
            [{"kind": "MULTIPLE_CHOICE", "pre_text": "", "answer": [True, False, True]}]
        )
    )

    proposed, _, _ = response_areas.propose("Which hold?", options, "A and C", backend)

    assert response_areas.to_response_area(proposed[0], options).config["single"] is False


def test_a_part_that_asks_for_a_discussion_gets_no_box():
    backend = FakeBackend("[]")

    proposed, refused, _ = response_areas.propose(
        "Comment on what this means for the flow.", [], "", backend
    )

    assert (proposed, refused) == ([], [])


def test_a_part_may_be_given_more_than_one_box():
    backend = FakeBackend(
        json.dumps(
            [
                {"kind": "NUMERIC_UNITS", "pre_text": "$\\ell_0=$", "answer": "3.58e-7 m"},
                {"kind": "NUMERIC_UNITS", "pre_text": "$d=$", "answer": "0.541 mm"},
            ]
        )
    )

    proposed, refused, _ = response_areas.propose("Find both.", [], "", backend)

    assert [one["pre_text"] for one in proposed] == ["$\\ell_0=$", "$d=$"]
    assert refused == []


@pytest.mark.parametrize(
    "answered,says",
    [
        ('[{"kind": "FREE_TEXT", "answer": "anything"}]', "FREE_TEXT"),
        # One boolean per option, and this part has two.
        ('[{"kind": "MULTIPLE_CHOICE", "answer": [true, false, false]}]', "[True, False, False]"),
        # Nothing is correct, so nothing can be marked correct.
        ('[{"kind": "MULTIPLE_CHOICE", "answer": [false, false]}]', "[False, False]"),
        ('[{"kind": "NUMERIC_UNITS", "answer": 0.106}]', "0.106"),
        ('[{"kind": "NUMERIC_UNITS", "answer": ""}]', "''"),
        ("The part asks for a discussion.", "not JSON"),
        ('{"kind": "NUMERIC_UNITS", "answer": "1 m"}', "JSON list"),
    ],
)
def test_a_proposal_the_platform_could_not_mark_is_refused_with_the_reason(answered, says):
    proposed, refused, _ = response_areas.propose(
        "Is it valid?", ["Yes", "No"], "Yes", FakeBackend(answered)
    )

    assert proposed == []
    assert len(refused) == 1
    assert says in refused[0]


# --- a whole reply ----------------------------------------------------------------------


def test_every_part_with_something_to_answer_is_asked_about_once():
    backend = FakeBackend(*["[]"] * 12)

    attached = response_areas.attach(REPLY, backend)

    # The ME2 reply has twelve parts, each with a statement of its own.
    assert len(backend.calls) == 12
    assert attached.proposals == {}
    assert attached.refused == {}
    assert attached.tokens == sum(len(prompt) for _, prompt in backend.calls) + 12 * 2


def test_a_part_with_nothing_in_it_is_not_asked_about():
    empty = [{"title": "", "main_text": "A ball is thrown up.", "parts": [{"content": "", "options": [], "answer": "", "worked_solution": ""}]}]
    backend = FakeBackend()  # No replies: a call would raise rather than answer.

    attached = response_areas.attach(empty, backend)

    assert backend.calls == []
    assert attached.proposals == {}


def test_a_refused_proposal_is_handed_back_under_the_part_it_was_made_for():
    reply = [
        {
            "title": "",
            "main_text": "",
            "parts": [
                {"content": "Find the mass.", "options": [], "answer": "0.106 kg", "worked_solution": ""},
                {"content": "Find the force.", "options": [], "answer": "30 N", "worked_solution": ""},
            ],
        }
    ]
    backend = FakeBackend(
        "I am afraid I cannot help with that.",
        json.dumps([{"kind": "NUMERIC_UNITS", "pre_text": "$F=$", "answer": "30 N"}]),
    )

    attached = response_areas.attach(reply, backend)

    assert list(attached.refused) == ["q1.p1"]
    assert "not JSON" in attached.refused["q1.p1"][0]
    # The part after it is still asked about and still written.
    assert list(attached.proposals) == ["q1.p2"]


# --- what the export says ----------------------------------------------------------------


def test_the_exports_areas_are_read_by_part_in_the_order_the_platform_shows_them():
    found = response_areas.areas_of(EXPORT)

    assert found["q1.p1"] == [("NUMERIC_UNITS", "0.106 kg")]
    assert found["q3.p1"] == [("MATH_SINGLE_LINE", "(pi/6)*(rho)*(U**2)*(R**2)")]
    # Two boxes in one part, taken in the order the platform numbers them.
    assert found["q4.p2"] == [
        ("NUMERIC_UNITS", "3.58e-7 m"),
        ("NUMERIC_UNITS", "0.541 mm"),
    ]
    assert found["q4.p3"] == [("MULTIPLE_CHOICE", [True, False])]
    # A part with no box is not a part with an empty one.
    assert "q1.p2" not in found


def test_the_exports_own_areas_written_back_through_in2lambda_score_every_one(tmp_path):
    wanted = response_areas.areas_of(EXPORT)
    built = routes.to_set(
        REPLY, name="Introduction", areas=proposals_of(wanted)
    )

    zip_path = routes.build(built, tmp_path / "out")
    made = response_areas.areas_of(zip_path)
    scored = response_areas.score(made, wanted)

    assert scored.misses == []
    # The count is written out rather than taken from `wanted`, which is what
    # `score` counted: a box `areas_of` dropped would otherwise lower both sides
    # and still read as every box matched.
    assert (scored.matches, scored.total) == (12, 12)
    # The zip is what the platform reads, so the areas are read back out of it.
    assert "question_000_Hydraulic_scale.json" in zipfile.ZipFile(zip_path).namelist()


# --- scoring -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,made,wanted,same",
    [
        ("NUMERIC_UNITS", "2.65e25 m^-3", "2.65e+25 m^(-3)", True),
        ("NUMERIC_UNITS", "0.106 kg", "0.106kg", True),
        ("NUMERIC_UNITS", "0.1063 kg", "0.106 kg", True),
        ("NUMERIC_UNITS", "0.2 kg", "0.106 kg", False),
        ("NUMERIC_UNITS", "0.106 g", "0.106 kg", False),
        ("NUMERIC_UNITS", "kg", "0.106 kg", False),
        ("MATH_SINGLE_LINE", "(pi/6)*rho*U**2*R**2", "(pi/6)*(rho)*(U**2)*(R**2)", True),
        ("MATH_SINGLE_LINE", "(pi/6) * rho * U^2 * R^2", "(pi/6)*(rho)*(U**2)*(R**2)", True),
        ("MATH_SINGLE_LINE", "(pi/6)*rho*U**3*R**2", "(pi/6)*(rho)*(U**2)*(R**2)", False),
        # A bracket the character before or after binds tighter than `*` is not
        # spelling: these are two expressions, not one written two ways.
        ("MATH_SINGLE_LINE", "2/(a*b)", "2/a*b", False),
        ("MATH_SINGLE_LINE", "(U*R)**2", "U*R**2", False),
        ("MULTIPLE_CHOICE", [True, False], [True, False], True),
        ("MULTIPLE_CHOICE", [False, True], [True, False], False),
        ("MULTIPLE_CHOICE", [True], [True, False], False),
    ],
)
def test_an_answer_matches_the_export_once_units_and_symbols_are_normalised(
    kind, made, wanted, same
):
    assert response_areas.same_answer(kind, made, wanted) is same


def test_every_miss_names_the_part_the_box_and_both_answers():
    wanted = {
        "q1.p1": [("NUMERIC_UNITS", "0.106 kg")],
        "q2.p1": [("MATH_SINGLE_LINE", "2*x"), ("NUMERIC_UNITS", "30 N")],
    }
    made = {
        "q1.p1": [("NUMERIC_UNITS", "0.2 kg")],
        "q2.p1": [("NUMERIC_UNITS", "2 x")],
        "q3.p1": [("MULTIPLE_CHOICE", [True, False])],
    }

    scored = response_areas.score(made, wanted)

    assert (scored.matches, scored.total) == (0, 3)
    assert scored.misses == [
        "q1.p1[1]: wanted NUMERIC_UNITS '0.106 kg', made NUMERIC_UNITS '0.2 kg'",
        "q2.p1[1]: wanted MATH_SINGLE_LINE '2*x', made NUMERIC_UNITS '2 x'",
        "q2.p1[2]: wanted NUMERIC_UNITS '30 N', made nothing",
        "q3.p1[1]: wanted nothing, made MULTIPLE_CHOICE [True, False]",
    ]


def test_a_part_scored_right_is_counted_and_not_listed():
    wanted = {"q1.p1": [("NUMERIC_UNITS", "0.106 kg")], "q1.p2": [("MATH_SINGLE_LINE", "2*x")]}
    made = {"q1.p1": [("NUMERIC_UNITS", "0.106kg")], "q1.p2": [("MATH_SINGLE_LINE", "2*y")]}

    scored = response_areas.score(made, wanted)

    assert (scored.matches, scored.total) == (1, 2)
    assert [miss.split(":")[0] for miss in scored.misses] == ["q1.p2[1]"]


# --- the conversion ----------------------------------------------------------------------


def test_a_conversion_proposes_the_areas_and_writes_them_into_the_set(tmp_path):
    numeric = json.dumps(
        [{"kind": "NUMERIC_UNITS", "pre_text": "$m=$", "answer": "0.106 kg"}]
    )
    backend = FakeBackend(json.dumps(REPLY), *[numeric] * 12)

    result = routes.convert(
        ME2 / "questions.md",
        solutions=ME2 / "solutions.md",
        out_dir=tmp_path / "out",
        backend=backend,
        settings=Settings(),
    )

    # One call reading the documents, then one for each of the twelve parts.
    assert len(backend.calls) == 13
    assert result.set.questions[0].parts[0].response_areas[0].answer == "0.106 kg"
    assert list(result.areas) == [f"q{i}.p{j}" for i in range(1, 6) for j in range(1, len(REPLY[i - 1]["parts"]) + 1)]
    written = json.loads(
        zipfile.ZipFile(result.zip_path).read("question_000_Hydraulic_scale.json")
    )
    area = written["parts"][0]["responseAreas"][0]
    assert area["response"]["responseInput"]["responseType"] == "NUMERIC_UNITS"
    assert area["evaluationFunctionName"] == "comparePhysicalQuantities"


def test_areas_given_to_a_conversion_are_written_and_nothing_is_asked(tmp_path):
    # What a targets run hands back from the proposals it saved, and what the
    # gate replays: no part is asked about again.
    backend = FakeBackend()  # No replies: a call would raise rather than answer.

    result = routes.convert(
        ME2 / "questions.md",
        solutions=ME2 / "solutions.md",
        out_dir=tmp_path / "out",
        backend=backend,
        settings=Settings(),
        route_a=REPLY,
        areas={"q1.p1": [{"kind": "NUMERIC_UNITS", "pre_text": "$m=$", "answer": "0.106 kg"}]},
    )

    assert backend.calls == []
    assert result.tokens == 0
    assert [len(p.response_areas) for p in result.set.questions[0].parts] == [1, 0]


def test_a_conversion_told_there_are_no_areas_asks_nothing(tmp_path):
    backend = FakeBackend()  # No replies: a call would raise rather than answer.

    result = routes.convert(
        ME2 / "questions.md",
        solutions=ME2 / "solutions.md",
        out_dir=tmp_path / "out",
        backend=backend,
        settings=Settings(),
        route_a=REPLY,
        areas={},
    )

    assert backend.calls == []
    assert result.areas == {}
    assert all(p.response_areas == [] for q in result.set.questions for p in q.parts)


def test_a_refused_proposal_is_flagged_and_the_set_is_still_written(tmp_path):
    backend = FakeBackend("no thank you", *["[]"] * 11)

    result = routes.convert(
        ME2 / "questions.md",
        solutions=ME2 / "solutions.md",
        out_dir=tmp_path / "out",
        backend=backend,
        settings=Settings(),
        route_a=REPLY,
    )

    assert "q1.p1.areas" in [one.field for one in result.flags]
    assert result.zip_path.is_file()


def test_the_areas_are_a_stage_of_their_own_and_a_line_of_the_report(tmp_path):
    seen = []
    numeric = json.dumps([{"kind": "NUMERIC_UNITS", "pre_text": "", "answer": "1 m"}])

    result = routes.convert(
        ME2 / "questions.md",
        solutions=ME2 / "solutions.md",
        out_dir=tmp_path / "out",
        backend=FakeBackend(*[numeric] * 12),
        settings=Settings(),
        route_a=REPLY,
        on_stage=lambda name, message: seen.append((name, message)),
    )

    assert [name for name, _ in seen] == ["ocr", "route A", "route B", "areas", "fields", "build"]
    # The stage line says what the call cost as route A's does; the report, which
    # is read after the run, says what the set got.
    assert dict(seen)["areas"].startswith("12 for 12 parts, ")
    assert "areas     12 for 12 parts" in result.report()


# --- a target ------------------------------------------------------------------------------


def target_of(tmp_path):
    """The ME2 documents and export as a target, as test_targets makes one."""
    from test_targets import make_target

    make_target(tmp_path / "corpus", "ME2")
    (found,) = targets.find(tmp_path / "corpus")
    return found


def test_a_target_saves_what_it_proposed_and_replays_it_with_no_call(tmp_path, monkeypatch):
    from test_targets import fake_convert

    calls = fake_convert(monkeypatch, areas={"q1.p1": [{"kind": "NUMERIC_UNITS", "pre_text": "", "answer": "0.106 kg"}]})
    target = target_of(tmp_path)
    filters = tmp_path / "filters"
    (filters / "ME2").mkdir(parents=True)
    (filters / "ME2" / targets.FILTER_NAME).write_text("-- filter")

    first = targets.run_one(
        target, filters=filters, out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache", backend=FakeBackend(),
    )
    saved = json.loads((filters / "ME2" / targets.AREAS_NAME).read_text())
    second = targets.run_one(
        target, filters=filters, out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache", backend=FakeBackend(), replay=True,
    )

    assert first.error is None and second.error is None
    assert saved == {"q1.p1": [{"kind": "NUMERIC_UNITS", "pre_text": "", "answer": "0.106 kg"}]}
    # The second run converted what the first proposed rather than proposing again.
    assert calls[1]["areas"] == saved


def test_a_replay_refuses_a_target_whose_areas_are_not_saved(tmp_path, monkeypatch):
    from test_targets import fake_convert

    calls = fake_convert(monkeypatch)
    target = target_of(tmp_path)
    filters = tmp_path / "filters"
    (filters / "ME2").mkdir(parents=True)
    (filters / "ME2" / targets.FILTER_NAME).write_text("-- filter")
    (filters / "ME2" / targets.REPLY_NAME).write_text(json.dumps(REPLY))

    result = targets.run_one(
        target, filters=filters, out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache", backend=FakeBackend(), replay=True,
    )

    assert targets.AREAS_NAME in result.error
    assert f"--filters {filters}" in result.error
    assert calls == []


def test_a_targets_report_counts_the_areas_it_matched_and_lists_the_misses(tmp_path, monkeypatch):
    from test_targets import fake_convert

    wanted = response_areas.areas_of(EXPORT)
    # Every area of the export but one, which the report must name.
    proposals = proposals_of(wanted)
    proposals["q1.p1"] = [{"kind": "NUMERIC_UNITS", "pre_text": "", "answer": "9 kg"}]
    fake_convert(monkeypatch, areas=proposals)
    target = target_of(tmp_path)

    result = targets.run_one(
        target, filters=tmp_path / "filters", out_dir=tmp_path / "out",
        cache_dir=tmp_path / "cache", backend=FakeBackend("-- filter"),
    )

    total = sum(len(boxes) for boxes in wanted.values())
    assert (result.areas.matches, result.areas.total) == (total - 1, total)
    assert f"areas     ME2: {total - 1} of {total} match" in result.report()
    assert [line for line in result.report() if line.startswith("miss")] == [
        "miss      ME2: q1.p1[1]: wanted NUMERIC_UNITS '0.106 kg', made NUMERIC_UNITS '9 kg'"
    ]


# --- live -----------------------------------------------------------------------------------


@live
@pytest.mark.skipif(not ME2_TARGET.is_dir(), reason="private corpus")
def test_the_me2_target_scores_its_areas_against_the_export(tmp_path):
    # The ticket's run: the areas the agent proposes for the ME2 pair, against
    # the ones the platform exported. The report is written under out/ and read
    # back from there, so what the pull request quotes is a file.
    (found,) = targets.find(ME2_TARGET.parent, [Path(ME2_TARGET.name)])

    result = targets.run_one(
        found,
        filters=tmp_path / "filters",
        out_dir=tmp_path / "out",
        # The gate's cache, so that the pages are not read by Mathpix again.
        cache_dir=gate.DEFAULT_CACHE_DIR,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    report = OUT / "t44-me2-areas.txt"
    report.write_text("\n".join(result.report()) + "\n")
    print("\n" + report.read_text())
    assert result.error is None
    # The export holds twelve boxes over eleven parts, which is what the run is
    # scored out of. Written out rather than counted from the export, so that a
    # run reading fewer of them fails here instead of scoring out of fewer.
    assert result.areas.total == 12
