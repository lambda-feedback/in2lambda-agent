# in2lambda-agent

Turns a PDF, docx, tex or md file into a validated Lambda Feedback set.
[in2lambda](https://github.com/lambda-feedback/in2lambda) performs every deterministic
step and every write; this agent performs the OCR, the model calls and the loop.

One model call per document set writes a spec of selectors, which is layer 1, and
in2lambda runs that spec over the frozen source. A draft the checks fault returns to
the model as a fixing round, which repairs the draft one field at a time at layers 3
and 4, until the checks report no error or the round limit is reached.

[docs/how-it-works.md](docs/how-it-works.md) describes a run stage by stage: the line
each stage prints, the file each stage writes, and what each of the three model calls
is given and may write.

## Install

```sh
poetry install --with dev
```

The `ui` command needs Starlette and uvicorn as well, which are the `ui` extra:
`poetry install --with dev --extras ui`. Every other command runs without them.

The agent reads a document through [pandoc](https://pandoc.org/installing.html), which
must be on the path. `compare` renders a PDF's pages through
[poppler](https://poppler.freedesktop.org/)'s `pdftoppm`.

### Settings

Copy `.env.example` to `.env` and fill in the variables you hold. Git ignores `.env`.
The agent reads the environment, and loads a `.env` found from the working directory
upwards; a variable set in the environment wins over the same variable in `.env`. An
empty variable counts as unset.

| Variable | Read by |
| --- | --- |
| `MATHPIX_APP_ID` | converting a PDF source |
| `MATHPIX_API_KEY` | converting a PDF source |
| `ANTHROPIC_API_KEY` | the model calls, through the Anthropic API |
| `OPENROUTER_API_KEY` | the model calls, through OpenRouter |

The keys choose the backend. `ANTHROPIC_API_KEY` selects the Anthropic API,
`OPENROUTER_API_KEY` selects OpenRouter, and with neither variable set the calls run on
the Claude Code login, which needs [Claude Code](https://claude.com/claude-code)
installed and `claude login` run. A run over a set whose spec is saved makes no model
call, and needs no key at all. A stage that needs a variable names that variable and
the run exits 1.

## Run

```sh
poetry run in2lambda-agent run sheet.pdf
```

In full:

```sh
poetry run in2lambda-agent run SOURCE [--spec FILE] [--review none|sample|per-question] [--rounds N] [--tries N] [--sample N] [--cache DIR] [--fresh-ocr] [--out DIR]
```

`SOURCE` is a PDF, markdown, tex or docx file. Mathpix converts a PDF first, and the
agent keeps the markdown and the images under the PDF's hash in `--cache` (default
`./.in2lambda-agent`), so a second run over the same PDF makes no Mathpix call. The
cache also holds the copy of the set's other sheet each spec is run over.
`--fresh-ocr` converts the PDF again and restarts the run from the new markdown.
`--out` defaults to `./out`, where in2lambda writes the set's JSON folder and its zip.

The run writes a spec — the YAML selectors naming which blocks of the source are
questions, parts and solutions — and saves it as `in2lambda-spec.yaml` beside `SOURCE`.
A folder of sheets is one document set and shares one spec, so the second sheet in that
folder runs with no model call. `--spec` keeps the set's spec elsewhere; the agent reads
that file if it exists and writes it if it does not.

Every sheet of the set reuses that spec, so the run writes it in up to `--tries` model
calls, three by default, and keeps the best of them. Each call after the first reads the
spec before it, what running that spec covered, the errors the checks found, and the
blocks that spec left in no field in another document of the folder. The run keeps the
spec that left the fewest blocks unassigned and the fewest errors, and makes no further
call once a spec leaves neither.

Each spec is run over a copy of that other document, kept in `--cache`, so the run
writes nothing beside the set's own sheets: the draft an earlier run left beside a
sheet, and the fields and the commands in it, stay as that run wrote them. A PDF beside
a PDF source is passed over, because converting it takes a Mathpix call. The record's
`second` names the document the specs were run over, or names the one the run passed
over and says why, and a `set` line says the same.

A run appends a line to `in2lambda-agent-runs.jsonl` beside the spec. The line records
the layout, the blocks and the fields of the spec run, the tokens and the seconds of the
model calls, one `iterations` entry per spec the run wrote, and the commands of each
fixing round. A run that stops for a review appends its line at the last approval.

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

| Stage | What the line reports |
| --- | --- |
| `ocr` | the markdown of a PDF source, and whether Mathpix converted it |
| `freeze` | the draft in2lambda wrote from the source |
| `spec` | the spec file, with the backend and the tokens where the model wrote it |
| `coverage` | the layout, the blocks, the fields per layer, and the blocks in no field |
| `validate` | what the checks found, or `nothing to report` |
| `fix` | the round's number, the commands the model ran, and the tokens |
| `render` | the question PDFs a reviewer reads, or why there are none |
| `review` | the questions waiting for a verdict, or the mode that asked for none |
| `build` | the zip, or in2lambda's refusal to write one |

[docs/how-it-works.md](docs/how-it-works.md#the-stages) lists every message each stage
prints, and the in2lambda function each stage calls.

What the checks find — a block of the source in no field, two fields from the same
lines, a gap in the numbering, a part nothing answers — returns to the model as a
fixing round, with in2lambda's draft commands as its tools: `mark ignore`,
`question add`, `part add`, `question solution`, `split block`, and `field replace` for
wording that no range of the source gives. The model writes a field by naming where its
text is in the source rather than by typing the text out. in2lambda records each
command in the draft's log with the layer of the field it wrote, so a reader can read
off the draft what the model did to it, and replay it without the model.

`--rounds` is how many such rounds the run may make, three by default. The run
validates again after each round, and stops with the report and no zip, exiting 1, when
the rounds run out. A round that leaves only findings it was already given ends the run
there with those findings in the report: the agent reports a part whose solution is not
on the sheet and never writes one. `literal` types at most 80 characters, the length of
a repair such as a dropped brace, and the round refuses a longer one. `field replace`
types at most the same 80 characters, and the round refuses one that would replace the
whole of a field: it repairs wording inside a field and does not write a field. A round
that tries to write one ends the run, and the last line names the field and what the
checks say about it, for a person or a later command to quote the source range into.
Where the run
reused a saved spec and the checks fault the draft, the run writes the spec again with
what that spec covered in the prompt, if `--rounds` is 1 or more: a spec that covers the
whole set repairs every sheet in it. That rewrite takes `--tries` calls like any other
spec, and is not one of the rounds.

### Exit codes

`run` and `review` exit 0 where they wrote a zip, and where a review is still waiting
for a verdict. They exit 1 where the checks still fault the draft, where in2lambda
refused the build, and where a review has no question left to answer and no zip was
written. A missing credential, an unavailable backend, a reply that is not a spec, a
spec in2lambda refuses, a review command naming a question that is not under review,
and a draft command in2lambda refuses each print `in2lambda-agent: MESSAGE` on stderr
and exit 1.

`corpus` exits 0 where it ran at least one document and every row is `built` or
`skipped`. Any other outcome on any row exits 1.

`compare` exits 1 where Mathpix, the model or `pdftoppm` failed, and 0 otherwise. A
difference it reports does not change the exit code.

### Review

`--review` decides how much of a set someone reads before it is built. `none`, the
default, builds as soon as the checks report no error. `sample` shows a few questions —
`--sample N`, three by default, the ones a fixing round or an edit wrote first — and
`per-question` shows every question. In either mode the run writes no zip and prints
each question with the lines of the frozen source its fields were copied from:

```
render    in2lambda render is not there yet, so the review names each question by the lines of the source it was built from instead
review    mode sample, 2 of 2 questions waiting:
  q1 pending: not rendered, /home/me/sheets/sheet.md lines 5-5, 7-7
  q2 pending: not rendered, /home/me/sheets/sheet.md lines 13-13
  answer with `in2lambda-agent review approve Q --cache /home/me/.in2lambda-agent`, …
```

Those two lines are what a run prints today. in2lambda writes a PDF per question from
`in2lambda.draft.export.render`, and the agent does not call it yet (ticket t25), so
nothing writes the pages and each question reads `not rendered`. Once the agent calls
it, the same run writes `q1.pdf` and the rest under `--out`'s `render` folder, and the
two lines name those files instead — `render    2 questions to /home/me/out/render`,
and `q1 pending: /home/me/out/render/q1.pdf, /home/me/sheets/sheet.md lines 5-5, 7-7`.

The reviewer answers from the command line, in as many commands as they like:

```sh
poetry run in2lambda-agent review approve q1 [--cache DIR]
poetry run in2lambda-agent review reject q2 --note "the solution answers (a), not (b)" [--cache DIR]
poetry run in2lambda-agent review edit q1.text "m/s" "m/s^2" [--by NAME] [--cache DIR]
```

A rejection's note returns to the model as a fixing round of its own, the checks run
again, and the question returns to the reviewer. An edit runs `field replace` with the
reviewer as the log's author, and in2lambda marks the field as edited. Once the
reviewer has approved every question shown, the checks run once more — the review's own
rounds and edits have changed the draft since the checks last ran — and where they
report no error the agent writes the zip and appends the run's line, with the mode, the
verdicts and the notes in it. Where they report an error, the agent builds nothing: the
last line names what the checks found, and a rejection or an edit answers it. Until
then the review waits in `review.json` under `--cache`, which each of those commands
reads.

### Trying it in the browser

```sh
poetry run in2lambda-agent ui [--corpus DIR] [--port N] [--no-open]
```

This serves one page on `http://127.0.0.1:8765/` and opens it; `--no-open` prints the
address and opens nothing. The page lists `--corpus` — `./ExampleContents` by default,
or the current directory where there is no such folder — one directory at a time:
click a folder to list it, and a document to pick it. A source elsewhere goes into the
box by hand. Set the options the `run` command takes, and press Go. Each stage line
arrives on the page as the stage finishes, with the tokens
and seconds of each model call. A run in review mode stops with its questions, each
beside its rendered PDF, and approve, reject and edit answer them without leaving the
page; the stages of a rejection's fixing rounds arrive the same way. When the run
ends, the page links to the zip, the rendered PDFs, the draft, the spec and the run
record.

It is a harness for trying the agent by hand. It listens on this machine only, has no
authentication, and runs one run at a time.

### Checking the OCR against the page

A misread symbol that still renders passes every check in the pipeline, so a scanned
PDF has one more command:

```sh
poetry run in2lambda-agent compare sheet.pdf [--cache DIR] [--fresh-ocr]
```

It converts the PDF or reuses the cached conversion, renders the pages with poppler's
`pdftoppm`, and sends the pages to the model with the markdown in one call. It prints
one line per difference:

```
ocr       cached /home/me/sheets/.in2lambda-agent/1ed8…/source.md
p1: P(Z<\frac{0-0.120}{0.583}) → .0583 in the denominator  digit dropped
1 findings over 2 pages, 16092 tokens, 36.8s
```

`compare` writes no draft, no spec and no set, and no stage of the pipeline reads its
findings. `compare` does write the OCR cache entry `run` writes: a PDF the cache holds
no entry for is converted and cached, and `--fresh-ocr` deletes the entry a previous
run cached and converts the PDF again. [docs/ocr-comparison.md](docs/ocr-comparison.md)
lists the differences `compare` found over three corpus documents and judges each one.

## Corpus

The design spec's test plan is the agent run over a corpus of real documents, with one
row of a table recorded for each document:

```sh
poetry run in2lambda-agent corpus ExampleContents --suffix tex --suffix md
```

In full:

```sh
poetry run in2lambda-agent corpus ROOT [PATH ...] [--suffix S] [--replay] [--rounds N] [--tries N] [--results FILE] [--work DIR] [--specs DIR]
```

`ROOT` is the corpus directory and each `PATH` a folder under it to run, defaulting to
all of it. `--suffix` is repeatable and defaults to `tex`, `md` and `docx`; `--suffix
pdf` runs the PDFs too, which needs Mathpix credentials and one call per PDF. Every run
is review mode `none`.

The sweep never writes to the corpus. It copies each set's folder into `--work`
(default `./.in2lambda-agent/corpus`), empties that copy first, and runs the documents
there. It keeps the sets' specs in `--specs` (default `./corpus-specs`) in a tree
mirroring the corpus: set `A/B` keeps its spec at
`corpus-specs/A/B/in2lambda-spec.yaml`. So the copies are throwaway and the specs are
worth keeping — `--replay` reruns the saved specs and nothing else, making no model
call, which turns a document set into a deterministic test.

`--results` (default `./results.csv`) holds one row per document, sorted by path, with
these columns:

```
source, set, outcome, reason, spec, layout, blocks, fields, layer1..layer4, edited,
unassigned, rounds, input_tokens, output_tokens, model_seconds, wall_seconds,
review, rejections
```

[docs/how-it-works.md](docs/how-it-works.md#the-corpus-table) names each column and
where its value comes from. One document that fails is one row and not the end of the
sweep, and a set whose folder cannot be copied is a row for each of its documents.

## Docker

The image carries pandoc, a TeX Live whose xelatex runs the PDF generator's
`template.latex`, and Node:

```sh
docker build -t in2lambda-agent . && docker run --rm -v "$PWD:/work" -w /work in2lambda-agent run sheet.md
```
