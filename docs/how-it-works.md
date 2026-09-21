# How a run works

`in2lambda-agent run` converts one source file into a Lambda Feedback set. This page
names each stage of a run, the in2lambda function the stage calls, the file the stage
writes and every message the stage prints. [README.md](../README.md) gives the
commands and their options.

The agent makes three kinds of model call and no others: one writes the set's spec,
one rewrites a saved spec the checks fault, and one answers a validation report. A run
makes the spec call up to `--tries` times, three by default, and saves one of the specs
it wrote. in2lambda performs every other step and every write.

## The stages

A run prints one line per stage. The line is the stage name padded to nine characters,
then the message:

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

There are eleven stage names: `ocr`, `freeze`, `spec`, `coverage`, `replay`,
`validate`, `set`, `fix`, `render`, `review` and `build`. The spec loop prints
`freeze`, `spec`, `coverage`, `validate` and `set` once per try, and a run prints
`validate` once per check and `fix` once per fixing round, so those names repeat. Each
line is printed as the run makes it, so a `--tries 3` run prints seven lines before its
second model call: `ocr`, the five lines of the first try, and the `freeze` of the
second. The run above made two of its three tries, because the second spec left no
block unassigned and no error behind. A run prints `replay` only when it was given a
saved log to run, which is what a corpus replay is given.

| Stage | in2lambda function | What the stage writes |
| --- | --- | --- |
| `ocr` | none: Mathpix converts the PDF | `CACHE/HASH/source.md` and `CACHE/HASH/media/` |
| `freeze` | `in2lambda.source.add` | `SOURCE.draft.json`, beside the frozen source |
| `spec` | `in2lambda.source.show`, for the model's prompt | `in2lambda-spec.yaml`, beside `SOURCE` or at `--spec` |
| `coverage` | `in2lambda.draft.execute` with `in2lambda.draft.spec_command` | the layer 1 fields of the draft |
| `replay` | `in2lambda.draft.execute` once per saved command | the fields and the log of the draft |
| `set` | `in2lambda.source.add`, then `in2lambda.draft.execute` with `in2lambda.draft.spec_command` | the draft of the copy in `CACHE/second/`, and nothing beside the set's own sheets |
| `validate` | `in2lambda.draft.report.validate` | the report inside the draft |
| `fix` | `in2lambda.source.show`, then `in2lambda.draft.execute` once per command | the fields and the log of the draft |
| `render` | `in2lambda.draft.export.render` | `OUT/render/question_000_Question_1.pdf`, one PDF per question |
| `review` | none | `CACHE/review.json` |
| `build` | `in2lambda.draft.export.build` | `OUT/set.zip` and the set's JSON folder |

`CACHE` is `--cache`, `./.in2lambda-agent` by default. `OUT` is `--out`, `./out` by
default. `HASH` is the sha256 of the PDF's bytes.

A run also appends one line to `in2lambda-agent-runs.jsonl`, beside the spec. The run
appends that line last, whether it wrote a zip or ended with a report. A run that stops
for a review appends no line: the approval that completes the review appends it.

### `ocr`

The stage converts a PDF to markdown through Mathpix. A source of any other suffix is
already markdown, tex or docx, and the stage converts nothing. The stage prints one of
three messages:

* `not needed for sheet.tex` — the source is not a PDF, and the stage names it.
* `cached /home/me/.in2lambda-agent/9f2c…/source.md` — the cache already holds the
  markdown of this PDF, and Mathpix is not called.
* `fresh pass, restarting from /home/me/.in2lambda-agent/9f2c…/source.md` — Mathpix
  converted the PDF, because the cache held no entry for it or because the run was
  given `--fresh-ocr`.

`--fresh-ocr` deletes the whole cache entry first, so every stage below reads the new
markdown. A conversion that fails writes no entry.

### `freeze`

`in2lambda.source.add` reads the source through pandoc, cuts it into numbered blocks
and writes `SOURCE.draft.json` beside the source. The message is that path and nothing
else. The draft of a PDF is written beside `source.md` in the cache entry. Each run
freezes the source again, so a second run over one file replaces the draft the first
run wrote.

### `spec`

The stage prints one of three messages:

* `reused /home/me/sheets/in2lambda-spec.yaml` — the spec file exists, and the stage
  makes no model call. A run that reuses a spec the checks then fault prints this stage
  a second time in its `wrote` form.
