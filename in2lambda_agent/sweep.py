"""The two-route conversion over a corpus, one row of a table per sheet.

A set is a folder holding at least one questions document. Each set is
converted as `routes.convert_folder` converts one: a model call writes a Lua
filter from the first sheet of the set, and each sheet of the set then runs
through route A, the direct model call, and route B, that filter under pandoc.
Pandoc reads a PDF sheet as the markdown its OCR made, so a set of PDFs has a
filter and both routes like any other. The sweep runs the sheets itself rather
than calling `convert_folder`, so that it can time each sheet and write a row
for a sheet whose conversion raised.

The measures of docs/plan.md are the columns: how many fields the two routes
returned, how many they agreed on, how many the adjudication call decided, how
many are flagged for a person, how many are not quotes of the source, and what
the model calls read and wrote.

Nothing here writes into the corpus. Each set's filter, and each sheet's set
folder and zip, are written under the work directory; the OCR of each PDF is
read from and written to the cache directory.
"""

import csv
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional, Sequence

from in2lambda_agent import ocr, pair, routes
from in2lambda_agent.model import Backend, choose_backend
from in2lambda_agent.settings import Settings

DEFAULT_SUFFIXES = ("tex", "md", "docx")
"""What a sweep runs over unless told otherwise: everything but the PDFs, which
need Mathpix credentials and a call each."""

DEFAULT_RESULTS = Path("results.csv")
"""The table, under the directory the user ran from."""

DEFAULT_WORK_DIR = Path(".in2lambda-agent/corpus")
"""Where each set's filter and each sheet's zip are written."""

NO_SET = "no set: "
"""What the reason of a sheet that built no set begins with."""


@dataclass
class Row:
    """One sheet's line of the table, in the order the columns are written.

    Attributes:
        set: The sheet's folder, relative to the corpus root.
        sheet: The questions document, relative to the corpus root. The
            solutions document beside it is read into the same row.
        questions: How many questions the conversion returned.
        parts: How many parts those questions hold.
        fields: How many text fields the questions and parts hold.
        agreed: How many fields the two routes returned the same text for.
        adjudicated: How many fields the adjudication call decided. A field
            only one route filled is neither agreed nor adjudicated: the count
            of those is `fields - agreed - adjudicated`.
        flagged: How many fields a person is asked to read.
        not_verbatim: How many of the flagged fields are not quotes of the
            source.
        tokens: What the sheet's model calls read and wrote. The first sheet of
            a set carries the set's filter call as well as its own.
        seconds: How long the sheet took, the filter call included on the first
            sheet of a set.
        reason: Empty where both routes ran and the sheet built its set.
            `no set: <error>` where the conversion raised and the sheet built
            nothing, `route B failed: <pandoc's message>` where the set is
            route A's alone, and `no filter: <why>` where the set's filter call
            did not finish and so no sheet of it ran route B.
    """

    set: str
    sheet: str
    questions: int = 0
    parts: int = 0
    fields: int = 0
    agreed: int = 0
    adjudicated: int = 0
    flagged: int = 0
    not_verbatim: int = 0
    tokens: int = 0
    seconds: float = 0.0
    reason: str = ""

    @property
    def built(self) -> bool:
        """Whether the sheet wrote a set. A sheet route B failed on wrote one."""
        return not self.reason.startswith(NO_SET)


COLUMNS = tuple(one.name for one in fields(Row))
"""The table's header, which is the row's own order."""


def _one_line(text: str) -> str:
    """A reason as one line of the table: a message that wraps stays one cell."""
    return " ".join(text.split())


def _sheets(
    folder: Path, suffixes: Sequence[str] = DEFAULT_SUFFIXES
) -> list[tuple[Path, Optional[Path]]]:
    """The pairs of a folder a sweep of these suffixes converts.

    `pair.pairs_in` holds a sheet written both as `Sheet_1.tex` and as
    `Sheet_1.pdf` once, under the suffix it prefers, which is the tex. A sweep
    of the PDFs alone therefore has no such sheet to run.

    A tex file with no document body is a drawing or a preamble rather than a
    sheet, as `pair.is_document` reads one, and is left out: converting one
    makes two model calls and returns a set of no questions.
    """
    wanted = {"." + one.lower().lstrip(".") for one in suffixes}
    return [
        (sheet, solutions)
        for sheet, solutions in pair.pairs_in(folder)
        if sheet.suffix.lower() in wanted and pair.is_document(sheet)
    ]


def _filter_pair(
    pairs: Sequence[tuple[Path, Optional[Path]]]
) -> tuple[Path, Optional[Path]]:
    """The pair a set's filter is written from: the first tex, md or docx sheet,
    and the first PDF where the set has no other.

    `routes.write_filter` shows the call pandoc's tree of the document, and a
    PDF's tree is the tree of the markdown its OCR made, which is a reading of
    the printed page rather than the document's own structure. A sheet pandoc
    reads itself is the better one to write the filter from, so it is preferred
    however far down the set it is.
    """
    return next((one for one in pairs if one[0].suffix.lower() != ".pdf"), pairs[0])


