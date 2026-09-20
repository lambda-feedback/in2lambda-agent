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

It needs [pandoc](https://pandoc.org/installing.html) on the path to read a document.

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
poetry run in2lambda-agent run SOURCE [--spec FILE] [--review none|sample|per-question] [--rounds N] [--sample N] [--cache DIR] [--fresh-ocr] [--out DIR]
```

`SOURCE` is a PDF, markdown, tex or docx file. A PDF goes to Mathpix first, and its
markdown and images are kept under the PDF's hash in `--cache` (default
`./.in2lambda-agent`), so a second run over the same PDF makes no call. `--fresh-ocr`
converts it again and restarts the run from the new markdown. `--out` defaults to
`./out`, where the set's JSON folder and zip are written.

The run writes a spec — the YAML selectors saying which blocks of the source are
questions, parts and solutions — in one model call, and saves it as
`in2lambda-spec.yaml` beside `SOURCE`. A folder of sheets is one document set and
shares one spec, so the second sheet in that folder runs with no model call. `--spec`
keeps the set's spec somewhere else, and is read if it is there and written if it is
not. Each run appends a line to `in2lambda-agent-runs.jsonl` beside the spec, saying
what the spec covered, what the calls cost, and what each fixing round did.

Each stage prints a line:

```
ocr       fresh pass, restarting from /home/me/sheets/.in2lambda-agent/9f2c…/source.md
freeze    /home/me/sheets/.in2lambda-agent/9f2c…/draft.json
spec      wrote /home/me/sheets/in2lambda-spec.yaml via anthropic, 1883 tokens, 6.4s
coverage  PartsSepSol: 14 blocks, 9 fields at layer 1, 4 ignored, b13 unassigned
validate  b13 (lines 21-21) is in no field and not marked ignore.
fix       round 1: 1 command (question solution q2), 2604 tokens, 4.1s
validate  nothing to report
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
all. A saved spec that the checks fault is written again once before any of that, with
the report in the prompt, if `--rounds` is 1 or more, since a spec that covers the whole
set is worth more than a field repaired in one sheet of it; that rewrite is not itself
one of the rounds.

### Review

`--review` decides how much of a set someone sees before it is built. `none`, the
default, builds as soon as the checks are quiet. `sample` shows a few questions —
`--sample N`, three by default, the ones a fixing round or an edit touched first —
and `per-question` shows every one of them. Either way the run stops with no zip,
writes each question as a PDF and prints it with the lines of the frozen source it
was built from:

```
render    2 questions to /home/me/out/render
review    mode sample, 2 of 2 questions waiting:
  q1 pending: /home/me/out/render/q1.pdf, /home/me/sheets/sheet.md lines 5-5, 7-7
  q2 pending: /home/me/out/render/q2.pdf, /home/me/sheets/sheet.md lines 13-13
  answer with `in2lambda-agent review approve Q --cache /home/me/.in2lambda-agent`, …
```

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

## Docker

The image carries pandoc, a TeX Live with xelatex able to run the PDF generator's
`template.latex`, and Node:

```sh
docker build -t in2lambda-agent . && docker run --rm -v "$PWD:/work" -w /work in2lambda-agent run sheet.md
```
