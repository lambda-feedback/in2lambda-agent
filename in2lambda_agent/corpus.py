"""The test plan's sweep: the agent over a corpus, one row per document.

The design spec asks, per document, what share of the fields each layer wrote,
how many rounds the checks took to come clean or that they never did, what the
model calls cost in tokens and time, and whether the set's spec was reused. That
is one run of the pipeline per document and one row of a table per run, written
where two runs can be diffed against each other.

A questions document and the solutions document beside it are one run and one
row, which is the questions document's.

Nothing here writes into the corpus. A run leaves a draft beside its source,
and a spec and a record beside that, so each set's folder is copied into
a work directory and run there, and the specs are kept in a tree of their own
mirroring the corpus. The copy is thrown away and made again every run; the spec
is what survives, and is what makes a document replayable — a saved spec plus
its source rebuilt with no model call at all.
"""

import csv
import shutil
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional, Sequence

from in2lambda_agent import package, pair, pipeline
from in2lambda_agent.model import Backend, ModelError, ModelUnavailable
from in2lambda_agent.package import SpecRejected, is_document
from in2lambda_agent.settings import Settings
from in2lambda_agent.spec import RECORD_NAME, SPEC_NAME, BadSpec

DEFAULT_SUFFIXES = ("tex", "md", "docx")
"""What a sweep runs over unless told otherwise: everything but the PDFs, which
need Mathpix credentials and a call each."""

DEFAULT_RESULTS = Path("results.csv")
"""The table, under the directory the user ran from."""

DEFAULT_WORK_DIR = Path(".in2lambda-agent/corpus")
"""Where each set's folder is copied to be run, and wiped before it is."""

DEFAULT_SPEC_DIR = Path("corpus-specs")
"""The mirror tree the sets' specs are kept in, which the work directory being
wiped does not touch."""

ROOT_SET = "_root"
"""What the corpus root's own documents are staged under. They are a set like
any other, but the set's folder under the work directory would be the work
directory itself, and that is not one set's to empty."""


@dataclass
class Row:
    """One document's line of the table, in the order the columns are written.

    Attributes:
        source: The document, relative to the corpus root.
        set: The folder it is in, which is its document set.
        outcome: `built`, `build refused` where the checks came clean and
            in2lambda still would not write the set out, `faulted` for a draft
            the checks still fault and no zip, `skipped` for a file that is not
            a document and for a solutions document with no questions document
            beside it, `no spec` for a replay with nothing saved to replay,
            `no model`, `spec failed` and `fix failed` where a model call did
            not finish, `spec rejected`, `bad spec`, or `error: <exception>`.
        reason: What the run had to say for itself, in the words of whatever
            said it: the refusal, the first error the checks were still finding,
            or what the exception said. On a `built` row it holds the warnings
            the build proceeded past — a part whose solution is not on the sheet
            — and is empty where there were none. One line always, so that the
            table can be read on its own and two sweeps diffed.
        spec: `wrote`, `reused`, or `rewritten` where a saved spec the checks
            faulted was written again.
        layout: The layout the spec chose.
        blocks: How many blocks the spec run saw in the frozen source. A round
            that splits a block adds one this does not count: it is the spec's
            own reach, which is what decides whether the spec is worth reusing.
        fields: How many fields the draft ended with, over every layer.
        layer1: How many of them the spec wrote.
        layer2: How many a predicate wrote, which is not built yet.
        layer3: How many a fixing round quoted out of the source.
        layer4: How many a fixing round typed out.
        edited: How many fields no longer say what the lines they quote say.
        unassigned: How many blocks the spec left in no field and not ignored,
            which is spec-time like `blocks`: a round may since have assigned
            them, so a `built` row can still report some.
        rounds: How many fixing rounds ran.
        input_tokens: What the run's model calls read.
        output_tokens: What they wrote.
        model_seconds: How long they took.
        wall_seconds: How long the whole document took.
        review: The review mode the run was given.
        rejections: How many questions a reviewer turned down, which is always
            none in mode `none` and is the column a later mode fills.
    """

    source: str
    set: str
    outcome: str = ""
    reason: str = ""
    spec: str = ""
    layout: str = ""
    blocks: int = 0
    fields: int = 0
    layer1: int = 0
    layer2: int = 0
    layer3: int = 0
    layer4: int = 0
    edited: int = 0
    unassigned: int = 0
    rounds: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_seconds: float = 0.0
    wall_seconds: float = 0.0
    review: str = "none"
    rejections: int = 0


