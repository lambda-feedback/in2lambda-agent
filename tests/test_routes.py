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


# --- route B: the filter ----------------------------------------------------------------


@pytest.mark.skipif(not PHYS.is_dir() or shutil.which("pandoc") is None, reason="private corpus and pandoc")
def test_a_filter_written_for_the_set_reads_a_sheet_with_pandoc_alone():
    reply = routes.run_filter(FILTER, PHYS / "mechanics_23-24_PS1.tex")
    assert len(reply) == 10
    assert [len(q["parts"]) for q in reply] == [0] * 9 + [5]
    markdown = subprocess.check_output(["pandoc", str(PHYS / "mechanics_23-24_PS1.tex"), "-t", "commonmark_x", "--wrap=none"]).decode()
    assert routes.not_verbatim(reply, markdown) == []


# --- live -------------------------------------------------------------------------------


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
