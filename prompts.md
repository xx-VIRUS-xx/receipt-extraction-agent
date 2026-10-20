# Prompts and AI-tool transcript

**How to read this.** Lines marked **Me:** are my prompts, quoted verbatim (typos included). Everything else is the
agent's work log, written by the agent as it worked and edited only for formatting; its "I" is the agent. Raw session
exports are attached to the submission email alongside this file.

**Tools.** Build: Claude Code (Sonnet 5.5) in VS Code, plus Lucid for the first flowcharts. Independent code review
before submission (section 8): a separate Claude session (Opus 5.5) that had not seen the build.

## How I steered the work

| Stage | What I asked for | Why | What it changed |
|---|---|---|---|
| Brief | confirm understanding before any work | catch misreads early | agent read all 3 pages instead of saying "yes" |
| Design | a flowchart before code, then "does this satisfy every need?" | make the agent critique its own plan | 5 gaps found before a line of code: no calibration, missing-as-pass, retry bending numbers, self-judged legibility, deliverables missing |
| Design | pushed on whether multi-agent is needed | avoid architecture for its own sake | fixed pipeline + independent calls checked by code |
| Build | model-agnostic backends (Ollama, SGLang) | no lock-in; local serving is my day job | one OpenAI-compatible client covers 5 servers |
| Build | a `claude -p` backend | I had no API key; use what I had | cut per-call overhead 17x after measuring it |
| Evidence | "answer any question from outcomes and metrics" | claims must trace to numbers | 10 configs, labels, frozen v1 baseline, stress set |
| Publish | document what was *not* tested and why | honesty over coverage | TESTED_ON.md, untested backends labelled as such |
| Labels | audited a random sample myself; asked for the date format to be specified | the labels and the date rule carry every number | audit recorded as stated; date rule enforced in code (section 7) |
| Review | an independent review against the brief | the builder grades its own work too kindly | 3 real bugs found and fixed (section 8) |

Section numbers below follow the order the work happened.

## 1. Understanding the brief
**Me:** "Ingest the assignment brief in S4.pdf. Extract the core requirements, operational constraints, and deliverables, then synthesize an executive summary of your understanding before writing any code."
- Claude first looked for the PDF (none was named), found `S4.pdf`, read all 3 pages and summarised the brief.
- Note: Claude correctly refused to say a bare "yes" before reading anything.

## 2. Flowchart before code
**Me:** "Inspect the dataset in images/. Generate a clear pipeline architecture flowchart detailing ingestion, schema validation, confidence scoring, flagging criteria, and output formatting.Use: Lucid MCP"
- Claude counted 45 images and built flowchart v1 in Lucid (extract, validate, score, flag, CSV, review).

**Me:** "Perform a rigorous failure-mode analysis on this proposed v1 architecture. Identify unhandled edge cases, arithmetic validation blind spots, confidence calibration risks, and missing deliverables."
- Claude's critique of its own v1. Gaps found:
  1. Confidence was never checked against ground truth (calibration).
  2. Validation treated missing fields as a pass; it needs a "can't verify" state.
  3. Retry skipped re-validation, and could make the model bend numbers to fit the arithmetic.
  4. "Readable?" relied on the model judging itself.
  5. Submission deliverables (README, video, prompts, NOTES) were not in the plan.

**Me:** "Challenge the necessity of a multi-agent setup. Evaluate whether our workflow can be handled more reliably via a deterministic pipeline with targeted independent verification passes."
- Decision: no multi-agent. The workflow is a fixed pipeline and validation is deterministic arithmetic.
  We use several independent LLM calls (Pass A, Pass B, targeted re-read) checked by code instead.
- Flowchart v2 built in Lucid with two passes, a re-validation loop and a calibration step.

## 3. Build
**Me:** "Implement the extraction pipeline using Claude as the baseline vision provider, but abstract the client layer to remain model-agnostic. Support standard OpenAI-compatible endpoints (e.g., Ollama, SGLang, vLLM)."
- Environment check: Python 3.14, no `anthropic`/Pillow, no `ANTHROPIC_API_KEY` set, Ollama installed but only
  has text models (`qwen2.5-coder:7b`, `mistral`), so no local vision model yet.
