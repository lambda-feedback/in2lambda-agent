"""Two routes from a document to a set, and review only where they differ.

Route A, `direct`: one model call reads the markdown and answers with the set in the
export's shape. Route B, `run_filter`: a Lua filter the model wrote for the set's
structure, run by pandoc with no model call. Every field either route returns must be a
quote of the markdown (`not_verbatim`). The two replies are compared field by field
(`disputed`); a disputed field goes to a small second call that may pick one side or a
passage of the source, never its own words (`adjudicate`); what neither settles is a flag
for a person (`reconcile`). A field only one route filled is not a disagreement: the text
of the route that filled it is taken, and no call is made. A minus sign inside or beside a
display maths, which Mathpix reads from a separator line, is flagged too (`stray_minus`).
`to_set` and `build` write the result with in2lambda.

`convert` converts one document. `convert_folder` converts a folder of them: it pairs each
sheet with its solutions document, writes one filter from the first pair, and reports for
each sheet the number of fields agreed, defaulted, adjudicated and flagged.

A reply is a list of questions: {"title", "main_text", "parts": [{"content",
"options", "answer", "worked_solution"}]}. Field keys are 1-based: `q2.p1.content`.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from in2lambda.api.part import Part
from in2lambda.api.question import Question
from in2lambda.api.set import Set

from in2lambda_agent import pair
from in2lambda_agent.model import Backend, Reply, choose_backend
from in2lambda_agent.settings import Settings, load_settings

Reply_ = list[dict[str, Any]]

TEXT_FIELDS = ("content", "answer", "worked_solution")

STRAY_MINUS = "a stray minus sign inside or beside a display maths; Mathpix reads a separator line as one"

NOT_VERBATIM = "not a quote of the source"

_FOLDS = (
    ("\\left(", "("), ("\\right)", ")"), ("\\left[", "["), ("\\right]", "]"),
    ("\\mathrm{~", "\\mathrm{"), ("\\text {", "\\text{"), ("\\space", " "),
    ("\\,", " "), ("\;", " "), ("~", " "), ("&#x20;", " "),
)
_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)[^)]*\)")
_RULE = re.compile(r"^\s*(-{3,}|\*{3,})\s*$", re.M)


def fold(text: str) -> str:
    """Text as compared: notation that renders the same reads the same."""
    text = _RULE.sub("", text or "")
    # An image is compared as "an image here": the export renames every media file.
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)(\{[^}]*\})?", "![img]", text)
    for old, new in _FOLDS:
        text = text.replace(old, new)
    text = re.sub(r"\s*([=+\-*/,.:;()\[\]{}^_])\s*", r"\1", text)
    return " ".join(text.split())


def _squash(text: str) -> str:
    return " ".join((text or "").split())


def fields(reply: Reply_) -> dict[str, str]:
    """Every text field of a reply by key, options included."""
    found: dict[str, str] = {}
    for i, q in enumerate(reply, 1):
        found[f"q{i}.title"] = q.get("title", "")
        found[f"q{i}.main_text"] = q.get("main_text", "")
        for j, p in enumerate(q.get("parts", []), 1):
            for name in TEXT_FIELDS:
                found[f"q{i}.p{j}.{name}"] = p.get(name, "") or ""
            for k, option in enumerate(p.get("options", []) or [], 1):
                found[f"q{i}.p{j}.options[{k}]"] = option
    return found


def normalise(reply: Reply_) -> Reply_:
    """A copy in which every question has a part.

    Route A's prompt gives a question with no sub-questions one part whose content is
    empty. A filter leaves that question's parts out. The two shapes mean the same, so
    both are written as the one empty part; otherwise each such question is a structural
    dispute.
    """
    copied = json.loads(json.dumps(reply))
    for q in copied:
        if not q.get("parts"):
            q["parts"] = [{"content": "", "options": [], "answer": "", "worked_solution": ""}]
    return copied


def merge(questions: Reply_, solutions: Reply_) -> Reply_:
    """Route B's two runs as one reply: the answers of the solutions document by position.

    The filter reads the two documents apart, so the solutions run holds answers and
    worked solutions and nothing else. A question or part the solutions run does not
    return keeps the empty answer and worked solution of the questions run.
    """
    merged, answers = normalise(questions), normalise(solutions)
    for q, s in zip(merged, answers):
        for p, sp in zip(q["parts"], s["parts"]):
            for name in ("answer", "worked_solution"):
                if sp.get(name):
                    p[name] = sp[name]
    return merged


def not_verbatim(reply: Reply_, source: str) -> list[str]:
    """The fields that are not quotes of the source; titles are not quotes.

    A field may be several paragraphs quoted from different places - a question's text
    before and after its parts - so each paragraph is looked for on its own.
    """
    haystack = _squash(source)
    found = []
    for key, text in fields(reply).items():
        if key.endswith(".title"):
            continue
        paragraphs = [_squash(p) for p in re.split(r"\n\s*\n", text or "") if _squash(p)]
        if any(p not in haystack for p in paragraphs):
            found.append(key)
    return found


# A minus sign on a line of its own, after a $$ line or before one, blank lines between.
# Mathpix reads a separator line of the printed page either into the display maths beside
# it or as a paragraph of its own, so both forms are stray.
_LONE_MINUS = re.compile(
    r"\$\$[ \t]*\n(?:[ \t]*\n)*[ \t]*-[ \t]*(?:\n|\Z)"
    r"|(?:\A|\n)[ \t]*-[ \t]*\n(?:[ \t]*\n)*[ \t]*\$\$"
)


def stray_minus(reply: Reply_) -> list[str]:
    """The fields holding a minus sign Mathpix read from a separator line.

    A display maths begins or ends with the minus sign, or the minus sign stands on a
    line of its own beside the block.
    """
    found = []
    for key, text in fields(reply).items():
        blocks = [b.strip() for b in re.findall(r"\$\$(.*?)\$\$", text or "", re.S)]
        if any(b.startswith("-") or b.endswith("-") for b in blocks) or _LONE_MINUS.search(text or ""):
            found.append(key)
    return found


def disputed(a: Reply_, b: Reply_) -> list[str]:
    """Where two replies differ: a question or part one lacks, or a field worded differently."""
    found: list[str] = []
    for i in range(1, max(len(a), len(b)) + 1):
        if i > len(a) or i > len(b):
            found.append(f"q{i}")
            continue
        pa, pb = a[i - 1].get("parts", []), b[i - 1].get("parts", [])
        for j in range(1, max(len(pa), len(pb)) + 1):
            if j > len(pa) or j > len(pb):
                found.append(f"q{i}.p{j}")
    fa, fb = fields(a), fields(b)
    for key in fa:
        if key in fb and fold(fa[key]) != fold(fb[key]) and not any(key.startswith(s + ".") for s in found):
            found.append(key)
    return found


def to_set(reply: Reply_, name: str = "set", directory: Optional[Path] = None) -> Set:
    """The reply as in2lambda's Set. Images named in the texts are attached where they exist."""
    built = Set(_name=name)
    for q in reply:
        question = Question(title=q.get("title", ""), main_text=q.get("main_text", ""))
        for p in q.get("parts", []):
            question.parts.append(
                Part(text=p.get("content", "") or "", worked_solution=p.get("worked_solution", "") or "", answer=p.get("answer", "") or "")
            )
        if directory is not None:
            for text in [question.main_text] + [t for p in question.parts for t in (p.text, p.worked_solution, p.answer)]:
                for ref in _IMAGE.findall(text):
                    path = Path(directory) / ref
                    if path.is_file() and str(path) not in question.images:
                        question.images.append(str(path))
        built.questions.append(question)
    return built