COLUMNS = tuple(one.name for one in fields(Row))
"""The table's header, which is the row's own order."""


class NoModel:
    """A backend that refuses, so that a replay cannot make a call.

    `pipeline.run` asks a backend whether it can run before it calls one, and
    raises `ModelUnavailable` when it cannot. So a replay over a set with no
    saved spec stops there and says so in its row, rather than spending the call
    that would have written one.
    """

    name = "none"

    def unavailable(self) -> str:
        """Why no call may be made."""
        return "replay: no model call is allowed"

    def call(self, system: str, prompt: str, tools: Sequence = ()) -> None:
        """Never reached: nothing calls a backend it has been told is unavailable."""
        raise ModelUnavailable(self.unavailable())


def documents(
    root: Path, paths: Sequence[Path] = (), suffixes: Sequence[str] = DEFAULT_SUFFIXES
) -> list[Path]:
    """Every document of the corpus a sweep is to run.

    Every file of a wanted suffix, `is_document` or not: the ones that are not
    are the sweep's to say so in a row of the table, rather than to leave out
    of it silently.

    Args:
        root: The corpus directory.
        paths: Folders under it to run, relative to it; all of it if empty.
        suffixes: The file suffixes that are documents, with or without the dot.

    Returns:
        The documents, sorted by their path relative to the root, so that a set's
        sheets run together and in the same order every time. Paths that overlap
        — a folder and the root above it, or one folder named twice — name a
        document once: the table is one row per document.
    """
    root = Path(root)
    wanted = {"." + one.lower().lstrip(".") for one in suffixes}
    found = {
        path
        for where in ([root / one for one in paths] or [root])
        for path in where.rglob("*")
        if path.is_file() and path.suffix.lower() in wanted
    }
    return sorted(found, key=lambda path: path.relative_to(root).as_posix())


def stage(
    root: Path, folder: Path, work: Path, suffixes: Sequence[str] = DEFAULT_SUFFIXES
) -> Path:
    """Copies one set's folder into the work directory, and empties it first.

    The set is the folder, and its sheets are the files in it: a subfolder
    holding a document of its own is a set of its own and is left for its own
    staging, while one holding none — figures, styles — comes along, since the
    sheets refer to it. A tex file that is a drawing and not a sheet is no
    document, so the folder of them is one of those that come along. What no run could read is left behind: the archives, and
    the PDFs unless they are what is being run. They are most of what a corpus
    weighs, and the copy is made again every sweep.

    A set staged twice is emptied first, so a sweep starts from nothing every
    time. That is the set's own folder and never the work directory, which holds
    the other sets of the sweep and whatever else the user pointed `--work` at.

    Args:
        root: The corpus directory.
        folder: The set's folder under it.
        work: Where the copies are kept.
        suffixes: What counts as a document, as `documents` reads it.

    Returns:
        The folder's copy, which is what the runs are given.
    """
    relative = folder.relative_to(root)
    into = Path(work) / (ROOT_SET if relative == Path(".") else relative)
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    pdfs = "pdf" in {one.lower().lstrip(".") for one in suffixes}
    ignore = shutil.ignore_patterns(*(("*.zip",) if pdfs else ("*.zip", "*.pdf")))
    named = [path.name for path in folder.iterdir()]
    skipped = ignore(str(folder), named) | {SPEC_NAME, RECORD_NAME}
    for path in folder.iterdir():
        # A draft is named after the source it was frozen from, so there is one
        # per sheet rather than one per folder: the name is not known in advance
        # and the suffix is what says a file is one.
        if path.name.endswith(package.DRAFT_SUFFIX):
            continue
        if path.is_dir():
            # A folder of figures whose tex sources are drawings holds no
            # document, so it comes along with the sheets that refer to it
            # rather than being staged and run as a set of its own.
            if not any(
                is_document(one) for one in documents(path, suffixes=suffixes)
            ):
                shutil.copytree(path, into / path.name, ignore=ignore)
        elif path.name not in skipped:
            shutil.copy2(path, into / path.name)
    return into


def _one_line(text: str) -> str:
    """A reason as one line of the table: a message that wraps stays one cell."""
    return " ".join(text.split())


