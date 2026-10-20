# Receipt extraction agent that knows when it might be wrong

[![tests](https://github.com/xx-VIRUS-xx/receipt-extraction-agent/actions/workflows/tests.yml/badge.svg)](https://github.com/xx-VIRUS-xx/receipt-extraction-agent/actions/workflows/tests.yml)

Extracts vendor, date, line items, subtotal, tax, total and currency from receipt images, **checks its own output with
plain code**, scores confidence per row, and writes a CSV a human can open, sort, and review only where needed.

> The goal is not perfect extraction. It is an agent that **knows when it might be wrong and says so**.

![Architecture](docs/img/architecture.png)

## Results at a glance

45 real receipts, 10 model configurations, scored against labels drafted by Claude and sample-audited by me (caveats below).

| Configuration | Rows fully correct | OK / flagged | Wrong rows marked OK | Cost per receipt | Time per receipt |
|---|---|---|---|---|---|
| **Sonnet 5.5, two passes (shipped)** | **45 / 45** | 44 / 1 | **0** | $0.094 | 22 s |
| Opus 5.5, two passes | 45 / 45 | 43 / 2 | 0 | $0.096 | 25 s |
| Haiku 5.5 + Sonnet 5.5 (different model checks) | 44 / 45 | 41 / 4 | 0 | $0.136 | 25 s |
| Haiku 5.5, two passes (same model twice) | 44 / 45 | 38 / 7 | **1** | $0.167 | 27 s |
| Haiku 4.5, two passes | 37 / 45 | 36 / 9 | **4** | $0.136 | 156 s |
| Sonnet 5.5, single pass | 45 / 45 | 0 / 45 (never OK by design) | n/a | $0.041 | 9 s |

"Fully correct" = vendor, date, total and line-item sum all match. Cost is Claude Code list price.
Stress tests (illegible, blank, cropped, covered total, prompt injection, rotated): **28 of 28 passed**.

**Three honest findings, because they matter more than the headline:**
1. **A second pass by the same model repeats its own mistake.** Both Haiku passes misread a year as 2016 (printed 2018), agreed,
   and the row shipped as OK at 0.97 confidence. Using a *different* model for the second pass flagged it. This is the
   strongest evidence for the confidence design, because it is where the scoring logic met a real error.
2. **Model choice dominates.** Haiku 4.5 gets 82% right; Sonnet and Opus get 100%.
3. **For a strong model, the second pass adds no measured accuracy** (single-pass Sonnet is also 45/45 at 44% of the cost).
   It buys a trustworthy OK/REVIEW signal, but with zero errors in 45 receipts its error-catching is unproven here, and the
   true error rate can still be as high as about 6.4% (one-sided 95% bound).

Everything is traced to a table: [eval/REPORT.md](eval/REPORT.md) and [docs/DECISIONS.md](docs/DECISIONS.md).

## How it works

1. **Guard the input.** Reject corrupt, oversized or non-image files; flag byte-identical duplicates.
2. **Read twice.** Two independent readings with different prompts; the second transcribes every line first.
3. **Check in code, not with a model.** Do line items sum to the subtotal? Does subtotal + tax equal the total? Is the date
   real? Does the currency match the printed symbol? Each check is PASS, FAIL or SKIP. A missing field is SKIP, never a pass.
4. **Re-read once** only where something failed or the passes disagree, without letting the model bend numbers to fit.
5. **Score and flag.** Confidence blends agreement, validation and the model's own confidence, with hard caps. Unreadable
   receipts get no values and confidence 0: nothing is guessed.
6. **Write the CSV** with rows needing a human first, each with a plain-English reason.

It is deliberately **not** multi-agent: the steps are fixed and the key check is arithmetic. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the per-receipt flow, components and scoring rules.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
mkdir -p images   # put your receipt images here (.jpg .jpeg .png .webp); none are included
```

Pick one backend:

```bash
# A) Claude Code installed and logged in: no API key needed. This is the default and what the results above used
.venv/bin/python -m receipt_agent            # same as --provider claude-code:claude-sonnet-5-5

# B) Anthropic API (not exercised live in this repo's tests, see "What was not tested")
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python -m receipt_agent --provider anthropic:claude-sonnet-5-5

# C) A local or hosted OpenAI-compatible server (not exercised live, see below)
.venv/bin/python -m receipt_agent --provider ollama:<a-vision-model>
.venv/bin/python -m receipt_agent --provider sglang:<model> --base-url http://host:30000/v1
```

Output: `out/results.csv` (rows needing a human first, then lowest confidence first) and `out/results.json` (every check).
Useful flags: `--provider-b backend:model` (a different model for the second pass), `--single-pass`, `--no-reread`,
`--max-cost 5` (stop starting new receipts after $5 of new spend; `claude-code` backend only, refused elsewhere because
those backends do not report a price), `--workers N`, `--limit N`, `--no-cache`.
A sample of the shipped output is in [results/results.csv](results/results.csv).

### Supported backends

| `--provider` | Server | Default URL |
|---|---|---|
| `claude-code:<model>` | your local Claude Code session (`claude -p`, read-only, no key) | n/a |
| `anthropic:<model>` | Anthropic API | api.anthropic.com |
| `ollama:<model>` | Ollama | http://localhost:11434/v1 |
| `sglang:<model>` | SGLang | http://localhost:30000/v1 |
| `vllm:<model>` / `lmstudio:<model>` / `openai:<model>` | vLLM / LM Studio / OpenAI | :8000 / :1234 / api.openai.com |

Any OpenAI-compatible server works. Mixing is allowed, for example Claude for Pass A and a local model for Pass B.

## Reading the CSV

Filter on **`needs_review`** (`yes`/`no`); the file is already sorted with `yes` rows first, lowest confidence first.
`status` is `OK`, `REVIEW`, `UNREADABLE`, `ERROR` or `SKIPPED`. `review_reasons` is filled only on rows that need a look
and says why (for example `items_vs_total: items sum 0.94 + tax 0.06 + other None vs total 1.0`). On OK rows,
informational messages such as a minor pass disagreement go to `notes` instead. A flagged row can still have high
confidence (a correctly read duplicate file), which is why `needs_review`, not `confidence`, is the column to act on. `checks_failed` and `checks_skipped`
name the checks. `confidence` is a heuristic blend, **not** a probability: see [docs/METRICS.md](docs/METRICS.md).

## Data formats (dates, amounts, currency)

- **Dates are output as ISO `YYYY-MM-DD`**, and the date exactly as printed is kept in a `date_raw` column beside it.
- **Numeric dates are read as DAY/MONTH/YEAR**: `01/02/2018` is 1 February 2018. This is a fixed assumption, not a setting.
  It matters: 15 of the 45 sample receipts print a date that is ambiguous on paper, and 22 of the 38 numeric dates have a
  first number above 12, which can only be day-first. A code check re-derives each date from `date_raw` and flags any
  row where the model swapped day and month. Receipts from month-first locales (for example US) are not supported.
- Amounts are plain numbers with a decimal point and no thousands separator, in the receipt's own currency. Currency is an
  ISO 4217 code.

Full specification, evidence and limits: [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md).

## Guardrails

| Threat | Control |
|---|---|
| Corrupt, huge or non-image files | type, size and pixel limits before any paid call |
| Prompt injection printed on a receipt | document treated as data; two detectors; a hit forces REVIEW; read-only model session |
| Invented numbers | the total must appear in a transcription, from a different pass when one exists |
| Guessing when unreadable or hidden | null is allowed; no total means low confidence; unreadable means no values |
| Spreadsheet formula injection in the CSV | cells starting with `= + - @` are neutralised |
| Runaway spend or a dead backend | spend cap on new money only (refused on backends with no price); circuit breaker; skipped rows stay visible |
| Same receipt twice | byte-identical duplicates flagged |

Decisions, evidence and residual risks: [docs/GUARDRAILS.md](docs/GUARDRAILS.md).

## Tech stack

| Layer | Choice |
|---|---|
| Language | Python: the full test suite passes on 3.11, 3.12, 3.13 (GitHub Actions, Linux) and 3.14.7 (macOS, where the results were produced) |
| Schema and validation | Pydantic v2 |
| Image handling | Pillow |
| HTTP (OpenAI-compatible backends) | httpx |
| Anthropic backend | `anthropic` SDK (imported lazily) |
| Model runtime used for the results | Claude Code CLI 2.1.281 running Claude Sonnet 5.5, Opus 5.5, Haiku 5.5, Haiku 4.5 |
| Tests | pytest: 102 tests, no network, no keys needed |
| Diagrams | Mermaid, rendered with `@mermaid-js/mermaid-cli` |
| Built with | Claude Code (the full prompt log is in [prompts.md](prompts.md)) |

## Tested on, and what was not tested

Tested on a **MacBook Air (M2, 8 GB unified memory), macOS 26.6.2, Python 3.14.7**: 451 live model calls ($24.77 at list
price) through the `claude-code` backend. Full details: [docs/TESTED_ON.md](docs/TESTED_ON.md).

**Not tested against the real thing:**
- **Direct Anthropic API:** no API key was available. Unit-tested request construction only.
- **Ollama, SGLang, vLLM, LM Studio:** tested only against a fake OpenAI-compatible server. This machine has 8 GB of unified
  memory shared with the OS, so a vision-language model was not practical to run locally (a judgement from the memory
  figures, not a measured failure), the only Ollama models installed are text-only, and SGLang targets GPU servers.
  **No accuracy number in this repo applies to a local model**, and smaller models did much worse in my runs.
- **Anything beyond 45 Malaysian GST receipts:** other currencies, languages, multi-page invoices, handwriting.

## Limitations

- Labels were drafted by a Claude model reading the images. I audited a **random sample** of them against the images and
  found no errors (the sample size was not recorded); the rest were not individually reviewed by a human, so agreement may
  still be somewhat inflated.
- The date rule (day/month/year) is a fixed assumption that matches all 45 Malaysian samples and is untested elsewhere.
- Four fixes were made after reading the first results on the same 45 receipts, so the v2 numbers are optimistic; v1
  (the clean baseline) is kept in [eval/metrics_v1_frozen.json](eval/metrics_v1_frozen.json).
- The shipped configuration made no errors, so how well it would *catch* one is unmeasured.
- Receipt images are sent to the model provider and can contain personal data; there is no redaction step.

## Repository layout

```
receipt_agent/        the package: pipeline, providers, validation, scoring, guardrails, evaluation
tests/                102 tests (mock model, fake HTTP server, hand-computed metric checks)
eval/                 labels, stress generator, REPORT.md, metrics_v1_frozen.json, metrics_v2.json
docs/                 ARCHITECTURE, DATA_FORMAT, DECISIONS, GUARDRAILS, METRICS, TESTED_ON, diagram sources and images
results/results.csv   the shipped output (Sonnet 5.5, two passes)
prompts.md            the AI-assisted build log: prompts, decisions, dead ends, bugs
NOTES.md              one-page summary of approach and tradeoffs
images/               put receipts here (not included)
```

## Reproducing the evaluation

The 45 receipts are **not included** (they are real receipts supplied with the exercise, not mine to redistribute), so
`eval/labels.json` only applies if you have those same files. To evaluate on your own receipts, write a labels file in the
same format (`eval/labels_part1.py` shows the fields) and run:

```bash
.venv/bin/python -m receipt_agent.evaluate --list
.venv/bin/python -m receipt_agent.evaluate --configs dual:claude-sonnet-5-5 --labels my_labels.json --images my_images
.venv/bin/python eval/make_report.py        # renders eval/REPORT.md from eval/metrics_v2.json
.venv/bin/python -m pytest -q               # runs without images, keys or network
```

`eval/out/` (git-ignored) holds raw model answers, which contain receipt text such as card digits and names. Do not publish it.

## License

MIT, see [LICENSE](LICENSE).
