"""The run: source in, Lambda Feedback zip out.

The stages are the design spec's pipeline. The agent acts at three of them — the
OCR pass, the model calls that write the set's spec, and the rounds that answer
what the checks found — and in2lambda does the rest: freezing the source,
running the spec over it, checking the draft and writing the zip.

A spec that covers its source is layer 1 and builds with nothing more asked of
it. A draft the checks have something to say about gets the rounds: up to N
model calls, each with in2lambda's draft commands as its tools, writing fields
at layers 3 and 4 until the checks are quiet or the limit runs out, and then the
report and no zip. A round that leaves only what it was given ends the run there
rather than using the limit up: a finding no range of the source answers — a part
whose solution is not on the sheet — is reported, not invented, and the next
round would be the same prompt over the same report. A spec saved from an
earlier sheet is written again before any of that where the checks fault the
draft it filled, since a spec that covers the set is worth more than a field
repaired in one sheet of it; that rewrite is layer 1, and is not one of the
rounds. `spec.iterate_spec` is what writes a spec, in up to `tries` calls, and
what the run goes on with is the one of them that covered the set best.

A run asked for a review stops once the checks are quiet: it renders the
questions the reviewer is to see, leaves a record of them in the cache, and
builds nothing. `resume` is the other half of that run — one call per verdict,
out of another process — and it is what finally builds, once every question the
reviewer was shown has been approved.
"""

import random
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from in2lambda_agent import package, pair
from in2lambda_agent.fix import RoundResult, fix_round, summary, unrepaired
from in2lambda_agent.mathpix import MathpixClient
from in2lambda_agent.model import Backend, ModelUnavailable, Usage, choose_backend
from in2lambda_agent.ocr import MEDIA_NAME, ocr_pdf
from in2lambda_agent.review import RECORD, Question, Review, choose
from in2lambda_agent.settings import Settings
from in2lambda_agent.spec import (
    RECORD_NAME,
    Previous,
    Second,
    SpecTry,
    iterate_spec,
    record_run,
    spec_path,
)

# Where the OCR of each PDF is kept, under the directory the user ran from.
DEFAULT_CACHE_DIR = Path(".in2lambda-agent")

# Where the copy of the set's other document, which each candidate spec is run
# over, is kept under the cache directory.
SECOND_NAME = "second"

# A markdown image whose file is in the OCR's media folder, as far as the folder
# name: `![a plot](media/plot.png)`. What a second source's images are renamed
# by when they are copied beside the first source's.
MEDIA_REFERENCE = re.compile(rf"(!\[[^\]]*\]\(){MEDIA_NAME}/")

REVIEW_MODES = ("none", "sample", "per-question")


@dataclass
class StageResult:
    """What one stage did, as one line of output."""

    name: str
    message: str


@dataclass
class RunResult:
    """What a run did, in order, what it covered, and the zip it wrote.

    `draft` and `reused` are what the record already says, on the result as
    well, so that a harness running many documents can read a run's provenance
    and its spec reuse without reading the record back off disk; and `clean` is
    whether the draft can be built, which `zip_path` does not answer, since a
    build in2lambda refuses leaves a clean report and no zip.

    `reason` is what the run has to say for itself: why it ended without a zip,
    in the words of the stage that stopped it — the refusal, or the first error
    the checks were still finding — or, where it did build, the warnings it
    built past. The stage lines say as much, but they are printed and gone; this
    is what a harness has to write down.

    `on_stage` is called with each stage as the run adds it. A caller that
    reads `stages` reads them once the run has returned; the local web page
    sends each line to the browser while the run is still going.
    """

    stages: list[StageResult] = field(default_factory=list)
    zip_path: Optional[Path] = None
    coverage: Optional[package.Coverage] = None
    usage: Usage = field(default_factory=Usage)
    tries: list[SpecTry] = field(default_factory=list)
    second: Optional[Second] = None
    rounds: list[RoundResult] = field(default_factory=list)
    review: Optional[Review] = None
    draft: Optional[Path] = None
    reused: bool = False
    clean: bool = False
    reason: str = ""
    on_stage: Optional[Callable[[StageResult], None]] = field(
        default=None, compare=False, repr=False
    )

    def add_stage(self, name: str, message: str) -> None:
        """Records one stage's line and calls `on_stage` with it.

        Args:
            name: The stage, as the printed line names it.
            message: What the stage did, as the printed line says it.
        """
        stage = StageResult(name, message)
        self.stages.append(stage)
        if self.on_stage is not None:
            self.on_stage(stage)


