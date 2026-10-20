# Guardrails: threat model, decisions, evidence

Each guardrail answers one concrete threat. The receipt image is **untrusted input**: it can contain instructions aimed at
the model, text that becomes a spreadsheet formula, or nothing readable at all. The model's answer is also untrusted until
code has checked it.

Evidence labels used below: **live** = observed in the real runs (451 model calls); **stress** = the synthetic stress set;
**unit** = unit test only, never triggered by real data. Numbers come from [../eval/REPORT.md](../eval/REPORT.md).

## Summary

| # | Threat | Control | Code | Evidence |
|---|---|---|---|---|
| 1 | Corrupt, empty, non-image or huge file | type allow-list, 25 MB size cap, 60 M pixel cap, decode check, all before any paid call; bad file becomes an `ERROR` row | `guardrails.check_input` | unit |
| 2 | Prompt injection printed on the receipt | system prompt marks the document as data; model must set `suspicious_instructions`; regex over document text; a hit forces `REVIEW` and caps confidence at 0.40 | `prompts.py`, `guardrails.injection_flags` | stress 4/4 configs; live false positives found and fixed |
| 3 | A model session that obeys injected text and acts | `claude-code` backend runs read-only: `--tools Read`, MCP servers, skills and user settings off, isolated temp directory, no stdin | `claude_code_provider.py` | unit (command line asserted) |
| 4 | Invented numbers | the extracted total must appear verbatim in the independent Pass B transcript, else `REVIEW`, cap 0.40 | `guardrails.grounding_flags` | live: fired once on a genuinely wrong total (Haiku 4.5) |
| 5 | Guessing when information is missing | prompt forbids inferring; null is allowed; missing total fails `has_total` and caps confidence at 0.30; unverifiable arithmetic caps at 0.70 | `prompts.py`, `validate.py`, `score.py` | stress: 4/4 configs returned `total=null` for a cropped and a covered total |
| 6 | Unreadable image produces confident garbage | both passes must find something; otherwise `UNREADABLE`, confidence 0, no values | `pipeline._unreadable` | stress: blank and near-illegible images, 4/4 configs |
| 7 | CSV formula injection (`=HYPERLINK(...)` in a vendor name) | text cells starting with `= + - @` are prefixed with `'`; control characters stripped; length capped at 400 | `guardrails.sanitize_cell`, `output.py` | **unit only** (see residual risks) |
| 8 | Runaway spend or a dead backend | `--max-cost` counts only money spent now (cache replays count 0); circuit breaker after N consecutive failures; remaining rows are `SKIPPED` with a reason | `guardrails.Guard` | unit; the skip path was also hit by accident during development and behaved correctly |
| 9 | The same receipt counted twice | byte-identical duplicates are flagged `REVIEW`, not dropped | `guardrails.find_duplicates` | live: the two Lightroom credit notes are byte-identical (md5 match) |
| 10 | One agreeing-but-wrong reading marked OK | an OK row needs two independent passes that agree, passing arithmetic, and confidence >= 0.85; single-pass rows can never be OK | `score.py` | live: see decision 4 below |
| 11 | Day and month silently swapped on an ambiguous date (15 of 45 sample receipts print one) | `date_matches_raw` re-derives the date from the printed text with the day/month/year rule and fails on a contradiction | `validate.parse_printed_date`, `validate.validate` | live: 442 of 443 extractions exact, 1 correct abstention, 0 contradictions (never fired on real data); unit tests include the swap |

## Decisions and the reasoning behind them

1. **Flag, do not block, on a suspected injection.** A false positive costs one human look; a false negative costs a wrong
   number in a ledger. So a tripped guardrail forces REVIEW and caps confidence at 0.40, and it keeps the extracted values
   so the reviewer can see what the model read. In the stress test the real total (4.90) survived the injection in all
   four configurations, so the flag is a second line of defence, not the only one.
2. **Two detectors, because either can fail.** The model's own flag depends on the model cooperating; the regex depends on
   English wording. Neither is trusted alone.
3. **Scan only text that came from the document.** The first version also scanned the model's `notes` field and flagged clean
   receipts because the model wrote "I set the currency to MYR". Found in the first evaluation, fixed, covered by a test.
4. **Same-model second passes are not independence.** Both Haiku passes misread a year as 2016 and agreed, so the row
   shipped as OK at 0.97 confidence. A different model for Pass B flagged it. This is why the architecture lets Pass B use a
   different backend and why the grounding check uses the Pass B transcript. Details: [DECISIONS.md](DECISIONS.md#q4-same-model-or-a-different-model-for-pass-b).
5. **Read-only model session.** If the image convinces the model to run a command, there must be no command to run. The Read
   tool is the only one allowed. This control exists for the `claude-code` backend; the API and OpenAI-compatible backends
   give the model no tools at all.
6. **A missing currency forces review.** The brief asks for a currency per row, so a blank cell under an "OK" status would
   contradict itself. This cost Haiku 5.5 six review rows (it returned `null` where Sonnet inferred MYR from the address
   and GST); it is a model-quality effect, not a rule problem.
7. **Cap spend on new money only.** The first version counted cached replays as spend, so a cheap re-run from cache was
   blocked after 6 rows and 39 were SKIPPED. Skipped rows were visible with a reason (nothing silently dropped), but the
   accounting was wrong. Fixed and tested.
8. **Sort flagged rows first.** The only flagged row in the final output had confidence 0.98 (a duplicate), so sorting by
   confidence alone buried it at the bottom under 44 OK rows. Rows needing a human now sort ahead of OK rows.

## Considered and not done

- **Dropping duplicates automatically:** a duplicate file is not necessarily a duplicate purchase; a human should decide.
- **Making the model-reported injection flag authoritative:** it fired on one clean receipt (Oldtown's printed "THIS IS NOT A
  RECEIPT" marking) in one configuration, so it is a reason to look, not proof of an attack.
- **Redacting card digits and names before sending images to the model:** not implemented. See privacy below.

## Residual risks (what these guardrails do not cover)

- **Only one injection style was tested** (a loud block of text appended to a real receipt). Subtle, adaptive or multilingual
  injections were not tried, and the regex is English-only.
- **The CSV formula control is covered by a unit test, not by a real run.** In the stress case no model copied the formula
  text into the vendor field, so the sanitiser never had anything to neutralise.
- **The date check enforces the stated rule, not reality.** `date_matches_raw` catches a model that contradicts the
  day/month/year rule. A genuinely month-first receipt (for example US) would be read consistently under the rule and pass.
  See [DATA_FORMAT.md](DATA_FORMAT.md#what-this-does-not-cover).
- **Grounding needs a transcript.** Single-pass configurations have none, so check 4 does not apply to them (their rows are
  never OK anyway).
- **Privacy.** Receipt images are sent to the model provider, and they can contain names, phone numbers and partial card
  numbers. Raw answers (including transcripts) are cached in `out/cache/`; the repository's `.gitignore` excludes it, and
  you should not publish it. No redaction step exists.
- **Spend and breaker limits were never tripped by real traffic**, only by unit tests.
