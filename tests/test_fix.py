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
        "field_set",
        "part_solution",
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
        ("field_set", {"field": "q1.text", "text": "s22"}, "q1.text"),
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


def test_a_field_set_writes_a_written_field_from_the_lines_it_names(draft):
    # The finding it answers: a field that is empty, or that took the wrong
    # lines. What the field held before is dropped, so the lines it came from
    # are in no field and the next round is told about them.
    before = json.loads(draft.read_text())["fields"]["q1.text"]["ranges"]

    result = run(draft, "field_set", {"field": "q1.text", "text": "s22"})
    written = json.loads(draft.read_text())["fields"]["q1.text"]

    assert result == "field set wrote q1.text"
    assert before == [[5, 5]]
    assert (written["ranges"], written["layer"], written["edited"]) == (
        [[22, 22]],
        3,
        False,
    )


def test_a_part_solution_answers_one_part_rather_than_the_question(draft):
    # A sheet that writes a solution under each part: the part is added from
    # the lines holding it, and then answered on its own.
    run(draft, "part_add", {"question": "q1", "text": "b7"})

    result = run(draft, "part_solution", {"part": "q1.p3", "text": "s22"})
    written = json.loads(draft.read_text())["fields"]["q1.p3.solution"]

    assert result == "part solution wrote q1.p3.solution"
    assert (written["ranges"], written["layer"], written["edited"]) == (
        [[22, 22]],
        3,
        False,
    )
    assert package.command_log(draft)[-1] == {
        "command": "part solution",
        "args": {"part": "q1.p3", "text": "s22"},
        "by": package.BY,
    }


def test_a_part_solution_typed_out_is_layer_4(draft):
    run(draft, "part_add", {"question": "q1", "text": "b7"})

    result = run(draft, "part_solution", {"part": "q1.p3", "literal": "Term by term."})
    written = json.loads(draft.read_text())["fields"]["q1.p3.solution"]

    assert result == "part solution wrote q1.p3.solution"
    assert (written["layer"], written["edited"]) == (4, True)


def test_a_part_solution_longer_than_a_repair_is_refused_before_it_is_written(draft):
    typed = "x" * (fix.LITERAL_MAX + 1)
    run(draft, "part_add", {"question": "q1", "text": "b7"})

    result = run(draft, "part_solution", {"part": "q1.p3", "literal": typed})

    assert result.startswith("part solution was refused: ")
    assert f"literal is {len(typed)} characters" in result
    assert commands(draft) == ["spec run", "part add"]


def test_the_system_prompt_names_the_two_commands_that_write_a_written_field():
    assert "part_solution      one part's own worked solution" in fix.SYSTEM
    assert "field_set          quotes other lines into a field" in fix.SYSTEM
    assert "It has no literal" in fix.SYSTEM


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


@pytest.mark.parametrize(
    "old, regex",
    [
        ("", False),
        ("WHOLE", False),
        (".*", True),
        ("^.*$", True),
    ],
)
def test_a_field_replace_that_writes_the_whole_field_is_refused(draft, old, regex):
    # The one command whose typed argument could become the field's whole text:
    # `old` matching nothing or everything makes `new` the field, which is the
    # model writing what the document does not say.
    value = package.field_value(draft, "q1.text")
    arguments = {
        "field": "q1.text",
        "old": value if old == "WHOLE" else old,
        "new": "A ship's resistance force.",
    }
    if regex:
        arguments["regex"] = True

    result = run(draft, "field_replace", arguments)

    assert result.startswith("field replace was refused: q1.text ")
    assert "does not write a field" in result
    assert commands(draft) == ["spec run"]


def test_an_empty_field_is_not_written_by_a_regex_that_matches_it(draft):
    # The refusal the ticket is about: `q4.text (lines 32-33) is empty` answered
    # with `--old '^$' --regex`, which makes `new` the field's whole text. An
    # empty field is repaired by quoting the source range into it instead.
    run(draft, "question_add", {"literal": ""})

    result = run(
        draft,
        "field_replace",
        {"field": "q2.text", "old": "^$", "new": "A ship's hull.", "regex": True},
    )

    assert result.startswith("field replace was refused: q2.text ")
    assert "does not write a field" in result
    assert commands(draft) == ["spec run", "question add"]