def run(
    source: Path,
    *,
    out_dir: Path,
    settings: Settings,
    spec: Optional[Path] = None,
    review: str = "none",
    rounds: int = 3,
    tries: int = 3,
    sample: int = 3,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    fresh_ocr: bool = False,
    mathpix: Optional[MathpixClient] = None,
    backend: Optional[Backend] = None,
    rng: Optional[random.Random] = None,
    on_stage: Optional[Callable[[StageResult], None]] = None,
) -> RunResult:
    """Drives in2lambda over one source file.

    Args:
        source: The question file to convert, or the solutions file beside it,
            which runs the questions file it answers.
        out_dir: Where in2lambda writes the set's JSON folder and zip.
        settings: The environment the run has available.
        spec: The set's spec file, when it is not the one beside the source.
        review: One of REVIEW_MODES.
        rounds: The round limit, N in the design spec: how many model calls may
            answer what the checks found before the run stops without a zip.
        tries: How many specs may be written before the best of them is saved
            for the set.
        sample: How many questions a review in sample mode shows.
        cache_dir: Where the OCR of each PDF is kept, and where a review that
            is waiting to be answered is left.
        fresh_ocr: Convert a PDF again even if it is already cached.
        mathpix: The client to convert with, built from the settings if absent.
        backend: The model backend to write the spec with, chosen from the
            settings if absent and asked for only when a spec must be written.
        rng: What fills a sample out, so that a test can fix which questions it
            picks.
        on_stage: Called with each stage as the run adds it, for a caller that
            shows the lines while the run is still going.

    Returns:
        Each stage's line, what the spec covered, what the model calls cost,
        what each fixing round did, the zip where one was written — or the
        review waiting to be answered, where the run stopped for one — and why
        where no zip was written.

    Raises:
        MathpixError: If a PDF cannot be converted, MissingCredentials among
            them when the run has no Mathpix credentials.
        ModelUnavailable: If a spec must be written and no backend can run.
        BadSpec: If what the model answers with is not a spec.
        SpecRejected: If in2lambda will not run the spec.
        SourceError: If in2lambda cannot freeze or check the source.
        SolutionsWithoutQuestions: If `source` is a solutions file and no
            questions file is beside it.
    """
    # A relative --out means the directory the user ran from, whatever in2lambda
    # does with the working directory along the way.
    source = Path(source)
    out_dir = Path(out_dir).resolve()
    cache_dir = Path(cache_dir).resolve()

    # A sheet whose solutions are written as a file of their own is one run and
    # one draft, named after the questions file. So the run is the questions
    # file's from here on, whichever of the two the user named.
    source, solutions = pair.of(source)

    # The set is the folder the user's file is in, so this is settled before
    # OCR moves a PDF's markdown off into the cache.
    saved = spec_path(source, spec)

    result = RunResult(on_stage=on_stage)

    # The rest of the pipeline reads markdown, so a PDF becomes markdown first.
    frozen, _, message = _markdown(
        source, cache_dir=cache_dir, settings=settings, mathpix=mathpix, fresh=fresh_ocr
    )
    frozen_solutions = None
    if solutions is not None:
        frozen_solutions, solutions_media, said = _markdown(
            solutions,
            cache_dir=cache_dir,
            settings=settings,
            mathpix=mathpix,
            fresh=fresh_ocr,
        )
        message += f"; {said}"
        # in2lambda freezes into one draft the documents of one directory, and
        # the OCR of each PDF is cached in an entry named after its own hash. So
        # the solutions markdown, and the images it refers to, are copied beside
        # the questions markdown.
        if frozen_solutions.parent != frozen.parent:
            frozen_solutions = _copy_beside(
                frozen_solutions,
                solutions_media,
                name=solutions.stem,
                into=frozen.parent,
            )
    result.add_stage("ocr", message)

    # The saved spec's own pass, where the set has one. A spec the checks fault
    # is written again, and what that pass covered is what the first call is
    # asked to improve on.
    reused = saved.is_file()
    previous = None
    if reused:
        draft = result.draft = package.source_add(
            frozen, *([frozen_solutions] if frozen_solutions is not None else [])
        )
        result.add_stage(
            "freeze",
            package.froze(draft, solutions.name if solutions is not None else None),
        )
        result.add_stage("spec", f"reused {saved}")
        result.coverage = package.spec_run(draft, saved)
        result.add_stage("coverage", str(result.coverage))
        report = package.validate(draft)
        if report.clean or rounds < 1:
            result.add_stage(
                "validate",
                package.said(report) if report.clean else "; ".join(report.errors),
            )
        else:
            result.add_stage(
                "validate",
                "; ".join(report.errors) + " — writing the set's spec again",
            )
            previous = Previous(
                text=saved.read_text(encoding="utf-8"),
                coverage=result.coverage,
                report=report,
            )
            reused = False

    if not reused:
        backend = backend or choose_backend(settings)
        if (reason := backend.unavailable()) is not None:
            raise ModelUnavailable(reason)
        result.second = _second(source, cache_dir, solutions)
        draft, coverage, report, result.tries, stages = iterate_spec(
            frozen,
            saved,
            backend,
            tries=tries,
            second=result.second,
            previous=previous,
            solutions=frozen_solutions,
            solutions_name=solutions.name if solutions is not None else "",
        )
        result.draft = draft
        result.coverage = coverage
        for name, message in stages:
            result.add_stage(name, message)
        for one in result.tries:
            result.usage.input_tokens += one.usage.input_tokens
            result.usage.output_tokens += one.usage.output_tokens
            result.usage.seconds += one.usage.seconds

    # Layers 3 and 4, a round at a time. Reached only with a spec this run
    # wrote, so the backend is the one that wrote it.
    report = _fix_rounds(draft, report, backend, rounds, result)
    # What the corpus harness reads off the result rather than off the
    # record: set here so that a run that stops for a review carries them
    # too, since that return is above the record this run never writes.
    result.clean = report.clean
    result.reused = reused
    if not report.clean:
        # The first error left is the reason there is no zip. A warning is not
        # one: the build goes past it, so what it says belongs in the reason of
        # a run that built rather than in the reason one stopped.
        result.reason = report.errors[0]
    else:
        # What the build will say and go on past, which the stage line prints
        # and the harness's table keeps.
        result.reason = "; ".join(report.warnings)

    if report.clean and review != "none":
        # The run stops here: the questions the reviewer is to see, a record of
        # them in the cache, and no zip until `resume` is told they are right.
        # Every path in the record is absolute, as out_dir and cache_dir
        # already are: the command that answers it is another process, run from
        # wherever the reviewer happens to be, and a relative one would point
        # at nothing from there.
        waiting = Review(
            mode=review,
            count=sample,
            source=str(source.resolve()),
            spec=str(saved.resolve()),
            out_dir=str(out_dir),
            limit=rounds,
            draft=str(draft.resolve()),
            frozen=str(package.frozen_source(draft).resolve()),
            reused=reused,
            coverage=result.coverage,
            usage=result.usage,
            tries=result.tries,
            second=result.second,
            rounds=result.rounds,
        )
        infos = package.questions(draft)
        rendered, message = _render(draft, out_dir)
        result.add_stage("render", message)
        waiting.questions = [
            Question(
                key=key,
                pdf=str(rendered[key]) if key in rendered else None,
                lines=infos[key].ranges,
            )
            for key in choose(infos, review, sample, rng)
        ]
        waiting.save(cache_dir / RECORD)
        result.review = waiting
        result.add_stage("review", _asked(waiting, cache_dir))
        return result

    if report.clean:
        result.add_stage("review", f"not asked for (mode {review})")
        _build(draft, out_dir, result, report.warnings)

    record_run(
        saved.parent / RECORD_NAME,
        source,
        reused=reused,
        coverage=result.coverage,
        usage=result.usage,
        tries=result.tries,
        second=result.second,
        rounds=result.rounds,
    )
    return result


