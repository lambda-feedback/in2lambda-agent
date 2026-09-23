"""Each target against the set Lambda Feedback exported from it.

A target is a folder holding one set: one questions document, a solutions
document where the set has one, and the folder the platform exported for that
set, named `set_<Name>`. The export is what the conversion is trying to
reproduce, so a target is the one place the agent can be marked right or wrong
rather than merely flagged, and `find` looks for that `set_*` folder to find
one.

Each target converts through `routes.convert` under a filter of its own, and
`in2lambda.compare.differences` reports every place the zip and the export say
something else. A difference the maintainer has read and accepted is a line of
a `differs.txt` beside that target's filter, which `in2lambda.compare.known`
reads: those are reported as known, and what is left is new. A run with a new
difference is a run that changed what the agent makes of a document nobody
looked at again.

The filters live in a tree of their own mirroring the targets, so that nothing
is written into the corpus and the filter of a target is read again rather than
paid for a second time.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from in2lambda.api.set import Set
from in2lambda.compare import differences, known

from in2lambda_agent import pair, routes
from in2lambda_agent.model import Backend, choose_backend
from in2lambda_agent.settings import Settings, load_settings

DEFAULT_FILTER_DIR = Path("targets")
"""The tree the targets' filters are kept in, mirroring the targets themselves."""

FILTER_NAME = "filter.lua"
"""A target's saved filter, under its own folder of the tree: read if it is
there, written by one model call if it is not."""

DIFFERS_NAME = "differs.txt"
"""The differences the maintainer has accepted, beside the target's filter: one
line as `differences` words it, with the ticket that would close it after `#`."""

EXPORT_PREFIX = "set_"
"""What Lambda Feedback names an exported set's folder with. The rest of the
name is the set's own, which is what the conversion is built under so that the
two zips are comparable file by file."""


@dataclass
class Target:
    """One folder holding one set: its documents, and what to compare with.

    Attributes:
        name: The folder, relative to the root, which is what a line of the
            report and the folder of its filter are named after.
        export: The `set_*` folder Lambda Feedback exported.
        questions: The questions document.
        solutions: The solutions document, or None where there is none.
        error: What is wrong with the folder, where a target cannot be read at
            all: two exports in it, or no document to convert. A target holding
            one is reported and not run, and the targets after it still run.
    """

    name: str
    export: Path
    questions: Optional[Path] = None
    solutions: Optional[Path] = None
    error: Optional[str] = None


@dataclass
class Result:
    """What one target's comparison found.

    Attributes:
        name: The target's.
        differences: Every line the comparison reported.
        known: Those of them the target's `differs.txt` holds.
        new: Those it does not, which is what a run is read for.
        flags: How many fields the conversion flagged for a person.
        tokens: What its model calls cost.
        error: What stopped the target, and nothing else filled.
    """

    name: str
    differences: list[str] = field(default_factory=list)
    known: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    flags: int = 0
    tokens: int = 0
    error: Optional[str] = None

    def report(self) -> list[str]:
        """The new differences, the accepted ones, and the counts.

        The new lines come first, because they are what a reader is looking
        for; the known ones are printed too, so that a line the maintainer
        accepted and the run no longer reports can be seen to have gone.
        """
        if self.error:
            return [f"error     {self.name}: {self.error}"]
        return (
            [f"differs   {self.name}: {line}" for line in self.new]
            + [f"known     {self.name}: {line}" for line in self.known]
            + [
                f"{self.name}: {len(self.differences)} differ, "
                f"{len(self.known)} known, {len(self.new)} new, "
                f"{self.flags} flagged"
            ]
        )


def find(root: Path, paths: Sequence[Path] = ()) -> list[Target]:
    """Every target under a root, by the export folder each one holds.

    Args:
        root: The directory the targets are under, directly or grouped by
            course.
        paths: Folders under it to look in, relative to it; all of it if empty.

    Returns:
        One target per folder holding a `set_*` folder, in name order. A folder
        that cannot be read as a target — two exports in it, or no document —
        is a target carrying an `error` rather than an exception, so that one
        bad folder does not stop the run.
    """
    root = Path(root)
    exports: dict[Path, set[Path]] = {}
    for where in [root / one for one in paths] or [root]:
        for path in where.rglob(f"{EXPORT_PREFIX}*"):
            if path.is_dir():
                exports.setdefault(path.parent, set()).add(path)

    found = []
    for folder in sorted(exports, key=lambda one: one.relative_to(root).as_posix()):
        held = sorted(exports[folder])
        name = folder.relative_to(root).as_posix()
        if len(held) > 1:
            named = ", ".join(one.name for one in held)
            found.append(
                Target(
                    name=name,
                    export=held[0],
                    error=f"{folder} holds {len(held)} exported sets: {named}. "
                    "A target folder holds one.",
                )
            )
            continue
        try:
            questions, solutions = pair.target_documents(folder)
        except ValueError as problem:
            found.append(Target(name=name, export=held[0], error=str(problem)))
            continue
        found.append(
            Target(
                name=name, export=held[0], questions=questions, solutions=solutions
            )
        )
    return found


