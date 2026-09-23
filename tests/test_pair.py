"""Reading a solutions document's questions document off the file names."""

from pathlib import Path

import pytest

from in2lambda_agent import pair


@pytest.mark.parametrize(
    "name, stem",
    [
        ("Worksheet_1_solutions.pdf", "Worksheet_1"),
        ("Tutorial_2_Solutions.pdf", "Tutorial_2"),
        ("sheet-solutions.md", "sheet"),
        ("Sheet 1 Solutions.docx", "Sheet 1"),
        ("Sheet1_Sol.pdf", "Sheet1"),
        ("worksheet-sol.md", "worksheet"),
        # No separator before `solutions`, so the word is part of a longer one.
        ("resolutions.pdf", None),
        ("aerosol.pdf", None),
        # Nothing before the separator, so there is no stem to pair with.
        ("Solutions.pdf", None),
        ("Worksheet_1.pdf", None),
    ],
)
def test_a_solutions_document_is_read_off_its_name(name, stem):
    assert pair.questions_stem(Path("sheets") / name) == stem


def test_the_questions_document_is_the_one_beside_it(tmp_path):
    (tmp_path / "Worksheet_1.pdf").write_bytes(b"%PDF")
    solutions = tmp_path / "Worksheet_1_solutions.pdf"
    solutions.write_bytes(b"%PDF")

    assert pair.questions_beside(solutions) == tmp_path / "Worksheet_1.pdf"
    assert pair.solutions_beside(tmp_path / "Worksheet_1.pdf") == solutions


def test_a_file_of_another_suffix_is_not_the_questions_document(tmp_path):
    # The pairing is within one suffix: a tex sheet does not answer to the
    # solutions someone scanned.
    (tmp_path / "Worksheet_1.tex").write_text("\\begin{document}\\end{document}")
    solutions = tmp_path / "Worksheet_1_solutions.pdf"
    solutions.write_bytes(b"%PDF")

    assert pair.questions_beside(solutions) is None
    assert pair.solutions_beside(tmp_path / "Worksheet_1.tex") is None


def test_a_document_with_no_solutions_beside_it_is_run_alone(tmp_path):
    questions = tmp_path / "Worksheet_1.md"
    questions.write_text("# Sheet\n")

    assert pair.of(questions) == (questions, None)


def test_naming_the_solutions_document_runs_the_questions_document(tmp_path):
    questions = tmp_path / "Worksheet_1.md"
    questions.write_text("# Sheet\n")
    solutions = tmp_path / "Worksheet_1_solutions.md"
    solutions.write_text("# Solutions\n")

    assert pair.of(solutions) == (questions, solutions)
    assert pair.of(questions) == (questions, solutions)


def test_a_folder_that_is_not_there_holds_no_companion(tmp_path):
    # The pairing is the first thing a run does, so a mistyped path reaches it
    # before anything has read the source. It leaves the complaining to
    # in2lambda rather than raising an OSError of its own here.
    absent = tmp_path / "nope"

    assert pair.questions_beside(absent / "Worksheet_1_solutions.pdf") is None
    assert pair.solutions_beside(absent / "Worksheet_1.pdf") is None
    assert pair.of(absent / "Worksheet_1.pdf") == (absent / "Worksheet_1.pdf", None)
    assert pair.of(absent / "Worksheet_1_solutions.pdf") == (
        absent / "Worksheet_1_solutions.pdf",
        None,
    )


def test_the_sheets_of_a_folder_come_paired_and_in_name_order(tmp_path):
    for name in ("Sheet_2.tex", "Sheet_1.tex", "Sheet_1_solutions.tex", "notes.png"):
        (tmp_path / name).write_text("x")
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "ball.tex").write_text("x")

    assert pair.pairs_in(tmp_path) == [
        (tmp_path / "Sheet_1.tex", tmp_path / "Sheet_1_solutions.tex"),
        (tmp_path / "Sheet_2.tex", None),
    ]


def test_a_sheet_and_the_file_compiled_from_it_are_one_sheet(tmp_path):
    # The folder holds the LaTeX source beside the PDF built from it. The two
    # hold the same questions, so converting both would convert the sheet
    # twice, under one name. Pandoc reads the source and cannot read the PDF,
    # so the source is the sheet.
    for name in ("Sheet_1.tex", "Sheet_1.pdf"):
        (tmp_path / name).write_text("x")

    assert pair.pairs_in(tmp_path) == [(tmp_path / "Sheet_1.tex", None)]


def test_a_folders_solutions_file_is_not_a_sheet_of_its_own(tmp_path):
    # Its questions document is not there, so there is nothing to compare two
    # routes over. The folder run leaves it out; `of` still converts it alone.
    (tmp_path / "Sheet_3_Sol.pdf").write_bytes(b"%PDF")

    assert pair.pairs_in(tmp_path) == []


def test_a_targets_two_documents_are_read_by_role_not_by_name(tmp_path):
    # The ME2 target: the solutions document was printed from Lambda Feedback
    # months after the sheet, so the two names share nothing but the course.
    questions = tmp_path / "mech50010_fluid_mechanics_S1_20251210163022 (2).pdf"
    solutions = tmp_path / "mech50010_fluid_mechanics_S1_20260921091254_solutions.pdf"
    for document in (questions, solutions):
        document.write_bytes(b"%PDF")
    (tmp_path / "set_Introduction").mkdir()

    assert pair.target_documents(tmp_path) == (questions, solutions)
    # And by stem, which is what a folder of sheets is paired by, they are two
    # sheets and neither answers the other.
    assert pair.pairs_in(tmp_path) == [(questions, None)]


def test_a_targets_documents_may_share_a_stem(tmp_path):
    # EART40013's CW2: the same pair, named the way a folder of sheets names it.
    questions = tmp_path / "EART40013_S2_20260202164327.tex"
    solutions = tmp_path / "EART40013_S2_20260202164327_solutions.tex"
    for document in (questions, solutions):
        document.write_text("x")

    assert pair.target_documents(tmp_path) == (questions, solutions)


def test_a_target_with_no_solutions_document_has_none(tmp_path):
    questions = tmp_path / "CW1.pdf"
    questions.write_bytes(b"%PDF")

    assert pair.target_documents(tmp_path) == (questions, None)


@pytest.mark.parametrize(
    "names, complaint",
    [
        (["Sheet_1.tex", "Sheet_2.tex"], "2 questions documents: Sheet_1.tex, Sheet_2.tex"),
        (
            ["Sheet_1.tex", "Sheet_1_solutions.tex", "Sheet_2_sol.tex"],
            "2 solutions documents: Sheet_1_solutions.tex, Sheet_2_sol.tex",
        ),
        (["notes.png"], "no questions document"),
    ],
)
def test_a_target_folder_that_holds_more_than_one_set_is_refused(tmp_path, names, complaint):
    for name in names:
        (tmp_path / name).write_text("x")

    with pytest.raises(ValueError) as refused:
        pair.target_documents(tmp_path)
    assert complaint in str(refused.value)


def test_solutions_with_no_questions_run_alone(tmp_path):
    # The markers above the solutions are this document's questions, so the
    # document converts with no second file.
    solutions = tmp_path / "Tutorial_2_Solutions.pdf"
    solutions.write_bytes(b"%PDF")

    assert pair.of(solutions) == (solutions, None)
