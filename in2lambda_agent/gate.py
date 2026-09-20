"""The merge gate: a replay over real documents, compared with a baseline.

Every ticket before this one was tested on synthetic fixtures. The first sweep
over a folder of real PDFs failed on four faults no fixture had: a list-valued
selector, a file of solutions with no questions, an image path, and a wrapped
line. A merge now requires an end-to-end run over real documents.

The run is `corpus.sweep(replay=True)` over the specs the repository keeps, so
it makes no model call. The baseline file names the folders to run and records,
per folder, how many documents built. The gate fails when a folder builds fewer
documents than the baseline records. A folder the baseline records no count for
passes on any count, which is how a folder is added to the gate before it
replays to a build worth defending. The baseline is committed, and a change to
it belongs in a pull request that states why the counts changed.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from in2lambda_agent import corpus
from in2lambda_agent.settings import Settings

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "in2lambda-agent"
"""Where the gate reads the OCR of each PDF. The directory is outside every
worktree, so a PDF converted on one branch is not converted again on the next.
`corpus` on its own keeps its cache under the directory the user ran from."""


@dataclass
class Folder:
    """One folder of a corpus, as the baseline holds it.

    Attributes:
        root: The corpus directory the folder is under. The repository itself
            for `ci-corpus`, which is committed; an absolute path for a corpus
            outside the repository.
        suffixes: The file suffixes that are documents in the folder: `pdf` for
            a folder of scans, `tex` or `docx` for sources.
        built: How many documents built when the baseline was recorded, or None
            where no count is recorded. `root` and `suffixes` are written by
            hand; `built` is what `--record` writes.
    """

    root: Path
    suffixes: list[str]
    built: Optional[int] = None


@dataclass
class Baseline:
    """The committed file the gate compares a sweep against.

    Attributes:
        specs: The tree the folders' specs are kept in, relative to the
            repository. Folder `A/B` reads `<specs>/A/B/in2lambda-spec.yaml`.
        folders: The folders to run, by their path under their own root.
    """

    specs: Path
    folders: dict[str, Folder]


@dataclass
class Summary:
    """What one folder did this run, beside what the baseline records.

    Attributes:
        built: How many documents built.
        counts: How many did each other thing, by outcome.
        recorded: How many built when the baseline was recorded, or None where
            no count is recorded.
    """

    built: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    recorded: Optional[int] = None

    @property
    def failed(self) -> bool:
        """Whether this folder fails the gate."""
        return self.recorded is not None and self.built < self.recorded


@dataclass
class Report:
    """The whole run: one summary per folder, in the baseline's order."""

    folders: dict[str, Summary] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        """Whether any folder fails the gate."""
        return any(one.failed for one in self.folders.values())


def read_baseline(path: Path) -> Baseline:
    """Reads the committed baseline.

    Args:
        path: The JSON file.

    Returns:
        The baseline.
    """
    written = json.loads(Path(path).read_text(encoding="utf-8"))
    return Baseline(
        specs=Path(written["specs"]),
        folders={
            name: Folder(
                root=Path(one["root"]),
                suffixes=list(one["suffixes"]),
                built=one.get("built"),
            )
            for name, one in written["folders"].items()
        },
    )


def write_baseline(baseline: Baseline, path: Path) -> None:
    """Writes the baseline back, counts and all.

    Args:
        baseline: What to write.
        path: The JSON file, which is overwritten.
    """
    written = {
        "specs": baseline.specs.as_posix(),
        "folders": {
            name: {
                "root": one.root.as_posix(),
                "suffixes": one.suffixes,
                "built": one.built,
            }
            for name, one in baseline.folders.items()
        },
    }
    Path(path).write_text(json.dumps(written, indent=2) + "\n", encoding="utf-8")


def run(
    baseline: Baseline,
    *,
    record: bool = False,
    cache: Path,
    work: Path,
    settings: Optional[Settings] = None,
) -> Report:
    """Replays every folder the baseline names and counts what each one built.

    Nothing is written into the repository or into a corpus: each folder is
    swept into its own directory under `work`, and the table is written there.

    Args:
        baseline: The folders to run and the counts to compare against. In
            record mode this run's counts replace them.
        record: Take this run as the new baseline rather than checking it.
        cache: Where the OCR of each PDF is kept. The directory is shared
            between worktrees, so Mathpix converts each PDF once.
        work: Where the folders are copied to be run.
        settings: The environment the runs have available.

    Returns:
        One summary per folder.
    """
    work = Path(work)
    report = Report()
    for name, folder in baseline.folders.items():
        rows = corpus.sweep(
            folder.root,
            paths=[Path(name)],
            suffixes=folder.suffixes,
            results=work / name / "results.csv",
            work=work / "work",
            specs=baseline.specs,
            replay=True,
            cache=cache,
            settings=settings,
        )
        summary = Summary(recorded=folder.built)
        for row in rows:
            if row.outcome == "built":
                summary.built += 1
            else:
                summary.counts[row.outcome] = summary.counts.get(row.outcome, 0) + 1
        if record:
            folder.built = summary.built
            # A recording run reports what this sweep did, so it fails nothing.
            summary.recorded = summary.built
        report.folders[name] = summary
    return report


def folder_line(name: str, summary: Summary) -> str:
    """The gate's one line for a folder: what it did, beside what is recorded.

    Args:
        name: The folder, as the baseline names it.
        summary: What it did.

    Returns:
        The line.
    """
    counts = "  ".join(
        f"{outcome} {summary.counts.get(outcome, 0)}"
        for outcome in ("faulted", "build refused", "skipped")
    )
    other = sorted(set(summary.counts) - {"faulted", "build refused", "skipped"})
    recorded = (
        "not recorded"
        if summary.recorded is None
        else f"baseline built {summary.recorded}"
    )
    return (
        f"{name:<40} built {summary.built}  {counts}"
        + "".join(f"  {outcome} {summary.counts[outcome]}" for outcome in other)
        + f"   ({recorded})"
    )
