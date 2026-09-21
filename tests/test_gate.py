"""The merge gate: a replay over a corpus, checked against a recorded baseline."""

from pathlib import Path

import pytest
from test_corpus import make_set
from test_pipeline import SPEC, TEX_SPEC

from in2lambda_agent import gate, pair
from in2lambda_agent.gate import Baseline, Folder
from in2lambda_agent.settings import Settings
from in2lambda_agent.spec import SPEC_NAME


@pytest.fixture
def corpus_root(tmp_path):
    """A corpus of two folders, with a saved spec each so a replay builds."""
    made = tmp_path / "corpus"
    make_set(made, "sheets", ["sheet.md", "sheet-2.md"])
    make_set(made, "tex", ["tex-sheet.tex", "tex-sheet-2.tex"])
    for folder, text in (("sheets", SPEC), ("tex", TEX_SPEC)):
        saved = tmp_path / "specs" / folder / SPEC_NAME
        saved.parent.mkdir(parents=True)
        saved.write_text(text)
    return made


@pytest.fixture
def baseline(corpus_root, tmp_path):
    """The baseline as it is written by hand, before anything is recorded."""
    return Baseline(
        specs=tmp_path / "specs",
        folders={
            "sheets": Folder(root=corpus_root, suffixes=["md"]),
            "tex": Folder(root=corpus_root, suffixes=["tex"]),
        },
    )


def run(baseline, tmp_path, **kwargs):
    """A gate run with its cache and work directory under tmp_path."""
    return gate.run(
        baseline,
        cache=tmp_path / "cache",
        work=tmp_path / "gate",
        settings=Settings(),
        **kwargs,
    )


def test_recording_fills_the_counts_and_leaves_what_was_written_by_hand(
    baseline, corpus_root, tmp_path
):
    report = run(baseline, tmp_path, record=True)

    assert not report.failed
    assert baseline.folders["sheets"].built == 2
    assert baseline.folders["tex"].built == 2
    assert baseline.folders["tex"].documents == {
        "tex/tex-sheet.tex": "built",
        "tex/tex-sheet-2.tex": "built",
    }
    # The hand-written half is the run's to read, not to write over.
    assert baseline.folders["tex"].root == corpus_root
    assert baseline.folders["tex"].suffixes == ["tex"]


def test_a_recorded_baseline_passes_the_run_that_recorded_it(baseline, tmp_path):
    run(baseline, tmp_path, record=True)

    report = run(baseline, tmp_path)

    assert not report.failed
    assert [one.built for one in report.folders.values()] == [2, 2]


def test_a_folder_that_builds_fewer_than_recorded_fails(baseline, tmp_path):
    run(baseline, tmp_path, record=True)
    baseline.folders["tex"].built += 1

    report = run(baseline, tmp_path)

    assert report.failed
    assert report.folders["tex"].failed and not report.folders["sheets"].failed


def test_a_folder_whose_documents_no_longer_build_fails(baseline, tmp_path):
    run(baseline, tmp_path, record=True)
    # The spec a replay has nothing to replay without.
    (tmp_path / "specs" / "tex" / SPEC_NAME).unlink()

    report = run(baseline, tmp_path)

    assert report.failed
    summary = report.folders["tex"]
    assert (summary.built, summary.recorded) == (0, 2)
    assert summary.counts == {"no spec": 2}
    assert sorted(one.document for one in summary.regressions) == [
        "tex/tex-sheet-2.tex",
        "tex/tex-sheet.tex",
    ]


def test_a_folder_the_baseline_records_no_count_for_passes(baseline, tmp_path):
    run(baseline, tmp_path, record=True)
    # A folder added to the gate before it replays to a build worth defending:
    # `--record` writes both halves, so neither is there yet.
    baseline.folders["tex"].built = None
    baseline.folders["tex"].documents = {}
    (tmp_path / "specs" / "tex" / SPEC_NAME).unlink()

    report = run(baseline, tmp_path)

    assert not report.failed
    assert report.folders["tex"].built == 0