def build(built: Set, out_dir: Path) -> Path:
    """Writes the set's folder and zip under out_dir; returns the zip."""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    built.to_json(str(out_dir))
    # in2lambda names the folder and the zip after the set.
    return Path(out_dir) / f"{built._name}.zip"


# --- route A -------------------------------------------------------------------------

SYSTEM = (
    "You convert a problem sheet, and its solutions where given, from markdown into a JSON "
    "list of questions for a learning platform. Answer with JSON only, no prose, no code fence."
)


def _prompt(markdown: str, solutions: Optional[str]) -> str:
    return (
        "Return a JSON array, one object per question in order: "
        '{"title": str, "main_text": str, "parts": [{"content": str, "options": [str], "answer": str, "worked_solution": str}]}. '
        "title: the question's name without its number, or an empty string where the sheet gives none. "
        "main_text: the statement shared by all parts, copied verbatim from the QUESTIONS markdown, maths and image "
        "references included; leave out timing lines, star ratings and sentences addressed to the student about the "
        "course. parts: one per lettered or numbered sub-question; content is the part's own statement copied verbatim "
        "without its label; a question with no sub-questions has one part whose content is empty. options holds the "
        "choices of a multiple-choice part, verbatim, and those choices are then not in content; an empty list otherwise. "
        "answer is the part's final answer and worked_solution its worked solution, each copied verbatim from the "
        "SOLUTIONS markdown where there is one, or empty strings. Copy; never paraphrase, never invent.\n\n"
        "QUESTIONS markdown:\n\n" + markdown
        + ("\n\nSOLUTIONS markdown:\n\n" + solutions if solutions else "")
    )


