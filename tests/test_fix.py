"""The tools a fixing round has, and what running one leaves in the draft's log."""

import json
import shutil
from pathlib import Path

import pytest
from conftest import FakeBackend

from in2lambda_agent import fix, package
from in2lambda_agent.model import ToolCall

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def draft(tmp_path):
    """A draft of the faulty sheet, with the spec's layer 1 fields in it.

    The same starting point a fixing round is given: q1 written with both its
    parts and their solutions, and two blocks of the source in no field at all.
    """
    folder = tmp_path / "sheets"
    folder.mkdir()
    for name in ("faulty.md", "faulty-spec.yaml"):
        shutil.copy(FIXTURES / name, folder / name)
    written = package.source_add(folder / "faulty.md")
    package.spec_run(written, folder / "faulty-spec.yaml")
    return written


def run(draft, name, arguments):
    """Calls one tool the way a backend's loop calls it, and returns the result."""
    tool = next(one for one in fix.tools(draft) if one.name == name)
    return tool.run(arguments)


def commands(draft):
    """The name of every command the draft records having been built by."""
    return [entry["command"] for entry in package.command_log(draft)]


def test_the_tools_are_the_packages_draft_commands(draft):
    # Underscored, which is the only shape a tool name may have on the wire.
    assert [one.name for one in fix.tools(draft)] == [
        "mark_ignore",
        "question_add",
        "part_add",
        "question_solution",
        "field_replace",
        "split_block",
    ]


@pytest.mark.parametrize(
    "tool, arguments, wrote",
    [
        ("mark_ignore", {"block": "b11"}, "b11.ignore"),
        ("question_add", {"text": "b11"}, "q2.text"),
        ("part_add", {"question": "q1", "text": "b11"}, "q1.p3.text"),
        ("question_solution", {"question": "q1", "text": "s22"}, "q1.solution"),
        (
            "field_replace",
            {"field": "q1.text", "old": "ball", "new": "stone"},
            "q1.text",
        ),
        ("split_block", {"block": "b7", "at": 14}, "b7a and b7b"),
    ],
)
def test_each_tool_runs_its_command_and_in2lambda_logs_it(
    draft, tool, arguments, wrote
):
    result = run(draft, tool, arguments)

    assert result == f"{tool.replace('_', ' ')} wrote {wrote}"
    # The log is in2lambda's, written as it applied the command: the agent keeps
    # no record of its own, and this is the one the fix has to be visible in.
    assert package.command_log(draft)[-1] == {
        "command": tool.replace("_", " "),
        "args": arguments,
        "by": package.BY,
    }


@pytest.mark.parametrize(
    "arguments, layer, edited",
    [
        ({"text": "b11"}, 3, False),
        ({"literal": "Show that the field is solenoidal."}, 4, True),
    ],
)
def test_copying_is_layer_3_and_typing_is_layer_4(draft, arguments, layer, edited):
    run(draft, "question_add", arguments)
    fields = json.loads(draft.read_text())["fields"]

    # in2lambda decides the layer from which argument it was given, so a field's
    # provenance follows from copying or typing rather than from anything said.
    assert (fields["q2.text"]["layer"], fields["q2.text"]["edited"]) == (layer, edited)


def test_the_layers_are_counted_off_the_draft(draft):
    # The spec's own fields, and then one quoted out of the source and one typed.
    spec_fields = package.layers(draft)
    run(draft, "question_add", {"text": "b11"})
    run(draft, "part_add", {"question": "q2", "literal": "Typed out."})

    assert spec_fields == {
        "layer1": 5,
        "layer2": 0,
        "layer3": 0,
        "layer4": 0,
        "edited": 0,
    }
    assert package.layers(draft) == {
        "layer1": 5,
        "layer2": 0,
        "layer3": 1,
        "layer4": 1,
        "edited": 1,
    }


def test_a_literal_the_length_of_a_repair_is_written(draft):
    typed = "x" * fix.LITERAL_MAX

    result = run(draft, "question_solution", {"question": "q1", "literal": typed})

    assert result == "question solution wrote q1.solution"
    assert commands(draft) == ["spec run", "question solution"]


def test_a_literal_longer_than_a_repair_is_refused_before_it_is_written(draft):
    typed = "x" * (fix.LITERAL_MAX + 1)

    result = run(draft, "question_solution", {"question": "q1", "literal": typed})

    assert result.startswith("question solution was refused: ")
    assert f"literal is {len(typed)} characters" in result
    assert f"at most {fix.LITERAL_MAX} may be typed" in result
    # Refused by the agent rather than by in2lambda, so the draft never sees it:
    # what the source does not hold is a finding to report, not a field to fill.
    assert commands(draft) == ["spec run"]


