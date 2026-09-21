"""The sweep over a corpus: one row per document, and a replay with no model in it."""

import json
import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from conftest import FakeBackend
from test_pipeline import FAULTY_SPEC, FIXES, PAIRED_SPEC, SPEC, TEX_SPEC

from in2lambda_agent import corpus, package, pipeline
from in2lambda_agent.settings import Settings
from in2lambda_agent.spec import SPEC_NAME

FIXTURES = Path(__file__).parent / "fixtures"


def make_set(root, folder, names):
    """One document set of the miniature corpus: a folder of sheets."""
    made = root / folder
    made.mkdir(parents=True)
    for name in names:
        shutil.copy(FIXTURES / name, made / name)
    return made


@pytest.fixture
def root(tmp_path):
    """A corpus of two sets, a markdown one and a tex one."""
    made = tmp_path / "corpus"
    make_set(made, "sheets", ["sheet.md", "sheet-2.md"])
    make_set(made, "tex", ["tex-sheet.tex", "tex-sheet-2.tex"])
    return made


def sweep(root, tmp_path, **kwargs):
    """A sweep with its three directories under tmp_path rather than the corpus."""
    return corpus.sweep(
        root,
        results=tmp_path / "results.csv",
        work=tmp_path / "work",
        specs=kwargs.pop("specs", tmp_path / "specs"),
        settings=Settings(),
        **kwargs,
    )


def contents(folder):
    """Every file under a folder, by its path relative to it."""
    return sorted(
        path.relative_to(folder).as_posix()
        for path in folder.rglob("*")
        if path.is_file()
    )


def test_a_fresh_sweep_calls_once_per_set_and_the_rows_say_which(root, tmp_path):
    backend = FakeBackend(SPEC, TEX_SPEC)
    before = contents(root)

    rows = sweep(root, tmp_path, backend=backend)

    # One call per set, and the sheets of a set run together and in order, so
    # the first of them writes the spec and the rest reuse it.
    assert len(backend.calls) == 2
    assert [row.source for row in rows] == [
        "sheets/sheet-2.md",
        "sheets/sheet.md",
        "tex/tex-sheet-2.tex",
        "tex/tex-sheet.tex",
    ]
    assert [row.set for row in rows] == ["sheets", "sheets", "tex", "tex"]
    assert [row.spec for row in rows] == ["wrote", "reused", "wrote", "reused"]
    assert [row.outcome for row in rows] == ["built"] * 4
    # A zip was written, so the reason says what the build went past rather
    # than why there is none: nothing for the sheets, whose solutions are on
    # them, and the no-solution warnings for the tex set, whose are not.
    assert [row.reason for row in rows[:2]] == ["", ""]
    assert all("has no solution" in row.reason for row in rows[2:])
    # The specs are kept in a tree mirroring the corpus, which is what makes a
    # later sweep a replay.
    assert (tmp_path / "specs" / "sheets" / SPEC_NAME).read_text() == SPEC
    assert (tmp_path / "specs" / "tex" / SPEC_NAME).read_text() == TEX_SPEC
    # And the corpus is untouched: every run happened in the work directory.
    assert contents(root) == before


def test_the_row_says_what_the_spec_made_of_the_document(root, tmp_path):
    rows = sweep(root, tmp_path, backend=FakeBackend(SPEC, TEX_SPEC))
    sheet = next(row for row in rows if row.source == "sheets/sheet.md")

    assert (sheet.layout, sheet.blocks, sheet.fields) == ("PartsSepSol", 14, 10)
    assert (sheet.layer1, sheet.layer3, sheet.edited) == (10, 0, 0)
    assert (sheet.unassigned, sheet.rounds) == (0, 0)
    assert (sheet.review, sheet.rejections) == ("none", 0)
    # The reused run made no call; the wall clock still ran.
    assert sheet.input_tokens == sheet.output_tokens == 0
    assert sheet.model_seconds == 0.0 and sheet.wall_seconds > 0


def test_the_sweeps_cache_is_where_each_run_looks_for_its_ocr(
    root, tmp_path, monkeypatch
):
    given = []

    def record(source, **kwargs):
        given.append(kwargs["cache_dir"])
        raise RuntimeError("as far as this goes")

    monkeypatch.setattr(pipeline, "run", record)

    rows = sweep(root, tmp_path, cache=tmp_path / "shared")

    assert given == [tmp_path / "shared"] * len(rows)