class BadReply(ValueError):
    """What the model answered with is not a JSON list."""


def _json(text: str) -> list:
    """The JSON list a call was asked for.

    Raises:
        BadReply: the text is not JSON, or is JSON that is not a list. A model that
            answers with a sentence, and an answer cut short at the output-token
            limit, both arrive here; `fields` and `to_set` read a list, and neither
            reports the text they were given instead.
    """
    stripped = re.sub(r"^```(json)?\s*|\s*```$", "", text.strip())
    try:
        answered = json.loads(stripped)
    except json.JSONDecodeError as error:
        raise BadReply(f"The reply is not JSON: {error}.") from None
    if not isinstance(answered, list):
        raise BadReply(f"A reply is a JSON list, which {_squash(stripped)[:60]!r} is not.")
    return answered


def direct(markdown: str, solutions: Optional[str], backend: Backend) -> tuple[Reply_, Reply]:
    """Route A: one call, the reply as a list of questions, and the call's usage."""
    reply = backend.call(SYSTEM, _prompt(markdown, solutions))
    return _json(reply.text), reply


# --- route B -------------------------------------------------------------------------


def run_filter(lua: Path, document: Path, role: str = "questions") -> Reply_:
    """Route B at run time: pandoc, the filter, and the JSON it wrote. No model.

    The role, `questions` or `solutions`, is passed to the filter as pandoc metadata,
    which is how the filter tells a set's two documents apart.
    """
    out = subprocess.run(
        ["pandoc", str(document), "--lua-filter", str(lua), "-M", f"in2lambda_role={role}",
         "-t", "plain", "--wrap=none"],
        capture_output=True, check=True,
    )
    return json.loads(out.stdout.decode("utf-8").strip())


# --- tiers 2 and 3 ---------------------------------------------------------------------

ADJUDICATE = (
    "Two readings of a problem sheet disagree on some fields. For each field you are given reading A, reading B and "
    "the lines of the source they were taken from. Answer with JSON only: a list of "
    '{"field": str, "choice": "A"|"B"|"text"|"person", "text": str, "reason": str}. '
    "Choose A or B where one is the faithful copy of the source; choose text, with the exact passage of the source, "
    "where neither is; choose person where the two disagree on what belongs in the field. Never write words of your own."
)


def _source_lines(source: str, *texts: str, around: int = 2) -> str:
    lines = source.splitlines()
    hits: set[int] = set()
    for text in texts:
        probe = _squash(text)[:40]
        for n, line in enumerate(lines):
            if probe and probe[:25] in _squash(line):
                hits.update(range(max(0, n - around), min(len(lines), n + around + 1)))
    return "\n".join(lines[n] for n in sorted(hits)) or "(not found)"


