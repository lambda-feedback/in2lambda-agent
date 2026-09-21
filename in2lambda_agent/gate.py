"""The merge gate: a replay over real documents, compared with a baseline.

Every ticket before this one was tested on synthetic fixtures. The first sweep
over a folder of real PDFs failed on four faults no fixture had: a list-valued
selector, a file of solutions with no questions, an image path, and a wrapped
line. A merge now requires an end-to-end run over real documents.

The run is `corpus.sweep(replay=True)` over the specs the repository keeps, so
it makes no model call. The baseline file names the folders to run and records
both how many documents each folder built and what each single document did.
The gate fails when a folder builds fewer documents than the baseline records,
or when any one document does worse than it is recorded as doing. The second
check is what gives a baseline of no builds at all teeth: a folder where every
document faults still notices the day one of them stops being read. A folder
the baseline records no count for passes on any count, which is how a folder is
added to the gate before it replays to a build worth defending. A change to a
recorded count belongs in a pull request that states why the count changed.

A baseline names its specs, and a corpus of its own, relative to the directory
the file is in. `ci-baseline.json` is at the root of the repository and names
`ci-corpus/specs`; the baseline for the private corpus is at
`corpus-specs/gate-baseline.json`, beside the specs it names, and the
repository holds neither file.
"""

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from in2lambda_agent import corpus
from in2lambda_agent.settings import Settings

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "in2lambda-agent"
"""Where the gate reads the OCR of each PDF. The directory is outside every
worktree, so a PDF converted on one branch is not converted again on the next.
`corpus` on its own keeps its cache under the directory the user ran from."""

RANK = {"built": 0, "build refused": 1, "faulted": 2, "skipped": 3}
"""How bad each outcome is. `skipped` is the worst of them: a document that
faults was at least read, and one that is skipped was not. Anything else is a
document that did not run either — a spec gone, a file unreadable, a document
the sweep no longer finds — and ranks with `skipped`."""

MISSING = "missing"
"""What a recorded document the sweep no longer finds is compared as."""


def worse(current: str, recorded: str) -> bool:
    """Whether a document did worse this run than the baseline records.

    A document that stops being read is a regression whatever it did before,
    which is the case a baseline of nothing but faults rests on: widen what
    counts as a solutions file and a document leaves the sweep as `skipped`
    without a single count changing.

    Args:
        current: What the document did this run.
        recorded: What the baseline records it doing.

    Returns:
        Whether it is a regression.
    """
    return RANK.get(current, 3) > RANK.get(recorded, 3)


@dataclass
class Folder:
    """One folder of a corpus, as the baseline holds it.

    Attributes:
        root: The corpus directory the folder is under, read relative to the
            directory the baseline file is in. `ci-corpus` for the committed
            corpus; an absolute path for a corpus outside the repository.
        suffixes: The file suffixes that are documents in the folder: `pdf` for
            a folder of scans, `tex` or `docx` for sources.
        built: How many documents built when the baseline was recorded, or None
            where no count is recorded.
        documents: What each document did when the baseline was recorded, by
            its path under the corpus root. `root` and `suffixes` are written
            by hand; `built` and `documents` are what `--record` writes.
    """

    root: Path
    suffixes: list[str]
    built: Optional[int] = None
    documents: dict[str, str] = field(default_factory=dict)


@dataclass
class Regression:
    """One document that did worse this run than the baseline records.

    Attributes:
        document: Its path under the corpus root, as the baseline names it.
        recorded: What the baseline records it doing.
        current: What it did this run, or `missing` where the sweep no longer
            finds it.
        reason: What the sweep said about it, where it said anything.
    """

    document: str
    recorded: str
    current: str
    reason: str = ""


@dataclass
class Baseline:
    """The file the gate compares a sweep against.

    Attributes:
        specs: The tree the folders' specs are kept in, read relative to the
            directory the baseline file is in. Folder `A/B` reads
            `<specs>/A/B/in2lambda-spec.yaml`.
        folders: The folders to run, by their path under their own root.
        directory: The directory the baseline file was read from. `specs` and
            a relative `root` are read from there, so the command gives the
            same run whichever directory it is run in. The paths themselves are
            held as they are written, so `--record` writes them back unchanged.
    """

    specs: Path
    folders: dict[str, Folder]
    directory: Path = Path(".")