- Looked at one receipt (`X51005230605.jpg`, Petron). Finding: Malaysian GST receipt, prices tax-INCLUSIVE, no
  printed subtotal. A naive "items = subtotal, subtotal + tax = total" validator would mark it wrong.
  Added `prices_include_tax` and `other_charges` to the schema and made the validator handle both modes.
- Design decision: Ollama, SGLang, vLLM and LM Studio all speak the OpenAI `/v1/chat/completions` protocol, so
  two backends (Anthropic native + OpenAI-compatible with presets) cover them all.

### Build log (what was built, in order)
1. `schema.py` / `prompts.py`: one Pydantic schema shared by every backend; prompts forbid guessing and
   forbid "fixing" numbers to satisfy arithmetic. Pass B uses a different prompt (transcribe first) so the two
   passes are not identical.
2. `providers/`: `anthropic` (forced tool call for structured output) and an OpenAI-compatible client used for
   ollama / sglang / vllm / lmstudio / openai via presets. If a server rejects `response_format: json_schema`
   it falls back to prompting for JSON and parsing leniently.
3. `validate.py`: pure functions returning PASS / FAIL / SKIP. SKIP (missing field) is never counted as a pass.
4. `compare.py`, `score.py`, `pipeline.py`, `output.py`, `cli.py`.

### Dead end: image-quality score
Idea: use blur/contrast statistics from Pillow as a confidence input and to detect the near-illegible receipt.
Measured it on all 45 images: scores only span 0.78 to 1.00. The lowest-scoring image (`X51005442388.jpg`)
is perfectly legible; it is a small receipt on a large white page, so "contrast" just measured whitespace.
Result: kept as an informational CSV column, weight 0 in confidence. Legibility comes from the model's output instead.

### Bug caught in review of my own test
`test_single_pass_confidence_is_capped` had `or True` in its assertion, so it could never fail. Fixing it showed
the code only capped single-pass confidence when Pass B *failed*, not when `--single-pass` was chosen, which
contradicted the CLI help. Fixed the code (cap in both cases) and made the test real.

### Not yet done / not yet verified
- No live model run yet (`ANTHROPIC_API_KEY` not set in the build environment); the pipeline is tested against
  a scripted mock and a fake OpenAI-compatible server only.
- No local vision model installed in Ollama (only `qwen2.5-coder:7b`, `mistral`), so the Ollama path is untested live.
- Confidence calibration against hand-labelled receipts has not been done.

## 4. Add a `claude -p` backend
**Me:** "Implement an alternative provider utilizing local 'claude -p' headless sessions. Ensure cost-reduction flags are profiled and tool permissions are sandboxed strictly to read-only."
- Checked `claude --help` on the installed CLI (2.1.281) for print-mode flags, then probed a real call with one receipt.
- Finding 1: `--json-schema` + `--output-format json` returns a clean `structured_output` field.
- Finding 2 (cost): the first probe cost $0.34 list price because a default session carries ~38k tokens of
  MCP/skills overhead; changing `--model` and `--system-prompt` did not help (still $0.31, no cache hit).
  Adding `--strict-mcp-config --disable-slash-commands --setting-sources ""` cut it to $0.019 (17x), ~4 s/call.
- Safety: the receipt image is untrusted input, so the session gets `--tools Read` only (no shell, no write).
- Tests: subprocess is faked, covering the locked-down flags, retry-once, timeout, error reply, missing binary.
- First real end-to-end run, 2 receipts, 33 s, both OK. Spot-checked one against the image: vendor, date
  (DD/MM assumption correct), total and items right. The two passes disagreed on currency (no symbol printed;
  one pass inferred MYR from the address), and the row was flagged as a minor disagreement, which is the
  behaviour we want.

## 5. Compare models, metrics, guardrails
**Me:** "Establish an evaluation harness comparing model configurations across latency, cost, and extraction accuracy. Implement runtime guardrails (file integrity, injection defense, formula sanitization) backed by measurable metrics."

### Decisions made before writing code
- Metrics need ground truth and none was provided, so I hand-labelled all 45 images by reading them.
  Rules I set up front so labels could not be bent to fit model output:
  - Labelled BEFORE running any model. One exception, recorded in the label's own `note`: for `X51005200931.jpg`
    I had already seen a model's output (from the earlier 2-image smoke test).
  - Rounding cases carry both defensible totals (`total_alts`). Ambiguous dates carry `date_alts`.
  - Discounts: receipts that print gross item + separate negative discount are judged by item SUM, not exact list.
  - One receipt (`X51005447844.jpg`, right edge cropped) is marked `items_uncertain`; its items are not scored.
