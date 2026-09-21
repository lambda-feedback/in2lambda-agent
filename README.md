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

A sheet whose solutions are a document of their own is one run. The solutions file is
the one beside `SOURCE` whose name is the sheet's with `_solutions`, `-solutions` or
` Solutions` after it, in any case, and whose suffix is the same: `Worksheet_1.pdf`
and `Worksheet_1_solutions.pdf`. Both are frozen into the one draft, the questions
first and the solutions second, and the run is named after the questions file whether
you name that file or the solutions one. A solutions file with no questions file
beside it is converted on its own, and a `pair` line names the questions file the
agent looked for. The marker above each group of solutions is that run's question.

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
sheet, and the fields and the commands in it, stay as that run wrote them. The sheet's
own solutions file is not another document, since it is in this run's draft already. A
PDF beside a PDF source is passed over, because converting it takes a Mathpix call. The
record's `second` names the document the specs were run over, or names the one the run
passed over and says why, and a `set` line says the same.

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
| `set` | what the spec covered of the set's other document, or why none was run over |
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
`question add`, `part add`, `question solution`, `part solution`, `split block`,
`field set` for a field that is empty or took the wrong lines, and `field replace` for
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
each question with the PDF it was rendered to and the lines of the frozen source its
fields were copied from:

```
render    2 questions to /home/me/out/render
review    mode sample, 2 of 2 questions waiting:
  q1 pending: /home/me/out/render/question_000_Question_1.pdf, /home/me/sheets/sheet.md lines 5-5, 7-7
  q2 pending: /home/me/out/render/question_001_Question_2.pdf, /home/me/sheets/sheet.md lines 13-13
  answer with `in2lambda-agent review approve Q --cache /home/me/.in2lambda-agent`, …
```

Rendering the questions needs pandoc and xelatex. Without them the `render` line
carries in2lambda's own message, each question reads `not rendered`, and the review
goes on with the lines of the frozen source.

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
poetry run in2lambda-agent corpus ROOT [PATH ...] [--suffix S] [--replay] [--rounds N] [--tries N] [--results FILE] [--work DIR] [--specs DIR] [--cache DIR]
```

`ROOT` is the corpus directory and each `PATH` a folder under it to run, defaulting to
all of it. `--suffix` is repeatable and defaults to `tex`, `md` and `docx`; `--suffix
pdf` runs the PDFs too, which needs Mathpix credentials and one call per PDF. Every run
is review mode `none`.

A sheet and the solutions file beside it are one run and one row, named after the
questions file. A solutions file with no questions file beside it is converted on its
own, and is a row like any other document.

The sweep never writes to the corpus. It copies each set's folder into `--work`
(default `./.in2lambda-agent/corpus`), empties that copy first, and runs the documents
there. The copy holds everything under the folder — the figures a sheet names among it —
less what an earlier run left there: a spec, a draft, an `.in2lambda-agent` directory.
It keeps three kinds of file in `--specs` (default `./corpus-specs`), in a tree
mirroring the corpus: the set's spec, each document's log of the commands its fixing
rounds ran, and the `in2lambda-agent-runs.jsonl` every run appends a line to. Set `A/B`
keeps its spec at `corpus-specs/A/B/in2lambda-spec.yaml` and the log of `A/B/sheet.tex`
at `corpus-specs/A/B/sheet.tex.commands.json`. A log entry holds the block ids, field
keys and line ranges its command named, and the wording a `field replace` or a typed
field spells out. It does not hold the draft's fields, which hold every field's
captured text.

So the copies are throwaway and the specs and the logs are worth keeping. `--replay`
runs the set's spec, then the document's log, and nothing from the model, which turns
a document set into a deterministic test: a document a fixing round repaired replays
to the set the sweep built.

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

`--cache` is where the OCR of each PDF is kept. It defaults to `./.in2lambda-agent`,
the directory `run` caches into, so a sweep over PDFs that `run` has already converted
makes no Mathpix call and needs no Mathpix credentials.

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
tex                  built 2  faulted 0  build refused 0  skipped 0  no spec 2   (baseline built 4)
  worse  tex/sheet-3.tex  built -> no spec: replay: no model call is allowed
```

The gate reads `specs`, and a folder's `root` where `root` is relative, from the
directory `BASELINE` is in, so the command gives the same run from any directory.

There are two corpora and a baseline for each:

