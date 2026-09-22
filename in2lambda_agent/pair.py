"""Which document holds the solutions to which, going by the file names.

A folder of worksheets often writes the questions and the solutions as separate
documents: `Worksheet_1.pdf` beside `Worksheet_1_solutions.pdf`. The run
freezes the two into one draft, the questions first and the solutions second,
and is named after the questions document. A solutions document with no
questions document beside it is converted on its own.

The pairing is by name. A solutions document is one whose stem ends in
`solutions` or `sol` after a space, an underscore or a hyphen, in any case. Its
questions document is the file beside it whose stem is the stem before that
ending and whose suffix is the same.

`pairs_in` reads a whole folder that way: every sheet in it with its solutions
document, which is what a run over a folder converts.
"""

import re
from pathlib import Path
from typing import Optional

SOLUTIONS = re.compile(r"^(?P<stem>.+?)[ _-](solutions|sol)$", re.IGNORECASE)
"""A solutions document's stem, and the questions document's stem within it.

The separator is required, so `resolutions.pdf` is not a solutions document and
`Solutions.pdf` names no questions document.
"""

DOCUMENTS = (".tex", ".docx", ".md", ".pdf")
"""The suffixes a folder's sheets are looked for under, first preferred.

A folder often holds one sheet twice, as the source and as the file compiled
from it: `Sheet_1.tex` beside `Sheet_1.pdf`. The earlier suffix is the sheet,
because pandoc reads it, and a PDF costs an OCR call and gives the pandoc
filter nothing to read.
"""


def questions_stem(document: Path) -> Optional[str]:
    """The stem of the questions document a solutions document answers.

    Args:
        document: Any file.

    Returns:
        The stem before the `solutions` ending, or None where the name has no
        such ending.
    """
    found = SOLUTIONS.match(Path(document).stem)
    return found.group("stem") if found else None


def questions_beside(solutions: Path) -> Optional[Path]:
    """The questions document of a solutions document, in the same folder.

    Args:
        solutions: A solutions document, as `questions_stem` reads one.

    Returns:
        The file with that stem and the same suffix, or None where the folder
        holds no such file. The suffix is compared without regard to case.
    """
    stem = questions_stem(solutions)
    if stem is None:
        return None
    solutions = Path(solutions)
    return next(
        (
            path
            for path in _files_in(solutions.parent)
            if path.stem == stem and path.suffix.lower() == solutions.suffix.lower()
        ),
        None,
    )


def solutions_beside(questions: Path) -> Optional[Path]:
    """The solutions document of a questions document, in the same folder.

    Args:
        questions: A document that is not itself a solutions document.

    Returns:
        The first file, in name order, whose `questions_stem` is this
        document's stem and whose suffix is the same, or None where the folder
        holds no such file.
    """
    questions = Path(questions)
    return next(
        (
            path
            for path in _files_in(questions.parent)
            if path.suffix.lower() == questions.suffix.lower()
            and questions_stem(path) == questions.stem
        ),
        None,
    )


def _files_in(folder: Path) -> list[Path]:
    """The files of a folder, in name order, and none where there is no folder.

    The pairing is the first thing a run does, before anything has read the
    source, so it is where a mistyped path arrives first. It has nothing to say
    about one: a source that is not there is in2lambda's to complain about, in
    the words it uses for every file it cannot read.
    """
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file())


def pairs_in(folder: Path) -> list[tuple[Path, Optional[Path]]]:
    """The documents a folder run converts, in name order.

    Args:
        folder: A folder of sheets and their solutions.

    Returns:
        One pair per sheet: the questions document and the solutions document
        beside it, or None where there is none. One sheet per stem, under the
        suffix `DOCUMENTS` prefers, so that a sheet held twice converts once. A
        solutions document whose questions document is missing is left out,
        since there is no sheet for it to answer. Subfolders, `figures/` among
        them, are not looked into. An empty list where the folder is not there.
    """
    sheets: dict[str, Path] = {}
    for path in _files_in(Path(folder)):
        if path.suffix.lower() not in DOCUMENTS or questions_stem(path) is not None:
            continue
        held = sheets.get(path.stem)
        if held is None or DOCUMENTS.index(path.suffix.lower()) < DOCUMENTS.index(held.suffix.lower()):
            sheets[path.stem] = path
    return [(path, solutions_beside(path)) for path in sheets.values()]


def of(source: Path) -> tuple[Path, Optional[Path]]:
    """The two documents a run freezes, whichever of them the user named.

    Args:
        source: The document the user named, the questions or the solutions.

    Returns:
        The questions document, and the solutions document to freeze after it,
        or None where the folder holds no solutions document for it. A
        solutions document with no questions document beside it is returned as
        the first of the two, so the run converts it on its own.
    """
    source = Path(source)
    if questions_stem(source) is None:
        return source, solutions_beside(source)
    questions = questions_beside(source)
    if questions is None:
        return source, None
    return questions, source
