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
a `differs.txt` beside that target's filter: the field it is in, `field_key`,
and the reason after a `#`. A run reports the differences in accepted fields as
known and the rest as new, and a run with a new difference is a run that
changed what the agent makes of a document nobody looked at again.

A `differs.txt` line names a field rather than a sentence because the report
quotes a model's wording. A model writes the same field differently each time
it is asked. The filter and route A's reply are saved beside each other and
read back for the same reason: a second run over a target makes neither of the
two calls that read the document, so the two runs differ from the export in the
same fields. `fresh` reads the documents again and writes a new reply.

Those are the only two calls a run saves. A target with a filter runs route B
on every run, and `routes.reconcile` has a model adjudicate every field the two
routes word differently. A verdict can go the other way on a later run, so the
wording of a difference and the number of fields flagged move between runs
while the fields `differs.txt` accepts stay accepted.

The filters, the replies and the accepted fields are kept in a tree of their
own mirroring the targets, so that no file is written into the corpus and a
target's saved files are found again by the target's name.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from in2lambda.api.set import Set
from in2lambda.compare import differences

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
field key a line, with the reason it differs written after a `#`."""

REPLY_NAME = "reply.json"
"""Route A's reply, beside the target's filter: read back by every run after
the first, so that a target is converted the same way twice."""

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
            all: two exports in it, no document to convert, or the root passed
            being the target itself. A target holding one is reported and not
            run, and the targets after it still run.
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
        known: Those of them in a field the target's `differs.txt` accepts.
        new: Those in a field it does not, which is what a run is read for.
        agreed: The keys it accepts that nothing differs in any more, which are
            lines to take out of it.
        flags: How many fields the conversion flagged for a person.
        tokens: What route A cost, and nothing where the saved reply was read
            back. The adjudication a target with a filter pays for on every run
            is not counted here.
        error: What stopped the target, and nothing else filled.
    """

    name: str
    differences: list[str] = field(default_factory=list)
    known: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)
    agreed: list[str] = field(default_factory=list)
    flags: int = 0
    tokens: int = 0
    error: Optional[str] = None

    def report(self) -> list[str]:
        """The new differences, then the accepted ones, then the counts.

        A known difference is printed as well as a new one, so that the
        maintainer reads what a field the `differs.txt` accepts says now.
        """
        if self.error:
            return [f"error     {self.name}: {self.error}"]
        return (
            [f"differs   {self.name}: {line}" for line in self.new]
            + [f"known     {self.name}: {line}" for line in self.known]
            + [
                f"agrees    {self.name}: {key} now agrees, remove the line"
                for key in self.agreed
            ]
            + [
                f"{self.name}: {len(self.differences)} differ, "
                f"{len(self.known)} known, {len(self.new)} new, "
                f"{self.flags} flagged"
            ]
        )


_LOCATION = re.compile(
    r'Question (\d+) "[^"]*"(?:, part \(([a-z]+)\))?(?:, ([a-z ]+))?: '
)
"""How `in2lambda.compare.differences` names where a difference is."""


def field_key(line: str) -> str:
    """The field a difference is in, as a `differs.txt` names it.

    A difference names its location in words and quotes what each set says
    there: `Question 2 "", part (a), worked solution: the agent says ...`. The
    quotation is a model's wording of that run and changes between runs. The
    location does not change, so a `differs.txt` records the location.

    Args:
        line: A line as `differences` words it.

    Returns:
        The question, the part and the field as a key: `q2.p1.worked_solution`,
        or `q2.p1` and `q2` where the difference is a whole part or question
        one side wrote and the other did not. A line naming no location returns
        the line itself, which no `differs.txt` holds, so a difference this
        function cannot read is reported as new.
    """
    match = _LOCATION.match(line)
    if match is None:
        return line
    number, part, name = match.groups()
    key = f"q{number}"
    if part is not None:
        key += f".p{ord(part[0]) - ord('a') + 1}"
    if name is not None:
        key += f".{name.replace(' ', '_')}"
    return key


def accepted(path: Path) -> list[str]:
    """The field keys a target's `differs.txt` accepts.

    Args:
        path: The file, which a target that differs from its export nowhere
            does not have.

    Returns:
        One key a line, in the order the file writes them, with everything from
        a `#` on dropped: that is where the reason a field differs is written,
        and a line that is all reason names no field.
    """
    path = Path(path)
    if not path.is_file():
        return []
    lines = (line.split("#")[0].strip() for line in path.read_text().splitlines())
    return [line for line in lines if line]


