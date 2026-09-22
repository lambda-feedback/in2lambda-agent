"""The two-route conversion: direct call, filter per set, agreement, adjudication, flags.

Written before the module, from the ME2 introduction pair: the questions PDF and the
solutions PDF Lambda Feedback printed from its own set (their Mathpix markdown is the
fixture), the set as the platform exported it, and one direct-route reply the model gave
for them on 2026-09-21. The PHYS sheet tests read a private corpus and skip without it.
"""

import copy
import difflib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from conftest import FakeBackend

import in2lambda_agent.routes as routes
from in2lambda_agent.settings import Settings

ME2 = Path(__file__).parent / "fixtures" / "me2"
QUESTIONS = (ME2 / "questions.md").read_text()
SOLUTIONS = (ME2 / "solutions.md").read_text()
REPLY = json.loads((ME2 / "direct.json").read_text())
EXPORT = ME2 / "export"
PHYS = Path(
    "/Users/peterbjohnson/code/lambdafeedback/in2lambda-agent/ExampleContents/"
    "PHYS40002-Mechanics/problem_sheets_and_figures"
)
FILTER = Path(__file__).parent / "fixtures" / "ps1-filter.lua"

live = pytest.mark.skipif(not os.environ.get("IN2LAMBDA_LIVE"), reason="calls Mathpix and a model")


def exported():
    return [json.loads(f.read_text()) for f in sorted(EXPORT.glob("question_*.json"))]


def ratio(a, b):
    fold = lambda s: " ".join(routes.fold(s).split())
    return difflib.SequenceMatcher(None, fold(a), fold(b)).ratio()


# --- verbatim -------------------------------------------------------------------------


def test_every_field_of_the_reply_is_a_quote_of_the_markdown():
    assert routes.not_verbatim(REPLY, QUESTIONS + "\n" + SOLUTIONS) == []


def test_a_field_the_model_reworded_is_named():
    reworded = copy.deepcopy(REPLY)
    reworded[0]["parts"][0]["content"] = "What is the mass of the piston, in kg?"
    assert routes.not_verbatim(reworded, QUESTIONS + "\n" + SOLUTIONS) == ["q1.p1.content"]


def test_an_empty_field_is_not_a_quote_of_anything_and_is_not_flagged():
    empty = copy.deepcopy(REPLY)
    empty[4]["parts"][0]["worked_solution"] = ""
    assert routes.not_verbatim(empty, QUESTIONS + "\n" + SOLUTIONS) == []


# --- the document as markdown -----------------------------------------------------------


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc")
def test_an_underlined_run_of_a_docx_is_written_without_a_bracketed_span(tmp_path):
    # The table has a cell of two paragraphs, which commonmark_x cannot write as a pipe
    # table and so writes as raw HTML: a conversion that dropped raw HTML to be rid of
    # the span would write [TABLE] here instead of the numbers.
    source = tmp_path / "sheet.md"
    source.write_text(
        "Find [the mass]{.underline} of the piston.\n\n"
        "+-----------+-----------+\n"
        "| Stress    | Strain    |\n"
        "+===========+===========+\n"
        "| 120 MPa   | 0.8%      |\n"
        "|           |           |\n"
        "| at 400 °C | in 1000 h |\n"
        "+-----------+-----------+\n"
    )
    docx = tmp_path / "sheet.docx"
    subprocess.run(["pandoc", str(source), "-f", "markdown", "-o", str(docx)], check=True)
    markdown, _ = routes.markdown_of(docx, tmp_path, Settings())
    assert "Find the mass of the piston." in markdown
    assert "{.underline}" not in markdown
    assert "<u>" not in markdown and "<span" not in markdown
    for cell in ("Stress", "Strain", "120 MPa", "0.8%", "at 400 °C", "in 1000 h"):
        assert cell in markdown


# --- a display maths that begins or ends with a minus sign --------------------------------


def test_the_me2_worked_solutions_with_separator_minus_signs_are_named():
    assert routes.stray_minus(REPLY) == ["q2.p1.worked_solution", "q3.p1.worked_solution"]


