"""The run: source in, Lambda Feedback zip out.

The stages are the design spec's pipeline. The agent acts at three of them — the
OCR pass, the one model call that writes the set's spec, and the rounds that
answer what the checks found — and in2lambda does the rest: freezing the source,
running the spec over it, checking the draft and writing the zip.

A spec that covers its source is layer 1 and builds with nothing more asked of
it. A draft the checks have something to say about gets the rounds: up to N
model calls, each with in2lambda's draft commands as its tools, writing fields
at layers 3 and 4 until the checks are quiet or the limit runs out, and then the
report and no zip. A round that leaves only what it was given ends the run there
rather than using the limit up: a finding no range of the source answers — a part
whose solution is not on the sheet — is reported, not invented, and the next
round would be the same prompt over the same report. A spec saved from an
earlier sheet gets one rewrite before
any of that, since a spec that covers the set is worth more than a field
repaired in one sheet of it; that rewrite is layer 1, and is not one of the
rounds.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from in2lambda_agent import package
from in2lambda_agent.fix import RoundResult, fix_round, summary
from in2lambda_agent.mathpix import MathpixClient
from in2lambda_agent.model import Backend, ModelUnavailable, Usage, choose_backend
from in2lambda_agent.ocr import ocr_pdf
from in2lambda_agent.settings import Settings
from in2lambda_agent.spec import RECORD_NAME, record_run, spec_path, write_spec

# Where the OCR of each PDF is kept, under the directory the user ran from.
DEFAULT_CACHE_DIR = Path(".in2lambda-agent")

REVIEW_MODES = ("none", "sample", "per-question")


@dataclass
class StageResult:
    """What one stage did, as one line of output."""

    name: str
    message: str


@dataclass
class RunResult:
    """What a run did, in order, what it covered, and the zip it wrote."""

    stages: list[StageResult] = field(default_factory=list)
    zip_path: Optional[Path] = None
    coverage: Optional[package.Coverage] = None
    usage: Usage = field(default_factory=Usage)
    rounds: list[RoundResult] = field(default_factory=list)


def run(
    source: Path,
    *,
    out_dir: Path,
    settings: Settings,
    spec: Optional[Path] = None,
    review: str = "none",
    rounds: int = 3,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    fresh_ocr: bool = False,
    mathpix: Optional[MathpixClient] = None,
    backend: Optional[Backend] = None,
) -> RunResult:
    """Drives in2lambda over one source file.

    Args:
        source: The question file to convert.
        out_dir: Where in2lambda writes the set's JSON folder and zip.
        settings: The environment the run has available.
        spec: The set's spec file, when it is not the one beside the source.
        review: One of REVIEW_MODES.
        rounds: The round limit, N in the design spec: how many model calls may
            answer what the checks found before the run stops without a zip.
        cache_dir: Where the OCR of each PDF is kept.
        fresh_ocr: Convert a PDF again even if it is already cached.
        mathpix: The client to convert with, built from the settings if absent.
        backend: The model backend to write the spec with, chosen from the
            settings if absent and asked for only when a spec must be written.

    Returns:
        Each stage's line, what the spec covered, what the model calls cost,
        what each fixing round did, and the zip where one was written.

    Raises:
        MathpixError: If a PDF cannot be converted, MissingCredentials among
            them when the run has no Mathpix credentials.
        ModelUnavailable: If a spec must be written and no backend can run.
        BadSpec: If what the model answers with is not a spec.
        SpecRejected: If in2lambda will not run the spec.
        SourceError: If in2lambda cannot freeze, check or export the source.
    """
    # A relative --out means the directory the user ran from, whatever in2lambda
    # does with the working directory along the way.
    source = Path(source)
    out_dir = Path(out_dir).resolve()

    # The set is the folder the user's file is in, so this is settled before
    # OCR moves a PDF's markdown off into the cache.
    saved = spec_path(source, spec)

    result = RunResult()

    # The rest of the pipeline reads markdown, so a PDF becomes markdown first.
    if source.suffix.lower() == ".pdf":
        client = mathpix or MathpixClient.from_settings(settings)
        ocr = ocr_pdf(
            source,
            cache_dir=Path(cache_dir).resolve(),
            client=client,
            fresh=fresh_ocr,
        )
        frozen = ocr.markdown
        # A fresh pass is a restart: every stage below reads the new markdown.
        message = (
            f"fresh pass, restarting from {frozen}"
            if ocr.fresh
            else f"cached {frozen}"
        )
    else:
        frozen = source
        message = f"not needed for {source.name}"
    result.stages.append(StageResult("ocr", message))

    # One pass, or two where a saved spec leaves something for the checks to
    # find: the second writes the spec again with the report in the prompt.
    reused = saved.is_file()
    report = package.Report(clean=False, errors=[])
    while True:
        draft_dir = package.source_add(frozen)
        result.stages.append(
            StageResult("freeze", str(draft_dir / package.DRAFT))
        )

        # What the set's spec said before this pass wrote over it, where it
        # said anything: a rewrite in2lambda then refuses puts it back.
        replaced = None
        if reused:
            result.stages.append(StageResult("spec", f"reused {saved}"))
        else:
            backend = backend or choose_backend(settings)
            if (reason := backend.unavailable()) is not None:
                raise ModelUnavailable(reason)
            text, reply = write_spec(
                package.source_show(draft_dir),
                backend,
                report if report.errors else None,
            )
            if saved.is_file():
                replaced = saved.read_text(encoding="utf-8")
            saved.write_text(text, encoding="utf-8")
            result.usage.input_tokens += reply.usage.input_tokens
            result.usage.output_tokens += reply.usage.output_tokens
            result.usage.seconds += reply.usage.seconds
            tokens = reply.usage.input_tokens + reply.usage.output_tokens
            result.stages.append(
                StageResult(
                    "spec",
                    f"wrote {saved} via {reply.backend}, {tokens} tokens, "
                    f"{reply.usage.seconds:.1f}s",
                )
            )

        try:
            result.coverage = package.spec_run(draft_dir, saved)
        except package.SpecRejected:
            # A spec is only kept once in2lambda has run it. One it refuses,
            # left beside the sources, is read by every later run over the set
            # — which then makes no call, and fails in the same place, until
            # someone deletes the file by hand.
            if not reused:
                if replaced is None:
                    saved.unlink()
                else:
                    saved.write_text(replaced, encoding="utf-8")
            raise
        result.stages.append(StageResult("coverage", str(result.coverage)))

        report = package.validate(draft_dir)
        if report.clean:
            result.stages.append(StageResult("validate", "nothing to report"))
            break
        errors = "; ".join(report.errors)
        if reused and rounds >= 1:
            result.stages.append(
                StageResult("validate", f"{errors} — writing the set's spec again")
            )
            reused = False
            continue
        result.stages.append(StageResult("validate", errors))
        break

    # Layers 3 and 4, a round at a time: the report and the source go to the
    # model, whose commands go straight into the draft as it runs them, and the
    # checks say what is left. Reached only with a spec this run wrote, so the
    # backend is the one that wrote it.
    number = 0
    while not report.clean and number < rounds:
        number += 1
        given = {(finding.check, finding.field) for finding in report.findings}
        reply = fix_round(
            draft_dir, package.source_show(draft_dir), report, backend
        )
        result.usage.input_tokens += reply.usage.input_tokens
        result.usage.output_tokens += reply.usage.output_tokens
        result.usage.seconds += reply.usage.seconds
        tokens = reply.usage.input_tokens + reply.usage.output_tokens
        result.stages.append(
            StageResult(
                "fix",
                f"round {number}: {summary(reply.calls)}, {tokens} tokens, "
                f"{reply.usage.seconds:.1f}s",
            )
        )

        report = package.validate(draft_dir)
        result.rounds.append(
            RoundResult(number, reply.calls, reply.usage, len(report.findings))
        )
        if report.clean:
            result.stages.append(StageResult("validate", "nothing to report"))
        else:
            errors = "; ".join(report.errors)
            # Nothing left that the round was not already given: it answered what
            # it could and left the rest, which is what it is told to do with a
            # finding no range of the source answers. Another round would be the
            # same prompt and the same report, so the run ends with them in it.
            if all(
                (finding.check, finding.field) in given
                for finding in report.findings
            ):
                result.stages.append(
                    StageResult(
                        "validate", f"{errors} — left by round {number}, no zip"
                    )
                )
                break
            if number == rounds:
                errors += f" — round limit {rounds} reached, no zip"
            result.stages.append(StageResult("validate", errors))

    if report.clean:
        result.stages.append(
            StageResult(
                "review",
                f"waiting for the model stages (mode {review}, round limit {rounds})",
            )
        )
        result.zip_path = package.build(draft_dir, out_dir)
        result.stages.append(StageResult("build", str(result.zip_path)))

    record_run(
        saved.parent / RECORD_NAME,
        source,
        reused=reused,
        coverage=result.coverage,
        usage=result.usage,
        rounds=result.rounds,
    )
    return result
