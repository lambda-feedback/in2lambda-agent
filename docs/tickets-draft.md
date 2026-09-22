# Ticket drafts, for review. None of these is on the board.

Every ticket writes its tests before its code. Every ticket's pull request reports a run over
a target set or a corpus folder, with the number of flagged fields.

## 1. Convert a whole folder of sheets through both routes and compare the two results

Today the `convert` function converts one document through route A, the direct model call.
It runs route B, the pandoc filter, only when a filter file is supplied by hand. This ticket
adds the folder case. For a folder, the tool pairs each sheet with its solutions file by
name (for example `Sheet1.tex` with `Sheet1_solutions.tex`, or `Sheet1.pdf` with
`Sheet1_Sol.pdf`, ignoring case). The model writes one filter from the first sheet in the
folder, and pandoc runs that filter over every sheet. The model also converts each sheet
directly. For each sheet, the tool compares the two results field by field, sends the
disputed fields to the adjudication call, and writes one line of report: the number of
fields, the number agreed without a call, the number the adjudicator decided, and the number
flagged for a person. The ticket is done when the nine PHYS sheets, with their solutions
files, have run live and the pull request shows the report.

## 2. Add a `convert` command to the command line

The command is `in2lambda-agent convert DOCUMENT [--solutions FILE] [--filter FILE |
--write-filter] [--out DIRECTORY]`. It prints the report and writes the zip. The existing
`run` command keeps the spec route behind a flag, `--route spec`, and the README describes
`convert` as the way to convert a document. The ticket is done when the ME2 pair converts
from the command line with no flagged field.

## 3. Point the corpus sweep at the new route

The `corpus` command runs the folder conversion of ticket 1 over each set in the corpus and
writes `results.csv` with one row per sheet and these columns: set, sheet, questions, parts,
fields, agreed, adjudicated, flagged, not verbatim, tokens, seconds, and a reason where the
sheet produced no set. The ticket is done when the three ExampleContents folders have been
swept and the pull request shows the table.

## 4. Compare each target document with the set Lambda Feedback exported from it

A target is a folder holding one set: one questions document, an optional solutions document
whose name ends in `_solutions`, and the folder Lambda Feedback exported for that set, named
`set_<Name>`. Targets live under `ExampleContents/targets/`, either directly
(`targets/ME2_Fluids_introduction/`) or grouped by course
(`targets/EART40013_Mathematical_Methods_II/CW1/`, `.../CW2/`). The tool finds a target by
its `set_*` folder. For each target, the tool converts the documents and compares the built
set with the export through in2lambda's comparison function. Differences the maintainer has
accepted are listed in a file beside the target's saved filter. The ticket is done when the
ME2 target and the two EART40013 targets report their known differences and no other.

## 5. Point the web page at the new route

The page runs the `convert` command, shows each stage line as it happens, and shows each
flagged field with both versions and the reason. The ticket is done when the ME2 pair runs
from the page.

## 6. Fix two faults in the input markdown

First, pandoc writes an underlined run in a docx file as `[text]{.underline}`, which Lambda
Feedback does not render. The tool converts docx files with pandoc's `bracketed_spans`
extension switched off, so the text is written without the brackets. Second, when Mathpix
reads a PDF that Lambda Feedback printed, it reads the horizontal separator lines as minus
signs at the start or end of the neighbouring maths. The tool flags a display maths that
begins or ends with a lone minus sign. The ticket is done when the MECH docx sheets carry no
bracketed span and the ME2 solutions report the fields with a stray minus sign.

## 7. Remove the spec route from the agent

After tickets 1 to 5 are merged, delete the spec writer, the fixing loop and the wrapper
around the draft commands from the agent, together with their tests. The in2lambda package
keeps its spec engine unchanged. The ticket is done when the agent's tests pass and
`in2lambda-agent --help` lists `convert`, `corpus`, `gate`, `compare` and `ui`.

## 8. Create response areas (deferred, not scheduled)

The built set has no response areas, so an imported part has no answer box. A model call
per part could propose them: the kind of box, the text before it, the answer in the
platform's machine form, and the correct options for a multiple-choice part. in2lambda's
`ResponseArea` class writes all three kinds. The ME2 export holds 13 response areas to
measure against.
