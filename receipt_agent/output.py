from __future__ import annotations

import csv
import json
from pathlib import Path

from .guardrails import sanitize_cell
from .pipeline import RowResult
from .validate import FAIL, PASS, SKIP

COLUMNS = ["file", "status", "confidence", "review_reasons", "vendor", "date", "date_raw", "currency", "subtotal", "tax",
           "other_charges", "total", "prices_include_tax", "line_item_count", "line_items", "checks_failed",
           "checks_skipped", "pass_agreement", "image_quality", "models", "cost_usd", "latency_s"]


def _row(r: RowResult) -> dict:
    e = r.extraction
    base = {"file": r.file, "status": r.status, "confidence": r.confidence,
            "review_reasons": " | ".join(r.reasons), "image_quality": r.quality, "models": r.models,
            "pass_agreement": "" if r.agreement is None else round(r.agreement, 3),
            "cost_usd": round(r.cost_usd, 4), "latency_s": round(r.latency_ms / 1000, 1)}
    if not e:
        return base
    base.update({
        "vendor": e.vendor, "date": e.date, "date_raw": e.date_raw, "currency": e.currency, "subtotal": e.subtotal, "tax": e.tax,
        "other_charges": e.other_charges, "total": e.total, "prices_include_tax": e.prices_include_tax,
        "line_item_count": len(e.line_items),
        "line_items": "; ".join(f"{i.description} = {i.amount}" for i in e.line_items),
        "checks_failed": ", ".join(c.name for c in r.checks if c.status == FAIL),
        "checks_skipped": ", ".join(c.name for c in r.checks if c.status == SKIP),
    })
    return base


TEXT_COLUMNS = {"file", "review_reasons", "vendor", "date_raw", "line_items", "checks_failed", "checks_skipped", "models", "currency"}


def _clean(row: dict) -> dict:
    """Receipt text is untrusted: neutralise spreadsheet formulas before it reaches a CSV."""
    return {k: sanitize_cell(v) if k in TEXT_COLUMNS else v for k, v in row.items()}


def write_outputs(results: list[RowResult], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # utf-8-sig so Excel opens non-ASCII vendor names correctly
    with open(out_dir / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(_clean(_row(r)) for r in results)
    detail = [{
        "file": r.file, "status": r.status, "confidence": r.confidence, "reasons": r.reasons,
        "passes": r.passes, "models": r.models, "agreement": r.agreement, "cost_usd": r.cost_usd,
        "latency_ms": r.latency_ms, "signals": r.signals,
        "extraction": r.extraction.model_dump() if r.extraction else None,
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in r.checks],
    } for r in results]
    (out_dir / "results.json").write_text(json.dumps(detail, indent=2, ensure_ascii=False))


def summary(results: list[RowResult]) -> str:
    n = len(results)
    counts = {s: sum(r.status == s for r in results) for s in ("OK", "REVIEW", "UNREADABLE", "ERROR", "SKIPPED")}
    return (f"{n} receipts: " + ", ".join(f"{v} {k}" for k, v in counts.items())
            + f". A reviewer needs to look at {n - counts['OK']} of {n}.")