def test_a_minus_on_a_line_of_its_own_beside_a_display_maths_is_stray():
    reply = [
        {
            "title": "",
            "main_text": "The mass entering is:\n-\n\n$$\nm = \\rho U A\n$$\n\n- \n\nwhere $A$ is the area.",
            "parts": [],
        }
    ]
    assert routes.stray_minus(reply) == ["q1.main_text"]


def test_a_minus_inside_the_maths_or_inline_is_not_stray():
    reply = [
        {
            "title": "",
            "main_text": "A difference $$ a-b $$ and an inline $-x$.",
            "parts": [{"content": "- $$\nx = 1\n$$", "options": [], "answer": "", "worked_solution": ""}],
        }
    ]
    assert routes.stray_minus(reply) == []


def test_convert_reports_the_stray_minus_as_a_flag(tmp_path):
    result = routes.convert(
        ME2 / "questions.md",
        solutions=ME2 / "solutions.md",
        out_dir=tmp_path / "out",
        backend=FakeBackend(json.dumps(REPLY)),
        settings=Settings(),
    )
    assert [(f.field, f.reason) for f in result.flags] == [
        ("q2.p1.worked_solution", routes.STRAY_MINUS),
        ("q3.p1.worked_solution", routes.STRAY_MINUS),
    ]


# --- tier 1: agreement ----------------------------------------------------------------


def test_two_identical_replies_dispute_nothing():
    assert routes.disputed(REPLY, copy.deepcopy(REPLY)) == []


def test_notation_that_renders_the_same_is_not_a_dispute():
    other = copy.deepcopy(REPLY)
    text = other[0]["main_text"]
    other[0]["main_text"] = text.replace(r"\mathrm{~m}", r"\space\mathrm{m}").replace("\\left(", "(").replace("\\right)", ")")
    other[0]["parts"][0]["content"] = "  " + other[0]["parts"][0]["content"].replace(" ", "  ")
    assert routes.disputed(REPLY, other) == []


def test_different_wording_is_a_dispute_naming_the_field():
    other = copy.deepcopy(REPLY)
    other[1]["parts"][1]["content"] = "Determine the drag force on the plate, in newtons."
    assert routes.disputed(REPLY, other) == ["q2.p2.content"]


def test_a_part_one_route_did_not_find_is_a_structural_dispute():
    other = copy.deepcopy(REPLY)
    del other[3]["parts"][2]
    assert routes.disputed(REPLY, other) == ["q4.p3"]


# --- the set ----------------------------------------------------------------------------


def test_the_reply_becomes_a_set_with_the_exports_questions():
    built = routes.to_set(REPLY, name="Introduction")
    assert [q.title for q in built.questions] == [q["title"] for q in exported()]
    assert [len(q.parts) for q in built.questions] == [len(q["parts"]) for q in exported()]


def test_the_zip_holds_one_file_per_question_with_the_exports_keys(tmp_path):
    zip_path = routes.build(routes.to_set(REPLY, name="Introduction"), tmp_path / "out")
    names = zipfile.ZipFile(zip_path).namelist()
    assert sorted(n for n in names if n.startswith("question_")) == sorted(f.name for f in EXPORT.glob("question_*.json"))
    written = json.loads(zipfile.ZipFile(zip_path).read("question_000_Hydraulic_scale.json"))
    export_keys = set(exported()[0].keys())
    assert set(written.keys()) <= export_keys
    assert set(written["parts"][0].keys()) <= set(exported()[0]["parts"][0].keys())


def test_the_reply_matches_the_export_it_was_printed_from():
    built = routes.to_set(REPLY, name="Introduction")
    for q, e in zip(built.questions, exported()):
        assert ratio(q.main_text, e["masterContent"]) >= 0.9, q.title
        for p, ep in zip(q.parts, e["parts"]):
            assert ratio(p.text, ep["content"]) >= 0.6, (q.title, ep["content"][:40])
            # 0.5, not higher: Mathpix reads the platform's separator lines as minus signs
            # inside the maths of the printed worked solutions (Towing a submarine, part 1).
            expected = (ep.get("workedSolution") or {}).get("content", "")
            if expected:
                assert ratio(p.worked_solution, expected) >= 0.5, (q.title, "worked solution")


def test_the_options_of_a_multiple_choice_part_are_kept_apart_from_its_text():
    built = routes.to_set(REPLY, name="Introduction")
    frames = built.questions[4]
    assert "location of a particle" not in frames.parts[0].text
    assert len(REPLY[4]["parts"][0]["options"]) == 4


