# Metrics: what is measured, how, and how far to trust it

Full tables for all 10 configurations are generated into [../eval/REPORT.md](../eval/REPORT.md) by `eval/make_report.py`
from `eval/metrics_v2.json` (after fixes) and `eval/metrics_v1_frozen.json` (before any fix).

## Ground truth

There was no label file, so all 45 receipts were labelled by reading each image (drafted with Claude) (`eval/labels.json`, built by
`eval/labels_part*.py`). Rules, fixed before any model ran:

- Labelled **before** model output was seen. One exception is recorded in that label's own note (`X51005200931.jpg`).
- Cash-rounding receipts accept both defensible totals (`total_alts`); an unreadable day digit accepts both readings
  (`date_alts`).
- A receipt that prints gross items plus separate negative discount lines is judged by the item **sum**, not the exact list.
- One cropped receipt (`X51005447844.jpg`) has `items_uncertain` and its item list is not scored.
- Every label was checked by code: each receipt's items reconcile to its printed total, subtotal or tax.

**Audit status.** I reviewed a **random sample** of the labels and found no errors. The sample size was
not recorded, and the remaining labels were not individually reviewed by a human.

**Limits.** The labeller is a Claude model, the same family as the systems being scored, so agreement may be inflated even
after the sample audit. All receipts are Malaysian GST receipts. Label dates are ISO `YYYY-MM-DD`, produced with the
day/month/year rule in [DATA_FORMAT.md](DATA_FORMAT.md).

## Per-row correctness

A row is **correct** when all of these match the label:

| Field | Rule |
|---|---|
| vendor | normalised fuzzy match >= 0.8 against the label or its alternates, or one name contained in the other (min. 5 characters) |
| date | exact ISO date (or an accepted alternate), read with the day/month/year rule |
| total | within 0.005 of the label or an accepted alternate |
| line items | sum of priced items within 0.05 of the label's sum (unpriced lines add nothing) |

Reported separately, not part of "correct": **items exact** (same multiset of amounts), **tax**, and **currency** as
correct / abstained (`null`) / wrong, because a receipt that prints no currency symbol can reasonably get `null`.

## Trust metrics (the ones that decide the design)

| Metric | Definition | Why it matters |
|---|---|---|
| **Silent errors** | wrong rows with status `OK` | the dangerous failure: a wrong number a human will not look at |
| Error catch rate | share of wrong rows that were flagged | does the flagging work |
| False alarm rate | share of correct rows flagged anyway (also shown excluding the intentional duplicate flag) | wasted review time |
| OK-row precision | share of `OK` rows that are correct | can you trust the green rows |
| Flag rate | share of all rows flagged | size of the review queue |
| AUROC | chance a correct row has higher confidence than a wrong one; 0.5 is a coin flip | is the confidence number worth sorting by |
| Calibration buckets | accuracy within confidence ranges 0.90-1.00, 0.80-0.90, 0.60-0.80, 0.00-0.60 | does 0.9 mean "usually right" |
| Signal ablation | for each wrong row, which independent signal caught it alone | is the second pass worth its cost |
| Cost, latency | list-price `total_cost_usd` and wall time summed per receipt | what it costs to run |

Single-pass rows can never be `OK` by design, so their false-alarm rate is 100% and silent errors are 0 trivially. Read
those rows for accuracy and cost only.

## Headline results (v2, n = 45)

| Configuration | Rows correct | OK / flagged | Silent errors | $ per receipt | Mean latency |
|---|---|---|---|---|---|
| **Sonnet 5.5, two passes (shipped)** | **100% (45/45)** | 44 / 1 | **0** | $0.094 | 21.8 s |
| Opus 5.5, two passes | 100% | 43 / 2 | 0 | $0.096 | 25.4 s |
| Sonnet-A + Haiku 5.5-B | 100% | 42 / 3 | 0 | $0.136 | 26.2 s |
| Haiku 5.5-A + Sonnet-B | 98% | 41 / 4 | 0 | $0.136 | 25.2 s |
| Haiku 5.5, two passes | 98% | 38 / 7 | 1 | $0.167 | 27.0 s |
| Haiku 4.5, two passes | 82% | 36 / 9 | 4 | $0.136 | 155.6 s |
| Sonnet 5.5, single pass | 100% | 0 / 45 (by design) | n/a | $0.041 | 9.4 s |
| Haiku 4.5, single pass | 80% | 0 / 45 (by design) | n/a | $0.053 | 59.5 s |

(The one flagged row in the shipped configuration is the intentional duplicate-file flag.) Opus and Haiku single-pass rows
are in the full report.

## v1 versus v2

v1 is the out-of-the-box result. v2 includes four fixes I made **after reading v1's errors on these same 45 receipts**:
the injection regex no longer scans the model's own notes; the validator accepts either tax reading; unpriced line items no
longer break a sum that matches; the scorer ignores unpriced lines. Model calls were not repeated (only a handful of extra
re-reads). Treat **v2 as optimistic** and v1 as the clean baseline. The v1-to-v2 changes that matter:

| Configuration | Rows correct v1 -> v2 | Flagged v1 -> v2 |
|---|---|---|
| Sonnet 5.5, two passes | 100% -> 100% | 2 -> 1 |
| Opus 5.5, two passes | 98% -> 100% | 4 -> 2 |
| Haiku 5.5, two passes | 96% -> 98% | 10 -> 7 |
| Haiku 4.5, two passes | 78% -> 82% | 10 -> 9 |

## Stress and guardrail results

7 synthetic cases built from one real receipt, run on 4 configurations (28 checks, 28 passed):

| Case | Expectation | Result |
|---|---|---|
| near-illegible (blur + noise) | not OK | `UNREADABLE`, no values (4/4) |
| blank noisy page | `UNREADABLE`, nothing invented | `UNREADABLE`, no values (4/4) |
| total cropped off | not OK | `REVIEW`, `total=null` (4/4) |
| total covered by a patch | not OK | `REVIEW`, `total=null` (4/4) |
| prompt injection on the page | real total survives, flagged | total 4.90 held, `REVIEW` (4/4) |
| rotated 90 degrees | read correctly | read correctly (4/4; single pass is REVIEW by design) |
| formula text in the merchant line | no unsafe CSV cell | passed, but the formula never reached the CSV, so this did not exercise the sanitiser |

## What these numbers cannot tell you

- **0 errors in 45 is not 0% error.** The 95% upper bound on the true error rate is about **6.4%**.
- The shipped configuration made no errors, so its **error-catching ability is unmeasured**. The evidence that the detector
  works comes from the weaker models and from the stress set.
- The calibration of confidence is only measurable where wrong rows exist. Where it is measurable it is good with different
  models for the two passes (AUROC 0.99) and bad with the same weak model twice (0.26).
- One country, one tax regime, one labeller, one receipt behind all stress cases.
- Run-to-run variability of live models was not measured (answers are cached).
