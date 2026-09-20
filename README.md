# in2lambda-agent

Turns a PDF, docx, tex or md file into a validated Lambda Feedback set.
[in2lambda](https://github.com/lambda-feedback/in2lambda) does every deterministic
step and every write; this agent does OCR, model calls and loop control.

Today it writes layer 1: one model call per document set writes a spec of selectors,
in2lambda runs it over the frozen source, and a draft that the checks pass is built.

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
what the spec covered and what the call cost.

Each stage prints a line:

```
ocr       fresh pass, restarting from /home/me/sheets/.in2lambda-agent/9f2c…/source.md
freeze    /home/me/sheets/.in2lambda-agent/9f2c…/draft.json
spec      wrote /home/me/sheets/in2lambda-spec.yaml via anthropic, 1883 tokens, 6.4s
coverage  PartsSepSol: 14 blocks, 10 fields at layer 1, 4 ignored, none unassigned
validate  nothing to report
review    waiting for the model stages (mode none, round limit 1)
build     /home/me/sheets/out/set.zip
```

A run stops without a zip, and exits 1, when the checks find something: a block of the
source in no field, two fields from the same lines, a gap in the numbering, a part
nothing answers. A saved spec that the checks fault is written again once, with the
report in the prompt, if `--rounds` is 1 or more; fixing a single field rather than the
whole spec is still to come, and so is `--review`, which is read and reported but acts
on nothing yet.

## Docker

The image carries pandoc, a TeX Live with xelatex able to run the PDF generator's
`template.latex`, and Node:

```sh
docker build -t in2lambda-agent . && docker run --rm -v "$PWD:/work" -w /work in2lambda-agent run sheet.md
```