def sets(
    root: Path, paths: Sequence[Path] = (), suffixes: Sequence[str] = DEFAULT_SUFFIXES
) -> list[Path]:
    """The folders of a corpus a sweep converts.

    Args:
        root: The corpus directory.
        paths: Folders under it to run, relative to it; all of it if empty.
        suffixes: The file suffixes that are documents, with or without the dot.

    Returns:
        Every folder holding at least one questions document of a wanted
        suffix, the named folders themselves included, sorted by their path
        relative to the root. A folder of figures, and a folder holding a
        solutions document alone, are not sets and have no row.
    """
    root = Path(root)
    found = {
        folder
        for where in ([root / one for one in paths] or [root])
        for folder in [where, *(one for one in where.rglob("*") if one.is_dir())]
        if _sheets(folder, suffixes)
    }
    return sorted(found, key=lambda folder: folder.relative_to(root).as_posix())


def row_of(
    set_name: str, sheet: str, converted: routes.Converted, seconds: float
) -> Row:
    """The row of a sheet that converted.

    `converted.fields` counts the fields the two routes were compared over and
    is zero where route B did not run, so the column is read off the reply
    instead, which holds every field either route filled.

    Args:
        set_name: The sheet's folder, relative to the corpus root.
        sheet: The sheet, relative to the corpus root.
        converted: What `routes.convert` returned.
        seconds: How long the conversion took.
    """
    return Row(
        set=set_name,
        sheet=sheet,
        questions=len(converted.reply),
        parts=sum(len(q.get("parts", [])) for q in converted.reply),
        fields=len(routes.fields(converted.reply)),
        agreed=converted.agreed,
        adjudicated=converted.adjudicated,
        flagged=len(converted.flags),
        not_verbatim=len(
            [one for one in converted.flags if one.reason == routes.NOT_VERBATIM]
        ),
        tokens=converted.tokens,
        seconds=round(seconds, 3),
        reason=(
            f"route B failed: {_one_line(converted.route_b_error)}"
            if converted.route_b_error
            else ""
        ),
    )


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
    cache: Path = ocr.DEFAULT_CACHE_DIR,
    settings: Optional[Settings] = None,
    backend: Optional[Backend] = None,
) -> list[Row]:
    """Converts every set of a corpus, printing a line per sheet and writing the table.

    What a sheet raises is that sheet's row, and the sheets after it still run.
    A set whose filter call raised converts every sheet of itself through route
    A alone, and says so in each of its rows.

    Args:
        root: The corpus directory.
        paths: Folders under it to run, relative to it; all of it if empty.
        suffixes: The file suffixes that are documents.
        results: Where to write the table.
        work: Where each set's filter and each sheet's zip are written.
        cache: Where the OCR of each PDF is kept, so that a sweep pointed at a
            cache another run filled converts no PDF again.
        settings: The environment the runs have available.
        backend: The backend the calls are made to, chosen from the settings if
            absent.

    Returns:
        One row per sheet, in the order they ran.
    """
    root = Path(root).resolve()
    work = Path(work).resolve()
    settings = settings if settings is not None else Settings()
    backend = backend if backend is not None else choose_backend(settings)

    rows = []
    for folder in sets(root, paths, suffixes):
        set_name = folder.relative_to(root).as_posix()
        pairs = _sheets(folder, suffixes)
        into = work / set_name
        into.mkdir(parents=True, exist_ok=True)
        lua: Optional[Path] = None
        no_filter = ""
        filter_tokens = 0
        written_from = _filter_pair(pairs)
        started = time.monotonic()
        try:
            source, usage = routes.write_filter(
                written_from[0], written_from[1], backend, cache_dir=cache, settings=settings
            )
        except Exception as problem:
            no_filter = f"no filter: {_one_line(str(problem))}"
        else:
            lua = into / "filter.lua"
            lua.write_text(source, encoding="utf-8")
            filter_tokens = usage.usage.input_tokens + usage.usage.output_tokens
        filter_seconds = time.monotonic() - started

        for number, (sheet, solutions) in enumerate(pairs):
            name = sheet.relative_to(root).as_posix()
            started = time.monotonic()
            try:
                converted = routes.convert(
                    sheet,
                    solutions,
                    out_dir=into / sheet.stem,
                    cache_dir=cache,
                    backend=backend,
                    settings=settings,
                    lua=lua,
                    name=sheet.stem,
                )
            except Exception as problem:
                row = Row(
                    set=set_name,
                    sheet=name,
                    seconds=round(time.monotonic() - started, 3),
                    reason=f"{NO_SET}{_one_line(str(problem))}",
                )
            else:
                row = row_of(set_name, name, converted, time.monotonic() - started)
                if no_filter:
                    row.reason = no_filter
            if number == 0:
                # The filter is the set's and is written once, so the set's
                # first sheet carries what writing it read, wrote and took.
                row.tokens += filter_tokens
                row.seconds = round(row.seconds + filter_seconds, 3)
            print(
                f"{'built' if row.built else 'no set':<8} {row.sheet}: "
                f"{row.fields} fields, flagged {row.flagged}"
                + (f"  {row.reason}" if row.reason else "")
            )
            rows.append(row)

    write_results(rows, results)
    return rows