* `wrote /home/me/sheets/in2lambda-spec.yaml via anthropic, 1883 tokens, 6.4s (try 1 of
  3)` — the model wrote the spec. The backend is `anthropic`, `openrouter` or
  `agent-sdk`. The token count is the call's input and output tokens added together,
  and the time is the wall time of the call to one decimal place. The try number counts
  from 1 to `--tries`.
* `kept try 2 of 3` — the loop wrote more than one spec, and this names the try saved
  for the set: the one that left the fewest blocks unassigned and the fewest errors,
  over this source and over the set's other document. The loop writes one spec where
  the first leaves neither, and prints no `kept` line.

in2lambda refuses a spec it cannot run, and the run raises `SpecRejected`. A spec this
run wrote is deleted before that refusal reaches the user, and a spec this run wrote
over an older one is replaced by the older one, so the next run over the set reads a
spec in2lambda accepts.

### `coverage`

`in2lambda.draft.execute` runs the spec over the frozen source and fills the draft's
layer 1 fields. The message has one form:

```
PartsSepSol: 14 blocks, 9 fields at layer 1, 4 ignored, b13 unassigned
```

The layout is the spec's own `layout` key. `14 blocks` is the number of blocks in the
frozen source. The field counts are one phrase per layer, in layer order, and the
stage prints `no fields` where the spec wrote none. `4 ignored` is the number of
blocks the spec's `ignore` selector matched. The unassigned blocks are listed by id,
and the stage prints `none unassigned` where every block reached a field.

### `replay`

The stage runs the commands an earlier run's fixing rounds ran, read from the file the
run was given, in the order they were saved. It calls no model: the commands name the
blocks, field keys and line ranges each one wrote, so the draft the checks then see is
the draft the earlier run's rounds left. The message has one form:

```
5 commands from /home/me/corpus-specs/sheets/sheet.md.commands.json
```

in2lambda refusing one of the commands ends the run, and the refusal names which
command it was: `command 3 of 5, question add: b7b is in a field already`. The corpus
sweep writes these files and `corpus --replay` reads them; a `run` is given one through
`pipeline.run`.

### `set`

The stage runs the try's spec over another document of the set, so that a spec is
judged on the set it is saved for rather than on this one sheet. The document is the
first other document of the source's folder, by name, whose suffix is the source's. The
run copies it under `CACHE/second/` and freezes that copy, so the draft an earlier run
left beside that sheet stays as that run wrote it. The stage prints one of four
messages:

* `sheet-2.md: PartsSepSol: 11 blocks, 7 fields at layer 1, 3 ignored, b9 unassigned` —
  the spec ran over the other document, in the `coverage` line's own form. The blocks
  it left in no field there count toward the try's score, beside the blocks and the
  errors this source left.
* `sheet-2.md: in2lambda refused the spec: ERROR` — in2lambda ran the spec over this
  source and refused it over the other document. The try scores as leaving every block
  of that document in no field. The next try is run over the document again.
* `sheet-2.md cannot be read: ERROR` — in2lambda refuses the document itself, a Word
  lock file beside a docx among them. The run continues, judges its tries on this
  source alone, and runs no later try over the document.
* `sheet-2.pdf passed over: converting it takes an OCR call, and the spec loop makes no
  call but the model's` — the run passed the document over before running a spec over
  it. A PDF beside a PDF source is passed over for the reason the message gives, and a
  document the run cannot copy is passed over saying so.

A folder holding one sheet prints no `set` line. The sheet's own solutions file is no
other document of the set: it is a second source of this run's own draft already.

### `validate`

`in2lambda.draft.report.validate` checks the draft and writes its report into the
draft file. A finding is an error or a warning. `build` refuses an error and proceeds
past a warning, so a report holding warnings alone is one the run builds. The stage
prints one of six messages:

* `nothing to report` — the checks found nothing.
* `a part of q2 has no solution. — warnings, building` — the checks found warnings
  alone. The warnings are joined with `; `.
* `b13 (lines 21-21) is in no field and not marked ignore.` — the errors, joined with
  `; `. A fixing round follows where `--rounds` is 1 or more. Under `--rounds 0` the run
  ends on this line, with no `fix` line after it.