def test_a_saved_spec_and_its_source_replay_with_no_model_call(root, tmp_path):
    for folder, text in (("sheets", SPEC), ("tex", TEX_SPEC)):
        saved = tmp_path / "specs" / folder / SPEC_NAME
        saved.parent.mkdir(parents=True)
        saved.write_text(text)
    backend = FakeBackend(reason="a replay makes no call")

    rows = sweep(root, tmp_path, replay=True, backend=backend)
    again = sweep(root, tmp_path, replay=True, backend=backend)

    assert backend.calls == []
    assert [row.outcome for row in rows] == ["built"] * 4
    assert [row.spec for row in rows] == ["reused"] * 4
    assert all(row.input_tokens == row.output_tokens == 0 for row in rows)
    # Deterministic: a saved spec plus its source is the same draft every time,
    # and only the clocks differ between two replays of it.
    assert [without_clocks(row) for row in rows] == [
        without_clocks(row) for row in again
    ]


def test_a_sweep_keeps_each_documents_log_beside_the_sets_spec(tmp_path):
    root = tmp_path / "corpus"
    make_set(root, "faulty", ["faulty.md"])

    sweep(root, tmp_path, backend=FakeBackend(FAULTY_SPEC, FIXES))
    saved = tmp_path / "specs" / "faulty" / f"faulty.md{corpus.COMMANDS_SUFFIX}"
    draft = package.draft_of(tmp_path / "work" / "faulty" / "faulty.md")

    # The round's commands, and not the spec run before them: a replay runs the
    # set's spec itself.
    assert json.loads(saved.read_text()) == package.fix_log(draft)
    assert [one["command"] for one in json.loads(saved.read_text())] == [
        "split block",
        "question add",
        "part add",
        "question solution",
        "field replace",
    ]
    # The log and nothing beside it: the draft's `fields`, which hold every
    # field's captured text, stay in the work directory.
    assert "fields" not in saved.read_text()


def test_a_document_that_took_no_round_keeps_an_empty_log(root, tmp_path):
    sweep(root, tmp_path, backend=FakeBackend(SPEC, TEX_SPEC))
    saved = tmp_path / "specs" / "sheets" / f"sheet.md{corpus.COMMANDS_SUFFIX}"

    # Written all the same: a replay that finds no file there reads it as a
    # document no sweep has run.
    assert json.loads(saved.read_text()) == []


def test_a_replay_runs_the_saved_log_and_builds_what_the_rounds_repaired(
    tmp_path, monkeypatch
):
    root = tmp_path / "corpus"
    make_set(root, "faulty", ["faulty.md"])
    (swept,) = sweep(root, tmp_path, backend=FakeBackend(FAULTY_SPEC, FIXES))
    monkeypatch.setattr(
        pipeline,
        "choose_backend",
        lambda settings: pytest.fail("a replay chose a backend"),
    )

    (row,) = sweep(root, tmp_path, replay=True, backend=None)

    # The sweep took a round to build this document, and the replay builds it
    # with the same fields and no model call at all.
    assert (swept.outcome, row.outcome) == ("built", "built")
    assert (row.rounds, row.input_tokens, row.output_tokens) == (0, 0, 0)
    assert (row.layer3, row.edited) == (swept.layer3, swept.edited) == (3, 1)


def test_a_log_in2lambda_refuses_names_the_command_in_the_rows_reason(tmp_path):
    root = tmp_path / "corpus"
    make_set(root, "faulty", ["faulty.md"])
    sweep(root, tmp_path, backend=FakeBackend(FAULTY_SPEC, FIXES))
    saved = tmp_path / "specs" / "faulty" / f"faulty.md{corpus.COMMANDS_SUFFIX}"
    written = json.loads(saved.read_text())
    written[0]["args"]["block"] = "b99"
    saved.write_text(json.dumps(written))

    (row,) = sweep(root, tmp_path, replay=True, backend=None)

    assert row.outcome == "replay refused"
    assert "command 1 of 5, split block" in row.reason