def test_a_document_the_baseline_does_not_know_is_not_a_failure(
    baseline, corpus_root, tmp_path
):
    run(baseline, tmp_path, record=True)
    (corpus_root / "sheets" / "sheet-3.md").write_text(
        (corpus_root / "sheets" / "sheet.md").read_text()
    )

    report = run(baseline, tmp_path)

    assert not report.failed
    assert report.folders["sheets"].built == 3


def test_a_document_the_corpus_no_longer_holds_is_a_regression(
    baseline, corpus_root, tmp_path
):
    run(baseline, tmp_path, record=True)
    (corpus_root / "tex" / "tex-sheet-2.tex").unlink()

    report = run(baseline, tmp_path)

    assert report.failed
    regression = report.folders["tex"].regressions[0]
    assert (regression.document, regression.current) == (
        "tex/tex-sheet-2.tex",
        gate.MISSING,
    )


def test_a_baseline_of_no_builds_still_notices_a_document_that_did_worse(
    baseline, tmp_path
):
    # The check that gives a corpus where nothing builds teeth: the built count
    # is 0 on both sides, so only the document's own outcome says anything.
    baseline.folders["tex"].built = 0
    baseline.folders["tex"].documents = {"tex/tex-sheet.tex": "faulted"}
    (tmp_path / "specs" / "tex" / SPEC_NAME).unlink()

    report = run(baseline, tmp_path)

    summary = report.folders["tex"]
    assert summary.built == summary.recorded == 0
    assert report.failed
    assert [one.document for one in summary.regressions] == ["tex/tex-sheet.tex"]


@pytest.mark.parametrize(
    ("current", "recorded", "expected"),
    [
        ("built", "built", False),
        ("built", "faulted", False),
        ("skipped", "built", True),
        # A document read and faulted, then not read at all.
        ("skipped", "faulted", True),
        ("build refused", "built", True),
        ("faulted", "built", True),
        ("faulted", "build refused", True),
        ("no spec", "faulted", True),
        (gate.MISSING, "built", True),
        (gate.MISSING, "no spec", False),
    ],
)
def test_what_counts_as_worse(current, recorded, expected):
    assert gate.worse(current, recorded) is expected


def test_the_folder_line_says_what_each_outcome_came_to(baseline, tmp_path):
    run(baseline, tmp_path, record=True)
    summary = run(baseline, tmp_path).folders["sheets"]

    line = gate.folder_line("sheets", summary)

    assert line.split() == [
        "sheets",
        "built",
        "2",
        "faulted",
        "0",
        "build",
        "refused",
        "0",
        "skipped",
        "0",
        "(baseline",
        "built",
        "2)",
    ]


def test_the_folder_line_names_an_outcome_of_its_own(baseline, tmp_path):
    summary = gate.Summary(built=1, counts={"no spec": 2}, recorded=3)

    line = gate.folder_line("tex", summary)

    assert "no spec 2" in line and "(baseline built 3)" in line


def test_the_folder_line_names_each_document_that_did_worse():
    summary = gate.Summary(
        built=0,
        counts={"faulted": 1},
        recorded=0,
        regressions=[
            gate.Regression("tex/sheet-3.tex", "built", "faulted", "KaTeX"),
            gate.Regression("tex/sheet-4.tex", "built", gate.MISSING),
        ],
    )

    first, second, third = gate.folder_line("tex", summary).splitlines()

    assert first.startswith("tex")
    assert second == "  worse  tex/sheet-3.tex  built -> faulted: KaTeX"
    assert third == "  worse  tex/sheet-4.tex  built -> missing"


def test_the_folder_line_says_where_no_count_is_recorded(baseline, tmp_path):
    line = gate.folder_line("tex", gate.Summary(built=1))

    assert line.endswith("(not recorded)")


