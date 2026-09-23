"""The merge gate: every target against its export, and no model call.

Every ticket before this one was tested on synthetic fixtures. The first sweep
over real documents failed on four faults no fixture had. A merge now requires
an end-to-end run over real documents, and a target is the one place the agent
can be marked right or wrong rather than merely flagged: what it converts is
compared with the set Lambda Feedback exported from the same two documents.

So the gate is a `targets` run with `replay` set. The filter and route A's
reply are read back from the tree the repository keeps rather than asked for, so
the run makes neither of the two calls that read the document: what fails is a
change to the agent and not a model wording a field differently today. A target
whose filter or reply is not saved is an error naming the file, rather than a
call nobody asked for.

`in2lambda-agent gate ci-corpus/targets --filters ci-corpus/filters` is what CI
runs, over the one target the repository holds. The private targets are run the
same way, by the same command, over the tree on the machine that holds them.
"""

from pathlib import Path
from typing import Optional, Sequence

from in2lambda_agent import targets
from in2lambda_agent.model import Backend
from in2lambda_agent.settings import Settings

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "in2lambda-agent"
"""Where the gate reads the OCR of each PDF. The directory is outside every
worktree, so a PDF converted on one branch is not converted again on the next.
`targets` on its own keeps its cache under the directory the user ran from."""


def run(
    root: Path,
    *,
    paths: Sequence[Path] = (),
    filters: Path,
    work: Path,
    cache: Path = DEFAULT_CACHE_DIR,
    settings: Optional[Settings] = None,
    backend: Optional[Backend] = None,
) -> list[targets.Result]:
    """Replays every target under a root and compares each with its export.

    Nothing is written into the repository or into the targets: each target's
    set is written under `work`.

    Args:
        root: The directory the targets are under.
        paths: Folders under it to run, relative to it; all of it if empty.
        filters: The tree the targets' filters and saved replies are kept in.
        work: Where each target's set is written, under the target's own name.
        cache: Where the OCR of each PDF is kept, shared between worktrees.
        settings: The environment the runs have available.
        backend: The backend the adjudication calls are made to, chosen from
            the settings if absent.

    Returns:
        One result per target, in the order they ran.
    """
    return targets.run(
        root,
        paths=paths,
        filters=filters,
        out_dir=work,
        cache_dir=cache,
        settings=settings,
        backend=backend,
        fresh=False,
        replay=True,
    )