def adjudicate(
    a: Reply_, b: Reply_, keys: list[str], source: str, backend: Backend
) -> tuple[dict[str, tuple[str, str]], Optional[Reply]]:
    """Tier 2: one small call over the disputed fields; a choice it may not make becomes 'person'.

    Returns the verdicts and the call's usage, which is None where no field was
    disputed and no call was made.
    """
    fa, fb = fields(a), fields(b)
    keys = [k for k in keys if k in fa and k in fb]
    if not keys:
        return {}, None
    shown = "\n\n".join(
        f"FIELD {k}\nA: {fa[k]}\nB: {fb[k]}\nSOURCE LINES:\n{_source_lines(source, fa[k], fb[k])}" for k in keys
    )
    reply = backend.call(ADJUDICATE, shown)
    verdicts: dict[str, tuple[str, str]] = {k: ("person", "no verdict") for k in keys}
    for v in _json(reply.text):
        k, choice, reason = v.get("field"), v.get("choice"), v.get("reason", "")
        if k not in verdicts:
            continue
        if choice in ("A", "B"):
            verdicts[k] = (choice, reason)
        elif choice == "text" and _squash(v.get("text", "")) and _squash(v["text"]) in _squash(source):
            verdicts[k] = ("text:" + v["text"], reason)
        else:
            verdicts[k] = ("person", reason or "the adjudicator's own words")
    return verdicts, reply


@dataclass
class Flag:
    field: str
    a: str
    b: str
    reason: str


@dataclass
class Reconciled:
    fields: Reply_
    agreed: int
    defaulted: int
    adjudicated: int
    flags: list[Flag] = field(default_factory=list)
    # What the adjudication call read and wrote, and zero where it was not made.
    tokens: int = 0


def _set_field(reply: Reply_, key: str, text: str) -> None:
    m = re.fullmatch(r"q(\d+)(?:\.p(\d+))?\.(\w+)(?:\[(\d+)\])?", key)
    q, p, name, k = m.group(1), m.group(2), m.group(3), m.group(4)
    target = reply[int(q) - 1] if p is None else reply[int(q) - 1]["parts"][int(p) - 1]
    if k is None:
        target[name] = text
    else:
        target[name][int(k) - 1] = text


def reconcile(a: Reply_, b: Reply_, source: str, backend: Optional[Backend] = None) -> Reconciled:
    """Tiers 1 to 3: agreed fields kept, disputes adjudicated, the rest flagged. Starts from A.

    A field only one route filled is not a disagreement about wording: the text of the
    route that filled it is taken, counted as defaulted, and the adjudicator is not asked.
    A field of a question or part the other route did not find at all is defaulted too -
    there is nothing to compare it with - though the structure itself is still flagged.
    """
    a, b = normalise(a), normalise(b)
    merged = json.loads(json.dumps(a))
    keys = disputed(a, b)
    structural = [k for k in keys if re.fullmatch(r"q\d+(\.p\d+)?", k)]
    fa, fb = fields(a), fields(b)
    disagreed = [k for k in keys if k not in structural]
    defaulted = [k for k in fa if any(k.startswith(s + ".") for s in structural)]
    defaulted += [k for k in disagreed if not (fold(fa[k]) and fold(fb[k]))]
    wording = [k for k in disagreed if fold(fa[k]) and fold(fb[k])]
    result = Reconciled(
        fields=merged,
        agreed=len(fa) - len(defaulted) - len(wording),
        defaulted=len(defaulted),
        adjudicated=len(wording),
    )
    for k in defaulted:
        # Where the other route has no such field at all, A's is already in the merge.
        if not fold(fa[k]) and k in fb:
            _set_field(merged, k, fb[k])
    for k in structural:
        result.flags.append(Flag(k, "present" if k in _structure(a) else "absent", "present" if k in _structure(b) else "absent", "one route did not find it"))
    verdicts, usage = adjudicate(a, b, wording, source, backend) if wording and backend is not None else ({}, None)
    if usage is not None:
        result.tokens = usage.usage.input_tokens + usage.usage.output_tokens
    for k in wording:
        choice, reason = verdicts.get(k, ("person", "not adjudicated"))
        if choice == "B":
            _set_field(merged, k, fb[k])
        elif choice.startswith("text:"):
            _set_field(merged, k, choice[5:])
        elif choice == "person":
            result.flags.append(Flag(k, fa[k], fb[k], reason))
    for k in not_verbatim(merged, source):
        if not any(f.field == k for f in result.flags):
            result.flags.append(Flag(k, fields(merged)[k], "", NOT_VERBATIM))
    return result