# --- tier 2: adjudication ---------------------------------------------------------------


def test_the_adjudicator_may_pick_one_side_and_its_pick_is_kept():
    other = copy.deepcopy(REPLY)
    other[1]["parts"][1]["content"] = "Determine the drag force on the plate, in newtons."
    backend = FakeBackend(json.dumps([{"field": "q2.p2.content", "choice": "A", "reason": "B adds words the source lacks"}]))
    verdicts = routes.adjudicate(REPLY, other, ["q2.p2.content"], QUESTIONS + "\n" + SOLUTIONS, backend)
    assert verdicts == {"q2.p2.content": ("A", "B adds words the source lacks")}
    ((_, prompt),) = backend.calls
    assert "Determine the drag force on the plate." in prompt and "in newtons" in prompt
    assert len(prompt) < 4000  # the disputed field and its source lines, not the document


def test_the_adjudicators_own_words_are_refused_and_the_field_is_flagged():
    other = copy.deepcopy(REPLY)
    other[1]["parts"][1]["content"] = "Determine the drag force on the plate, in newtons."
    backend = FakeBackend(json.dumps([{"field": "q2.p2.content", "choice": "text", "text": "Find the drag on the plate.", "reason": "shorter"}]))
    verdicts = routes.adjudicate(REPLY, other, ["q2.p2.content"], QUESTIONS + "\n" + SOLUTIONS, backend)
    assert verdicts["q2.p2.content"][0] == "person"


# --- tier 3: the report -----------------------------------------------------------------


def test_the_report_lists_only_what_a_person_must_read():
    other = copy.deepcopy(REPLY)
    other[1]["parts"][1]["content"] = "Determine the drag force on the plate, in newtons."
    del other[3]["parts"][2]
    result = routes.reconcile(REPLY, other, QUESTIONS + "\n" + SOLUTIONS, backend=FakeBackend(json.dumps([{"field": "q2.p2.content", "choice": "A", "reason": "B adds words"}])))
    assert result.agreed >= 50
    assert [f.field for f in result.flags] == ["q4.p3"]
    assert result.fields[1]["parts"][1]["content"] == REPLY[1]["parts"][1]["content"]


# --- route B over two documents -----------------------------------------------------------

# Short enough to read whole, so that what a test changes is the only difference.
SHEET = "A ball is thrown straight up.\n\nFind the greatest height.\n\n$h = v^2/2g = 20.4$"


def one(content="Find the greatest height.", **over):
    part = {"content": content, "options": [], "answer": "", "worked_solution": ""}
    return [{"title": "", "main_text": "A ball is thrown straight up.", "parts": [part | over]}]


def test_route_b_takes_its_answers_from_the_run_over_the_solutions():
    solutions = [
        {"parts": [{"answer": "$h = 20.4$", "worked_solution": "$h = v^2/2g$"}]},
        {"parts": [{"answer": "of a question this sheet does not have"}]},
    ]
    merged = routes.merge(one(), solutions)
    assert len(merged) == 1
    assert merged[0]["parts"][0]["content"] == "Find the greatest height."
    assert merged[0]["parts"][0]["answer"] == "$h = 20.4$"
    assert merged[0]["parts"][0]["worked_solution"] == "$h = v^2/2g$"


def test_a_question_with_no_sub_questions_is_one_empty_part_either_way():
    # Route A's prompt says so; a filter that leaves the parts out means the same.
    partless = [{"title": "", "main_text": "A ball is thrown straight up.", "parts": []}]
    result = routes.reconcile(one(content=""), partless, SHEET)
    assert result.flags == []
    assert result.adjudicated == 0


def test_a_field_only_one_route_found_is_taken_from_it_with_no_call():
    backend = FakeBackend()  # No replies: a call would raise rather than answer.
    result = routes.reconcile(one(), one(answer="$h = v^2/2g = 20.4$"), SHEET, backend)
    assert backend.calls == []
    assert result.defaulted == 1
    assert result.adjudicated == 0
    assert result.agreed + result.defaulted == len(routes.fields(one()))
    assert result.fields[0]["parts"][0]["answer"] == "$h = v^2/2g = 20.4$"
    assert result.flags == []


