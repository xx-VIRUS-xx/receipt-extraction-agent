"""Evaluate pipeline configurations against hand labels (eval/labels.json).

Everything a reviewer might ask ("which model, is the second pass worth it, can I trust OK rows, what does
it cost") is answered from the numbers this produces. Labels were read from the images by a Claude model, not an
independent human; see eval/NOTES in the report for what that does and does not prove.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from difflib import SequenceMatcher
from pathlib import Path

from .cache import Cache
from .guardrails import Guard
from .output import write_outputs
from .pipeline import Config, RowResult, run_folder
from .providers import make_provider

MODELS = ["claude-haiku-4-5", "claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]


def _cc(m: str) -> str:
    return f"claude-code:{m}"


def default_configs() -> dict[str, dict]:
    cfgs: dict[str, dict] = {}
    for m in MODELS:
        cfgs[f"single:{m}"] = dict(a=_cc(m), b=None, reread=False)
    for m in MODELS:
        cfgs[f"dual:{m}"] = dict(a=_cc(m), b=_cc(m), reread=True)
    cfgs["mixed:sonnet+haiku55"] = dict(a=_cc("claude-sonnet-5-5"), b=_cc("claude-haiku-5-5"), reread=True)
    cfgs["mixed:haiku55+sonnet"] = dict(a=_cc("claude-haiku-5-5"), b=_cc("claude-sonnet-5-5"), reread=True)
    return cfgs


# ---------------- per-field correctness ----------------

def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def vendor_ok(ex, label) -> bool:
    got = _norm(ex.vendor)
    if not got:
        return False
    for want in [label["vendor"], *label.get("vendor_alts", [])]:
        w = _norm(want)
        if SequenceMatcher(None, got, w).ratio() >= 0.8 or (len(w) >= 5 and (w in got or got in w) and len(got) >= 5):
            return True
    return False


def date_ok(ex, label) -> bool:
    return ex.date in [label["date"], *label.get("date_alts", [])]


def total_ok(ex, label) -> bool:
    return ex.total is not None and any(abs(ex.total - t) <= 0.005 for t in [label["total"], *label.get("total_alts", [])])


def currency_state(ex, label) -> str:
    if not ex.currency:
        return "abstain"
    return "correct" if ex.currency.upper() == label["currency"] else "wrong"


def tax_ok(ex, label) -> bool:
    if label["tax"] is None:
        return ex.tax in (None, 0, 0.0)
    return ex.tax is not None and abs(ex.tax - label["tax"]) <= 0.005


def items_sum_ok(ex, label) -> bool:
    got = [i.amount for i in ex.line_items if i.amount is not None]  # unpriced lines add nothing to a sum
    if not got:
        return False
    return abs(sum(got) - sum(label["items"])) <= 0.05


def items_exact(ex, label) -> bool:
    got = sorted(round(i.amount, 2) for i in ex.line_items if i.amount is not None)
    return got == sorted(round(a, 2) for a in label["items"])


def score_row(r: RowResult, label: dict) -> dict:
    ex = r.extraction
    out = dict(file=r.file, status=r.status, confidence=r.confidence, cost=r.cost_usd, latency_s=r.latency_ms / 1000)
    if ex is None:
        out.update(vendor=False, date=False, total=False, items_sum=False, items_exact=False, tax=False,
                   currency="abstain", correct=False, produced=False)
    else:
        out.update(vendor=vendor_ok(ex, label), date=date_ok(ex, label), total=total_ok(ex, label),
                   items_sum=items_sum_ok(ex, label), items_exact=items_exact(ex, label), tax=tax_ok(ex, label),
                   currency=currency_state(ex, label), produced=True)
        items_needed = not label.get("items_uncertain")
        out["correct"] = bool(out["vendor"] and out["date"] and out["total"] and (out["items_sum"] or not items_needed))
    out["flagged"] = r.status != "OK"
    out["signals"] = r.signals
    return out


# ---------------- aggregate metrics ----------------

def auroc(pos: list[float], neg: list[float]) -> float | None:
    """P(confidence of a correct row > confidence of a wrong row). 0.5 = useless, 1.0 = perfect ranking."""
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def bucket_table(rows: list[dict]) -> list[dict]:
    edges = [(0.9, 1.01, "0.90-1.00"), (0.8, 0.9, "0.80-0.90"), (0.6, 0.8, "0.60-0.80"), (0.0, 0.6, "0.00-0.60")]
    out = []
    for lo, hi, name in edges:
        b = [r for r in rows if lo <= r["confidence"] < hi]
        out.append(dict(bucket=name, n=len(b), accuracy=(sum(r["correct"] for r in b) / len(b)) if b else None))
    return out


def aggregate(rows: list[dict]) -> dict:
    n = len(rows)
    ok = [r for r in rows if r["status"] == "OK"]
    wrong = [r for r in rows if not r["correct"]]
    right = [r for r in rows if r["correct"]]
    flagged = [r for r in rows if r["flagged"]]
    silent = [r for r in ok if not r["correct"]]
    mean = lambda xs: (sum(xs) / len(xs)) if xs else None  # noqa: E731
    cur = [r["currency"] for r in rows]
    return dict(
        n=n,
        row_accuracy=len(right) / n,
        vendor=mean([r["vendor"] for r in rows]), date=mean([r["date"] for r in rows]),
        total=mean([r["total"] for r in rows]), tax=mean([r["tax"] for r in rows]),
        items_sum=mean([r["items_sum"] for r in rows]), items_exact=mean([r["items_exact"] for r in rows]),
        currency_correct=cur.count("correct") / n, currency_abstain=cur.count("abstain") / n,
        currency_wrong=cur.count("wrong") / n,
        n_ok=len(ok), n_flagged=len(flagged), flag_rate=len(flagged) / n,
        ok_precision=(sum(r["correct"] for r in ok) / len(ok)) if ok else None,
        silent_errors=len(silent), silent_error_rate=len(silent) / n,
        n_wrong=len(wrong),
        error_catch_rate=(sum(r["flagged"] for r in wrong) / len(wrong)) if wrong else None,
        false_alarm_rate=(sum(r["flagged"] for r in right) / len(right)) if right else None,
        # a flag raised only because the file is a byte-identical duplicate is intended behaviour, not noise
        false_alarm_rate_excl_dup=(sum(r["flagged"] and not r["signals"].get("duplicate") for r in right) / len(right)) if right else None,
        auroc=auroc([r["confidence"] for r in right], [r["confidence"] for r in wrong]),
        buckets=bucket_table(rows),
        cost_per_receipt=mean([r["cost"] for r in rows]), total_cost=sum(r["cost"] for r in rows),
        latency_mean_s=mean([r["latency_s"] for r in rows]),
        latency_p95_s=(sorted(r["latency_s"] for r in rows)[max(0, int(0.95 * n) - 1)] if n else None),
        status_counts={s: sum(r["status"] == s for r in rows) for s in ("OK", "REVIEW", "UNREADABLE", "ERROR", "SKIPPED")},
    )


def signal_ablation(rows: list[dict]) -> dict:
    """For wrong rows, which independent signal would have caught them on its own? Answers 'is X worth its cost'."""
    wrong = [r for r in rows if not r["correct"]]
    names = ("arithmetic_fail", "any_check_fail", "passes_disagree", "model_unsure", "ungrounded_total", "injection")
    out = {}
    for s in names:
        caught = [r for r in wrong if r["signals"].get(s)]
        fired_on_right = [r for r in rows if r["correct"] and r["signals"].get(s)]
        out[s] = dict(catches=len(caught), of_wrong=len(wrong), false_alarms=len(fired_on_right))
    out["any_signal"] = dict(catches=sum(any(r["signals"].get(s) for s in names) for r in wrong), of_wrong=len(wrong))
    return out


def run_config(name: str, spec: dict, images: Path, labels: dict, out_root: Path, cache: Cache, workers: int,
               max_cost: float | None) -> dict:
    a = make_provider(spec["a"])
    b = make_provider(spec["b"]) if spec.get("b") else None
    cfg = Config(provider_a=a, provider_b=b, reread=spec.get("reread", True), cache=cache,
                 guard=Guard(max_cost, 8))
    results = run_folder(images, cfg, workers=workers)
    safe = name.replace(":", "_").replace("+", "_")
    write_outputs(results, out_root / safe)
    rows = [score_row(r, labels[r.file]) for r in results if r.file in labels]
    return dict(name=name, spec=spec, metrics=aggregate(rows), ablation=signal_ablation(rows), rows=rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="receipt_agent.evaluate")
    ap.add_argument("--images", type=Path, default=Path("images"))
    ap.add_argument("--labels", type=Path, default=Path("eval/labels.json"))
    ap.add_argument("--out", type=Path, default=Path("eval/out"))
    ap.add_argument("--configs", default="all", help="comma-separated config names, or 'all'")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-cost", type=float, default=None, help="per-config spend cap (USD)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    cfgs = default_configs()
    if args.list:
        print("\n".join(cfgs)); return 0
    chosen = cfgs if args.configs == "all" else {k: cfgs[k] for k in args.configs.split(",")}
    labels = json.loads(args.labels.read_text())
    cache = Cache(args.out / "cache")
    results_path = args.out / "metrics.json"
    done = json.loads(results_path.read_text()) if results_path.exists() else {}
    for name, spec in chosen.items():
        print(f"[eval] {name} ...", flush=True)
        res = run_config(name, spec, args.images, labels, args.out, cache, args.workers, args.max_cost)
        done[name] = res
        results_path.parent.mkdir(parents=True, exist_ok=True)
        results_path.write_text(json.dumps(done, indent=1, default=str))
        m = res["metrics"]
        print(f"[eval] {name}: row_acc={m['row_accuracy']:.2f} flagged={m['n_flagged']} silent_err={m['silent_errors']} "
              f"cost/receipt=${m['cost_per_receipt']:.3f} latency={m['latency_mean_s']:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
