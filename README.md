# in2lambda-agent

Turns a PDF, docx, tex or md file into a validated Lambda Feedback set.
[in2lambda](https://github.com/lambda-feedback/in2lambda) performs every deterministic
step and every write; this agent performs the OCR, the model calls and the loop control.

`convert` reads the document by two routes and compares them. Route A is one model call
that returns the set as JSON. Route B is a Lua filter, written by one model call per
folder, that pandoc runs with no further call. A field the two routes read the same way
is taken as it stands; a field they read differently goes to a small adjudicating call;
a field that call cannot settle is flagged for a person to read. Every field of either
route must be a quote of the document. [docs/plan.md](docs/plan.md) describes each step
and what it detects.

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
installed and `claude login` run. A `gate` run over a target whose filter and reply are
saved makes no model call unless the two routes word a field differently, so the run
needs no key. A stage that needs a variable names that variable and the run exits 1.

## Convert

```sh
poetry run in2lambda-agent convert sheet.pdf
```

In full:

```sh
poetry run in2lambda-agent convert DOCUMENT [--solutions FILE] [--filter FILE | --write-filter] [--out DIR] [--cache DIR]
```

`DOCUMENT` is a PDF, markdown, tex or docx file. Mathpix converts a PDF first and the
agent keeps the markdown and the images under the PDF's hash in `--cache` (default
`./.in2lambda-agent`), so a second conversion of the same PDF makes no Mathpix call.
pandoc converts a tex or docx file. `--out` defaults to `./out`, where in2lambda writes
the set's JSON folder and its zip.

`--solutions` names the document holding the solutions. Without it the agent takes the
file beside `DOCUMENT` whose name is the document's with `_solutions`, `-solutions` or
` Solutions` after it, in any case, and whose suffix is the same: `Worksheet_1.pdf` and
`Worksheet_1_solutions.pdf`. Naming the solutions document converts the pair too, and
the set is named after the questions document whichever of the two you name; a solutions
document with no questions document beside it converts on its own. Route A reads both
documents in one call, and route B reads each under its own role. The `solutions` line
of the report names the document the
conversion read. Where the two names share no stem, as they do where the platform has
put the time of the download in each, the agent finds no solutions document and the
line names none:

```
solutions none found beside ME2_Fluids_2024-03-11.pdf; pass --solutions FILE
```

A conversion that reads that line and goes on writes an empty answer and an empty
worked solution for every question.

`--filter` names the Lua filter route B runs, which is the file `--write-filter` wrote
for another sheet of the same set. `--write-filter` writes one for this document with a
model call and keeps it at `OUT/filter.lua`. The two options together are refused: a
conversion runs one filter. With neither option route A converts the document alone, no
field is compared, and the counts line says so.

The command prints the solutions document, one line per flagged field, the counts of
the comparison, and the zip:

```
solutions /home/me/sheets/sheet_solutions.pdf
flag      q2.p1.worked_solution: a stray minus sign inside or beside a display maths; Mathpix reads a separator line as one
flag      q4.p2.content: two readings of the source
  A: Find the drag force on the plate.
  B: Find the drag force on the plate, in newtons.
fields    60 fields, agreed 54, defaulted 4, adjudicated 2, flagged 2
build     /home/me/out/sheet.zip
```

A flag names the field, the reason, and each route's text where both routes filled the
field. `fields` counts the fields the two routes agreed on, the fields one route alone
filled, the fields the adjudicating call settled, and the fields flagged. With no
filter there is no comparison to count, so the line is `60 fields, route B did not run`:
the fields are route A's, and each one is flagged or is route A's word for it. A filter
run that fails counts route A's fields in the same way, and adds a `route B failed` line
naming pandoc's message; the set is route A's reading alone.

`convert` exits 1 where a named file is not there, and where Mathpix, the model or
pandoc failed, and 0 otherwise. A flagged
field does not change the exit code: the zip is written whatever the flags say, and a
person reads the flags after it.

### Trying it in the browser

```sh
poetry run in2lambda-agent ui [--corpus DIR] [--port N] [--no-open]
```

This serves one page on `http://127.0.0.1:8765/` and opens it; `--no-open` prints the
address and opens nothing. The page lists `--corpus` — `./ExampleContents` by default,
or the current directory where there is no such folder — one directory at a time:
click a folder to list it, and a document to pick it. A source elsewhere goes into the
box by hand.

The page runs the `convert` command: the two routes, the reconciliation and the build.
Name the solutions document, or leave that box empty for the document beside the source;
name a Lua filter for route B, or tick Write filter for one model call that writes
`filter.lua` into the out directory; then press Go. Each stage line — `ocr`, `route A`,
`route B`, `fields`, `build` — arrives on the page as the stage finishes. When the run
ends, the page shows each flagged field with the reason it is flagged and each route's
reading of it, the counts of the reconciliation, the tokens, and links to the zip and to
the filter where the run wrote one.