def test_a_literal_that_is_not_text_at_all_is_left_to_in2lambda(draft):
    # What a backend that fills every parameter in sends: the range it means
    # beside a null for the one it does not. The cap measures a string or
    # nothing, so this is a copy like any other rather than the round ending on
    # a length it cannot take.
    result = run(draft, "question_add", {"text": "b11", "literal": None})
    fields = json.loads(draft.read_text())["fields"]

    assert result == "question add wrote q2.text"
    assert (fields["q2.text"]["layer"], fields["q2.text"]["edited"]) == (3, False)


def test_a_literal_of_the_wrong_shape_is_refused_by_in2lambda(draft):
    result = run(draft, "question_solution", {"question": "q1", "literal": 12})

    assert result.startswith("question solution was refused: ")
    assert "rather than a name" in result
    assert commands(draft) == ["spec run"]


def test_every_tool_that_types_says_how_little_it_may_type(draft):
    typing = [
        one for one in fix.tools(draft) if "literal" in one.parameters["properties"]
    ]

    assert [one.name for one in typing] == [
        "question_add",
        "part_add",
        "question_solution",
    ]
    assert all(
        one.parameters["properties"]["literal"]["maxLength"] == fix.LITERAL_MAX
        for one in typing
    )


def test_the_system_prompt_says_what_is_left_alone_rather_than_written():
    # The cap stops a long invention; this is what stops a short one, and what
    # tells a round that a finding it cannot answer is an answer in itself.
    assert "A finding no range of the source can answer is left as it is." in fix.SYSTEM
    assert "never for writing a solution" in fix.SYSTEM
    assert f"at most {fix.LITERAL_MAX} characters" in fix.SYSTEM


def test_a_refusal_comes_back_as_the_tools_result(draft):
    result = run(draft, "mark_ignore", {"block": "b99"})

    assert result.startswith("mark ignore was refused: ")
    assert "no block b99" in result
    # And nothing is recorded as having happened: a command in2lambda refused is
    # not a command the draft was built by.
    assert commands(draft) == ["spec run"]


def test_lines_another_field_has_taken_are_refused_rather_than_written_twice(draft):
    result = run(draft, "question_add", {"text": "b3"})

    assert "was refused" in result and "q1.text" in result
    assert commands(draft) == ["spec run"]


def test_the_prompt_carries_the_source_and_every_finding(draft):
    backend = FakeBackend([])
    shown = package.source_show(draft)
    report = package.validate(draft)

    fix.fix_round(draft, shown, report, backend)
    system, prompt = backend.calls[0]

    assert system == fix.SYSTEM
    assert shown in prompt
    for finding in report.findings:
        # One line of the prompt each: the level, the check, the field and the
        # lines it is about, and then the sentence. Named before the sentence
        # rather than only inside it, so that what a round is given does not
        # depend on how whichever check found it happens to word itself.
        (line,) = [one for one in prompt.splitlines() if finding.message in one]
        assert line.startswith(
            f"- {finding.level} {finding.check} {finding.field} "
        )
        assert all(f"{start}-{end}" in line for start, end in finding.ranges)


def test_a_round_runs_the_commands_the_model_asks_for(draft):
    backend = FakeBackend(
        [
            ("split_block", {"block": "b7", "at": 14}),
            ("question_add", {"text": "b7a"}),
        ]
    )

    reply = fix.fix_round(
        draft, package.source_show(draft), package.validate(draft), backend
    )

    assert [call.name for call in reply.calls] == ["split_block", "question_add"]
    assert reply.calls[1].result == "question add wrote q2.text"
    assert commands(draft) == ["spec run", "split block", "question add"]


def test_the_halves_of_a_split_block_are_shown_to_the_next_round(draft):
    run(draft, "split_block", {"block": "b7", "at": 14})
    shown = package.source_show(draft)

    # The source is read again each round, so that a block the last round cut in
    # two is in front of the model under the ids it can quote.
    assert "b7a" in shown and "b7b" in shown and "b7 " not in shown


@pytest.mark.parametrize(
    "calls, expected",
    [
        ([], "no commands"),
        ([("mark_ignore", {"block": "b9"})], "1 command (mark ignore b9)"),
        (
            [
                ("mark_ignore", {"block": "b9"}),
                ("question_solution", {"question": "q2", "text": "b11"}),
            ],
            "2 commands (mark ignore b9, question solution q2)",
        ),
    ],
)
def test_the_summary_names_what_each_command_was_about(calls, expected):
    made = [ToolCall(name, arguments, "") for name, arguments in calls]

    assert fix.summary(made) == expected