def test_the_fields_of_a_question_one_route_missed_are_defaulted_not_agreed():
    # Route B read nothing here. Counting the question's fields as agreed would
    # report the two routes as having checked each other over a sheet only one of
    # them read; there was nothing to compare, so they come from route A.
    result = routes.reconcile(one(), [], SHEET)
    assert (result.agreed, result.defaulted, result.adjudicated) == (0, 5, 0)
    assert result.defaulted == len(routes.fields(one()))
    assert [f.field for f in result.flags] == ["q1"]


# --- route B: the filter ----------------------------------------------------------------


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc")
def test_the_filter_is_told_which_of_the_two_documents_it_is_reading():
    role = Path(__file__).parent / "fixtures" / "role-filter.lua"
    sheet = Path(__file__).parent / "fixtures" / "sheet.md"
    assert routes.run_filter(role, sheet)[0]["title"] == "questions"
    assert routes.run_filter(role, sheet, role="solutions")[0]["title"] == "solutions"


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc")
def test_the_filter_call_sees_both_documents_and_how_to_tell_them_apart():
    fixtures = Path(__file__).parent / "fixtures"
    backend = FakeBackend("```lua\nfunction Pandoc(doc) end\n```")
    lua, _ = routes.write_filter(fixtures / "tex-sheet.tex", fixtures / "tex-sheet-2.tex", backend)
    ((_, prompt),) = backend.calls
    assert lua == "function Pandoc(doc) end"
    assert "Kinematics" in prompt and "Energy" in prompt
    assert "in2lambda_role" in prompt

    alone = FakeBackend("function Pandoc(doc) end")
    routes.write_filter(fixtures / "tex-sheet.tex", None, alone)
    ((_, prompt),) = alone.calls
    assert "Energy" not in prompt and "in2lambda_role" not in prompt


@pytest.mark.skipif(not PHYS.is_dir() or shutil.which("pandoc") is None, reason="private corpus and pandoc")
def test_a_filter_written_for_the_set_reads_a_sheet_with_pandoc_alone():
    reply = routes.run_filter(FILTER, PHYS / "mechanics_23-24_PS1.tex")
    assert len(reply) == 10
    assert [len(q["parts"]) for q in reply] == [0] * 9 + [5]
    markdown = subprocess.check_output(["pandoc", str(PHYS / "mechanics_23-24_PS1.tex"), "-t", "commonmark_x", "--wrap=none"]).decode()
    assert routes.not_verbatim(reply, markdown) == []


# --- a folder of sheets -------------------------------------------------------------------

FIXTURES = Path(__file__).parent / "fixtures"

# What the model would answer for the two fixture sheets, written to match what
# tests/fixtures/pair-filter.lua reads out of them: the same questions, one part each. The
# worked solution of paired's first question is left out, so that route B's is defaulted
# into it, and the second question's main_text is shortened, so that one field is
# adjudicated.
PAIRED_DIRECT = [
    {
        "title": "", "parts": [{"content": "", "options": [], "answer": "", "worked_solution": ""}],
        "main_text": "A cylinder of radius $r$ rolls along the ground without slipping.\n\n(a) Find its angular velocity at speed $v$.\n\n(b) Find its kinetic energy.",
    },
    {
        "title": "", "main_text": "A spring of stiffness $k$ carries a mass $m$.",
        "parts": [{"content": "", "options": [], "answer": "",
                   "worked_solution": "2(a) $T = 2\\pi\\sqrt{m/k}$\n\n2(b) $v = A\\sqrt{k/m}$"}],
    },
]
SHEET_DIRECT = [
    {
        "title": "", "parts": [{"content": "", "options": [], "answer": "", "worked_solution": ""}],
        "main_text": "A ball is thrown straight up at $20\\,\\mathrm{m/s}$.\n\n(a) Find the greatest height it reaches.\n\n(b) Find its time of flight.",
    },
    {
        "title": "", "parts": [{"content": "", "options": [], "answer": "", "worked_solution": ""}],
        "main_text": "A block of mass $m$ rests on a slope of angle $\\theta$.\n\n(a) Name the three forces acting on the block.\n\n(b) Find the least coefficient of friction that holds it still.",
    },
]


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc")
def test_a_folder_runs_one_filter_over_every_sheet_and_reports_each(tmp_path):
    folder = tmp_path / "sheets"
    folder.mkdir()
    for name in ("paired.md", "paired_solutions.md", "sheet.md"):
        shutil.copy(FIXTURES / name, folder / name)
    backend = FakeBackend(
        (FIXTURES / "pair-filter.lua").read_text(),
        json.dumps(PAIRED_DIRECT),
        json.dumps([{"field": "q2.main_text", "choice": "A", "reason": "B carries the parts too"}]),
        json.dumps(SHEET_DIRECT),
    )
    result = routes.convert_folder(folder, out_dir=tmp_path / "out", backend=backend)

    assert (tmp_path / "out" / "filter.lua").is_file()
    assert "in2lambda_role" in backend.calls[0][1]  # the filter call saw both documents
    assert [name for name, _ in result.sheets] == ["paired", "sheet"]
    assert result.report() == [
        "paired: 10 fields, agreed 8, defaulted 1, adjudicated 1, flagged 0",
        "sheet: 10 fields, agreed 10, defaulted 0, adjudicated 0, flagged 0",
        "2 sheets: 20 fields, agreed 18, defaulted 1, adjudicated 1, flagged 0",
    ]
    assert all(converted.zip_path.is_file() for _, converted in result.sheets)
    # The worked solution route A left empty is route B's, read from the solutions file.
    paired = dict(result.sheets)["paired"]
    assert paired.reply[0]["parts"][0]["worked_solution"].startswith("1(a) $\\omega")