def test_a_replay_with_nothing_saved_says_so_and_still_makes_no_call(root, tmp_path):
    backend = FakeBackend(reason="a replay makes no call")

    rows = sweep(root, tmp_path, replay=True, specs=tmp_path / "none", backend=backend)

    assert backend.calls == []
    assert [row.outcome for row in rows] == ["no spec"] * 4
    assert not (tmp_path / "none" / "sheets" / SPEC_NAME).exists()


def test_a_replay_refuses_the_call_even_with_no_backend_handed_to_it(
    root, tmp_path, monkeypatch
):
    # The path the CLI runs: it hands the sweep no backend, so without one of
    # its own a replay over a set with nothing saved would reach the settings'
    # backend and spend the call the replay promises not to make.
    monkeypatch.setattr(
        pipeline,
        "choose_backend",
        lambda settings: pytest.fail("a replay chose a backend"),
    )

    rows = sweep(root, tmp_path, replay=True, specs=tmp_path / "none", backend=None)

    assert [row.outcome for row in rows] == ["no spec"] * 4


def test_the_table_is_written_with_the_columns_in_order(root, tmp_path):
    sweep(root, tmp_path, backend=FakeBackend(SPEC, TEX_SPEC))
    written = (tmp_path / "results.csv").read_text().splitlines()

    assert written[0] == ",".join(corpus.COLUMNS)
    assert [line.split(",")[0] for line in written[1:]] == [
        "sheets/sheet-2.md",
        "sheets/sheet.md",
        "tex/tex-sheet-2.tex",
        "tex/tex-sheet.tex",
    ]


def test_the_rounds_a_document_took_are_counted_by_layer(tmp_path):
    root = tmp_path / "corpus"
    make_set(root, "faulty", ["faulty.md"])

    (row,) = sweep(root, tmp_path, backend=FakeBackend(FAULTY_SPEC, FIXES))

    # The spec's own fields, then the three the round quoted out of the source,
    # one of which it went on to edit.
    assert row.outcome == "built"
    assert (row.layer3, row.edited, row.rounds) == (3, 1, 1)
    assert row.layer1 > 0 and row.layer2 == row.layer4 == 0
    # The column the shares are taken against is the whole draft's, not the
    # spec run's: a document that took a round writes fields after it.
    assert row.fields == row.layer1 + row.layer3
    assert row.input_tokens > 0 and row.output_tokens > 0


def test_a_refused_build_is_its_own_outcome_and_not_a_faulted_draft(
    tmp_path, monkeypatch
):
    # Two sets a row can tell apart only by what the checks said: the sheet's
    # spec covers it and its report comes clean, and in2lambda still will not
    # write it out, while the faulty sheet's own checks fault and a replay has
    # no round to answer them with. The refusal is stubbed because every
    # refusal in2lambda has today — a missing image among them — is now a
    # finding of the report instead, which is a faulted draft and not this.
    root = tmp_path / "corpus"
    make_set(root, "sheets", ["sheet.md"])
    make_set(root, "faulty", ["faulty.md"])
    for folder, text in (("sheets", SPEC), ("faulty", FAULTY_SPEC)):
        saved = tmp_path / "specs" / folder / SPEC_NAME
        saved.parent.mkdir(parents=True)
        saved.write_text(text)
    monkeypatch.setattr(
        pipeline.package,
        "build",
        lambda draft, out_dir: (_ for _ in ()).throw(
            corpus.package.BuildRefused("figures/ball.png is not beside the draft")
        ),
    )

    faulted, refused = sweep(root, tmp_path, replay=True)

    assert (faulted.source, faulted.outcome) == ("faulty/faulty.md", "faulted")
    assert (refused.source, refused.outcome) == ("sheets/sheet.md", "build refused")
    # The draft was made and the checks came clean: the export is what stopped,
    # which the rounds column would otherwise read as a spec that never covered
    # the sheet.
    assert (refused.rounds, refused.spec) == (0, "reused")
    assert refused.layer1 > 0
    # And each says why, in the words of what stopped it: the refusal itself,
    # without the stage line's prefix, and one of the findings rather than the
    # whole line the validate stage joined them into.
    assert "figures/ball.png" in refused.reason
    assert not refused.reason.startswith("refused: ")
    assert faulted.reason and "; " not in faulted.reason