def resume(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    *,
    verdict: str,
    settings: Settings,
    key: Optional[str] = None,
    note: Optional[str] = None,
    field: Optional[str] = None,
    old: Optional[str] = None,
    new: Optional[str] = None,
    by: str = "reviewer",
    backend: Optional[Backend] = None,
    on_stage: Optional[Callable[[StageResult], None]] = None,
) -> RunResult:
    """Answers the review a run left waiting, and builds once it is answered.

    Args:
        cache_dir: Where the run left its review.
        verdict: `approve`, `reject` or `edit`.
        settings: The environment the run has available.
        key: The question approved or rejected.
        note: What a rejection says, which is what the fixing round is asked.
        field: The field an edit changes, by its key: `q1.text`.
        old: The wording that edit replaces, which is in the field once.
        new: What it puts there instead.
        by: Who the reviewer is, as the draft's log records their edit.
        backend: The backend a rejection's fixing round calls, chosen from the
            settings if absent and asked for only by a rejection.
        on_stage: Called with each stage as the resume adds it, for a caller
            that shows the lines of a rejection's fixing rounds while they run.

    Returns:
        Each stage's line, and the zip where the last approval wrote one —
        which the last approval does not where the checks fault the draft the
        reviewer's own rounds and edits have left.

    Raises:
        ReviewError: no review is waiting, or none of its questions is `key`.
        ModelUnavailable: a rejection has no backend to answer its note with.
        CommandRefused: in2lambda would not make the reviewer's edit.
    """
    cache_dir = Path(cache_dir).resolve()
    waiting = Review.load(cache_dir / RECORD)
    draft = Path(waiting.draft)
    # The result shares the review's usage and rounds rather than copying them,
    # so that what a rejection's rounds cost is in the record that is saved
    # below and in the run record the last approval writes.
    result = RunResult(
        coverage=waiting.coverage,
        usage=waiting.usage,
        tries=waiting.tries,
        second=waiting.second,
        rounds=waiting.rounds,
        review=waiting,
        draft=draft,
        reused=waiting.reused,
        on_stage=on_stage,
    )

    if verdict == "approve":
        waiting.question(key).status = "approved"
        if waiting.done:
            # The design spec decides that build runs only after validate
            # returns clean, and a reviewer's own rounds and edits have had
            # the draft since the run last checked it. So the checks run
            # again here, and a draft they fault is not built: the review
            # stays waiting, and a rejection or an edit is what answers them.
            result.add_stage("review", f"{key} approved, and that is all of them")
            report = package.validate(draft)
            waiting.errors = report.errors
            result.clean = report.clean
            result.add_stage(
                "validate",
                package.said(report) if report.clean else "; ".join(report.errors),
            )
            if report.clean:
                _build(draft, Path(waiting.out_dir), result, report.warnings)
            if result.zip_path is None:
                waiting.save(cache_dir / RECORD)
                result.add_stage("review", _asked(waiting, cache_dir))
                return result
            record_run(
                Path(waiting.spec).parent / RECORD_NAME,
                Path(waiting.source),
                reused=waiting.reused,
                coverage=waiting.coverage,
                usage=waiting.usage,
                tries=waiting.tries,
                second=waiting.second,
                rounds=waiting.rounds,
                review=waiting.to_json(),
            )
            (cache_dir / RECORD).unlink()
            return result
        waiting.save(cache_dir / RECORD)
        result.add_stage("review", f"{key} approved\n{_asked(waiting, cache_dir)}")
        return result

    if verdict == "reject":
        question = waiting.question(key)
        question.status = "rejected"
        question.note = note
        waiting.rejections.append({"key": key, "note": note})
        result.add_stage("review", f"{key} rejected: {note}")
        if waiting.limit < 1:
            # A run made with --rounds 0 has no round to answer the note with,
            # so the question comes back unchanged. Saying so is the whole of
            # what happens here: a backend is asked for only where a round will
            # actually run, so a machine with no key can still record this.
            result.add_stage(
                "fix",
                "no rounds left to answer the note with: the run was "
                f"--rounds {waiting.limit}",
            )
            report = package.validate(draft)
        else:
            backend = backend or choose_backend(settings)
            if (reason := backend.unavailable()) is not None:
                raise ModelUnavailable(reason)
            # The note is a finding of its own: the checks are quiet, and it is
            # what the round is for. Rounds after it answer what they leave.
            report = _fix_rounds(
                draft,
                package.validate(draft),
                backend,
                waiting.limit,
                result,
                instruction=f"The reviewer rejected {key}: {note}",
            )
        relisted = [key]
    else:
        package.command(
            draft, "field replace", {"field": field, "old": old, "new": new}, by=by
        )
        waiting.edits.append({"field": field, "by": by})
        result.add_stage("review", f"{by} edited {field}")
        report = package.validate(draft)
        result.add_stage(
            "validate",
            package.said(report) if report.clean else "; ".join(report.errors),
        )
        edited = field.split(".")[0]
        relisted = [edited] if any(
            one.key == edited for one in waiting.questions
        ) else []

    # What the checks make of the draft the rounds or the edit left, so that
    # the listing says a draft that cannot be built cannot be built, rather
    # than leaving the reviewer to find that out by approving it.
    waiting.errors = report.errors
    result.clean = report.clean
    _relist(waiting, result, relisted)
    waiting.save(cache_dir / RECORD)
    result.add_stage("review", _asked(waiting, cache_dir))
    return result


