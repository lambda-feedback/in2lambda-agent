# in2lambda-agent

Turns a PDF, docx, tex or md file into a validated Lambda Feedback set.
[in2lambda](https://github.com/lambda-feedback/in2lambda) does every deterministic
step and every write; this agent does OCR, model calls and loop control.

Today it is a scaffold: it drives in2lambda end to end with no model call, and the
stages that wait on in2lambda commands not yet built say so and carry on.

## Install

```sh
poetry install --with dev
```

It needs [pandoc](https://pandoc.org/installing.html) on the path to read a document.

Copy `.env.example` to `.env` and fill in what you have. `.env` is not committed, and
nothing the agent does today needs a credential.

## Run

```sh
poetry run in2lambda-agent run SOURCE [--spec FILE] [--review none|sample|per-question] [--rounds N] [--out DIR]
```

`SOURCE` is a markdown, tex or docx file. `--out` defaults to `./out`, where the set's
JSON folder and zip are written.

Each stage prints a line. A stage that is waiting on work not yet built prints the
in2lambda command or the agent stage it waits for, and the run carries on:

```
freeze    waiting for in2lambda source add
spec      waiting for in2lambda spec run; using the PartsSepSol layout
layout    PartsSepSol: 2 questions
validate  waiting for in2lambda validate
review    waiting for the model stages (mode none, round limit 1)
build     out/set.zip
```

So `--spec`, `--review` and `--rounds` are read and reported, but nothing acts on
them yet. The layout is PartsSepSol until a spec can choose one.

## Docker

The image carries pandoc, a TeX Live with xelatex able to run the PDF generator's
`template.latex`, and Node:

```sh
docker build -t in2lambda-agent . && docker run --rm -v "$PWD:/work" -w /work in2lambda-agent run sheet.md
```