def test_the_baseline_survives_being_written_and_read(baseline, tmp_path):
    run(baseline, tmp_path, record=True)
    baseline.folders["tex"].built = None
    path = tmp_path / "baseline.json"

    gate.write_baseline(baseline, path)
    read = gate.read_baseline(path)

    assert read == baseline


def test_a_run_writes_nothing_under_the_directory_it_was_run_from(
    baseline, tmp_path, monkeypatch
):
    ran_from = tmp_path / "empty"
    ran_from.mkdir()
    monkeypatch.chdir(ran_from)

    run(baseline, tmp_path)

    assert list(ran_from.iterdir()) == []


def test_a_run_leaves_the_committed_spec_tree_as_it_found_it(baseline, tmp_path):
    # A sweep writes a record of each run beside the spec it read. The specs
    # are the repository's, so the gate reads a copy and the records land there.
    specs = tmp_path / "specs"
    before = sorted(path.relative_to(specs) for path in specs.rglob("*"))

    run(baseline, tmp_path)

    assert sorted(path.relative_to(specs) for path in specs.rglob("*")) == before


def test_the_gates_cache_is_not_the_one_a_worktree_would_fill(tmp_path):
    # Shared between worktrees on purpose: OCR already fetched for a PDF is not
    # fetched again on the next branch.
    assert gate.DEFAULT_CACHE_DIR == Path.home() / ".cache" / "in2lambda-agent"


REPOSITORY = Path(__file__).resolve().parent.parent


def test_the_committed_baseline_names_its_folders_and_their_specs():
    baseline = gate.read_baseline(REPOSITORY / "ci-baseline.json")

    assert set(baseline.folders) == {"tex", "docx", "pdf"}
    for name, folder in baseline.folders.items():
        # The spec each folder replays, at the path the sweep reads it from.
        assert (REPOSITORY / baseline.specs / name / SPEC_NAME).is_file()
        # Recorded, however low: a count of none is what the gate defends.
        assert folder.built is not None
        assert folder.documents


def test_the_committed_baseline_names_no_path_outside_the_repository():
    # An absolute root is a path on one machine, and under ExampleContents it
    # is also the name of a folder of private documents.
    baseline = gate.read_baseline(REPOSITORY / "ci-baseline.json")

    assert not baseline.specs.is_absolute()
    assert all(not folder.root.is_absolute() for folder in baseline.folders.values())


def test_the_local_baseline_and_the_specs_it_reads_are_both_committed():
    # The workbench check runs `gate gate-baseline.json` in a worktree of its
    # own. Both files are read from the repository, so a worktree that holds
    # neither fails the check in read_baseline before a document is swept.
    baseline = gate.read_baseline(REPOSITORY / "gate-baseline.json")

    assert set(baseline.folders) == {
        "UCL_MechEng",
        "PHYS40002-Mechanics/problem_sheets_and_figures",
        "MECH60014_Stress_analysis_3",
    }
    for name in baseline.folders:
        assert (REPOSITORY / baseline.specs / name / SPEC_NAME).is_file()


def test_the_ci_corpus_pairs_a_solutions_document_with_its_questions():
    # The corpus exists to exercise the separate-solutions document, which it
    # does only when `pair` matches the file's name. Name it so that it does
    # not — solutions-2.tex rather than sheet-2-solutions.tex — and the sweep
    # reads it as a sheet of its own and the path is never run.
    assert pair.solutions_beside(REPOSITORY / "ci-corpus/tex/sheet-2.tex") is not None


def test_no_document_of_the_ci_corpus_is_a_solutions_file():
    # A solutions document is frozen as the second source of the questions
    # document beside it, so a sweep gives it no row of its own.
    baseline = gate.read_baseline(REPOSITORY / "ci-baseline.json")

    named = [
        document
        for folder in baseline.folders.values()
        for document in folder.documents
        if pair.questions_stem(Path(document)) is not None
    ]
    assert named == []