- Honest limitation: the labeller is a Claude model, the same family as the systems being scored. That can inflate
  agreement. Mitigation: the labels are arithmetically self-consistent (every receipt's items reconcile to its
  total or tax, checked in code), but a human should audit a sample.
- Labelling found things the 2-image smoke test could not:
  1. Two input files are byte-identical (`X51005268275.jpg` = `X51005301666.jpg`, confirmed by md5). Added duplicate detection.
  2. Several receipts print a tax-EXCLUSIVE "subtotal" beside tax-INCLUSIVE line items (F&P Pharmacy, Yong Tat,
     Sin Lianhap). My validator would have raised false alarms on correct extractions. Fixed and tested with the real numbers.
  3. None of the 45 real images is truly near-illegible (the brief says to expect one or two). So I built a
     synthetic stress set (blur+noise, blank page, sideways, cropped, total covered, prompt injection, CSV formula).
  4. All receipts are Malaysian (GST 6%, RM). Several print `$` instead of RM, which is ambiguous.

### Guardrails, each tied to a threat (code: `receipt_agent/guardrails.py`, tests: `tests/test_guardrails.py`)
| Threat | Guardrail |
|---|---|
| Corrupt / huge / non-image file | type, size, pixel-count limits; reject before any paid call |
| Prompt injection printed on a receipt | system prompt marks the document as untrusted data; model-reported flag; regex flag; model session is read-only (`--tools Read`); a tripped flag forces REVIEW and caps confidence at 0.40 |
| Invented numbers | the total must appear verbatim in the independent Pass B transcription |
| Spreadsheet formula injection via vendor text (`=HYPERLINK(...)`) | CSV cells starting with `= + - @` are prefixed with `'`; control chars stripped; length capped |
| Runaway spend / dead backend | `--max-cost` cap; circuit breaker after N consecutive failures; remaining rows become SKIPPED, never silently dropped |
| Same receipt submitted twice | byte-identical duplicates flagged for review |

### Bug in my own test code
`test_aggregate_hand_computed` contained a tautological assertion (a value compared with itself) next to the real
hand-computed check. Removed it. The real check is auroc = 4/6, worked out by hand in the comment.

### 5b. Running the comparison (what actually happened)
- Ran 10 configurations (4 models single-pass, 4 two-pass, 2 mixed) over the 45 images: ~400 real model calls through
  `claude -p`. Throughput was ~8 calls/min (40-60 s per call with 6 in parallel), so the full matrix took about an hour.
- **v1 results (frozen in `eval/metrics_v1_frozen.json`)** were read before any fix. Error analysis found:
  1. Haiku 4.5 and Haiku 5.5, used for both passes, agreed on a misread year (2016 vs 2018) and the row went out OK at 0.97.
  2. My injection regex flagged clean receipts because it scanned the model's own notes.
  3. My validator and scorer treated an unpriced line ("voucher issued") as an error.
  4. Strict tax-inclusive handling flagged Secure Parking, which prints "incl. GST" but adds GST on top.
- Applied 4 fixes, each with a test built from the real receipt, then re-ran (v2). Stated in the report that v2 is
  optimistic because the fixes were chosen on the same 45 receipts.
- Stress set: 7 synthetic cases x 4 configs = 28 checks, 28 passed. I checked the reasons behind each status instead of
  trusting the PASS: the CSV-formula case did not exercise the sanitizer (no model copied the formula), so that guardrail
  is evidenced by unit tests only. This is recorded in `docs/DECISIONS.md` Q8.
- While writing `docs/DECISIONS.md` I re-checked every number against the data and corrected three statements that were
  wrong (an inverted claim about single-pass flagging, 7 vs 5 `$` receipts, and the cause of 2 extra reviews).
- Not claimed: error-catch ability of the shipped config (it made no errors), OCR comparison, other countries, API pricing.