def _structure(reply: Reply_) -> set[str]:
    return {f"q{i}" for i in range(1, len(reply) + 1)} | {
        f"q{i}.p{j}" for i, q in enumerate(reply, 1) for j in range(1, len(q.get("parts", [])) + 1)
    }


# --- one document end to end -----------------------------------------------------------


@dataclass
class Converted:
    set: Set
    zip_path: Optional[Path]
    flags: list[Flag]
    reply: Reply_
    # Route A's reply, before reconciling. A caller that needs the same answer
    # twice saves this reply and passes it back as `convert`'s `route_a`; a
    # second call to the model returns different wording.
    route_a: Reply_ = field(default_factory=list)
    tokens: int = 0
    # The counts of the reconciliation, zero where route B did not run.
    fields: int = 0
    agreed: int = 0
    defaulted: int = 0
    adjudicated: int = 0
    route_b_error: Optional[str] = None

    def report(self) -> list[str]:
        """One line per flag, a line of the counts, and the zip.

        A flag prints each route's text where both routes filled the field, because the
        reader decides between the two. A field one route filled prints the reason
        alone.
        """
        lines = []
        for one in self.flags:
            lines.append(f"flag      {one.field}: {one.reason}")
            if one.a and one.b:
                lines += [f"  A: {_squash(one.a)}", f"  B: {_squash(one.b)}"]
        lines.append(f"fields    {self.counted()}")
        if self.route_b_error:
            lines.append(f"route B   failed: {self.route_b_error}")
        if self.zip_path:
            lines.append(f"build     {self.zip_path}")
        return lines

    def counted(self) -> str:
        """The `fields` line of the report, which the page shows as its own line too.

        Where no filter was given, or the filter run failed, no field was compared, and
        every field is route A's. The counts of a comparison that did not happen say
        nothing, so the line counts route A's fields instead.
        """
        if self.fields:
            return _counted(
                [self.fields, self.agreed, self.defaulted, self.adjudicated, len(self.flags)]
            )
        ran = "failed" if self.route_b_error else "did not run"
        return f"{len(fields(normalise(self.reply)))} fields, route B {ran}"


_UNDERLINE = Path(__file__).parent / "underline.lua"


def _ocr(document: Path, cache_dir: Path, settings: Settings):
    """The OCR of a PDF, from the cache where it holds one."""
    from in2lambda_agent.mathpix import MathpixClient
    from in2lambda_agent.ocr import ocr_pdf

    return ocr_pdf(document, cache_dir=cache_dir, client=MathpixClient.from_settings(settings))


def pandoc_reads(document: Path, cache_dir: Path, settings: Settings) -> Path:
    """The file pandoc is given for a document: for a PDF, its OCR markdown.

    Pandoc reads no PDF, so both halves of route B - the tree the filter is
    written from, and the run of that filter - read the markdown Mathpix made of
    the pages, which is what route A reads too. Every other document pandoc
    reads itself, and no credential is asked for.
    """
    document = Path(document)
    if document.suffix.lower() == ".pdf":
        return _ocr(document, cache_dir, settings).markdown
    return document