def find(root: Path, paths: Sequence[Path] = ()) -> list[Target]:
    """Every target under a root, by the export folder each one holds.

    Args:
        root: The directory the targets are under, directly or grouped by
            course.
        paths: Folders under it to look in, relative to it; all of it if empty.

    Returns:
        One target per folder holding a `set_*` folder, in name order. A folder
        that cannot be read as a target — two exports in it, or no document, or
        the root itself, which has no name to be kept under — is a target
        carrying an `error` rather than an exception, so that one bad folder
        does not stop the run.
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
        if name == ".":
            # The root is the target itself, so the path a target is named by
            # is `.` and everything built from it collapses onto the root of
            # the filter tree: the target's saved filter would be missed and
            # paid for again, and its accepted differences read from a file
            # that is not there. Refused, rather than run for a wrong answer.
            found.append(
                Target(
                    name=folder.resolve().name,
                    export=held[0],
                    error=f"{root} is a target itself, not a directory targets "
                    "are under, and a target is named by its path from that "
                    f"directory. Run `in2lambda-agent targets {root.parent} "
                    f"{folder.resolve().name}` instead.",
                )
            )
            continue
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
    fresh: bool = False,
    replay: bool = False,
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
        fresh: Read the document again rather than converting the reply saved
            beside the filter, which is how a target is given a new reading.
        replay: Refuse a target whose filter or reply is not saved rather than
            paying for one, so that the run reads what is committed and makes
            no call that reads the document.

    Returns:
        The target's result. Nothing a target raises leaves this function: what
        stopped it is its result's `error` and the targets after it still run.
    """
    if target.error:
        return Result(name=target.name, error=target.error)
    settings = settings or load_settings()
    backend = backend or choose_backend(settings)
    saved = Path(filters) / target.name
    reply = saved / REPLY_NAME
    # A PDF target converts through route A alone and has no filter, though
    # route B now reads a PDF as the markdown its OCR made: a filter here is
    # saved under `--filters` and replayed, which is its own ticket.
    lua = None if target.questions.suffix.lower() == ".pdf" else saved / FILTER_NAME
    if replay:
        absent = [one for one in (reply, lua) if one is not None and not one.is_file()]
        if absent:
            return Result(
                name=target.name,
                error=f"{absent[0]} is not saved, and a replay makes no call that "
                f"reads a document. `in2lambda-agent targets ROOT --filters "
                f"{filters}` writes it.",
            )
    route_a = None
    try:
        if reply.is_file() and not fresh:
            try:
                route_a = json.loads(reply.read_text(encoding="utf-8"))
            except ValueError as problem:
                # A run interrupted while writing the reply leaves part of a
                # JSON document behind, and json.loads names a column of it and
                # no file. The name of the file is what the maintainer needs.
                raise ValueError(f"{reply}: {problem}; --fresh writes a new one")
        if lua is not None and not lua.is_file():
            saved.mkdir(parents=True, exist_ok=True)
            lua.write_text(
                routes.write_filter(
                    target.questions,
                    target.solutions,
                    backend,
                    cache_dir=cache_dir,
                    settings=settings,
                )[0],
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
            route_a=route_a,
        )
        if route_a is None:
            # The reply is written before the comparison, so that a comparison
            # the export's files break does not discard the model's answer.
            saved.mkdir(parents=True, exist_ok=True)
            reply.write_text(json.dumps(converted.route_a, indent=2), encoding="utf-8")
        found = differences(
            Set.from_json(str(converted.zip_path)),
            Set.from_json(str(target.export)),
            left_name="the agent",
            right_name="the export",
        )
        accepts = accepted(saved / DIFFERS_NAME)
        keys = [field_key(line) for line in found]
        return Result(
            name=target.name,
            differences=found,
            known=[line for line, key in zip(found, keys) if key in accepts],
            new=[line for line, key in zip(found, keys) if key not in accepts],
            agreed=[key for key in accepts if key not in set(keys)],
            flags=len(converted.flags),
            tokens=converted.tokens,
        )
    except Exception as problem:
        # A missing credential, a model call that did not finish, a document
        # pandoc refused, an export half-copied into the corpus, a reply that
        # is not JSON: all of them are this target's line, and the run goes on
        # to the next target.
        return Result(name=target.name, error=" ".join(str(problem).split()))


def run(
    root: Path,
    *,
    paths: Sequence[Path] = (),
    filters: Path = DEFAULT_FILTER_DIR,
    out_dir: Path = Path("out"),
    cache_dir: Path = Path(".in2lambda-agent"),
    settings: Optional[Settings] = None,
    backend: Optional[Backend] = None,
    fresh: bool = False,
    replay: bool = False,
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
        fresh: Read every document again rather than converting the saved
            replies.
        replay: Refuse a target whose filter or reply is not saved rather than
            paying for one.

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
            fresh=fresh,
            replay=replay,
        )
        for line in result.report():
            print(line)
        results.append(result)
    return results
