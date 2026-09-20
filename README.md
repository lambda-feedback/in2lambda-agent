# in2lambda-agent

Turns a PDF, docx, tex or md file into a validated Lambda Feedback set.
[in2lambda](https://github.com/lambda-feedback/in2lambda) does every deterministic
step and every write; this agent does OCR, model calls and loop control.

One model call per document set writes a spec of selectors — layer 1 — and in2lambda
runs it over the frozen source. What the checks then fault goes back to the model as a
fixing round, which repairs the draft a field at a time at layers 3 and 4, until the
checks are quiet or the round limit runs out.

## Install

```sh
poetry install --with dev
```

It needs [pandoc](https://pandoc.org/installing.html) on the path to read a document,
and [poppler](https://poppler.freedesktop.org/)'s `pdftoppm` for `compare`, which
renders a PDF's pages.

Copy `.env.example` to `.env` and fill in what you have. `.env` is not committed.
Converting a PDF needs `MATHPIX_APP_ID` and `MATHPIX_API_KEY`. Writing a spec needs a
model backend; a set whose spec is already saved needs neither.

Model calls go to whichever backend the keys choose: `ANTHROPIC_API_KEY` uses the
Anthropic API, `OPENROUTER_API_KEY` uses OpenRouter, and with neither the calls run on
the Claude Code login, which needs [Claude Code](https://claude.com/claude-code)
installed and `claude login` run.

## Run

```sh
poetry run in2lambda-agent run sheet.pdf
```

That is the whole command. In full:

```sh
poetry run in2lambda-agent run SOURCE [--spec FILE] [--review none|sample|per-question] [--rounds N] [--tries N] [--sample N] [--cache DIR] [--fresh-ocr] [--out DIR]
```

`SOURCE` is a PDF, markdown, tex or docx file. A PDF goes to Mathpix first, and its
markdown and images are kept under the PDF's hash in `--cache` (default
`./.in2lambda-agent`), so a second run over the same PDF makes no call. `--fresh-ocr`
converts it again and restarts the run from the new markdown. `--out` defaults to
`./out`, where the set's JSON folder and zip are written.

The run writes a spec — the YAML selectors saying which blocks of the source are
questions, parts and solutions — and saves it as `in2lambda-spec.yaml` beside `SOURCE`.
A folder of sheets is one document set and shares one spec, so the second sheet in that
folder runs with no model call. `--spec` keeps the set's spec somewhere else, and is
read if it is there and written if it is not.

Every sheet of the set reuses that spec, so the run writes it in up to `--tries` calls,
three by default, and keeps the best of them. Each call after the first is shown the
spec before it, what running it covered, the errors the checks found, and the blocks it
left in no field in another document of the folder; the run keeps the spec that left the
fewest blocks unassigned and the fewest errors behind, and stops early at one that left
none. Each run appends a line to `in2lambda-agent-runs.jsonl` beside the spec, saying
what the spec covered, what the calls cost, what each spec written came to under
`iterations`, and what each fixing round did.

Each stage prints a line:

```
ocr       fresh pass, restarting from /home/me/sheets/.in2lambda-agent/9f2c…/source.md
freeze    /home/me/sheets/.in2lambda-agent/9f2c…/source.draft.json
spec      wrote /home/me/sheets/in2lambda-spec.yaml via anthropic, 1883 tokens, 6.4s (try 1 of 3)
coverage  PartsSepSol: 14 blocks, 8 fields at layer 1, 4 ignored, b12, b13 unassigned
validate  b12 (lines 19-19) is in no field and not marked ignore.; b13 (lines 21-21) is in no field and not marked ignore.
set       sheet-2.md: PartsSepSol: 11 blocks, 7 fields at layer 1, 3 ignored, b9 unassigned
freeze    /home/me/sheets/.in2lambda-agent/9f2c…/source.draft.json
spec      wrote /home/me/sheets/in2lambda-spec.yaml via anthropic, 2410 tokens, 7.1s (try 2 of 3)
coverage  PartsSepSol: 14 blocks, 10 fields at layer 1, 4 ignored, none unassigned
validate  nothing to report
set       sheet-2.md: PartsSepSol: 11 blocks, 8 fields at layer 1, 3 ignored, none unassigned
spec      kept try 2 of 3
review    not asked for (mode none)
build     /home/me/sheets/out/set.zip
```

What the checks find — a block of the source in no field, two fields from the same
lines, a gap in the numbering, a part nothing answers — goes back to the model as a
fixing round, with in2lambda's draft commands as its tools: `mark ignore`,
`question add`, `part add`, `question solution`, `split block`, and `field replace` for
wording that no range of the source gives. A field is written by naming where its text
is in the source rather than by typing it out. in2lambda records each command in the
draft's log with the layer of the field it wrote, so what a model did to a draft can be
read off it afterwards, and replayed without the model.

`--rounds` is how many such rounds there may be, three by default. The run validates
again after each one, and stops with the report and no zip, exiting 1, when they run
out. A round that leaves only findings it was already given ends the run there rather
than using the rest of the limit up, with those findings in the report: a part whose
solution is not on the sheet is reported, never answered by typing one out. Text typed
with a literal is capped at 80 characters, which is the length of a repair — a dropped
brace — and refused above it, since what the source does not hold is not written at
all. A saved spec that the checks fault is written again before any of that, with what
that spec covered in the prompt, if `--rounds` is 1 or more, since a spec that covers the
whole set is worth more than a field repaired in one sheet of it; that rewrite takes
`--tries` calls like any other spec, and is not itself one of the rounds.

### Review

`--review` decides how much of a set someone sees before it is built. `none`, the
default, builds as soon as the checks are quiet. `sample` shows a few questions —
`--sample N`, three by default, the ones a fixing round or an edit touched first —
and `per-question` shows every one of them. Either way the run stops with no zip and
prints each question with the lines of the frozen source it was built from:

```
render    in2lambda render is not there yet, so the review names each question by the lines of the source it was built from instead
review    mode sample, 2 of 2 questions waiting:
  q1 pending: not rendered, /home/me/sheets/sheet.md lines 5-5, 7-7
  q2 pending: not rendered, /home/me/sheets/sheet.md lines 13-13
  answer with `in2lambda-agent review approve Q --cache /home/me/.in2lambda-agent`, …
```

Those two lines are what a run prints today: in2lambda has no `render` command yet,
so nothing writes the pages and each question says `not rendered`. Once it does, the
same run writes `q1.pdf` and the rest under `--out`'s `render` folder, and the two
lines name them instead — `render    2 questions to /home/me/out/render`, and
`q1 pending: /home/me/out/render/q1.pdf, /home/me/sheets/sheet.md lines 5-5, 7-7`.

The reviewer answers from the command line, in as many commands as they like:

```sh
poetry run in2lambda-agent review approve q1 [--cache DIR]
poetry run in2lambda-agent review reject q2 --note "the solution answers (a), not (b)" [--cache DIR]
poetry run in2lambda-agent review edit q1.text "m/s" "m/s^2" [--by NAME] [--cache DIR]
```

A rejection's note goes back to the model as a fixing round of its own, the checks
run again, and the question is put back to the reviewer. An edit is `field replace`
with the reviewer as the log's author, which in2lambda marks as edited. Once every
question shown has been approved the checks run once more — a review's own rounds and
edits have had the draft since they last did — and if they are quiet the zip is
written and the run's line is appended, with the mode, the verdicts and the notes in
it. If they are not, nothing is built: the last line says what they found, and a
rejection or an edit answers it. Until then the review is waiting in `review.json`
under `--cache`, which is what each of those commands reads.

### Checking the OCR against the page

A misread symbol that still renders passes every check in the pipeline, so a scanned
PDF has one more command:

```sh
poetry run in2lambda-agent compare sheet.pdf [--cache DIR] [--fresh-ocr]
```

It converts the PDF or reuses the cached conversion, renders the pages with poppler's
`pdftoppm`, and sends them to the model with the markdown in one call, printing a line
per difference:

```
ocr       cached /home/me/sheets/.in2lambda-agent/1ed8…/source.md
p1: P(Z<\frac{0-0.120}{0.583}) → .0583 in the denominator  digit dropped
1 findings over 2 pages, 16092 tokens, 36.8s
```

It only reports: nothing in the pipeline reads what it says, and a finding does not
make it exit 1. [docs/ocr-comparison.md](docs/ocr-comparison.md) is what it found over
three corpus documents and what that is worth.

## Corpus

The design spec's test plan is the agent run over a corpus of real documents, with a
line of a table recorded for each one:

```sh
poetry run in2lambda-agent corpus ExampleContents --suffix tex --suffix md
```

In full:

```sh
poetry run in2lambda-agent corpus ROOT [PATH ...] [--suffix S] [--replay] [--rounds N] [--tries N] [--results FILE] [--work DIR] [--specs DIR]
```

`ROOT` is the corpus directory and each `PATH` a folder under it to run, defaulting to
all of it. `--suffix` is repeatable and defaults to `tex`, `md` and `docx`; `--suffix
pdf` runs the PDFs too, which needs Mathpix credentials and a call each. Every run is
review mode `none`, and exits 0 only if every document built.

The corpus is never written to. Each set's folder is copied into `--work` (default
`./.in2lambda-agent/corpus`), wiped first, and run there, and the sets' specs are kept
in `--specs` (default `./corpus-specs`) in a tree mirroring the corpus: set `A/B`
keeps its spec at `corpus-specs/A/B/in2lambda-spec.yaml`. So the copies are throwaway
and the specs are what is worth keeping — `--replay` reruns them and nothing else,
making no model call at all, which is how a document set becomes a deterministic test.

`--results` (default `./results.csv`) is one row per document, sorted by path, with
the columns:

```
source, set, outcome, reason, spec, layout, blocks, fields, layer1..layer4, edited,
unassigned, rounds, input_tokens, output_tokens, model_seconds, wall_seconds,
review, rejections
```

`outcome` is `built`, `build refused` for a draft the checks passed and in2lambda
still would not export — an image it refers to is not beside it — `faulted` for a
draft the checks never came clean on and no zip, `skipped` for a file that is not a
document — a `.tex` with no `\begin{document}`, such as a figure's TikZ source, which
comes along with the set that inputs it rather than being run as one — `no spec` for a
replay with nothing saved to replay, `no model`, `spec rejected`, `bad spec`, or
`error: <exception>` —
one document that fails is a row and not the end of the sweep, and a set the copy
cannot be made of is a row for each of its documents rather than the end of it.
`reason` is what the run had to say for itself, in the words of whatever said it:
the export's refusal, the first error the checks were still finding, or what the
exception said. On a `built` row it holds the warnings the build proceeded past —
a part whose solution is not on the sheet — and is empty where there were none, so
the table says on its own why each document is where it is. `spec` is `wrote`, `reused` or `rewritten`, which is the spec reuse within
a set. `fields` is the finished draft's, and `layer1` to `layer4` are how many of them
each layer wrote, `edited` how many no longer say what the lines they quote say.
`blocks` and `unassigned` are the spec run's own, before any fixing round, so they say
how far the spec got alone — which is why a `built` row can still report blocks
unassigned. `rejections` is always 0 while `--review`
acts on nothing, and is the column a later review mode fills.

## Docker

The image carries pandoc, a TeX Live with xelatex able to run the PDF generator's
`template.latex`, and Node:

```sh
docker build -t in2lambda-agent . && docker run --rm -v "$PWD:/work" -w /work in2lambda-agent run sheet.md
```
