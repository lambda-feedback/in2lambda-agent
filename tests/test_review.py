"""The record a run leaves for the reviewer, and which questions it shows them."""

import random
import re

import pytest

from in2lambda_agent.fix import RoundResult
from in2lambda_agent.model import ToolCall, Usage
from in2lambda_agent.package import Coverage, QuestionInfo
from in2lambda_agent.review import Question, Review, ReviewError, choose


def infos(*layers):
    """A draft's questions, numbered from 1, each at the layer given."""
    return {
        f"q{number}": QuestionInfo(f"q{number}", layer, [[number, number]])
        for number, layer in enumerate(layers, start=1)
    }


def review(**changed):
    """A review of two questions, as a run in sample mode writes one."""
    state = dict(
        mode="sample",
        count=3,
        source="sheets/sheet.md",
        spec="sheets/in2lambda-spec.yaml",
        out_dir="out",
        limit=3,
        draft="sheets/sheet.draft.json",
        frozen="sheets/sheet.md",
        reused=False,
        coverage=Coverage(layout="PartsSepSol", blocks=14, fields={1: 10}, ignored=4),
        questions=[
            Question("q1", pdf="out/render/q1.pdf", lines=[[5, 5], [7, 9]]),
            Question("q2", lines=[[13, 13]]),
        ],
    )
    return Review(**{**state, **changed})


def test_per_question_shows_every_question():
    assert choose(infos(1, 1, 3), "per-question", 3, random.Random(0)) == [
        "q1",
        "q2",
        "q3",
    ]


def test_a_sample_shows_what_something_past_the_spec_wrote_first():
    chosen = choose(infos(1, 3, 1, 4, 1), "sample", 3, random.Random(0))

    # The two the rounds or an edit touched, then one of the three the spec
    # wrote by itself, to make the count up.
    assert chosen[:2] == ["q2", "q4"]
    assert len(chosen) == 3 and chosen[2] in {"q1", "q3", "q5"}


def test_a_sample_stops_at_the_count():
    assert choose(infos(3, 4, 3, 4), "sample", 2, random.Random(0)) == ["q1", "q2"]


def test_a_sample_of_a_set_smaller_than_the_count_is_all_of_it():
    assert sorted(choose(infos(1, 1), "sample", 5, random.Random(0))) == ["q1", "q2"]


def test_a_sample_is_the_same_sample_twice_from_the_same_seed():
    first = choose(infos(1, 1, 1, 1, 1), "sample", 2, random.Random(7))
    second = choose(infos(1, 1, 1, 1, 1), "sample", 2, random.Random(7))

    assert first == second


def test_the_record_goes_to_json_and_comes_back(tmp_path):
    saved = review(
        usage=Usage(input_tokens=120, output_tokens=40, seconds=1.5),
        rounds=[
            RoundResult(1, [ToolCall("part_add", {"question": "q2"}, "wrote")], Usage(), 0)
        ],
    )
    saved.questions[1].status = "rejected"
    saved.questions[1].note = "the solution belongs to (b)"

    saved.save(tmp_path / "review.json")
    read = Review.load(tmp_path / "review.json")

    assert read == saved
    # The layers keep their numbers, which is how every other reader has them.
    assert read.coverage.fields == {1: 10}
    assert read.rounds[0].commands[0].name == "part_add"


def test_no_review_waiting_says_what_writes_one(tmp_path):
    with pytest.raises(ReviewError, match="--review sample"):
        Review.load(tmp_path / "review.json")


def test_a_record_cut_short_names_the_file_rather_than_breaking(tmp_path):
    # A run killed while it was writing the record, which the next review
    # command reads: the reviewer is told what to delete, not given a traceback.
    (tmp_path / "review.json").write_text('{"mode": "sample", "questions": [')

    with pytest.raises(ReviewError, match=re.escape(str(tmp_path / "review.json"))):
        Review.load(tmp_path / "review.json")


def test_a_record_an_older_agent_wrote_names_the_file_too(tmp_path):
    (tmp_path / "review.json").write_text('{"mode": "sample", "count": 3}')

    with pytest.raises(ReviewError, match="not a review this run can read"):
        Review.load(tmp_path / "review.json")


def test_a_question_that_is_not_under_review_names_the_ones_that_are():
    with pytest.raises(ReviewError, match="This review has q1, q2"):
        review().question("q9")


def test_the_listing_names_the_pdf_the_source_and_the_lines():
    listed = review().listing().splitlines()

    assert listed[0] == (
        "  q1 pending: out/render/q1.pdf, sheets/sheet.md lines 5-5, 7-9"
    )
    assert listed[1] == "  q2 pending: not rendered, sheets/sheet.md lines 13-13"


def test_the_listing_carries_a_rejection_note_back():
    one = review()
    one.questions[0].status = "rejected"
    one.questions[0].note = "part (b) is missing"

    assert one.listing().splitlines()[0].endswith("— part (b) is missing")


def test_a_review_is_done_only_once_every_question_is_approved():
    one = review()
    one.questions[0].status = "approved"

    assert one.done is False

    one.questions[1].status = "approved"

    assert one.done is True


def test_the_record_line_carries_the_verdicts_and_the_notes():
    one = review()
    one.questions[0].status = "approved"
    one.questions[1].status = "rejected"
    one.questions[1].note = "wrong solution"
    one.rejections.append({"key": "q2", "note": "wrong solution"})
    one.edits.append({"field": "q1.text", "by": "ada"})

    assert one.to_json() == {
        "mode": "sample",
        "questions": [
            {"key": "q1", "status": "approved", "note": None},
            {"key": "q2", "status": "rejected", "note": "wrong solution"},
        ],
        "rejections": [{"key": "q2", "note": "wrong solution"}],
        "edits": [{"field": "q1.text", "by": "ada"}],
    }