def markdown_of(document: Path, cache_dir: Path, settings: Settings) -> tuple[str, Path]:
    """The document as markdown, and the folder its images are in."""
    document = Path(document)
    if document.suffix.lower() == ".pdf":
        ocr = _ocr(document, cache_dir, settings)
        return ocr.markdown.read_text(encoding="utf-8"), ocr.markdown.parent
    if document.suffix.lower() in (".md", ".markdown"):
        return document.read_text(encoding="utf-8"), document.parent
    # An underlined run of a docx, and \underline{} of a tex file, is written by
    # commonmark_x as [text]{.underline}, which Lambda Feedback does not render. The
    # filter drops the underline and keeps the words. Turning bracketed_spans off instead
    # writes the run as raw HTML, and turning raw_html off with it drops every table
    # commonmark_x cannot write as a pipe table.
    out = subprocess.run(
        ["pandoc", str(document), "-t", "commonmark_x", "--wrap=none", "--lua-filter", str(_UNDERLINE)],
        capture_output=True, check=True,
    )
    return out.stdout.decode("utf-8"), document.parent


def _read_as(document: Path, cache_dir: Path) -> str:
    """How one document's markdown is got, for the ocr stage line.

    Asked before the conversion, because a PDF the cache held no entry for is cached by
    the time the line is written.
    """
    suffix = Path(document).suffix.lower()
    if suffix in (".md", ".markdown"):
        return "read"
    if suffix != ".pdf":
        return "pandoc"
    from in2lambda_agent.ocr import cached

    return "cached" if cached(document, cache_dir) is not None else "mathpix"


def convert(
    document: Path,
    solutions: Optional[Path] = None,
    *,
    out_dir: Path = Path("out"),
    cache_dir: Path = Path(".in2lambda-agent"),
    backend: Optional[Backend] = None,
    settings: Optional[Settings] = None,
    lua: Optional[Path] = None,
    name: str = "set",
    on_stage: Optional[Callable[[str, str], None]] = None,
    route_a: Optional[Reply_] = None,
) -> Converted:
    """Route A, route B where a filter is given, reconcile, verify, write.

    Route B reads the solutions document too, under its own role, and the two runs are
    merged before the comparison. Of a PDF it reads the markdown the OCR made, which is
    what route A reads. Where a filter run fails, the route A reply is the
    result and `route_b_error` holds pandoc's message, so that one sheet of a folder does
    not stop the other eight.

    `route_a`, where it is given, is a reply from an earlier run of this document, kept
    by a caller who needs the same answer twice: route A is not called, and the result's
    `route_a` is what was given.

    `on_stage`, where it is given, is called with a name and a message as each step
    finishes - `ocr`, `route A`, `route B`, `fields`, `build` - so that a caller watching
    a run shows each line as the step ends rather than the report at the end of it.
    """
    settings = settings or load_settings()
    backend = backend or choose_backend(settings)

    def said(stage: str, message: str) -> None:
        if on_stage is not None:
            on_stage(stage, message)

    read = [f"{Path(d).name}: {_read_as(d, cache_dir)}" for d in (document, solutions) if d is not None]
    markdown, images = markdown_of(document, cache_dir, settings)
    solutions_md = markdown_of(solutions, cache_dir, settings)[0] if solutions else None
    said("ocr", "; ".join(read))
    source = markdown + ("\n" + solutions_md if solutions_md else "")
    if route_a is None:
        route_a, usage = direct(markdown, solutions_md, backend)
        tokens = usage.usage.input_tokens + usage.usage.output_tokens
        said("route A", f"{tokens} tokens")
    else:
        tokens = 0
        said("route A", "the reply given, no call made")
    reply = route_a
    counts, error = (0, 0, 0, 0), None
    flags = [Flag(k, fields(reply)[k], "", NOT_VERBATIM) for k in not_verbatim(reply, source)]
    if lua is None:
        said("route B", "did not run: no filter")
    else:
        try:
            # A PDF's filter runs over the markdown its OCR made, which
            # `markdown_of` has by now put in the cache.
            other = run_filter(lua, pandoc_reads(document, cache_dir, settings))
            if solutions is not None:
                other = merge(other, run_filter(lua, pandoc_reads(solutions, cache_dir, settings), role="solutions"))
        except (subprocess.CalledProcessError, json.JSONDecodeError) as problem:
            stderr = getattr(problem, "stderr", None)
            error = (stderr.decode("utf-8", "replace") if stderr else str(problem)).strip()
            said("route B", f"failed: {error}")
        else:
            reconciled = reconcile(reply, other, source, backend)
            reply, flags = reconciled.fields, reconciled.flags
            # The adjudication call is the document's second call, so its tokens
            # are the document's too.
            tokens += reconciled.tokens
            counts = (
                reconciled.agreed + reconciled.defaulted + reconciled.adjudicated,
                reconciled.agreed, reconciled.defaulted, reconciled.adjudicated,
            )
            said("route B", "ran")
    for k in stray_minus(reply):
        if not any(f.field == k for f in flags):
            flags.append(Flag(k, fields(reply)[k], "", STRAY_MINUS))
    result = Converted(
        set=to_set(reply, name=name, directory=images), zip_path=None, flags=flags,
        reply=reply, route_a=route_a, tokens=tokens,
        fields=counts[0], agreed=counts[1], defaulted=counts[2], adjudicated=counts[3],
        route_b_error=error,
    )
    said("fields", result.counted())
    result.zip_path = build(result.set, out_dir)
    said("build", str(result.zip_path))
    return result