def run_one(
    source: Path,
    *,
    name: str,
    set_name: str,
    spec: Path,
    settings: Settings,
    rounds: int = 3,
    tries: int = 3,
    replay: bool = False,
    cache: Path = pipeline.DEFAULT_CACHE_DIR,
    backend: Optional[Backend] = None,
) -> Row:
    """Runs the pipeline over one document and reads the row off what it did.

    Nothing a document does ends the sweep: what the pipeline raises, and what
    anything else raises, becomes this document's outcome and the next one runs.

    Args:
        source: The document, in the work directory rather than the corpus.
        name: What to call it in the table, relative to the corpus root.
        set_name: Its set, relative to the corpus root.
        spec: The set's spec, in the mirror tree: read if it is there, written
            if it is not.
        settings: The environment the run has available.
        rounds: The round limit, ignored in a replay, which can run none.
        tries: How many specs the run may write before keeping the best.
        replay: Run the saved spec and nothing else, making no model call.
        cache: Where the OCR of each PDF is kept.
        backend: The backend to write a spec with, chosen from the settings if
            absent.

    Returns:
        The document's row.
    """
    existed = Path(spec).is_file()
    row = Row(source=name, set=set_name)
    started = time.monotonic()
    try:
        result = pipeline.run(
            source,
            out_dir=source.parent / "out",
            settings=settings,
            spec=spec,
            review=row.review,
            # A replay has no model to run a round with, so it stops at the
            # report: the row then says what the saved spec left rather than
            # that a call could not be made.
            rounds=0 if replay else rounds,
            tries=tries,
            cache_dir=cache,
            backend=NoModel() if replay else backend,
        )
    except ModelUnavailable as error:
        row.outcome = "no model" if existed else "no spec"
        row.reason = _one_line(str(error))
    except ModelError as error:
        # Which call did not finish, and what the provider said it stopped on.
        # A row reading `error: ResultError` says neither.
        row.outcome = f"{error.stage or 'model'} failed"
        row.reason = _one_line(str(error))
    except SpecRejected as error:
        row.outcome = "spec rejected"
        row.reason = _one_line(str(error))
    except BadSpec as error:
        row.outcome = "bad spec"
        row.reason = _one_line(str(error))
    except Exception as error:
        # Whatever else a document manages to raise is that document's row: a
        # sweep of a corpus is not worth ending over one file in it.
        row.outcome = f"error: {type(error).__name__}"
        row.reason = _one_line(str(error))
    else:
        # A build in2lambda refused is not a draft the checks faulted: the
        # report came clean and the export is what stopped, which the rounds
        # column would otherwise misreport as the spec never covering the sheet.
        if result.zip_path:
            row.outcome = "built"
        else:
            row.outcome = "build refused" if result.clean else "faulted"
        row.reason = _one_line(result.reason)
        row.spec = "reused" if result.reused else "rewritten" if existed else "wrote"
        if result.coverage is not None:
            row.layout = result.coverage.layout
            # Coverage is what the spec run alone made of the source, so these
            # two stay spec-time on purpose: they say how far the spec got
            # before any round, which is what says whether it is worth reusing.
            row.blocks = result.coverage.blocks
            row.unassigned = len(result.coverage.unassigned)
        if result.draft is not None:
            # The counts are keyed by the column names they fill, and `fields`
            # is their total, since a round writes fields the spec run's own
            # count knows nothing about.
            counted = package.layers(result.draft)
            for column, count in counted.items():
                setattr(row, column, count)
            row.fields = sum(
                count for column, count in counted.items() if column != "edited"
            )
        row.rounds = len(result.rounds)
        row.input_tokens = result.usage.input_tokens
        row.output_tokens = result.usage.output_tokens
        row.model_seconds = round(result.usage.seconds, 3)
    row.wall_seconds = round(time.monotonic() - started, 3)
    return row