def _markdown(
    document: Path,
    *,
    cache_dir: Path,
    settings: Settings,
    mathpix: Optional[MathpixClient],
    fresh: bool,
) -> tuple[Path, Optional[Path], str]:
    """The markdown a document is frozen from, and the OCR stage's line for it.

    Args:
        document: The file the user named, or the solutions file beside it.
        cache_dir: Where the OCR of each PDF is kept.
        settings: The environment the run has available.
        mathpix: The client to convert with, built from the settings if absent.
        fresh: Convert a PDF again even if it is already cached.

    Returns:
        The markdown, which is the document itself where it is not a PDF, the
        folder holding the images it refers to where OCR wrote any, and what
        the OCR stage says about it.

    Raises:
        MathpixError: If the PDF cannot be converted.
    """
    if document.suffix.lower() != ".pdf":
        return document, None, f"not needed for {document.name}"
    client = mathpix or MathpixClient.from_settings(settings)
    ocr = ocr_pdf(document, cache_dir=cache_dir, client=client, fresh=fresh)
    # A fresh pass is a restart: every stage below reads the new markdown.
    if ocr.fresh:
        return ocr.markdown, ocr.media, f"fresh pass, restarting from {ocr.markdown}"
    return ocr.markdown, ocr.media, f"cached {ocr.markdown}"