| Baseline | Corpus | Run by |
| --- | --- | --- |
| `corpus-specs/gate-baseline.json` | the three folders of `ExampleContents` | the workbench check |
| `ci-baseline.json` | the three folders of `ci-corpus` | `.github/workflows/gate.yml` |

The repository holds `ci-baseline.json` and the specs it names, under
`ci-corpus/specs/`, because `ci-corpus` is synthetic. The repository holds neither the
specs for `ExampleContents` nor the baseline that names them: the specs quote the
headings of private documents, and the baseline records those documents' file names and
the path of the corpus on one machine. `.gitignore` lists `corpus-specs/`, and
`gate-baseline.json` sits in that directory beside the specs it reads, with `"specs":
"."`.

`ci-corpus` is synthetic, so the repository holds its documents — every one but the
PDF, which xelatex compiles from `ci-corpus/tex/sheet-1.tex`. Run the command the
workflow runs before `gate ci-baseline.json`, because the PDF's bytes are the key the
OCR cache reads under:

```sh
cd ci-corpus/tex
SOURCE_DATE_EPOCH=0 FORCE_SOURCE_DATE=1 \
  xelatex -interaction=nonstopmode -output-directory=../pdf sheet-1.tex
rm -f ../pdf/sheet-1.aux ../pdf/sheet-1.log
```

Without the PDF the `pdf` folder holds no document, builds 0 against a recorded 1,
and the gate exits 1.

`ExampleContents` is a set of private documents and is never in the repository: the
gate reads it at the absolute `root` that `gate-baseline.json` gives, which is a path
on the machine the check runs on.

Each folder's `root` and `suffixes` are written by hand. `built`, the count of documents
that built, and `documents`, the outcome of each single document, are what `--record`
writes. A folder the file records no `built` for passes on any count, and its line reads
`(not recorded)`. A change to a recorded count or outcome belongs in a pull request that
says why the count or the outcome changed.

Over `ExampleContents` today, every document replays to `faulted` and the baseline
records 0 built for all three folders: pandoc's line wrapping is reported as a math
delimiter error, and each document needs a fixing round that a replay does not run. The
recorded outcomes are what the gate defends until a later ticket raises the count.

`--cache` defaults to `~/.cache/in2lambda-agent`, which is outside every worktree,
because the gate runs in a worktree of its own: a PDF converted on one branch is
converted again on the next if the cache sits in the branch's directory. `--work`
defaults to a new directory under the system temp
directory, which the gate does not delete: read the
drafts of a folder that failed there. The gate also copies the spec tree into the work
directory and replays the copy, because a sweep appends a record of each run beside the
spec it reads. Neither directory is inside the repository, so `git status` after a gate
run reports no new file.

`.github/workflows/gate.yml` runs pytest and then `gate ci-baseline.json` on every push
to `main` and every pull request. Mathpix reads `ci-corpus/pdf/sheet-1.pdf` once and the
workflow stores the markdown in the Actions cache under the PDF's hash. The job needs
two repository secrets, `MATHPIX_APP_ID` and `MATHPIX_API_KEY`.

A pull request from a fork is given neither secret. `actions/cache` restores the cache
of the base branch for a fork, and the cached markdown is what the `pdf` folder then
replays, so the job passes without the secrets. If the cache is empty — the PDF's bytes
changed, or GitHub evicted the entry — Mathpix cannot be called and the `pdf` folder
builds 0 against a recorded 1, so the job fails. Push that branch to a branch of this
repository, where the secrets are read, and the job runs Mathpix once.

The job is CI's report on a branch and no merge waits for it. The workbench merges with
`gh pr merge` as soon as its own check passes, and `gh pr merge` cannot wait for a
GitHub check, so requiring the job on `main` would refuse every merge the workbench
makes.

The workbench check runs the gate over `ExampleContents`, which is the larger corpus of
the two:

```sh
poetry install -q --with dev && poetry run pytest -q && poetry run in2lambda-agent gate /Users/peterbjohnson/code/lambdafeedback/in2lambda-agent/corpus-specs/gate-baseline.json
```

The path is absolute because the check runs in a worktree and the worktree holds
neither the baseline nor the specs it names.

## Docker

The image carries pandoc, a TeX Live whose xelatex runs the PDF generator's
`template.latex`, and Node:

```sh
docker build -t in2lambda-agent . && docker run --rm -v "$PWD:/work" -w /work in2lambda-agent run sheet.md
```