### 5c. Two more bugs found only by using the output like a reviewer would
1. **Sort order hid the only flag.** Sorting by confidence ascending (as the brief describes the workflow) put the one
   flagged row, a duplicate file at confidence 0.98, at the bottom, under 44 OK rows. Rows needing a human now sort
   first by status, then by confidence. Test added.
2. **The spend cap counted cache replays as new spend.** Regenerating the CSV from cache with a tiny `--max-cost` skipped
   39 of 45 rows. Skipped rows were visible (status SKIPPED, with a reason), so nothing was silently dropped, but the
   accounting was wrong: only money actually spent now should count. Added `spend_usd` (0 on a cache hit) next to
   `cost_usd` (original list price, still used for the per-config cost report). Test added; verified the cache file
   count did not change while regenerating (451 before and after = no new model calls).
- Final state: 76 tests pass. Shipped CSV: `results/results.csv` (config `dual:claude-sonnet-5-5`), 44 OK, 1 REVIEW.

## 6. Publishing as a public repository
**Me:** "Prepare the project for open-source delivery. Structure the documentation to detail architectural decisions, benchmark outcomes, hardware profiling constraints, and rationale for untested environments."

Decisions made while preparing it, and why:
- **System info was measured, not assumed.** `sysctl` / `system_profiler`: MacBook Air (M2, 8 GB unified memory), macOS
  26.6.2. I wrote the Ollama/SGLang explanation from those figures and said plainly that "a 7B vision model is
  impractical" is a judgement from the memory numbers, not a failure I measured: nobody tried to load one.
- **Not published, on purpose:** the exercise brief (`S4.pdf`), the 45 receipt images, and the stress images derived from
  one of them. The brief is the hiring company's material and the receipts are real receipts that were supplied for the
  exercise, so neither is mine to redistribute. Both are in `.gitignore`; `images/README.md` explains how to supply your own.
  Hand labels, results, metrics and all code are included.
- **Privacy of the commits:** the global git identity would put an email address in every public commit, so the repo uses
  GitHub's no-reply address (set for this repo only, global config untouched).
- **Dead end: Lucid for the diagrams.** Importing Mermaid into Lucid produced valid documents (a useful syntax check),
  but the PNG export was a placeholder (a "code block" shape that never rendered). Rendered the same Mermaid sources locally
  with `@mermaid-js/mermaid-cli` instead, so the repo contains source + PNG from one origin.
- **First architecture diagram was confusing:** three provider arrows crossed and looked like a chain. Looked at the PNG,
  restructured around a single provider-interface node, re-rendered, checked again.
- **Docs were checked against data:** link checker for every relative link/image; METRICS and README tables were
  re-derived from `metrics_v2.json` figures (two-pass Sonnet 44 OK / 1 flagged, etc.), not retyped from memory.
- **CI could not be enabled, and the docs say so.** The first push was rejected (`refusing to allow an OAuth App to
  create or update workflow ... without workflow scope`). Fixing that means widening the GitHub token's scope through a
  browser login, which is the account owner's decision, so I did not do it. The workflow ships as `docs/ci/tests.yml`
  (copy to `.github/workflows/`), and README and TESTED_ON state that Python 3.11 to 3.13 are unverified. I had only
  Python 3.14 locally. The repo was created empty before the rejection; the local commit was amended (it had never
  been pushed) before pushing again.

### 6b. CI resolved
After the account owner refreshed the GitHub token (it now has the `workflow` scope, confirmed with `gh auth status`
before acting), I moved the workflow to `.github/workflows/tests.yml` and pushed. First run: **success on Python 3.11,
3.12 and 3.13**, each job logging `76 passed`. Docs were updated only after the result was in, from "unverified" to what
was observed. The run also printed two warnings, not failures: the pinned actions (`checkout@v4`, `setup-python@v5`) run on
a deprecated Node 20 runtime, and `ubuntu-latest` is migrating to Ubuntu 26. Neither has been addressed yet.

## 7. Label audit and the date format
**Me:** "I have spot-checked a random sample of the ground-truth labels. Now, specify a deterministic date-parsing rule (DD/MM/YYYY vs MM/DD/YYYY) and add a verification check against printed raw text to prevent ambiguity." (earlier: "Human Audit of labels is done")
- **Audit recorded as stated, no more:** the docs now say the owner audited a *random sample* and found no errors. The sample
  size was not given, so none is claimed; the remaining labels are described as not individually reviewed. I did not
  write "audited" without the qualifier.