def test_a_questions_only_set_is_built_and_its_warnings_are_the_reason(tmp_path):
    # The sheets whose solutions are not on them, which a sweep has to report
    # as built rather than faulted — with what the build went past in the one
    # column that says it.
    root = tmp_path / "corpus"
    make_set(root, "unanswered", ["questions-only.md"])
    saved = tmp_path / "specs" / "unanswered" / SPEC_NAME
    saved.parent.mkdir(parents=True)
    saved.write_text(SPEC)

    (row,) = sweep(root, tmp_path, replay=True)

    assert (row.outcome, row.rounds, row.spec) == ("built", 0, "reused")
    assert row.reason.count("has no solution") == 4
    assert "\n" not in row.reason


def test_a_spec_in2lambda_refuses_says_so_in_the_row(root, tmp_path, monkeypatch):
    # A spec the package will not run, which is a row rather than the end of the
    # sweep — and the message is the only thing that says which set's spec.
    running = corpus.package.spec_run

    def refuse(draft, spec):
        if "tex" in str(spec):
            raise corpus.SpecRejected("selector `question` matches\nno node")
        return running(draft, spec)

    monkeypatch.setattr(pipeline.package, "spec_run", refuse)

    rows = sweep(root, tmp_path, backend=FakeBackend(SPEC, TEX_SPEC, TEX_SPEC))
    rejected = [row for row in rows if row.set == "tex"]

    assert [row.outcome for row in rejected] == ["spec rejected"] * 2
    # One line, whatever the message did with its own.
    assert all(
        row.reason == "selector `question` matches no node" for row in rejected
    )


def test_a_replay_with_nothing_saved_says_why_in_the_row(root, tmp_path):
    rows = sweep(
        root,
        tmp_path,
        replay=True,
        specs=tmp_path / "none",
        backend=FakeBackend(reason="a replay makes no call"),
    )

    assert [row.outcome for row in rows] == ["no spec"] * 4
    assert all(row.reason == corpus.NoModel().unavailable() for row in rows)


def test_a_set_that_cannot_be_staged_is_a_row_and_the_table_is_still_written(
    root, tmp_path, monkeypatch
):
    # A folder the copy cannot read. Monkeypatched rather than made, since what
    # a permission bit does depends on who is running the tests.
    staging = corpus.stage

    def refuse(root, folder, work, suffixes):
        if folder.name == "sheets":
            raise PermissionError(folder)
        return staging(root, folder, work, suffixes)

    monkeypatch.setattr(corpus, "stage", refuse)

    rows = sweep(root, tmp_path, backend=FakeBackend(TEX_SPEC))

    # Every sheet of the set is a row, since none of them ran, and the set after
    # it runs as it would have.
    assert [(row.source, row.outcome) for row in rows] == [
        ("sheets/sheet-2.md", "error: PermissionError"),
        ("sheets/sheet.md", "error: PermissionError"),
        ("tex/tex-sheet-2.tex", "built"),
        ("tex/tex-sheet.tex", "built"),
    ]
    # What the copy raised, so the folder it could not read is in the table.
    assert all("sheets" in row.reason for row in rows[:2])
    written = (tmp_path / "results.csv").read_text().splitlines()
    assert len(written) == 5


def test_a_file_that_cannot_be_read_is_a_row_and_the_table_is_still_written(
    root, tmp_path, monkeypatch
):
    # A file that goes, or that cannot be opened, while the sweep is deciding
    # whether it is a document at all. Monkeypatched rather than made, since
    # what a permission bit does depends on who is running the tests.
    reading = corpus.is_document

    def refuse(path):
        if path.name == "tex-sheet.tex":
            raise PermissionError(path)
        return reading(path)

    monkeypatch.setattr(corpus, "is_document", refuse)

    rows = sweep(root, tmp_path, backend=FakeBackend(SPEC, TEX_SPEC))

    # The one file is a row, the sheets beside it ran, and the table was written.
    assert [(row.source, row.outcome) for row in rows] == [
        ("sheets/sheet-2.md", "built"),
        ("sheets/sheet.md", "built"),
        ("tex/tex-sheet-2.tex", "built"),
        ("tex/tex-sheet.tex", "error: PermissionError"),
    ]
    assert "tex-sheet.tex" in rows[-1].reason
    assert len((tmp_path / "results.csv").read_text().splitlines()) == 5


