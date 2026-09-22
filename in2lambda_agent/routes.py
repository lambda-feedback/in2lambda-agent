"""Two routes from a document to a set, and review only where they differ.

Route A, `direct`: one model call reads the markdown and answers with the set in the
export's shape. Route B, `run_filter`: a Lua filter the model wrote for the set's
structure, run by pandoc with no model call. Every field either route returns must be a
quote of the markdown (`not_verbatim`). The two replies are compared field by field
(`disputed`); a disputed field goes to a small second call that may pick one side or a
passage of the source, never its own words (`adjudicate`); what neither settles is a flag
for a person (`reconcile`). `to_set` and `build` write the result with in2lambda.

A reply is a list of questions: {"title", "main_text", "parts": [{"content",
"options", "answer", "worked_solution"}]}. Field keys are 1-based: `q2.p1.content`.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from in2lambda.api.part import Part
from in2lambda.api.question import Question
from in2lambda.api.set import Set

from in2lambda_agent.model import Backend, Reply, choose_backend
from in2lambda_agent.settings import Settings, load_settings

Reply_ = list[dict[str, Any]]

TEXT_FIELDS = ("content", "answer", "worked_solution")

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


def _json(text: str) -> Any:
    return json.loads(re.sub(r"^```(json)?\s*|\s*```$", "", text.strip()))


def direct(markdown: str, solutions: Optional[str], backend: Backend) -> tuple[Reply_, Reply]:
    """Route A: one call, the reply as a list of questions, and the call's usage."""
    reply = backend.call(SYSTEM, _prompt(markdown, solutions))
    return _json(reply.text), reply


# --- route B -------------------------------------------------------------------------


def run_filter(lua: Path, document: Path) -> Reply_:
    """Route B at run time: pandoc, the filter, and the JSON it wrote. No model."""
    out = subprocess.run(
        ["pandoc", str(document), "--lua-filter", str(lua), "-t", "plain", "--wrap=none"],
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


def adjudicate(a: Reply_, b: Reply_, keys: list[str], source: str, backend: Backend) -> dict[str, tuple[str, str]]:
    """Tier 2: one small call over the disputed fields; a choice it may not make becomes 'person'."""
    fa, fb = fields(a), fields(b)
    keys = [k for k in keys if k in fa and k in fb]
    if not keys:
        return {}
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
    return verdicts


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
    adjudicated: int
    flags: list[Flag] = field(default_factory=list)


def _set_field(reply: Reply_, key: str, text: str) -> None:
    m = re.fullmatch(r"q(\d+)(?:\.p(\d+))?\.(\w+)(?:\[(\d+)\])?", key)
    q, p, name, k = m.group(1), m.group(2), m.group(3), m.group(4)
    target = reply[int(q) - 1] if p is None else reply[int(q) - 1]["parts"][int(p) - 1]
    if k is None:
        target[name] = text
    else:
        target[name][int(k) - 1] = text


def reconcile(a: Reply_, b: Reply_, source: str, backend: Optional[Backend] = None) -> Reconciled:
    """Tiers 1 to 3: agreed fields kept, disputes adjudicated, the rest flagged. Starts from A."""
    merged = json.loads(json.dumps(a))
    keys = disputed(a, b)
    structural = [k for k in keys if re.fullmatch(r"q\d+(\.p\d+)?", k)]
    wording = [k for k in keys if k not in structural]
    fa, fb = fields(a), fields(b)
    result = Reconciled(fields=merged, agreed=len(fa) - len(wording), adjudicated=len(wording))
    for k in structural:
        result.flags.append(Flag(k, "present" if k in _structure(a) else "absent", "present" if k in _structure(b) else "absent", "one route did not find it"))
    verdicts = adjudicate(a, b, wording, source, backend) if wording and backend is not None else {}
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
            result.flags.append(Flag(k, fields(merged)[k], "", "not a quote of the source"))
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
    tokens: int = 0


def markdown_of(document: Path, cache_dir: Path, settings: Settings) -> tuple[str, Path]:
    """The document as markdown, and the folder its images are in."""
    document = Path(document)
    if document.suffix.lower() == ".pdf":
        from in2lambda_agent.mathpix import MathpixClient
        from in2lambda_agent.ocr import ocr_pdf

        ocr = ocr_pdf(document, cache_dir=cache_dir, client=MathpixClient.from_settings(settings))
        return ocr.markdown.read_text(encoding="utf-8"), ocr.markdown.parent
    if document.suffix.lower() in (".md", ".markdown"):
        return document.read_text(encoding="utf-8"), document.parent
    out = subprocess.run(["pandoc", str(document), "-t", "commonmark_x", "--wrap=none"], capture_output=True, check=True)
    return out.stdout.decode("utf-8"), document.parent


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
) -> Converted:
    """Route A, route B where a filter is given, reconcile, verify, write."""
    settings = settings or load_settings()
    backend = backend or choose_backend(settings)
    markdown, images = markdown_of(document, cache_dir, settings)
    solutions_md = markdown_of(solutions, cache_dir, settings)[0] if solutions else None
    source = markdown + ("\n" + solutions_md if solutions_md else "")
    reply, usage = direct(markdown, solutions_md, backend)
    tokens = usage.usage.input_tokens + usage.usage.output_tokens
    if lua is not None:
        other = run_filter(lua, document)
        reconciled = reconcile(reply, other, source, backend)
        reply, flags = reconciled.fields, reconciled.flags
    else:
        flags = [Flag(k, fields(reply)[k], "", "not a quote of the source") for k in not_verbatim(reply, source)]
    built = to_set(reply, name=name, directory=images)
    return Converted(set=built, zip_path=build(built, out_dir), flags=flags, reply=reply, tokens=tokens)


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


def write_filter(document: Path, backend: Backend) -> tuple[str, Reply]:
    """Route B's one call: a Lua filter for the structure of this document's set."""
    version = subprocess.check_output(["pandoc", "--version"]).decode().split()[1]
    prompt = f"""A problem sheet is read by pandoc {version}. Its block structure (pandoc's AST, abbreviated) is:

{structure(document)}

Write a Lua filter that replaces the whole document with one CodeBlock holding a JSON array: one object per question, in order,
{{"title": "", "main_text": "...", "parts": [{{"content": "...", "options": [], "answer": "", "worked_solution": ""}}]}}
Rules: a question is a top-level item of the numbered list of questions, or a section where the sheet uses headings; its main_text is the question's own paragraphs; its parts are the items of a numbered list nested inside it, each part's content being that nested item's paragraphs; a question with no nested list has one part with empty content. Render each text with pandoc.write(pandoc.Pandoc(blocks), "commonmark_x", {{wrap_text = "wrap-none"}}), keeping maths and images. Leave title empty unless the sheet names its questions. Ignore headings and figures that belong to no question. Build the JSON string by hand: escape only the double quote, the backslash and ASCII control characters (bytes below 32) - never any other byte, so that UTF-8 text passes through unchanged. Return the filter as: function Pandoc(doc) ... return pandoc.Pandoc({{pandoc.CodeBlock(json)}}) end."""
    reply = backend.call(FILTER_SYSTEM, prompt)
    return re.sub(r"^```(lua)?\s*|\s*```$", "", reply.text.strip()), reply