- **Measured the date claim before writing it.** Across 443 cached extractions with a date: 442 matched the day/month/year
  rule exactly, 1 correctly returned no date (`2?/04/2017`, an unreadable digit), 0 contradicted it. My first tally said
  443 of 443 because `None == None` counted the abstention as a match, so I corrected the wording to the three-way split.
- **Why it is load-bearing:** 15 of 45 receipts (and 43% of numeric extractions) print dates that are ambiguous between D/M and M/D.
  22 of 38 numeric sample dates have a first number above 12, so they can only be day-first; none is month-first-only.
- **Three changes, not just a sentence:** (1) a `date_raw` column in the CSV so reviewers see printed vs ISO side by side;
  (2) a deterministic check `date_matches_raw` that re-derives the date from the printed text and fails on a contradiction
  (skips, never fails, when the text is unreadable); (3) `docs/DATA_FORMAT.md` stating format, rule, evidence and limits.
- **Verified no behaviour change on real data:** re-ran all 10 configurations from cache with a $0.01 spend tripwire:
  cache file count unchanged at 451 (zero new model calls) and 0 differences in any metric versus the committed v2 numbers.
  Shipped CSV regenerated the same way: `date_matches_raw` passed on 45/45.
- **Stated limit:** the check enforces the *rule*, not reality. A US-style receipt would be read consistently day-first and
  pass. There is no flag for another locale; it needs a prompt and parser change.
- Tests: 76 -> 93 (17 new: the rule across formats, the swap, and the skip cases).

## 8. Independent review against the brief

A fresh session (Claude Opus 5.5, claude.ai) with no access to the build conversation.

**Me:** "can you check https://github.com/xx-VIRUS-xx/receipt-extraction-agent"
- Cloned the repo, read all source, ran the suite, then wrote a probe script to confirm suspected bugs instead of
  reporting them from reading alone. Three were real:
  1. A schema-invalid reply was cached before validation, so it replayed on every later run (probe: 1 provider call
     across 2 runs).
  2. `--max-cost` was a no-op on the Anthropic and OpenAI-compatible backends, which report cost 0 (probe: a $0.0001
     cap processed every file).
  3. The grounding guard accepted a total of 1.00 because `Qty 1` appeared on the receipt.
- Also flagged: NOTES.md was written in the agent's voice; the 45/45 headline rests on Claude-drafted labels, while the
  same-model finding is the stronger evidence; the CSV's confidence column could hide a flagged row when re-sorted.

**Me:** "Have you read s4.pdf or shall I give it to you?" (then attached it)
- Checked every requirement and evaluation dimension in the brief against the repo. Biggest risk it raised: scope.
  The brief asks for about 2 hours and grades "judgment about scope"; NOTES claimed the 2-hour budget while the
  evaluation clearly took longer.

**Me:** "Remediate the confirmed bugs, update documentation to align with actual project timelines and audit provenance, and outline key talking points for an executive technical walkthrough."
- The agent asked me two questions before touching NOTES.md: how long the work really took, and whether I had checked
  any labels. NOTES.md now states my real time (about 1 hour build, about 3 hours multi-model evaluation).
- The section 7 commit landed upstream while this review was running; the fixes were rebased onto it, not over it.
- Fixes, each with a regression test in `tests/test_review_fixes.py` (102 tests pass):
  validated-only caching; `--max-cost` refused on backends with no price, with latency now recorded for them;
  bare-integer grounding limited to receipts with no decimal amounts, and self-grounding disclosed; a `needs_review`
  column with OK-row messages moved to `notes`; default backend changed to `claude-code`, the only one run live.
- Docs: label provenance made consistent ("drafted by Claude, sample-audited by me"), NOTES and README rewritten in my
  voice, the same-model finding moved first in the README, the error bound stated precisely (6.4%, one-sided 95%), test
  counts updated, and a "fixes after code review (not re-run live)" section added to `docs/DECISIONS.md`.
- Not done, on purpose: no live re-run of the 45 receipts. The grounding change can only add flags, and
  `results/results.csv` was reformatted to the new columns from the same run rather than re-extracted.