def test_a_folder_with_no_sheet_in_it_names_what_a_folder_run_converts(tmp_path):
    # A mistyped path and a folder holding solutions alone both pair to
    # nothing. Neither reaches in2lambda, so this is the only place that can
    # say what is wrong, and no model call is made for either.
    lone = tmp_path / "sheets"
    lone.mkdir()
    (lone / "Sheet_1_solutions.tex").write_text("x")
    backend = FakeBackend()  # No replies: a call would raise rather than answer.

    for folder in (lone, tmp_path / "nope"):
        with pytest.raises(ValueError, match="holds no sheet to convert"):
            routes.convert_folder(folder, out_dir=tmp_path / "out", backend=backend)
    assert backend.calls == []


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc")
def test_a_sheet_whose_filter_run_fails_keeps_its_route_a_reply(tmp_path):
    # One sheet of a folder must not stop the other eight.
    lua = tmp_path / "broken.lua"
    lua.write_text("this is not a filter\n")
    backend = FakeBackend(json.dumps(SHEET_DIRECT))
    result = routes.convert(FIXTURES / "sheet.md", out_dir=tmp_path / "out", backend=backend, lua=lua)

    assert result.route_b_error
    assert result.reply == SHEET_DIRECT
    assert (result.fields, result.agreed, result.flags) == (0, 0, [])


# --- live -------------------------------------------------------------------------------


@live
@pytest.mark.skipif(not PHYS.is_dir(), reason="private corpus")
def test_the_phys_folder_converts_through_both_routes(tmp_path):
    # The ticket's run: nine sheets and their solutions, one filter, one report.
    result = routes.convert_folder(PHYS, out_dir=tmp_path / "out", cache_dir=tmp_path / "cache")
    print("\n" + "\n".join(result.report()))
    assert len(result.sheets) == 9
    assert all(converted.zip_path.is_file() for _, converted in result.sheets)


@live
def test_the_me2_pair_converts_with_no_flag(tmp_path):
    target = Path("ExampleContents/targets/ME2_Fluids_introduction")
    (pdf,) = [p for p in target.glob("*.pdf") if "solutions" not in p.name]
    (solutions,) = target.glob("*solutions.pdf")
    result = routes.convert(pdf, solutions=solutions, out_dir=tmp_path / "out")
    # The printed solutions PDF holds separator lines that Mathpix reads as minus signs,
    # so the worked solutions of Friction on a plate and Towing a submarine are flagged.
    assert [(f.field, f.reason) for f in result.flags] == [
        ("q2.p1.worked_solution", routes.STRAY_MINUS),
        ("q3.p1.worked_solution", routes.STRAY_MINUS),
    ]
    assert [q.title for q in result.set.questions] == [q["title"] for q in exported()]
