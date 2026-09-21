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
poetry run in2lambda-agent run SOURCE [--spec FILE] [--review none|sample|per-question] [--rounds N] [--sample N] [--cache DIR] [--fresh-ocr] [--out DIR]
```

`SOURCE` is a PDF, markdown, tex or docx file. Mathpix converts a PDF first, and the
agent keeps the markdown and the images under the PDF's hash in `--cache` (default
`./.in2lambda-agent`), so a second run over the same PDF makes no Mathpix call.
`--fresh-ocr` converts the PDF again and restarts the run from the new markdown.
`--out` defaults to `./out`, where in2lambda writes the set's JSON folder and its zip.

A sheet whose solutions are a document of their own is one run. The solutions file is
the one beside `SOURCE` whose name is the sheet's with `_solutions`, `-solutions` or
` Solutions` after it, in any case, and whose suffix is the same: `Worksheet_1.pdf`
and `Worksheet_1_solutions.pdf`. Both are frozen into the one draft, the questions
first and the solutions second, and the run is named after the questions file whether
you name that file or the solutions one. A solutions file with no questions file
beside it stops the run, which exits 1 saying `solutions without questions`.

The run writes a spec — the YAML selectors naming which blocks of the source are
questions, parts and solutions — in one model call, and saves it as
`in2lambda-spec.yaml` beside `SOURCE`. A folder of sheets is one document set and
shares one spec, so the second sheet in that folder runs with no model call. `--spec`
keeps the set's spec elsewhere; the agent reads that file if it exists and writes it if
it does not. A run appends a line to `in2lambda-agent-runs.jsonl` beside the spec. The
line records the layout, the blocks and the fields of the spec run, the tokens and the
seconds of the model calls, and the commands of each fixing round. A run that stops for
a review appends its line at the last approval.

Each stage prints a line:

```
ocr       fresh pass, restarting from /home/me/sheets/.in2lambda-agent/9f2c…/source.md
freeze    /home/me/sheets/.in2lambda-agent/9f2c…/source.draft.json
spec      wrote /home/me/sheets/in2lambda-spec.yaml via anthropic, 1883 tokens, 6.4s
coverage  PartsSepSol: 14 blocks, 9 fields at layer 1, 4 ignored, b13 unassigned
validate  b13 (lines 21-21) is in no field and not marked ignore.
fix       round 1: 1 command (question solution q2), 2604 tokens, 4.1s
validate  nothing to report
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
the report in the prompt, if `--rounds` is 1 or more: a spec that covers the whole set
repairs every sheet in it. That rewrite is not one of the rounds.

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
poetry run in2lambda-agent corpus ROOT [PATH ...] [--suffix S] [--replay] [--rounds N] [--results FILE] [--work DIR] [--specs DIR] [--cache DIR]
```

`ROOT` is the corpus directory and each `PATH` a folder under it to run, defaulting to
all of it. `--suffix` is repeatable and defaults to `tex`, `md` and `docx`; `--suffix
pdf` runs the PDFs too, which needs Mathpix credentials and one call per PDF. Every run
is review mode `none`.

A sheet and the solutions file beside it are one run and one row, named after the
questions file. A solutions file with no questions file beside it is a `skipped` row
with the reason `solutions without questions`.

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

`--cache` (default `./.in2lambda-agent`) is where the OCR of each PDF is kept. A sweep
pointed at a cache that an earlier run filled makes no Mathpix call, and needs no
Mathpix credentials.

## Gate

Nothing merges without a replay over real documents. `gate` reruns the saved specs
over the folders a baseline file names, and compares what each folder did this run
with what the baseline records:

```sh
poetry run in2lambda-agent gate BASELINE [--record] [--cache DIR] [--work DIR]
```

Every run is `corpus --replay`, so no model call is made. The command prints one line
per folder, and exits 1 when a folder builds fewer documents than the baseline records
or when a single document does worse than the baseline records it doing. The second
check is what a baseline of no builds rests on: a corpus where every document faults
still reports the document that stops being read.

```
work      /tmp/in2lambda-agent-gate-3f1a
UCL_MechEng          built 0  faulted 0  build refused 0  skipped 1  no spec 2   (baseline built 0)
  worse  UCL_MechEng/Worksheet_2.pdf  faulted -> no spec: replay: no model call is allowed
```

Run the command from the repository root. The gate reads `specs`, and a folder's `root`
where `root` is relative, from the directory the command runs in.

The repository holds two baselines, because a clone holds the second corpus and not the
first:

| File | Corpus | Run by |
| --- | --- | --- |
| `gate-baseline.json` | the three folders of `ExampleContents`, which is private | the workbench check |
| `ci-baseline.json` | the three folders of `ci-corpus`, which is committed | `.github/workflows/gate.yml` |

Each folder's `root` and `suffixes` are written by hand. `built`, the count of documents
that built, and `documents`, the outcome of each single document, are what `--record`
writes. A folder the file records no `built` for passes on any count, and its line reads
`(not recorded)`. A change to a recorded count or outcome belongs in a pull request that
says why the count or the outcome changed.

`gate-baseline.json` records 0 built for all three folders. Every document of
`ExampleContents` replays to `faulted`, because pandoc's line wrapping is reported as a
math delimiter error and each document needs a fixing round that a replay does not run.
The recorded outcomes are what the gate defends until a later ticket raises the count.

`--cache` defaults to `~/.cache/in2lambda-agent`, outside any worktree, so that a PDF
converted on one branch is not converted again on the next. `--work` defaults to a new
directory under the system temp directory, which the gate does not delete: read the
drafts of a folder that failed there. The gate also copies the spec tree into the work
directory and replays the copy, because a sweep appends a record of each run beside the
spec it reads. Neither directory is inside the repository, so `git status` after a gate
run reports no new file.

`.github/workflows/gate.yml` runs pytest and then `gate ci-baseline.json` on every push
to `main` and every pull request. Mathpix reads `ci-corpus/pdf/sheet-1.pdf` once and the
workflow stores the markdown in the Actions cache under the PDF's hash. The job needs
two repository secrets, `MATHPIX_APP_ID` and `MATHPIX_API_KEY`.

A pull request from a fork is given neither secret. `actions/cache` restores the cache
of the base branch for a fork, and the cached markdown is what `ci-corpus/pdf` then
replays, so the job passes without the secrets. If the cache is empty — the PDF's bytes
changed, or GitHub evicted the entry — Mathpix cannot be called, `ci-corpus/pdf` builds
0 against a recorded 1, the job fails and the pull request cannot be merged. A
maintainer merges that branch by pushing it to a branch of this repository, where the
secrets are read.

The `gate` job is a required status check on `main`:

```sh
gh api -X PUT repos/{owner}/{repo}/branches/main/protection \
  --input .github/branch-protection.json
```

## Docker

The image carries pandoc, a TeX Live whose xelatex runs the PDF generator's
`template.latex`, and Node:

```sh
docker build -t in2lambda-agent . && docker run --rm -v "$PWD:/work" -w /work in2lambda-agent run sheet.md
```