@pytest.mark.parametrize(
    "paths",
    [(Path("sheets"),), (Path("."), Path("sheets")), (Path("sheets"), Path("sheets"))],
)
def test_overlapping_paths_name_a_document_once(root, tmp_path, paths):
    # `.` holds `sheets`, and a folder may be named twice: either way the same
    # document run twice would be two rows disagreeing about its spec, one
    # saying it wrote it and the other that it reused it.
    shutil.rmtree(root / "tex")

    rows = sweep(root, tmp_path, paths=paths, backend=FakeBackend(SPEC))

    assert [(row.source, row.spec) for row in rows] == [
        ("sheets/sheet-2.md", "wrote"),
        ("sheets/sheet.md", "reused"),
    ]


def test_a_document_that_raises_is_a_row_and_not_the_end_of_the_sweep(
    root, tmp_path
):
    # A file that says it is a docx and is not: pandoc refuses it, and the sweep
    # is over the sets after it as well as the one it is in.
    broken = root / "broken"
    broken.mkdir()
    (broken / "notes.docx").write_bytes(b"not really a docx")

    rows = sweep(root, tmp_path, suffixes=("md", "docx"), backend=FakeBackend(SPEC))

    assert rows[0].source == "broken/notes.docx"
    assert rows[0].outcome.startswith("error: ")
    assert [row.outcome for row in rows[1:]] == ["built", "built"]
    assert [row.source for row in rows[1:]] == ["sheets/sheet-2.md", "sheets/sheet.md"]


def test_a_staged_set_leaves_behind_what_no_run_reads(root, tmp_path):
    (root / "sheets" / "scan.pdf").write_bytes(b"%PDF-1.4 most of what a corpus weighs")
    (root / "sheets" / "sources.zip").write_bytes(b"PK the rest of what it weighs")
    (root / "sheets" / "figures").mkdir()
    (root / "sheets" / "figures" / "plot.png").write_bytes(b"PNG")
    (root / "sheets" / "figures" / "page.pdf").write_bytes(b"%PDF-1.4")

    staged = corpus.stage(root, root / "sheets", tmp_path / "work", ("md",))

    # The figures come along, since the sheets refer to them; the PDFs do not,
    # in the folder itself or under it.
    assert contents(staged) == ["figures/plot.png", "sheet-2.md", "sheet.md"]


def test_the_corpus_root_is_a_set_of_its_own_and_wipes_nothing_but_itself(
    root, tmp_path
):
    # A corpus with documents loose at its top, ExampleContents among them: the
    # set's folder is the root, whose copy would otherwise be the work directory
    # and take the sets already staged beside it — and anything else a
    # user-named --work holds — with it when it is emptied.
    shutil.copy(FIXTURES / "sheet.md", root / "loose.md")
    work = tmp_path / "work"
    already = corpus.stage(root, root / "sheets", work, ("md", "tex"))
    (work / "not-the-sweep's.txt").write_text("a user's own --work")

    staged = corpus.stage(root, root, work, ("md", "tex"))
    corpus.stage(root, root, work, ("md", "tex"))

    assert staged.parent == work and staged.name == corpus.ROOT_SET
    assert contents(staged) == ["loose.md"]
    assert contents(already) == ["sheet-2.md", "sheet.md"]
    assert (work / "not-the-sweep's.txt").exists()


TIKZ = """\\begin{tikzpicture}
  \\draw[->] (0,0) -- (4,0) node[right] {$x$};
\\end{tikzpicture}
"""


