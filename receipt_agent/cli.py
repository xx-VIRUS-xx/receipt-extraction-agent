from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cache import Cache
from .output import summary, write_outputs
from .guardrails import Guard
from .pipeline import Config, run_folder
from .providers import ProviderError, make_provider


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="receipt_agent", description="Extract and self-validate receipt data.")
    ap.add_argument("--images", type=Path, default=Path("images"))
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--provider", default="anthropic:claude-sonnet-5-5",
                    help="backend:model for Pass A and re-reads. Backends: anthropic, claude-code (uses your "
                         "Claude Code login via `claude -p`, no API key), ollama, sglang, vllm, lmstudio, openai")
    ap.add_argument("--provider-b", default=None,
                    help="backend:model for Pass B (default: same as --provider, with a different prompt)")
    ap.add_argument("--base-url", default=None, help="override the OpenAI-compatible server URL")
    ap.add_argument("--single-pass", action="store_true", help="skip Pass B (cheaper; confidence is capped)")
    ap.add_argument("--no-reread", action="store_true", help="skip the targeted re-read")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=None, help="only process the first N images")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--max-cost", type=float, default=None, help="stop starting new receipts once this much has been spent (USD)")
    ap.add_argument("--max-consecutive-errors", type=int, default=6, help="circuit breaker for a failing backend")
    args = ap.parse_args(argv)

    try:
        a = make_provider(args.provider, args.base_url)
        b = None if args.single_pass else make_provider(args.provider_b or args.provider, args.base_url)
    except ProviderError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    cfg = Config(provider_a=a, provider_b=b, reread=not args.no_reread,
                 cache=Cache(None if args.no_cache else args.out / "cache"),
                 guard=Guard(args.max_cost, args.max_consecutive_errors))
    if not args.images.is_dir():
        print(f"error: {args.images} is not a directory", file=sys.stderr)
        return 2
    results = run_folder(args.images, cfg, workers=args.workers, limit=args.limit)
    write_outputs(results, args.out)
    print(summary(results))
    print(f"wrote {args.out / 'results.csv'} (sorted lowest confidence first) and results.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
