"""The sweep over a corpus: one row per document, and a replay with no model in it."""

import shutil
from dataclasses import asdict
from pathlib import Path

import pytest
from conftest import FakeBackend
from test_pipeline import FAULTY_SPEC, FIXES, SPEC, TEX_SPEC

from in2lambda_agent import corpus, pipeline
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