@dataclass
class Summary:
    """What one folder did this run, beside what the baseline records.

    Attributes:
        built: How many documents built.
        counts: How many did each other thing, by outcome.
        recorded: How many built when the baseline was recorded, or None where
            no count is recorded.
        regressions: The documents that did worse than the baseline records.
    """

    built: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    recorded: Optional[int] = None
    regressions: list[Regression] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        """Whether this folder fails the gate."""
        if self.recorded is not None and self.built < self.recorded:
            return True
        return bool(self.regressions)


@dataclass
class Report:
    """The whole run: one summary per folder, in the baseline's order."""

    folders: dict[str, Summary] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        """Whether any folder fails the gate."""
        return any(one.failed for one in self.folders.values())


def read_baseline(path: Path) -> Baseline:
    """Reads a baseline file.

    Args:
        path: The JSON file. The paths it names are read from the directory it
            is in.

    Returns:
        The baseline.
    """
    written = json.loads(Path(path).read_text(encoding="utf-8"))
    return Baseline(
        directory=Path(path).parent,
        specs=Path(written["specs"]),
        folders={
            name: Folder(
                root=Path(one["root"]),
                suffixes=list(one["suffixes"]),
                built=one.get("built"),
                documents=dict(one.get("documents", {})),
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
                "documents": one.documents,
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
    """Replays every folder the baseline names and checks what each one did.

    Nothing is written into the repository or into a corpus: each folder is
    swept into its own directory under `work`, and the table is written there.

    Args:
        baseline: The folders to run and what to compare against. Its `specs`
            and each relative `root` are read from the directory it was read
            from. In record mode this run's counts and outcomes replace them.
        record: Take this run as the new baseline rather than checking it.
        cache: Where the OCR of each PDF is kept. The directory is shared
            between worktrees, so Mathpix converts each PDF once.
        work: Where the folders are copied to be run.
        settings: The environment the runs have available.

    Returns:
        One summary per folder.
    """
    work = Path(work)
    # A sweep writes a record of each run beside the spec it read, and the
    # record of a gate run is nobody's: it would land in the repository, under
    # the specs the gate exists to replay. So the specs are copied under `work`
    # and read from there.
    specs = work / "specs"
    # Fresh each run, so that a spec taken out of the tree is gone from the
    # copy the sweep reads rather than left over from the run before.
    shutil.rmtree(specs, ignore_errors=True)
    # Joining an absolute path to the baseline's directory returns the absolute
    # path, so a private corpus named by its path on one machine is unchanged.
    written = baseline.directory / baseline.specs
    if written.is_dir():
        shutil.copytree(written, specs)
    report = Report()
    for name, folder in baseline.folders.items():
        rows = corpus.sweep(
            baseline.directory / folder.root,
            paths=[Path(name)],
            suffixes=folder.suffixes,
            results=work / name / "results.csv",
            work=work / "work",
            specs=specs,
            replay=True,
            cache=cache,
            settings=settings,
        )
        summary = Summary(recorded=folder.built)
        outcomes = {row.source: row.outcome for row in rows}
        reasons = {row.source: row.reason for row in rows}
        for outcome in outcomes.values():
            if outcome == "built":
                summary.built += 1
            else:
                summary.counts[outcome] = summary.counts.get(outcome, 0) + 1
        if record:
            folder.built = summary.built
            folder.documents = outcomes
            # A recording run reports what this sweep did, so it fails nothing.
            summary.recorded = summary.built
        else:
            for document, was in folder.documents.items():
                now = outcomes.get(document, MISSING)
                if worse(now, was):
                    summary.regressions.append(
                        Regression(document, was, now, reasons.get(document, ""))
                    )
        report.folders[name] = summary
    return report


def folder_line(name: str, summary: Summary) -> str:
    """The gate's line for a folder: what it did, beside what is recorded.

    A folder whose documents all did as well as recorded is one line. Each
    document that did worse adds an indented line of its own beneath it, so
    that a failure names the document rather than only the count.

    Args:
        name: The folder, as the baseline names it.
        summary: What it did.

    Returns:
        The line, and a line per regression beneath it.
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
    return "\n".join(
        [
            f"{name:<40} built {summary.built}  {counts}"
            + "".join(f"  {outcome} {summary.counts[outcome]}" for outcome in other)
            + f"   ({recorded})",
            *(
                f"  worse  {one.document}  {one.recorded} -> {one.current}"
                + (f": {one.reason}" if one.reason else "")
                for one in summary.regressions
            ),
        ]
    )
