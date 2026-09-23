"""The merge gate: every target replayed against its export, and no call.

The targets themselves are tested in test_targets; what is tested here is that
the gate replays them — a target with nothing saved is an error rather than a
call — and that the one target the repository commits comes back clean under a
backend that refuses to answer.
"""

import json
from pathlib import Path

import pytest

from conftest import FakeBackend
from test_targets import REPLY, fake_convert, make_target

from in2lambda_agent import gate, targets

CI_TARGETS = Path(__file__).parent.parent / "ci-corpus" / "targets"
CI_FILTERS = Path(__file__).parent.parent / "ci-corpus" / "filters"


def save(filters, name, *, reply=True, areas=True, lua=True):
    """What a target must have saved for the gate to replay it."""
    saved = Path(filters) / name
    saved.mkdir(parents=True, exist_ok=True)
    if reply:
        (saved / targets.REPLY_NAME).write_text(json.dumps(REPLY))
    if areas:
        (saved / targets.AREAS_NAME).write_text("{}")
    if lua:
        (saved / targets.FILTER_NAME).write_text("-- filter")
    return saved


def test_a_saved_target_is_replayed_and_its_new_differences_come_back(
    tmp_path, monkeypatch
):
    calls = fake_convert(monkeypatch)
    make_target(tmp_path / "corpus", "ME2")
    save(tmp_path / "filters", "ME2")

    results = gate.run(
        tmp_path / "corpus",
        filters=tmp_path / "filters",
        work=tmp_path / "work",
        cache=tmp_path / "cache",
        backend=FakeBackend(),
    )

    assert [one.name for one in results] == ["ME2"]
    assert results[0].error is None
    assert results[0].new == results[0].differences and results[0].new
    # The saved reply was converted rather than asked for again.
    assert calls[0]["route_a"] == REPLY


def test_a_target_with_nothing_saved_is_an_error_and_makes_no_call(
    tmp_path, monkeypatch
):
    calls = fake_convert(monkeypatch)
    make_target(tmp_path / "corpus", "ME2")

    results = gate.run(
        tmp_path / "corpus",
        filters=tmp_path / "filters",
        work=tmp_path / "work",
        cache=tmp_path / "cache",
        backend=FakeBackend(),
    )

    assert targets.REPLY_NAME in results[0].error
    assert calls == []


def test_the_run_writes_nothing_under_the_directory_it_was_run_in(
    tmp_path, monkeypatch
):
    fake_convert(monkeypatch)
    make_target(tmp_path / "corpus", "ME2")
    save(tmp_path / "filters", "ME2")
    here = tmp_path / "here"
    here.mkdir()
    monkeypatch.chdir(here)

    gate.run(
        tmp_path / "corpus",
        filters=tmp_path / "filters",
        work=tmp_path / "work",
        cache=tmp_path / "cache",
        backend=FakeBackend(),
    )

    assert list(here.iterdir()) == []
    assert (tmp_path / "work" / "ME2").is_dir()


def test_the_cache_is_shared_between_worktrees():
    # Outside every worktree, so that a PDF converted on one branch is not
    # converted again on the next.
    assert gate.DEFAULT_CACHE_DIR == Path.home() / ".cache" / "in2lambda-agent"


@pytest.mark.skipif(
    not (CI_TARGETS / "sheet" / "set_Sheet").is_dir(), reason="the CI target"
)
def test_the_committed_target_replays_clean_with_no_backend_to_call(tmp_path):
    # What CI runs, and the whole of it: the sheet, the filter and the reply the
    # repository commits, compared with the set in2lambda's own writer wrote
    # from that reply. The backend raises if anything asks it to answer, so a
    # run that needs a model call fails here rather than in a job with no key.
    results = gate.run(
        CI_TARGETS,
        filters=CI_FILTERS,
        work=tmp_path / "work",
        cache=tmp_path / "cache",
        backend=FakeBackend(AssertionError("the gate made a model call")),
    )

    assert [one.name for one in results] == ["sheet"]
    assert results[0].error is None
    assert results[0].new == []
    assert results[0].flags == 0