# --- route B: writing the filter -------------------------------------------------------

FILTER_SYSTEM = "You write pandoc Lua filters. Answer with the Lua source only, no prose, no code fence."


def structure(document: Path) -> str:
    """Pandoc's tree of a document, abbreviated to one line per block, for the filter-writing call."""
    ast = json.loads(subprocess.check_output(["pandoc", str(document), "-t", "json"]))

    def text(inlines: list) -> str:
        return "".join(i.get("c", "") if i["t"] == "Str" else " " if i["t"] == "Space" else "$" if i["t"] == "Math" else "" for i in inlines)

    def brief(b: dict, depth: int = 0) -> list[str]:
        t, pad = b["t"], "  " * depth
        if t in ("Para", "Plain"):
            return [f"{pad}{t}: {text(b['c'])[:70]}"]
        if t == "Header":
            return [f"{pad}Header({b['c'][0]}): {text(b['c'][2])[:70]}"]
        if t in ("OrderedList", "BulletList"):
            items = b["c"][1] if t == "OrderedList" else b["c"]
            out = [f"{pad}{t} with {len(items)} items"]
            for item in items[:40]:
                out.append(f"{pad}  item:")
                for sub in item:
                    out += brief(sub, depth + 2)
            return out
        if t == "Div":
            return [f"{pad}Div classes={b['c'][0][1]}"] + [l for sub in b["c"][1] for l in brief(sub, depth + 1)]
        return [f"{pad}{t}"]

    return "\n".join(l for b in ast["blocks"] for l in brief(b))


def write_filter(
    document: Path,
    solutions: Optional[Path],
    backend: Backend,
    *,
    cache_dir: Path = Path(".in2lambda-agent"),
    settings: Optional[Settings] = None,
) -> tuple[str, Reply]:
    """Route B's one call: a Lua filter for the structure of this document's set.

    Where a set writes its solutions in a second document, one filter reads both: the
    call is shown the structure of each, and the filter it writes tells them apart by the
    role `run_filter` passes.

    A PDF is shown as the markdown its OCR made (`pandoc_reads`), which is the tree
    `convert` then runs the filter over; `cache_dir` and `settings` are where that OCR is
    kept and the credentials that fetch it.
    """
    settings = settings or load_settings()
    document = pandoc_reads(document, cache_dir, settings)
    solutions = None if solutions is None else pandoc_reads(solutions, cache_dir, settings)
    version = subprocess.check_output(["pandoc", "--version"]).decode().split()[1]
    both = "" if solutions is None else f"""

The set's solutions are in a second document, which the same filter reads. Its block structure is:

{structure(solutions)}

The filter tells the two documents apart by pandoc's metadata: pandoc.utils.stringify(doc.meta.in2lambda_role) is "questions" or "solutions". Under "questions" emit the objects described above, leaving answer and worked_solution empty. Under "solutions" emit one object per question of the sheet, in the same order, each with an empty title and main_text and one object per part of that question in order, whose answer holds that part's final answer and whose worked_solution holds its working, content and options staying empty. The two runs are merged part by part by position, so a question the solutions document does not answer must still have its object in place.
"""
    prompt = f"""A problem sheet is read by pandoc {version}. Its block structure (pandoc's AST, abbreviated) is:

{structure(document)}

Write a Lua filter that replaces the whole document with one CodeBlock holding a JSON array: one object per question, in order,
{{"title": "", "main_text": "...", "parts": [{{"content": "...", "options": [], "answer": "", "worked_solution": ""}}]}}
Rules: a question is a top-level item of the numbered list of questions, or a section where the sheet uses headings; its main_text is the question's own paragraphs; its parts are the items of a numbered list nested inside it, each part's content being that nested item's paragraphs; a question with no nested list has one part with empty content. Render each text with pandoc.write(pandoc.Pandoc(blocks), "commonmark_x", {{wrap_text = "wrap-none"}}), keeping maths and images. Leave title empty unless the sheet names its questions. Ignore headings and figures that belong to no question. Build the JSON string by hand: escape only the double quote, the backslash and ASCII control characters (bytes below 32) - never any other byte, so that UTF-8 text passes through unchanged.
{both}
Return the filter as: function Pandoc(doc) ... return pandoc.Pandoc({{pandoc.CodeBlock(json)}}) end."""
    reply = backend.call(FILTER_SYSTEM, prompt)
    return re.sub(r"^```(lua)?\s*|\s*```$", "", reply.text.strip()), reply


