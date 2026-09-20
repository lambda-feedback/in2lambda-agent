"""The merge gate: a replay over a corpus, checked against a recorded baseline."""

from pathlib import Path

import pytest
from test_corpus import make_set
from test_pipeline import SPEC, TEX_SPEC

from in2lambda_agent import gate
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


def test_a_folder_the_baseline_records_no_count_for_passes(baseline, tmp_path):
    run(baseline, tmp_path, record=True)
    # A folder added to the gate before it replays to a build worth defending.
    baseline.folders["tex"].built = None
    (tmp_path / "specs" / "tex" / SPEC_NAME).unlink()

    report = run(baseline, tmp_path)

    assert not report.failed
    assert report.folders["tex"].built == 0


def test_a_folder_that_builds_more_than_recorded_passes(baseline, corpus_root, tmp_path):
    run(baseline, tmp_path, record=True)
    (corpus_root / "sheets" / "sheet-3.md").write_text(
        (corpus_root / "sheets" / "sheet.md").read_text()
    )

    report = run(baseline, tmp_path)

    assert not report.failed
    assert report.folders["sheets"].built == 3


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


def test_the_gates_cache_is_not_the_one_a_worktree_would_fill(tmp_path):
    # Shared between worktrees on purpose: OCR already fetched for a PDF is not
    # fetched again on the next branch.
    assert gate.DEFAULT_CACHE_DIR == Path.home() / ".cache" / "in2lambda-agent"


def test_the_committed_baseline_names_the_ci_corpus_and_its_specs():
    repository = Path(__file__).resolve().parent.parent
    baseline = gate.read_baseline(repository / "corpus-specs" / "baseline.json")

    assert set(baseline.folders) == {
        "ci-corpus/tex",
        "ci-corpus/docx",
        "ci-corpus/pdf",
    }
    for name, folder in baseline.folders.items():
        # The spec each folder replays, at the path the sweep reads it from.
        assert (repository / baseline.specs / name / SPEC_NAME).is_file()
        assert folder.built is not None