The page is a harness for trying the agent by hand. It listens on this machine only, has
no authentication, and runs one conversion at a time.

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

`compare` writes no set, and no other command reads its findings. `compare` does write
the OCR cache entry `convert` writes: a PDF the cache holds no entry for is converted
and cached, and `--fresh-ocr` deletes the entry a previous run cached and converts the
PDF again. `compare` exits 1 where Mathpix, the model or `pdftoppm` failed, and 0
otherwise. A difference it reports does not change the exit code.
[docs/ocr-comparison.md](docs/ocr-comparison.md) lists the differences `compare` found
over three corpus documents and judges each one.

## Corpus

The design spec's test plan is the two-route conversion run over a corpus of real
documents, with one row of a table recorded for each sheet:

```sh
poetry run in2lambda-agent corpus ExampleContents --suffix tex --suffix md
```

In full:

```sh
poetry run in2lambda-agent corpus ROOT [PATH ...] [--suffix S] [--results FILE] [--work DIR] [--cache DIR]
```

`ROOT` is the corpus directory and each `PATH` a folder under it to run, defaulting to
all of it. `--suffix` is repeatable and defaults to `tex`, `md` and `docx`; `--suffix
pdf` runs the PDFs too, which needs Mathpix credentials and one call per PDF.

A set is a folder holding at least one questions document. Each set converts as a
folder run of `convert` does: one model call writes the set's filter from the first
sheet of it pandoc reads itself, or from the first PDF where the set has no other, and
each sheet of the set then runs through route A and that filter. A sheet and the solutions file beside it are one conversion and one row,
named after the questions file. A folder of figures, a tex drawing with no
`\begin{document}` among them, and a folder holding a solutions file alone, are not
sets and have no row.

The sweep never writes to the corpus. It writes each set's filter to
`WORK/SET/filter.lua` and each sheet's set folder and zip to `WORK/SET/SHEET/`, where
`--work` defaults to `./.in2lambda-agent/corpus`.

`--results` (default `./results.csv`) holds one row per sheet, in path order, with these
twelve columns:

```
set, sheet, questions, parts, fields, agreed, adjudicated, flagged, not_verbatim,
tokens, seconds, reason
```

`reason` is empty where the sheet ran through both routes
and built its set. One sheet whose conversion raises is one row, with `no set:` and the
error as its reason, and the sheets after it still run. A set whose filter call did not
finish converts every sheet through route A alone, and each of those rows reads `no
filter:` and why. A PDF runs route B over the markdown its OCR made, which is the
markdown route A reads, so a set of PDFs has a filter like any other; where the filter
was written from a tex or docx sheet beside it, that filter may still fail on the PDF,
and that row alone reads `route B failed:`.

`corpus` exits 0 where it ran at least one sheet and every sheet built a set. It exits 1
where it found no sheet, and where any sheet built no set — a row whose reason begins
`no set:`. A sheet route B failed on built its set from route A and does not change the
exit code.

`--cache` is where the OCR of each PDF is kept. It defaults to `./.in2lambda-agent`,
the directory `convert` caches into, so a sweep over PDFs that `convert` has already
converted makes no Mathpix call. It still reads `MATHPIX_APP_ID` and `MATHPIX_API_KEY`: `convert`
builds the Mathpix client before it asks the cache, and refuses a PDF where either
variable is unset, whether or not the cache holds that PDF.

## Targets

A target is a folder holding one set: a questions document, a solutions document where
the set has one, and the folder Lambda Feedback exported for that set, named
`set_<Name>`. The export is what the conversion is trying to reproduce, so a target is
the one place the agent can be told right from wrong rather than merely flagged:

```sh
poetry run in2lambda-agent targets ExampleContents/targets
```

In full:

```sh
poetry run in2lambda-agent targets ROOT [PATH ...] [--filters DIR] [--out DIR] [--cache DIR] [--fresh]
```

`ROOT` is the directory the targets are under and each `PATH` a folder under it to run,
defaulting to all of them. A target is found by the `set_*` folder it holds, either
directly under `ROOT` or grouped by course a folder down:
`targets/ME2_Fluids_introduction/` and `targets/EART40013_Mathematical_Methods_II/CW1/`
are both targets. The folder's two documents are read by role rather than by name: the
one whose name ends in `_solutions` is the solutions document, and the other is the
questions document, which is how a sheet pairs with a solutions document the platform
printed months later under a name of its own. A folder holding two of either is
reported and not run.

Each target is converted, and the zip it wrote is compared question by question with
the export. The run prints one line per difference and one line of counts per target:

```
differs   ME2_Fluids_introduction: Question 2 "", part (a), text: the agent says … and the export says …
known     ME2_Fluids_introduction: Question 1 "", main text: the agent says … and the export says …
agrees    ME2_Fluids_introduction: q3.p2.worked_solution now agrees, remove the line
ME2_Fluids_introduction: 4 differ, 3 known, 1 new, 2 flagged
```

A difference you have read and accepted goes into `differs.txt` beside that target's
filter. A line of that file names the field the difference is in, and states after a `#`
why the field differs:

```
q1.main_text           # the export keeps the spacing the platform wrote around display maths
q2.p1.worked_solution  # Mathpix reads the separator line under the working as a minus sign
```

The file records a field rather than a sentence because the report quotes a model's
wording. Route A reads the document on every run, and a model writes the same field
differently each time it is asked. A difference in a field the file names is reported as
`known` whatever its wording; a difference in any other field is reported as `differs`
and is new; a field the file names that no longer differs is reported as `agrees`, which
is a line to delete, and does not fail the run. The command exits 0 when every target
ran and reported no new difference, and 1 otherwise, so a target set is a check as well
as a report.

`--filters` (default `./targets`) is the tree of saved filters, mirroring the targets:
target `A/B` keeps its filter at `targets/A/B/filter.lua`, route A's reply at
`targets/A/B/reply.json` and its accepted fields at `targets/A/B/differs.txt`. The first
run over a target makes one model call for the filter and one for route A's reply, and
writes both. Every run after that reads the two files and makes neither of those two
calls, so the second run over a target differs from the export in the same fields as the
first. `--fresh` reads the documents again and writes a new reply, which changes the
wording of the report and the number of fields flagged.

Those two are the only calls a saved target spares. A target with a filter runs route B
on every run, and a model adjudicates every field the two routes word differently. A
verdict can go the other way on a later run, so the wording of a difference and the
`flagged` count move from run to run while the fields `differs.txt` accepts stay
accepted. A target whose questions document is a PDF has no filter: route B does not
run, and route A converts the pages' markdown alone. `--out` (default `./out`) is where each target's set is written, under
the target's own name, and `--cache` (default `./.in2lambda-agent`) is where the OCR of
each PDF is kept.

## Gate

Nothing merges without a run over real documents. `gate` replays every target under
`ROOT` against the set Lambda Feedback exported from it:

```sh
poetry run in2lambda-agent gate ROOT [PATH ...] --filters DIR [--cache DIR] [--work DIR]
```

`gate` converts and compares each target as `targets` does, prints the same lines, and
exits 0 where every target ran and reported no new difference. `gate` differs from
`targets` in one thing: a target whose `reply.json`, or whose `filter.lua`, is not saved
under `--filters` is reported as an error and is not converted. The two model calls that
read a document are therefore never made, and what the gate reports is a change to the
agent and not a model wording a field differently today.

```
work      /tmp/in2lambda-agent-gate-3f1a
sheet: 0 differ, 0 known, 0 new, 0 flagged
1 target, 0 new differences
```

The error names the file and the command that writes it. Run
`in2lambda-agent targets ROOT --filters DIR` over that target, read the reply and the
filter it saves, and commit them.

`--cache` defaults to `~/.cache/in2lambda-agent`, which is outside every worktree,
because the gate runs in a worktree of its own: a cache inside the branch's directory
converts each PDF again on the next branch. `--work` defaults to a new directory under
the system temp directory, which the gate does not delete: the set of a target that
differs is written there for a person to read. Neither directory is inside the
repository, so `git status` after a gate run reports no new file.

The repository holds one target, `ci-corpus/targets/sheet`: two synthetic markdown
documents, and the export `set_Sheet` that in2lambda's writer wrote from the saved
reply. `ci-corpus/filters/sheet` holds that target's filter and reply. The two routes
agree on every field of the sheet, so the run adjudicates nothing and reads no
credential. `.github/workflows/gate.yml` runs pytest and then

```sh
poetry run in2lambda-agent gate ci-corpus/targets --filters ci-corpus/filters
```

on every push to `main` and every pull request.

The private targets under `ExampleContents/targets` are replayed by the same command,
over the filter tree on the machine that holds them. The repository holds neither those
documents nor their filters: a filter is written from a private document's structure,
and a `reply.json` holds that document's text. `.gitignore` lists `/targets/`, which is
the default `--filters` directory.

The job is CI's report on a branch and no merge waits for it. The workbench merges with
`gh pr merge` as soon as its own check passes, and `gh pr merge` cannot wait for a
GitHub check, so requiring the job on `main` would refuse every merge the workbench
makes.

## Docker

The image carries pandoc, a TeX Live whose xelatex runs the PDF generator's
`template.latex`, and Node:

```sh
docker build -t in2lambda-agent . && docker run --rm -v "$PWD:/work" -w /work in2lambda-agent convert sheet.md
```