# --- a folder of sheets ------------------------------------------------------------------


@dataclass
class Folder:
    filter: Path
    sheets: list[tuple[str, Converted]]
    tokens: int = 0

    def report(self) -> list[str]:
        """One line per sheet, and a line of the totals."""
        lines, totals = [], [0, 0, 0, 0, 0]
        for name, sheet in self.sheets:
            counts = [sheet.fields, sheet.agreed, sheet.defaulted, sheet.adjudicated, len(sheet.flags)]
            totals = [total + count for total, count in zip(totals, counts)]
            lines.append(f"{name}: {_counted(counts)}" + (f" (route B failed: {sheet.route_b_error})" if sheet.route_b_error else ""))
        return lines + [f"{len(self.sheets)} sheets: {_counted(totals)}"]


def _counted(counts: list[int]) -> str:
    return "{} fields, agreed {}, defaulted {}, adjudicated {}, flagged {}".format(*counts)


def convert_folder(
    folder: Path,
    *,
    out_dir: Path = Path("out"),
    cache_dir: Path = Path(".in2lambda-agent"),
    backend: Optional[Backend] = None,
    settings: Optional[Settings] = None,
) -> Folder:
    """Every sheet of a folder, through both routes, under one filter.

    One model call writes the filter from the first sheet and its solutions document,
    because the sheets of a folder share one structure, and pandoc then runs that filter
    over every sheet with no further call. Each sheet's set is written under a folder
    named after the sheet.

    Raises:
        ValueError: The folder holds no sheet, because the path names no folder or
            because every document in it is a solutions document.
    """
    settings = settings or load_settings()
    backend = backend or choose_backend(settings)
    pairs = pair.pairs_in(folder)
    if not pairs:
        raise ValueError(
            f"{folder} holds no sheet to convert. A folder run converts the files in the "
            f"folder whose suffix is one of {' '.join(pair.DOCUMENTS)} and whose name does "
            "not end in `solutions` or `sol`. Name a single document to convert that "
            "document on its own."
        )
    lua_source, usage = write_filter(
        pairs[0][0], pairs[0][1], backend, cache_dir=cache_dir, settings=settings
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lua = out_dir / "filter.lua"
    lua.write_text(lua_source, encoding="utf-8")
    result = Folder(filter=lua, sheets=[], tokens=usage.usage.input_tokens + usage.usage.output_tokens)
    for sheet, solutions in pairs:
        converted = convert(
            sheet, solutions, out_dir=out_dir / sheet.stem, cache_dir=cache_dir,
            backend=backend, settings=settings, lua=lua, name=sheet.stem,
        )
        result.sheets.append((sheet.stem, converted))
        result.tokens += converted.tokens
    return result