def run_one(
    target: Target,
    *,
    filters: Path = DEFAULT_FILTER_DIR,
    out_dir: Path = Path("out"),
    cache_dir: Path = Path(".in2lambda-agent"),
    settings: Optional[Settings] = None,
    backend: Optional[Backend] = None,
) -> Result:
    """Converts one target and compares what came out with its export.

    Args:
        target: The target, as `find` read it.
        filters: The tree of filters and accepted differences.
        out_dir: Where each target's set is written, under its own name.
        cache_dir: Where the OCR of each PDF is kept.
        settings: The environment the run has available.
        backend: The backend to write a filter with, chosen from the settings
            if absent.

    Returns:
        The target's result. Nothing a target raises leaves this function: what
        stopped it is its result's `error` and the targets after it still run.
    """
    if target.error:
        return Result(name=target.name, error=target.error)
    settings = settings or load_settings()
    backend = backend or choose_backend(settings)
    saved = Path(filters) / target.name
    # Pandoc reads neither a PDF nor the markdown an OCR made of one back into
    # the document's structure, so route B cannot run over a scanned target:
    # it converts through route A alone, and no filter is written for it.
    lua = None if target.questions.suffix.lower() == ".pdf" else saved / FILTER_NAME
    try:
        if lua is not None and not lua.is_file():
            saved.mkdir(parents=True, exist_ok=True)
            lua.write_text(
                routes.write_filter(target.questions, target.solutions, backend)[0],
                encoding="utf-8",
            )
        converted = routes.convert(
            target.questions,
            target.solutions,
            out_dir=Path(out_dir) / target.name,
            cache_dir=cache_dir,
            backend=backend,
            settings=settings,
            lua=lua,
            # The export's own name, so that the zip holds the files the export
            # holds and the two are compared file by file.
            name=target.export.name[len(EXPORT_PREFIX) :],
        )
    except Exception as problem:
        # A missing credential, a model call that did not finish, a document
        # pandoc refused: all of them are this target's line, and the run goes
        # on to the next target.
        return Result(name=target.name, error=" ".join(str(problem).split()))

    found = differences(
        Set.from_json(str(converted.zip_path)),
        Set.from_json(str(target.export)),
        left_name="the agent",
        right_name="the export",
    )
    accepted = known(saved / DIFFERS_NAME)
    return Result(
        name=target.name,
        differences=found,
        known=[line for line in found if line in accepted],
        new=[line for line in found if line not in accepted],
        flags=len(converted.flags),
        tokens=converted.tokens,
    )


def run(
    root: Path,
    *,
    paths: Sequence[Path] = (),
    filters: Path = DEFAULT_FILTER_DIR,
    out_dir: Path = Path("out"),
    cache_dir: Path = Path(".in2lambda-agent"),
    settings: Optional[Settings] = None,
    backend: Optional[Backend] = None,
) -> list[Result]:
    """Runs every target under a root, printing each one's report as it finishes.

    Args:
        root: The directory the targets are under.
        paths: Folders under it to run, relative to it; all of it if empty.
        filters: The tree of filters and accepted differences.
        out_dir: Where each target's set is written, under its own name.
        cache_dir: Where the OCR of each PDF is kept.
        settings: The environment the runs have available.
        backend: The backend to write the filters with.

    Returns:
        One result per target, in the order they ran.
    """
    settings = settings if settings is not None else load_settings()
    results = []
    for target in find(root, paths):
        result = run_one(
            target,
            filters=filters,
            out_dir=out_dir,
            cache_dir=cache_dir,
            settings=settings,
            backend=backend,
        )
        for line in result.report():
            print(line)
        results.append(result)
    return results
