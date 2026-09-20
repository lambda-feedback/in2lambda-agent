"""Which document holds the solutions to which, going by the file names.

A folder of worksheets often writes the questions and the solutions as separate
documents: `Worksheet_1.pdf` beside `Worksheet_1_solutions.pdf`. A solutions
document alone holds no questions for its solutions to answer, so the run
freezes the two into one draft, the questions first and the solutions second,
and is named after the questions document.

The pairing is by name. A solutions document is one whose stem ends in
`solutions` after a space, an underscore or a hyphen, in any case. Its questions
document is the file beside it whose stem is the stem before that ending and
whose suffix is the same.
"""

import re
from pathlib import Path
from typing import Optional

SOLUTIONS = re.compile(r"^(?P<stem>.+?)[ _-]solutions$", re.IGNORECASE)
"""A solutions document's stem, and the questions document's stem within it.

The separator is required, so `resolutions.pdf` is not a solutions document and
`Solutions.pdf` names no questions document.
"""


class SolutionsWithoutQuestions(ValueError):
    """A solutions document has no questions document beside it."""


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
            for path in sorted(solutions.parent.iterdir())
            if path.is_file()
            and path.stem == stem
            and path.suffix.lower() == solutions.suffix.lower()
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
            for path in sorted(questions.parent.iterdir())
            if path.is_file()
            and path.suffix.lower() == questions.suffix.lower()
            and questions_stem(path) == questions.stem
        ),
        None,
    )


def of(source: Path) -> tuple[Path, Optional[Path]]:
    """The two documents a run freezes, whichever of them the user named.

    Args:
        source: The document the user named, the questions or the solutions.

    Returns:
        The questions document, and the solutions document to freeze after it,
        or None where the folder holds no solutions document for it.

    Raises:
        SolutionsWithoutQuestions: `source` is a solutions document and the
            folder holds no questions document for it.
    """
    source = Path(source)
    stem = questions_stem(source)
    if stem is None:
        return source, solutions_beside(source)
    questions = questions_beside(source)
    if questions is None:
        raise SolutionsWithoutQuestions(
            f"solutions without questions: nothing named {stem}{source.suffix} "
            f"beside {source.name}"
        )
    return questions, source
