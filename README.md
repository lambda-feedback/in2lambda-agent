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
poetry run in2lambda-agent run SOURCE [--spec FILE] [--review none|sample|per-question] [--rounds N] [--cache DIR] [--fresh-ocr] [--out DIR]
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
review    waiting for the model stages (mode none, round limit 3)
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
out. A saved spec that the checks fault is written again once before any of that, with
the report in the prompt, since a spec that covers the whole set is worth more than a
field repaired in one sheet of it; that rewrite is not one of the rounds. `--review` is
read and reported but acts on nothing yet.

## Docker

The image carries pandoc, a TeX Live with xelatex able to run the PDF generator's
`template.latex`, and Node:

```sh
docker build -t in2lambda-agent . && docker run --rm -v "$PWD:/work" -w /work in2lambda-agent run sheet.md
```
