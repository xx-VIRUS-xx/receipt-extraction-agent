# NOTES

**Approach.** A vision model reads each receipt twice with different prompts (Pass A extraction; Pass B transcribes first).
Code then checks the arithmetic, date and currency. Disputed or failing fields get one targeted re-read. Confidence blends
pass agreement, validation pass-rate and the model's own confidence, with hard caps. Output is `results.csv`, rows needing
a human first, each with a plain-English reason. The model is pluggable (Claude Code, Anthropic API, or any
OpenAI-compatible server such as Ollama, SGLang, vLLM).

**Evidence.** I hand-labelled all 45 images and compared 10 configurations (`eval/REPORT.md`, reasoning in
`docs/DECISIONS.md`). Chosen: Sonnet 5.5 two-pass, 45/45 rows correct, 1 flag (an intended duplicate-file flag), $0.094 and
22 s per receipt. A second pass by the *same* model shares its blind spots: two Haiku 5.5 passes agreed on a misread year
and shipped it as OK, and Haiku 4.5 marked 4 wrong rows OK. With Sonnet as the second reader the Haiku 5.5 error was flagged.

**Assumptions.** Numeric dates are read day/month/year and written ISO `YYYY-MM-DD`; the printed text is kept in `date_raw`
and a code check flags a swapped day and month (`docs/DATA_FORMAT.md`). Cash-rounded totals accept either figure. A bare `$`
is read in context (all samples are Malaysian, GST 6%). Either tax arithmetic (inclusive or added) is accepted.

**Tradeoffs (2-hour budget).** CSV only, no review UI. No OCR baseline. One re-read, not a loop.

**Production, differently.** Audit more labels (I made them and I am a Claude model; the owner checked a random sample, no
errors found). Test other currencies, languages and month-first dates. Use a different-model second pass. Sample-audit OK rows
forever. 0 errors in 45 still allows a true error rate up to about 6% (95% bound), and fixes after v1 were made on the same
data, so v1 is the clean number.