def test_a_field_replace_repairing_wording_is_still_written(draft):
    result = run(
        draft, "field_replace", {"field": "q1.text", "old": "ball", "new": "stone"}
    )

    assert result == "field replace wrote q1.text"
    assert commands(draft) == ["spec run", "field replace"]


def test_a_replacement_longer_than_a_repair_is_refused_before_it_is_written(draft):
    typed = "x" * (fix.LITERAL_MAX + 1)

    result = run(
        draft, "field_replace", {"field": "q1.text", "old": "ball", "new": typed}
    )

    assert result.startswith("field replace was refused: ")
    assert f"new is {len(typed)} characters" in result
    assert f"at most {fix.LITERAL_MAX} may be typed" in result
    assert commands(draft) == ["spec run"]


def test_a_replacement_the_length_of_a_repair_is_written(draft):
    typed = "x" * fix.LITERAL_MAX

    result = run(
        draft, "field_replace", {"field": "q1.text", "old": "ball", "new": typed}
    )

    assert result == "field replace wrote q1.text"
    assert commands(draft) == ["spec run", "field replace"]


def test_a_field_replace_naming_no_field_of_the_draft_is_left_to_in2lambda(draft):
    result = run(
        draft, "field_replace", {"field": "q9.text", "old": "", "new": "Anything."}
    )

    assert result.startswith("field replace was refused: ")
    assert "does not write a field" not in result
    assert commands(draft) == ["spec run"]


def test_the_replacement_is_capped_as_a_literal_is(draft):
    tool = next(one for one in fix.tools(draft) if one.name == "field_replace")

    assert tool.parameters["properties"]["new"]["maxLength"] == fix.LITERAL_MAX
    assert "not be empty or the whole of the field" in tool.description
    assert f"at most {fix.LITERAL_MAX} characters" in tool.description


def test_the_system_prompt_says_a_field_replace_repairs_rather_than_writes():
    assert "it may not\n                     be empty or the whole of the field" in (
        fix.SYSTEM
    )
    assert "does not write a field" in fix.SYSTEM
    assert f"at most {fix.LITERAL_MAX} characters, as a literal is" in fix.SYSTEM


@pytest.mark.parametrize(
    "arguments, named",
    [
        ({"field": "q1.text", "old": "", "new": "Typed out."}, ["q1.text"]),
        ({"field": "q1.text", "old": "ball", "new": "stone"}, []),
        ({"field": "q9.text", "old": "ball", "new": "stone"}, []),
    ],
)
def test_unrepaired_names_the_field_a_write_was_refused_over(draft, arguments, named):
    # What the pipeline reads to tell a finding the loop cannot repair from one
    # it answered, or from a command in2lambda refused for its own reasons.
    result = run(draft, "field_replace", arguments)

    assert fix.unrepaired([ToolCall("field_replace", arguments, result)]) == named


def test_unrepaired_says_nothing_of_a_refusal_that_is_not_a_write(draft):
    result = run(draft, "mark_ignore", {"block": "b99"})

    assert fix.unrepaired([ToolCall("mark_ignore", {"block": "b99"}, result)]) == []


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

    # `field_set` is not among them: in2lambda takes no literal for it, so a
    # field it writes says what the source says and nothing else.
    assert [one.name for one in typing] == [
        "question_add",
        "part_add",
        "question_solution",
        "part_solution",
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
        (
            [("part_solution", {"part": "q1.p3", "text": "s22"})],
            "1 command (part solution q1.p3)",
        ),
    ],
)
def test_the_summary_names_what_each_command_was_about(calls, expected):
    made = [ToolCall(name, arguments, "") for name, arguments in calls]

    assert fix.summary(made) == expected