def _copy_beside(
    markdown: Path, media: Optional[Path], *, name: str, into: Path
) -> Path:
    """Copies a second source, and the images it refers to, beside the first.

    The images cannot come across under the name their folder has, because each
    PDF's OCR calls its own folder `media` and two sheets can each hold a
    `plot.png`: the second copy would be the first one gone. So they arrive in a
    folder named after their document, and the references in the copy are
    rewritten to it. in2lambda resolves a reference from the folder the draft is
    in, which is the folder copied into, so any name does.

    Args:
        markdown: The markdown to copy.
        media: The folder its images are in, where the document has any.
        name: What the copies are named after: the solutions document's stem.
        into: The folder the first source's markdown is in.

    Returns:
        The copy, which is what the draft freezes as its second source.
    """
    folder = f"{name}-{MEDIA_NAME}"
    copied = into / f"{name}.md"
    copied.write_text(
        MEDIA_REFERENCE.sub(
            lambda found: f"{found.group(1)}{folder}/",
            markdown.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    if media is not None and media.is_dir():
        shutil.copytree(media, into / folder, dirs_exist_ok=True)
    return copied


def _fix_rounds(
    draft: Path,
    report: package.Report,
    backend: Optional[Backend],
    rounds: int,
    result: RunResult,
    instruction: Optional[str] = None,
) -> package.Report:
    """The fixing rounds: the report and the source to the model, its commands
    to the draft, and the checks again after each one.

    Args:
        draft: The draft file.
        report: What the checks found, which is what the rounds are to answer.
        backend: The backend to call, already known to be available.
        rounds: How many rounds there may be, from here.
        result: The run so far, which each round adds its stage, its cost and
            its record to.
        instruction: A reviewer's note, which the first round answers as well
            as the report — and which is reason for a round by itself, since a
            rejection arrives with the checks already quiet.

    Returns:
        What the checks found after the last round, or what they had found
        already where there was no round to run.
    """
    number = len(result.rounds)
    limit = number + rounds
    while (instruction is not None or not report.clean) and number < limit:
        number += 1
        # The errors are what a round has to answer, and so what counts as
        # having answered nothing. The prompt still carries every finding: a
        # warning about a part nothing answers is worth a round knowing about,
        # in case the sheet does hold the solution somewhere.
        given = {
            (one.check, one.field)
            for one in report.findings
            if one.level == package.ERROR
        }
        reply = fix_round(
            draft, package.source_show(draft), report, backend, instruction
        )
        instruction = None
        result.usage.input_tokens += reply.usage.input_tokens
        result.usage.output_tokens += reply.usage.output_tokens
        result.usage.seconds += reply.usage.seconds
        tokens = reply.usage.input_tokens + reply.usage.output_tokens
        result.add_stage(
            "fix",
            f"round {number}: {summary(reply.calls)}, {tokens} tokens, "
            f"{reply.usage.seconds:.1f}s",
        )

        report = package.validate(draft)
        result.rounds.append(
            RoundResult(number, reply.calls, reply.usage, len(report.errors))
        )
        if report.clean:
            result.add_stage("validate", package.said(report))
        else:
            errors = "; ".join(report.errors)
            # A finding the round answered by writing a field rather than by
            # quoting one — an empty field, a field the source words nowhere.
            # The rounds have no command for it, so another round would be the
            # same refusal, and the run ends naming the field for a person or a
            # later command to quote the right source range into.
            stuck = unrepaired(reply.calls)
            if stuck:
                result.stages.append(
                    StageResult(
                        "validate",
                        "; ".join(_stuck(one, report) for one in stuck)
                        + " — cannot be repaired by the loop, quote the source "
                        f"range into it; left by round {number}, no zip",
                    )
                )
                break
            # Nothing left that the round was not already given: it answered what
            # it could and left the rest, which is what it is told to do with a
            # finding no range of the source answers. Another round would be the
            # same prompt and the same report, so the run ends with them in it.
            if all(
                (one.check, one.field) in given
                for one in report.findings
                if one.level == package.ERROR
            ):
                result.add_stage(
                    "validate", f"{errors} — left by round {number}, no zip"
                )
                break
            if number == limit:
                errors += f" — round limit {rounds} reached, no zip"
            result.add_stage("validate", errors)
    return report


def _build(draft: Path, out_dir: Path, result: RunResult, said: list[str]) -> None:
    """Writes the set out, or says as a stage line why in2lambda would not.

    The checks passed and in2lambda still would not write the set out — an
    image beside the draft that is not there, say. That is part of the run's
    story rather than a fault in it, so it is a stage line like a validate
    one, and the run ends without a zip.

    in2lambda warns about each warning-level finding as it builds. The validate
    stage line lists the same findings, so this function repeats none of them.
    A warning the validate line does not list gets a stage line of its own.

    Args:
        draft: The draft file.
        out_dir: Where the zip goes.
        result: The run so far, which gets the build's stage line and, where
            one was written, the zip — and where one was not, the refusal as
            the reason, since the stage line is printed and gone.
        said: The warnings the validate stage line lists, from the report that
            let the build run.
    """
    try:
        built = package.build(draft, out_dir)
    except package.BuildRefused as error:
        result.add_stage("build", f"refused: {error}")
        result.reason = str(error)
        return
    for message in built.warnings:
        if message not in said:
            result.add_stage("build", f"warning: {message}")
    result.zip_path = built.zip_path
    result.add_stage("build", str(result.zip_path))


def _second(
    source: Path, cache_dir: Path, solutions: Optional[Path] = None
) -> Optional[Second]:
    """Another document of the set, which each candidate spec is also run over.

    The spec is saved for the whole folder, so one that covers the sheet in hand
    and covers no other sheet of the set is not the spec to save.

    Each spec is run over a copy under `cache_dir`, never over the document in
    the folder: running a spec over a sheet freezes it, and freezing writes that
    sheet's draft again from the source, which deletes the fields a fixing round
    or a reviewer wrote there and the log of the commands that wrote them. A tex
    file that inputs files beside it does not find them beside the copy; where
    in2lambda refuses it for that, the spec loop reports it as a document
    in2lambda cannot read and judges the specs on this source.

    A PDF sibling is passed over rather than copied: converting it takes an OCR
    call, and the spec loop makes no call but the model's.

    Args:
        source: The file the user asked to convert, whose folder is the set.
        cache_dir: Where the copy each spec is run over is written.
        solutions: The solutions document of this sheet, which is in this run's
            own draft and so is no other document of the set.

    Returns:
        The first other document of the folder, by name, as the copy to run the
        specs over or as the document the run passed over, and None where the
        folder holds no other document.
    """
    source = Path(source).resolve()
    solutions = None if solutions is None else Path(solutions).resolve()
    for path in sorted(source.parent.glob(f"*{source.suffix}")):
        if path in (source, solutions) or not path.is_file():
            continue
        if not package.is_document(path):
            continue
        if source.suffix.lower() == ".pdf":
            return Second(
                path.name,
                passed_over="converting it takes an OCR call, and the spec "
                "loop makes no call but the model's",
            )
        copy = Path(cache_dir) / SECOND_NAME / path.name
        try:
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, copy)
        except OSError as error:
            # A sheet the OS will not let the run read, or a cache directory it
            # will not let the run write. The other document is evidence about a
            # spec and not the source the run converts, so the run goes on and
            # says what it passed over, as it does for one in2lambda cannot read.
            return Second(path.name, passed_over=f"copying it failed: {error}")
        return Second(path.name, path=copy)
    return None


def _stuck(field: str, report: package.Report) -> str:
    """One field the rounds cannot repair, with what the report says about it."""
    said = [one.message for one in report.findings if one.field == field]
    if not said:
        return f"{field}: field replace was refused as writing the field"
    return f"{field}: {'; '.join(said)}"


def _render(draft: Path, out_dir: Path) -> tuple[dict[str, Path], str]:
    """Renders the draft's questions, or says why there are no pages to show."""
    try:
        rendered = package.render(draft, out_dir / "render")
    except package.RenderUnavailable as unavailable:
        return {}, str(unavailable)
    return rendered, f"{len(rendered)} questions to {out_dir / 'render'}"


def _relist(waiting: Review, result: RunResult, keys: list[str]) -> None:
    """Renders again and puts the named questions back to the reviewer."""
    draft = Path(waiting.draft)
    rendered, message = _render(draft, Path(waiting.out_dir))
    result.add_stage("render", message)
    infos = package.questions(draft)
    for key in keys:
        question = waiting.question(key)
        question.status = "pending"
        question.pdf = str(rendered[key]) if key in rendered else None
        question.lines = infos[key].ranges if key in infos else []


def _asked(waiting: Review, cache_dir: Path) -> str:
    """The questions still to answer, and the commands that answer them."""
    left = [one for one in waiting.questions if one.status != "approved"]
    faulted = (
        "\n  the checks fault the draft, so it cannot be built yet: "
        f"{'; '.join(waiting.errors)}"
        if waiting.errors
        else ""
    )
    return (
        f"mode {waiting.mode}, {len(left)} of {len(waiting.questions)} questions "
        f"waiting:\n{waiting.listing()}{faulted}\n"
        f"  answer with `in2lambda-agent review approve Q --cache {cache_dir}`, "
        '`review reject Q --note "..."` or `review edit FIELD OLD NEW`'
    )
