# Comparing OCR output against the page

The design spec leaves open how to catch an OCR misread that still renders: compare the
markdown against the page image with a vision model, or diff against a second OCR
engine. This is the first of those, tried on three documents from ExampleContents. It
is a record of an experiment, not a description of the pipeline — nothing in the
pipeline reads what `compare` prints.

## The method

`in2lambda-agent compare PDF` converts the PDF with Mathpix (or reuses the cached
conversion), renders every page with `pdftoppm` at 150 dpi, and sends the pages and the
whole markdown to the model in one call, asking for a JSON array of differences. The
call goes through `Backend.call`, which now carries images as well as text, so it runs
on whichever backend the settings chose. These three runs used the Claude Code login
through the Agent SDK, which is what this machine has; there is no `ANTHROPIC_API_KEY`
here, and the Agent SDK does pass image blocks through to the API.

A second OCR engine was not tried: the free ones do not read LaTeX at all, and a second
one that does is a new service and a new credential. Two things about the shape of the
call are worth knowing when reading the numbers below:

- All the pages go in one call with the whole markdown. Mathpix returns one markdown
  document with no page boundaries in it, so there is no per-page markdown to pair with
  a page image, and the `page` number on a finding is the model's own attribution.
- Only the rendered images are sent. Nothing gives the model the PDF's text layer, so a
  born-digital document is being read as an image, like a scan.

## What it found

| Document | Pages | Findings | Confirmed | Tokens | Seconds |
| --- | --- | --- | --- | --- | --- |
| `PHYS40002-Mechanics/mechanics_Q_min_working_example.pdf` (born-digital LaTeX) | 1 | 0 | — | 8,208 | 4.2 |
| `MECH4000_ME1_materials/MECH4_Materials_Tutorial sheet solutions/Sheet 8 Solutions.pdf` (scanned handwriting) | 4 | 3 | 3 | 19,474 | 26.0 |
| `PEN_1_Quiz/Quiz_text/…Expectation and Variance FILLED IN QUIZ.pdf` (scanned handwriting) | 2 | 10 | 10 | 16,092 | 36.8 |

Tokens are input plus output as the Agent SDK reports them, cached input included.
Every finding below was checked against the rendered page by eye.

### mechanics_Q_min_working_example.pdf — no findings, and the markdown is right

The one page is LaTeX, and the Mathpix markdown says what it says. A true negative: the
only difference is markdown escaping (`begin\{subsubqlist\}`), which the prompt tells
the model to ignore. A document with a text layer has little for this to find.

### Sheet 8 Solutions.pdf — 3 findings, 3 confirmed

| Finding | Page shows | Verdict |
| --- | --- | --- |
| `$R$ is material constant` | `k is material constant` | Confirmed, but the glyph is genuinely ambiguous in this hand; the equation above it, `\sigma_y = \sigma_i + k/\sqrt{d}`, is what settles it |
| `As $d \downarrow, \sigma_{y} q$` | `As d ↓, σ_y ↑` | Confirmed: an up-arrow read as the letter q |
| `II = >I slip sonstem` | `II = >1 slip system` | Confirmed: the digit 1 read as a capital I |

No false positives. What it missed is the striking part: this markdown holds roughly
two dozen other plainly wrong words — `Hall-Petch Bu.` for `Eqn.`, `yold` for `yield`,
`interent (intruisic)` for `inherent (intrinsic)`, `dijbaction` and `didocation` for
`dislocation`, `Poberystal` for `Polycrystal`, `anulistion` for `annihilation`, and so
on — none of them reported. The three it did report are the three that are about a
symbol or a digit rather than a word. Every equation on the sheet was transcribed
correctly and it flagged none of them falsely.

### …Expectation and Variance FILLED IN QUIZ.pdf — 10 findings, 10 confirmed

| Finding | Page shows | Verdict |
| --- | --- | --- |
| running header absent | `MECH40001 Professional Engineering Skills 1  Probability and Statistics for Engineering` | Confirmed |
| `P(Z<\frac{0-0.120}{0.583})` | `.0583` in the denominator | Confirmed, and the worst of them: it renders, and it is wrong by a factor of ten |
| `= 0 \%` | `2%` | Confirmed: the answer to the question, with its digit changed |
| `This is a 'transition 'fit` | a second line, `some fit and some don't!` | Confirmed |
| line absent | `0.955 = P(Z<\frac{1.6-\mu}{\sigma})` and `\frac{1.6-\mu}{\sigma} = 1.7 ②` | Confirmed: a whole worked step dropped |
| line absent | `Combining ① & ②  \mu = 1.513  \sigma = 0.0513` | Confirmed |
| `\mu=3.026` | `\mu = 3.026 \qquad \sigma = .0725` | Confirmed: σ dropped from the same box |
| `\mid 2.8<x_{1}<x_{2}<3.2` | `2.8 < X_1 + X_2 < 3.2` | Confirmed: a plus read as a second `<` |
| `\frac{P(1.48<x_{1}<1.6)}{…}` | `P(1.4<X_1<1.6 \text{ and } 1.4<X_2<1.6)` | Confirmed: a conjunct dropped from the numerator |
| answer absent | boxed `= 0.894` | Confirmed |

Again no false positives. Again misses, and here they are mathematical: `P(x<1.6)=-.955`
for `.955`, `=-986` for `.9986`, `P(B-AKO)` for `P(B-A<0)`, and a badly garbled integral
in question 2 — four or five more the model did not mention.

## What this says about the open question

**Use the vision model, and use it as a reviewer's aid rather than a gate.**

- It finds the thing nothing else can. Six of the thirteen findings — `0.583` for
  `.0583`, `0%` for `2%`, a `+` read as `<`, and three dropped lines of working — are
  markdown that parses, renders in KaTeX, compiles in pdflatex, and is wrong. No check
  in the pipeline can see any of them.
- Precision was 13 out of 13 over these three documents, which is what makes the output
  worth a human's attention at all.
- Recall is partial, and not evenly so: it reports symbols, digits and missing lines,
  and stays quiet about misread words. So it cannot become a validate check — a clean
  answer does not mean a clean page, and failing a build on it would be failing on a
  sample of the errors rather than on the errors.
- It is not worth running on a document with a text layer. The born-digital sheet
  returned nothing and had nothing to return; the cost there is 8k tokens for a page
  that Mathpix was never going to get wrong. Run it on scans.
- Cost is about 5k tokens and 10 seconds a page, which is of the order of one fixing
  round — affordable per document, not affordable per round.

The second OCR engine is now the less attractive of the two. It would need a new
credential and a new service to answer a question this already answers, and a diff
between two engines reports every disagreement rather than the model's ranked few; the
dropped lines of working, which are the most damaging findings here, are exactly what a
second engine might also drop.

What would follow from this, and is not in this ticket: feeding the findings into sample
review, where a question whose lines a comparison flagged is a question to show the
reviewer first. That, rather than a check, is where the signal seems to belong.
