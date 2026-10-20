# What this was tested on, and what was not (and why)

Nothing here is estimated: the figures come from commands run on the machine below (2026-10-20) and from the
metadata the model CLI recorded for each call.

## The machine

| | |
|---|---|
| Computer | MacBook Air (Mac14,2) |
| Chip | Apple M2, 8 CPU cores (4 performance + 4 efficiency), 8 GPU cores |
| Memory | **8 GB unified** (shared between the OS, apps and the GPU) |
| OS | macOS 26.6.2 (build 25G83) |
| Disk | 228 GB, 23 GB free at test time |
| Python | 3.14.7 |
| Libraries | pydantic 2.14.1, Pillow 13.0.0, httpx 0.28.1, anthropic 1.15.0, pytest 9.1.1 |
| Model runtime used | Claude Code CLI 2.1.281 (`claude -p`) |
| Other installed | Ollama 0.40.6, with only two text-only models pulled (`qwen2.5-coder:7b`, `mistral`) |

## What was exercised for real

| Area | How | Scale |
|---|---|---|
| `claude-code` backend, four models | live calls through `claude -p` | **451 unique model calls**, **$24.77** at list price, about 3.0 hours of summed call time (about 1 hour wall-clock with 6 parallel workers) |
| Models | Claude Sonnet 5.5, Opus 5.5, Haiku 5.5, Haiku 4.5 | all four, single-pass, two-pass and mixed (10 configurations) |
| Data | 45 real receipts + 7 synthetic stress images | scored against labels (Claude-drafted, sample-audited) |
| Unit and integration tests | `pytest`, mock model + fake HTTP server | 102 tests, no network, no keys |

Costs are the list-price `total_cost_usd` that `claude -p` reports per call. They describe this backend, not API
billing, and they depend on how your Claude Code plan accounts for usage.

## What was NOT tested against the real thing, and why

### 1. The direct Anthropic API backend (`anthropic:...`)
**Not run live: no `ANTHROPIC_API_KEY` was available in the test environment.** The code path is covered by unit tests of
the request construction only. Specific unverified assumptions: that `tool_choice` forcing a single tool, the image block
format, and `max_tokens=4096` behave as documented for every model. Per-call price is also not reported on this path
(it records token counts, cost stays 0), because the API does not return a price.

### 2. Ollama, SGLang, vLLM, LM Studio, OpenAI (the `OpenAICompatProvider`)
**Not run against a real server. The code is tested against a fake OpenAI-compatible HTTP server only.** That test
covers the request shape, the fallback when a server rejects `response_format: json_schema`, and parsing of fenced or
chatty JSON replies, for each backend's default URL.

Why no real server:
- **Memory.** The test machine has 8 GB of unified memory shared with macOS. A vision-language model needs the language
  model, a separate image encoder, and room for the context of a tall receipt image. A 7B-class vision model is not
  practical in that budget. I did not try to load one, so this is a judgement from the memory figures, not a measured
  failure.
- **No vision model installed.** The only Ollama models present are text-only, so a real Ollama call could not read an
  image at all.
- **SGLang** is built for GPU servers (NVIDIA/AMD). I did not attempt it on Apple Silicon.

What this means for you: the OpenAI-compatible path is a reasonable, tested-in-isolation integration, but **no accuracy
number in this repo applies to a local model**. Expect to re-run `python -m receipt_agent.evaluate` (with your own labels)
before trusting one. Smaller models behaved much worse in my runs (Haiku 4.5: 80 to 82% of rows correct versus 100%).

### 3. Everything outside the sample data
- **One country, one tax regime.** All 45 receipts are Malaysian (RM, 6% GST). Other currencies, languages, VAT
  receipts, multi-page invoices and handwritten receipts are untested.
- **Date format.** Numeric dates are read day/month/year and written ISO `YYYY-MM-DD`. That is right for these receipts
  (22 of 38 numeric dates can only be day-first) and unverified elsewhere; month-first locales are unsupported. See
  [DATA_FORMAT.md](DATA_FORMAT.md).
- **Operating systems and Python versions.** The model runs and results were produced on macOS with Python 3.14.7. The test
  suite (mocks only, no model calls) also passes on **Linux with Python 3.11, 3.12 and 3.13** in GitHub Actions
  (`.github/workflows/tests.yml`, first run 2026-10-20). That verifies the code and the mock-backed tests on those versions;
  it does **not** mean the live model results were reproduced there. Windows is untested.
- **Scale.** Largest run was 45 receipts with 6 workers. Rate-limit behaviour at higher concurrency is unknown.
- **Stability across reruns.** Model answers are cached, so a rerun replays the same answers by construction. Run-to-run
  variability of the live models was not measured.

## Reproducing with what you have
| You have | Do this |
|---|---|
| Claude Code installed and logged in | `python -m receipt_agent --provider claude-code:claude-sonnet-5-5` (no key needed) |
| An Anthropic API key | `export ANTHROPIC_API_KEY=...` then `--provider anthropic:claude-sonnet-5-5` |
| A GPU server running Ollama, SGLang or vLLM | `--provider ollama:<vision-model>` (or `sglang:`, `vllm:`) and `--base-url` if not local |
| Nothing | `python -m pytest -q`: runs the full suite against mocks |
