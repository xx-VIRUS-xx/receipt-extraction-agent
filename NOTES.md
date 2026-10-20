# NOTES

**Approach.** A vision model reads each receipt twice with different prompts (Pass A extraction; Pass B transcription
first). Deterministic code then checks the arithmetic (items vs subtotal vs tax vs total, date, currency). Disputed or
failing fields get one targeted re-read. Confidence blends pass agreement, validation pass-rate and the model's own
field confidence, with hard caps. Output is `results.csv`, lowest confidence first, with a plain-English reason per flag.
The model is pluggable (`claude-code`, `anthropic`, or any OpenAI-compatible server: Ollama, SGLang, vLLM, LM Studio).

**Evidence.** I hand-labelled all 45 images and compared 10 model configurations (`eval/REPORT.md`, reasoning in
`docs/DECISIONS.md`). Chosen: Sonnet 5.5 two-pass, 45/45 rows correct, 1 flag (an intended duplicate-file flag),
$0.094 and 22 s per receipt. Weaker models make same-model second passes agree on the same mistake (Haiku 4.5: 4 wrong
rows marked OK); a different-model Pass B flagged them.

**Assumptions.** Dates are day/month. Totals with cash rounding accept either figure. Receipts printing only `$` are
read in context (all samples are Malaysian, GST 6%). Prices are tax-inclusive or tax-added; either arithmetic is accepted.

**Tradeoffs for the 2-hour budget.** CSV only, no review UI. No OCR baseline. One re-read, not a loop.

**Production, differently.** Human-audit the labels (I produced them, and I am a Claude model, so agreement may be
inflated); test other currencies and languages; run a different-model Pass B; sample-audit OK rows forever. 0 errors in 45
still allows up to a ~6% true error rate (95% bound). Fixes after v1 were made on the same data, so v1 is the clean number.
