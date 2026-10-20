# Data formats and assumptions

What the agent outputs, what it assumes about the receipts it reads, and how each assumption is enforced and measured.

## Dates

**Output format: ISO 8601 calendar date, `YYYY-MM-DD`** (for example `2018-02-01`). This is what the `date` column of
`results.csv`, the `date` field of `results.json`, and the `date` field of the labels (`eval/labels.json`) contain.
The time of day is not kept. The date exactly as printed on the receipt is kept alongside it as **`date_raw`** (CSV and
JSON), so a reviewer can see what the receipt said next to what the agent concluded:

| `date_raw` (as printed) | `date` (output) |
|---|---|
| `02/01/2019 2:47:14 PM` | `2019-01-02` |
| `25/03/18` | `2018-03-25` |
| `25 Mar 2018 18:26` | `2018-03-25` |
| `2?/04/2017` (a digit is unreadable) | *(empty)*, and the row is flagged |

### The reading rule: day / month / year

Numeric dates on receipts are read as **DAY/MONTH/YEAR**. `01/02/2018` means **1 February 2018**, not 2 January.

| Printed form | Interpretation |
|---|---|
| `dd/mm/yyyy`, `d-m-yy`, `dd.mm.yy` (`/`, `-` or `.`) | day, month, year |
| four-digit year first (`2018-03-20`, `2018/3/5`) | year, month, day |
| month name in either order (`22 Mar 18`, `Mar 22, 2018`) | unambiguous: the name decides |
| two-digit year (`18`) | `20` + year, so `2018` |
| anything unreadable (`2?/04/2017`) | no date; never a guess |

### Why this has to be stated

A numeric date whose first two numbers are both 12 or less is ambiguous on paper, and reading it the wrong way gives a
plausible, valid, **wrong** date that nothing else would flag. In the 45 sample receipts:

- 38 print a numeric date and 7 print a month name.
- **15 of the 45 receipts (33%) print a numeric date that is ambiguous on paper** (for example `01/02/2018`).
- Across all 443 model extractions that carried a date, 166 of 390 numeric ones (43%) were ambiguous.

### Why the rule is right for this data

- **22 of the 38 numeric dates have a first number above 12** (`20/11/2017`, `25/03/18`, `18/01/2018`). Those can only be
  day-first.
- **None has a second number above 12**, which is what a month-first receipt would show.

So the sample is consistent with day-first and contains no evidence of month-first.

### How the rule is enforced (not only requested)

1. **The prompt** tells the model the rule and asks for both `date` (ISO) and `date_raw` (as printed).
2. **A deterministic check, `date_matches_raw`**, re-derives the date from `date_raw` using the rule above and compares it
   with the ISO date the model returned. A confident contradiction (a swapped day and month) fails the check and sends the
   row to review, with a message such as `printed '01/02/2018' reads as 2018-02-01 (day/month/year) but the model returned
   2018-01-02`. If the printed text cannot be read by the rule, the check is skipped, never failed.
3. **`date_plausible`** rejects dates in the future and before the year 2000.

Measured on the real data: of the 443 model extractions that carried a printed date, **442 matched the rule exactly, 1
correctly returned no date (the unreadable `2?/04/2017`), and 0 contradicted it**. `date_matches_raw` passed on all 45
rows of the shipped output. Unit tests cover the swap case, month names in both
orders, year-first dates, two-digit years, dot separators, and unreadable or impossible dates.

### What this does *not* cover

- **The rule is a fixed assumption, not a setting.** A receipt from a month-first locale (for example US `03/04/2018` meaning
  4 March) would be read as 3 April, and `date_matches_raw` would not catch it because the output is consistent with the
  rule. All 45 samples are Malaysian. To support another locale, change the rule in `receipt_agent/prompts.py` and in
  `parse_printed_date` in `receipt_agent/validate.py` together; there is no command-line flag for it.
- A two-digit year is always read as 20YY. A receipt from before 2000 printed as `99` would read as 2099, fail
  `date_plausible` (future date), and be flagged.
- Only the date is kept. Time of day, time zone and the "billing date versus print date" distinction are not modelled.

## Amounts and currency

- Amounts are plain numbers with a **decimal point**, **no thousands separator**, in the receipt's own currency
  (`1234.50`, not `1,234.50` or `RM1234.50`).
- A line item's amount is the **line total after quantity**. Discounts are **negative** line items.
- `subtotal` is only filled if the receipt prints one; `tax` is the total tax printed; `other_charges` is a service
  charge, tip, shipping or rounding adjustment. `total` is the amount due, not the cash tendered.
- Arithmetic checks allow a tolerance of **0.05** in currency units, which absorbs cash rounding to the nearest 0.05. Where a
  receipt prints both a pre-rounding and a rounded total, either is accepted when scoring against labels.
- **Currency is an ISO 4217 code** (`MYR`). It is inferred from a printed symbol or from context (`RM` means `MYR`). A bare
  `$` is ambiguous and is resolved from context such as the address and tax regime, or left empty. If it is empty the row is
  flagged, because the output requires one. The symbol as printed is kept in `currency_symbol_raw` (JSON).

## `results.csv`

UTF-8 **with a byte-order mark** so Excel opens non-ASCII names correctly. One row per image, rows needing a human first,
then by ascending confidence.

| Column | Meaning |
|---|---|
| `file` | image file name |
| `status` | `OK`, `REVIEW`, `UNREADABLE`, `ERROR` (input rejected or extraction failed) or `SKIPPED` (spend cap or circuit breaker) |
| `confidence` | 0 to 1, a heuristic blend, **not** a probability |
| `review_reasons` | plain-English reasons, separated by ` \| ` |
| `vendor` | merchant name as printed |
| `date` | ISO `YYYY-MM-DD` (see above) |
| `date_raw` | the date exactly as printed |
| `currency` | ISO 4217 code, or empty |
| `subtotal`, `tax`, `other_charges`, `total` | numbers, empty if not printed |
| `prices_include_tax` | `True`, `False` or empty, the model's reading of the receipt text (informational) |
| `line_item_count`, `line_items` | count, and `description = amount` pairs separated by `;` |
| `checks_failed`, `checks_skipped` | names of validation checks that failed or could not run |
| `pass_agreement` | 0 to 1, field agreement between the independent passes |
| `image_quality` | informational only; carries no weight in confidence |
| `models`, `cost_usd`, `latency_s` | what ran, list-price cost, and seconds |

Text cells that begin with `=`, `+`, `-` or `@` are prefixed with `'` so a spreadsheet cannot treat receipt text as a
formula (see [GUARDRAILS.md](GUARDRAILS.md)). `results.json` carries every check, the full extraction, and the signals.

## Label file (`eval/labels.json`)

One object per image, keyed by file name: `vendor` (+ `vendor_alts`), `date` (ISO, + `date_alts`), `total` (+
`total_alts`), `items` (line amounts), `tax`, `subtotal`, `prices_include_tax`, `currency`, `items_uncertain`, `note`. See
[METRICS.md](METRICS.md) for how rows are scored against it and for the label audit status.