def test_a_tex_file_that_is_a_drawing_is_skipped_and_comes_with_its_set(
    root, tmp_path
):
    # What the first sweep of PHYS40002 made a set of: a figures/ folder of TikZ
    # sources, staged and built on its own, and left out of the sheets that
    # input it — so every sheet with a figure was refused for a missing image.
    figures = root / "tex" / "figures"
    figures.mkdir()
    (figures / "tunnel-potential.tex").write_text(TIKZ)

    backend = FakeBackend(SPEC, TEX_SPEC)
    rows = sweep(root, tmp_path, backend=backend)

    skipped = next(row for row in rows if "tunnel-potential" in row.source)
    assert (skipped.source, skipped.set) == (
        "tex/figures/tunnel-potential.tex",
        "tex/figures",
    )
    assert (skipped.outcome, skipped.reason) == ("skipped", "no \\begin{document}")
    # Nothing was frozen on its account: no draft beside it and no zip, and —
    # since staging the set wipes the work folder the figures sit in, which is
    # where those would have been — no spec written for it and no call made.
    staged_figures = tmp_path / "work" / "tex" / "figures"
    assert not corpus.package.draft_of(
        staged_figures / "tunnel-potential.tex"
    ).exists()
    assert not (staged_figures / "out").exists()
    assert not (tmp_path / "specs" / "tex" / "figures").exists()
    assert len(backend.calls) == 2
    # And the sheets that input it have it.
    assert "figures/tunnel-potential.tex" in contents(tmp_path / "work" / "tex")
    assert [row.outcome for row in rows if row.set == "tex"] == ["built"] * 2


def test_a_tex_file_is_a_document_only_with_a_begin_document_in_it(root):
    assert corpus.is_document(root / "sheets" / "sheet.md")
    assert corpus.is_document(root / "tex" / "tex-sheet.tex")
    drawing = root / "tex" / "tunnel-potential.tex"
    drawing.write_text(TIKZ)
    assert not corpus.is_document(drawing)


def test_only_the_named_folders_are_run(root):
    assert [path.name for path in corpus.documents(root, ["tex"])] == [
        "tex-sheet-2.tex",
        "tex-sheet.tex",
    ]


def without_clocks(row):
    """A row without the two timings, which are what differs between two runs."""
    return {
        key: value
        for key, value in asdict(row).items()
        if key not in ("model_seconds", "wall_seconds")
    }


def test_a_sheet_and_its_solutions_file_are_one_row(tmp_path):
    # UCL_MechEng writes each worksheet as two documents: Worksheet_1.pdf and
    # Worksheet_1_solutions.pdf beside it.
    root = tmp_path / "corpus"
    folder = root / "worksheets"
    folder.mkdir(parents=True)
    shutil.copy(FIXTURES / "paired.md", folder / "Sheet_3.md")
    shutil.copy(FIXTURES / "paired_solutions.md", folder / "Sheet_3_solutions.md")
    backend = FakeBackend(PAIRED_SPEC)

    rows = sweep(root, tmp_path, backend=backend)

    assert [(row.source, row.set, row.outcome) for row in rows] == [
        ("worksheets/Sheet_3.md", "worksheets", "built")
    ]
    assert len(backend.calls) == 1
    # The solutions file was frozen as the draft's second source, so the four
    # part solutions came out of it.
    draft = corpus.package.draft_of(tmp_path / "work" / "worksheets" / "Sheet_3.md")
    frozen = json.loads(draft.read_text())
    assert [one["source"] for one in frozen["sources"]] == [
        "Sheet_3.md",
        "Sheet_3_solutions.md",
    ]
    assert [
        key
        for key, written in frozen["fields"].items()
        if written.get("source") == 2 and not key.endswith(".ignore")
    ] == [
        "q1.p1.solution",
        "q1.p2.solution",
        "q2.p1.solution",
        "q2.p2.solution",
    ]


def test_solutions_with_no_questions_beside_them_are_skipped(tmp_path):
    root = tmp_path / "corpus"
    folder = root / "worksheets"
    folder.mkdir(parents=True)
    shutil.copy(
        FIXTURES / "paired_solutions.md", folder / "Tutorial_2_Solutions.md"
    )
    backend = FakeBackend(PAIRED_SPEC)

    rows = sweep(root, tmp_path, backend=backend)

    # `skipped` is an outcome the command exits 0 on, as a drawing's row is.
    assert [(row.source, row.outcome, row.reason) for row in rows] == [
        ("worksheets/Tutorial_2_Solutions.md", "skipped", "solutions without questions")
    ]
    # Nothing was frozen or called on its account.
    assert len(backend.calls) == 0
    assert not (tmp_path / "work" / "worksheets").exists()