def write_results(rows: Sequence[Row], path: Path) -> None:
    """Writes the table, header and all, over whatever was there before.

    Args:
        rows: The rows, in the order they are to be written.
        path: The file to write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def sweep(
    root: Path,
    *,
    paths: Sequence[Path] = (),
    suffixes: Sequence[str] = DEFAULT_SUFFIXES,
    results: Path = DEFAULT_RESULTS,
    work: Path = DEFAULT_WORK_DIR,
    specs: Path = DEFAULT_SPEC_DIR,
    replay: bool = False,
    rounds: int = 3,
    tries: int = 3,
    cache: Path = pipeline.DEFAULT_CACHE_DIR,
    settings: Optional[Settings] = None,
    backend: Optional[Backend] = None,
) -> list[Row]:
    """Runs every document of a corpus, printing a line each and writing the table.

    Args:
        root: The corpus directory.
        paths: Folders under it to run, relative to it; all of it if empty.
        suffixes: The file suffixes that are documents.
        results: Where to write the table.
        work: Where each set's folder is copied to be run.
        specs: The tree the sets' specs are kept in, mirroring the corpus.
        replay: Run the saved specs and nothing else, making no model call.
        rounds: The round limit each run is given.
        tries: How many specs each run may write before keeping the best.
        cache: Where the OCR of each PDF is kept, so that a sweep pointed at a
            cache another run filled converts nothing.
        settings: The environment the runs have available.
        backend: The backend to write the specs with, chosen from the settings
            if absent.

    Returns:
        One row per document, in the order they ran.
    """
    root = Path(root).resolve()
    work = Path(work).resolve()
    specs = Path(specs).resolve()
    settings = settings if settings is not None else Settings()

    staged: dict[Path, Optional[Path]] = {}
    unstageable: dict[Path, tuple[str, str]] = {}
    rows = []
    for document in documents(root, paths, suffixes):
        folder = document.parent
        relative = document.relative_to(root)
        # Reading a file to see whether it is a document can fail the way any
        # other read can, and that is this document's row as it was when the
        # read happened inside `run_one`, rather than the end of the sweep.
        try:
            of_its_own = is_document(document)
        except OSError as error:
            row = Row(
                source=relative.as_posix(),
                set=relative.parent.as_posix(),
                outcome=f"error: {type(error).__name__}",
                reason=_one_line(str(error)),
            )
            print(f"{row.outcome:<20} {row.source}")
            rows.append(row)
            continue
        # Before the staging, so that a folder of drawings is never staged on
        # one of their account: they are its parent set's, and came along with it.
        if not of_its_own:
            row = Row(
                source=relative.as_posix(),
                set=relative.parent.as_posix(),
                outcome="skipped",
                reason="no \\begin{document}",
            )
            print(f"{row.outcome:<20} {row.source}")
            rows.append(row)
            continue
        # A solutions document is frozen into the run of the questions document
        # it answers, so the pair is one row, which is the questions document's.
        # One with no questions document beside it has no questions to attach
        # its solutions to, and is a row of its own saying so.
        if pair.questions_stem(document) is not None:
            if pair.questions_beside(document) is not None:
                continue
            row = Row(
                source=relative.as_posix(),
                set=relative.parent.as_posix(),
                outcome="skipped",
                reason="solutions without questions",
            )
            print(f"{row.outcome:<20} {row.source}")
            rows.append(row)
            continue
        # Staging is per set and the row is per document, so the guard is here
        # rather than in `run_one`: what a copy raises — an unreadable folder, a
        # file that goes while it is being read — is a row for every sheet of
        # the set, none of which ran, and the sets after it still do.
        if folder not in staged:
            try:
                staged[folder] = stage(root, folder, work, suffixes)
            except Exception as error:
                staged[folder] = None
                unstageable[folder] = (
                    f"error: {type(error).__name__}",
                    _one_line(str(error)),
                )
        if staged[folder] is None:
            outcome, reason = unstageable[folder]
            row = Row(
                source=relative.as_posix(),
                set=relative.parent.as_posix(),
                outcome=outcome,
                reason=reason,
            )
            print(f"{row.outcome:<20} {row.source}")
            rows.append(row)
            continue
        spec = specs / relative.parent / SPEC_NAME
        spec.parent.mkdir(parents=True, exist_ok=True)
        row = run_one(
            staged[folder] / document.name,
            name=relative.as_posix(),
            set_name=relative.parent.as_posix(),
            spec=spec,
            settings=settings,
            rounds=rounds,
            tries=tries,
            replay=replay,
            cache=cache,
            backend=backend,
        )
        print(f"{row.outcome:<20} {row.source}")
        rows.append(row)

    write_results(rows, results)
    return rows
