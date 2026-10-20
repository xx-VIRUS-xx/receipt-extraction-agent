# NOTES

**Approach.** A vision model reads each receipt twice with different prompts (Pass A extracts; Pass B transcribes
first). Plain code checks the arithmetic, date and currency; a missing field is SKIP, never a pass. Failing or disputed
fields get one re-read that may not bend numbers to fit. Confidence blends pass agreement, validation and the model's
own confidence, with hard caps. The CSV lists `needs_review = yes` rows first, each with a reason.

**Evidence.** 10 model configurations scored on all 45 receipts (`eval/REPORT.md`). Key finding: one weak model
running both passes agrees with its own misread (Haiku read 2018 as 2016 twice, shipped OK at 0.97); a different model
on Pass B flagged it. Shipped: Sonnet 5.5 two-pass, 45/45 correct, 1 flag (a duplicate file), $0.094/receipt. With
zero errors its error-catching is unproven; 0/45 still allows up to ~6.4% true errors (one-sided 95%).

**Time and scope.** About 1 hour building, then about 3 hours on the multi-model evaluation, mostly waiting on runs.
Over budget on purpose: I could not claim calibration without ground truth. Cut: review UI, OCR baseline, re-read loop.

**Assumptions.** Numeric dates are day/month/year, output as ISO with the printed text kept in `date_raw` and a code
check for swaps (`docs/DATA_FORMAT.md`). Cash-rounded totals accept either figure. `$`-only receipts read in context
(all samples Malaysian, GST 6%). Tax-inclusive and tax-added prices both accepted.

**For production.** Labels audited in full (Claude drafted them; I audited a random sample, no errors). Other
currencies, languages, month-first dates, multi-page invoices. Different model for Pass B. Ongoing sample audits of OK
rows. Price the API backends so the spend cap works there. v2 fixes were made on the same 45 receipts, so
`metrics_v1_frozen.json` is the clean number.