* `ERRORS — writing the set's spec again` — the run reused a saved spec, the checks
  fault the draft, and `--rounds` is 1 or more. The run writes the spec again and
  prints `freeze`, `spec`, `coverage` and `validate` a second time.
* `ERRORS — left by round 2, no zip` — round 2 answered no error it was given, so the
  run ends with those errors and writes no zip.
* `ERRORS — round limit 3 reached, no zip` — the last round of `--rounds` ran and the
  checks still fault the draft.

### `fix`

One round is one model call with in2lambda's draft commands as its tools. The stage
prints one message per round:

```
round 1: 1 command (question solution q2), 2604 tokens, 4.1s
```

The count and the commands come from the calls the model made: `no commands` where it
made none, `1 command` or `2 commands` and then each command with the block, question,
field or range it names. The tokens and the time are the round's own.

`in2lambda-agent review reject` prints a second form, where the run that stopped for
the review was given a round limit below 1. The message quotes that limit:

```
no rounds left to answer the note with: the run was --rounds 0
```

### `render`

The stage runs only where the run is in review. It calls
`in2lambda.draft.export.render(draft, output_dir)`, which compiles one PDF per
question under `OUT/render` as Lambda Feedback's own PDF generator compiles it. Each
file is named after the question's place in the set and its title:
`question_000_Question_1.pdf`. The stage prints one of two messages:

* `2 questions to /home/me/out/render` — the PDFs were written, and each question's
  line in the listing names its file.
* in2lambda's own message, such as `Rendering questions needs xelatex.` — pandoc or
  xelatex is missing, or no question compiled. The review goes on, and each question
  reads `not rendered`.

### `review`

The stage prints one of six messages:

* `not asked for (mode none)` — `--review` is `none`, and the run builds.
* the listing of the questions still waiting, printed by every command that leaves a
  review unfinished:

  ```
  mode sample, 2 of 2 questions waiting:
    q1 pending: /home/me/out/render/question_000_Question_1.pdf, /home/me/sheets/sheet.md lines 5-5, 7-7
    q2 rejected: /home/me/out/render/question_001_Question_2.pdf, /home/me/sheets/sheet.md lines 13-13 — the solution answers (a), not (b)
    answer with `in2lambda-agent review approve Q --cache /home/me/.in2lambda-agent`, `review reject Q --note "..."` or `review edit FIELD OLD NEW`
  ```

  A question's line names the question, its status, its PDF or `not rendered`, the
  frozen source with the lines its fields were copied from, and the note of a
  rejection. A question with no lines reads `lines none`. Where the checks fault the
  draft, one more line follows the questions: `the checks fault the draft, so it
  cannot be built yet: ERRORS`.
* `q1 approved` — one approval, with the listing after it.
* `q1 approved, and that is all of them` — the last approval, which runs the checks
  again and builds.
* `q2 rejected: the solution answers (a), not (b)` — one rejection, before its fixing
  round.
* `me edited q1.text` — one `review edit`, naming the reviewer.

### `build`

`in2lambda.draft.export.build` writes the set's JSON folder and its zip under `--out`.
The stage prints one of two messages:

* `/home/me/sheets/out/set.zip` — the zip that was written.
* `refused: q1 refers to ball.png, which is not beside the draft` — in2lambda would
  not write the set out. The run ends with no zip.

## The three model calls

Each call is a system prompt, a user prompt, an optional list of tools and one reply.
Each backend limits a call differently:

* Tool rounds: every backend stops a call that asks for tools 8 times without
  answering. `anthropic` and `openrouter` raise `the anthropic backend asked for tools
  for 8 rounds without answering`, naming themselves. `agent-sdk` passes the same 8 to
  the SDK as `max_turns`, and the SDK's own stop raises `the agent-sdk backend stopped
  on SUBTYPE: RESULT`.
* Output tokens: `anthropic` asks the API for at most 8192 output tokens per reply.
  `openrouter` and `agent-sdk` send no limit.
* Timeout: `anthropic` and `openrouter` fail a request that takes longer than 300
  seconds. `agent-sdk` sets no timeout.

| Call | What it is given | What it may write |
| --- | --- | --- |
| Spec | the spec system prompt, the frozen source as `in2lambda.source.show` prints it, and, from the second call on, the spec before it, that spec's coverage line, the errors the report holds and the blocks that spec left in no field in the set's other document | `in2lambda-spec.yaml`, and nothing else |
| Spec rewrite | the same, with the saved spec and what running it covered as the first call's try 0 | `in2lambda-spec.yaml`, and nothing else |
| Fixing round | the fixing system prompt, the frozen source, every finding of the report, and a reviewer's note where there is one | the eight draft commands, and nothing else |

The spec call has no tools. Its reply is the YAML of a spec, past a code fence where
the model wrote one. The agent refuses a reply that is not YAML, a reply that is not a
mapping, and a reply naming a `layout` outside `PartsSepSol`, `PartsOneSol`,
`PartSolPartSol` and `PartPartSolSol`.

A run makes the spec call up to `--tries` times, three by default. Each call after the
first is asked for a spec that leaves fewer blocks unassigned and fewer errors behind
than the one before it, over this source and over the set's other document. The run
makes no further call once a spec leaves no block unassigned and no error behind, and
saves the try that left the fewest of both.

The spec rewrite is the same loop, with the saved spec and what running it covered as
try 0. It runs where the run reused a saved spec and the checks fault the draft, before
any fixing round, and takes `--tries` calls like any other spec. It writes layer 1
fields, and it is not one of the `--rounds`.

The fixing round's tools are the eight in2lambda draft commands: `mark ignore`,
`question add`, `part add`, `question solution`, `part solution`, `field replace`,
`field set` and `split block`.
in2lambda writes the field, records the command in the draft's log as `in2lambda-agent`
and decides the layer. A command in2lambda refuses returns its refusal to the model as
the tool's result, and the round continues. A field's text is named by block id or line
range; `literal` types at most 80 characters, and the round refuses a longer one. A
round that leaves only errors it was already given ends the run, because the next round
would read the same prompt and the same report.

## The four layers and the `edited` flag

Every field of a draft carries the layer that wrote it.

| Layer | Written by |
| --- | --- |
| 1 | the spec, run by `in2lambda spec run` |
| 2 | a predicate, which no spec the agent writes names |
| 3 | a draft command naming a block id or a line range |
| 4 | a draft command's `literal` |

in2lambda runs predicates: a spec's `predicates` key names a Python file, and a
selector calls a function from that file. The agent's spec prompt lists the keys a
spec may hold and leaves `predicates` out, so layer 2 is 0 in every run the agent
makes.

`field replace` writes no layer. in2lambda leaves the field at the layer that wrote it,
leaves it quoting the lines it was copied from, and sets the field's `edited` flag. So a
round that replaces a layer 1 field records `layer1=1, layer4=0, edited=1`, and the
corpus table's `layer1` to `layer4` columns count each field under the layer in2lambda
recorded. `package.questions` is the one reader that counts an edited field as layer 4,
and it does so to sort the questions a `sample` review shows.

## Review modes

`--review` decides how much of a set a reviewer reads before it is built.

| Mode | What the reviewer reads | What follows |
| --- | --- | --- |
| `none` | nothing | the run builds as soon as the checks report no error |
| `sample` | `--sample N` questions, 3 by default | the run builds after the last approval |
| `per-question` | every question | the run builds after the last approval |

A sample lists the questions of layer 3 or 4 first, in question order, and fills the
rest of the count by drawing from the remaining questions at random.

A run in `sample` or `per-question` mode stops once the checks report no error. It runs
the `render` stage over the chosen questions, writes `review.json` under `--cache`,
prints the listing and writes no zip. Every path in that file is absolute, because the reviewer's commands
run from another directory.

`in2lambda-agent review` answers the record, one command per verdict:

* `approve` marks the question approved. Where questions remain, the command saves the
  record and prints the listing. Where none remains, the command runs the checks again,
  builds the set, appends the run record with a `review` key and deletes `review.json`.
  Where those checks fault the draft, the command builds nothing and the review stays
  open.
* `reject` marks the question rejected, stores the note and runs a fixing round with
  the note as its instruction. The round limit is the `--rounds` of the run that wrote
  the record. The checks run again, the question returns to `pending`, and the listing
  is printed.
* `edit` runs `field replace` with the reviewer's name as the log's author, which sets
  the field's `edited` flag. The checks run again, and the question holding that field
  returns to `pending`.

## The run record

`in2lambda-agent-runs.jsonl` sits beside the spec and holds one JSON object per line:

| Key | Value |
| --- | --- |
| `source` | the file the run converted |
| `reused` | whether the run ran the saved spec without a model call |
| `layout` | the layout the spec named |
| `blocks` | how many blocks the frozen source holds |
| `fields` | how many fields each layer wrote, keyed by layer number |
| `ignored` | how many blocks the spec's `ignore` selector matched |
| `unassigned` | the ids of the blocks the spec run left in no field |
| `input_tokens` | what the run's model calls read |
| `output_tokens` | what they wrote |
| `seconds` | how long they took, to three decimal places |
| `rounds` | one object per fixing round: `round`, `input_tokens`, `output_tokens`, `seconds`, `commands` and `left`, how many errors the checks still found after the round |
| `review` | the review's `mode`, its `questions` with a `status` and a `note` each, its `rejections` and its `edits`. The key is absent from an unreviewed run. |

## The corpus table

`in2lambda-agent corpus` writes one row per document to `--results`. The columns are
these 21, in this order:

| Column | Where it comes from |
| --- | --- |
| `source` | the document, relative to the corpus root |
| `set` | the folder the document is in |
| `outcome` | `built`, `build refused`, `faulted`, `skipped`, `no spec`, `replay refused` where in2lambda would not run one of the saved commands, `no model`, `spec rejected`, `bad spec`, or `error: <exception>` |
| `reason` | the build's refusal, the first error the checks still found, the warnings a build proceeded past, or what an exception said |
| `spec` | `wrote`, `reused`, or `rewritten` where the spec rewrite ran |
| `layout` | the coverage's layout |
| `blocks` | the coverage's block count, before any fixing round |
| `fields` | how many fields the finished draft holds |
| `layer1` | `package.layers`: how many fields the spec wrote |
| `layer2` | `package.layers`: how many a predicate wrote, so 0, because the agent's spec prompt asks for no predicate |
| `layer3` | `package.layers`: how many a round quoted out of the source |
| `layer4` | `package.layers`: how many a round typed out |
| `edited` | `package.layers`: how many fields carry the `edited` flag |
| `unassigned` | how many blocks the coverage left in no field, before any round |
| `rounds` | how many fixing rounds ran |
| `input_tokens` | what the run's model calls read |
| `output_tokens` | what they wrote |
| `model_seconds` | how long they took |
| `wall_seconds` | how long the whole document took |
| `review` | the review mode the run was given, which the sweep sets to `none` |
| `rejections` | how many questions a reviewer turned down, so 0 under mode `none` |

`blocks` and `unassigned` report the spec run alone, so a `built` row can still name
blocks the spec left unassigned and a later round covered.

## Backends and settings

`load_settings` reads four variables. Where the agent reads the process's own
environment, it first loads a `.env` found from the working directory upwards; a
variable already set in the environment wins over the `.env`. An unset or empty
variable becomes `None`.

| Variable | Read by |
| --- | --- |
| `MATHPIX_APP_ID` | converting a PDF |
| `MATHPIX_API_KEY` | converting a PDF |
| `ANTHROPIC_API_KEY` | the model calls |
| `OPENROUTER_API_KEY` | the model calls |

`choose_backend` reads the settings and returns the first backend the keys allow:

1. `ANTHROPIC_API_KEY` is set: the `anthropic` backend, calling the Anthropic Messages
   API with the model `claude-sonnet-5`.
2. `OPENROUTER_API_KEY` is set: the `openrouter` backend, calling OpenRouter's
   OpenAI-compatible endpoint with the model `anthropic/claude-sonnet-5`.
3. Neither is set: the `agent-sdk` backend, calling the Claude Code login through the
   Claude Agent SDK. It needs `claude` on the path.

The three backends limit a call differently, as [the three model
calls](#the-three-model-calls) records.

A run asks for a backend only where it must write a spec, and `review reject` asks for
one only where a round is left to run. So a set whose spec is saved runs with no key,
and a `--rounds 0` rejection is recorded on a machine with no key. A backend that
cannot run raises `ModelUnavailable` with one of three messages:

```
set ANTHROPIC_API_KEY to use the anthropic backend
set OPENROUTER_API_KEY, a key from https://openrouter.ai/keys, to use the openrouter backend
install Claude Code and run `claude login` to use the agent-sdk backend, or set ANTHROPIC_API_KEY
```
