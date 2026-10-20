"""Confidence = a transparent heuristic, not a probability.

Blend of: agreement between independent passes, validation pass-rate, and the model's own per-field
confidence. Hard caps then stop a row from looking trustworthy when a real check failed or could not run.
Weights and caps are tunable (see Weights); the calibration eval in eval.py measures whether they work.
"""
from __future__ import annotations

from dataclasses import dataclass

from .schema import Extraction
from .validate import FAIL, SKIP, Check

KEY_FIELDS = ("vendor", "date", "total")


@dataclass
class Weights:
    agreement: float = 0.40
    validation: float = 0.35
    model: float = 0.25
    quality: float = 0.0  # image quality heuristic does not discriminate on this data set; see preprocess.py
    ok_threshold: float = 0.85
    cap_arithmetic_fail: float = 0.45
    cap_missing_total: float = 0.30
    cap_unverifiable: float = 0.70
    cap_key_disagreement: float = 0.55
    cap_single_pass: float = 0.70


@dataclass
class Score:
    confidence: float
    status: str  # OK | REVIEW | UNREADABLE | ERROR
    reasons: list[str]


def validation_score(checks: list[Check]) -> float:
    ran = [c for c in checks if c.status != SKIP]
    total = sum(c.weight for c in ran)
    if not total:
        return 0.0
    return 1 - sum(c.weight for c in ran if c.status == FAIL) / total


def model_confidence(e: Extraction) -> float:
    vals = [v for v in e.field_confidence.model_dump().values() if v is not None]
    return min(1.0, max(0.0, sum(vals) / len(vals))) if vals else 0.5


def score_row(e: Extraction, checks: list[Check], agreement: float | None, disputed: list[str],
              quality: float, single_pass: bool, w: Weights) -> Score:
    reasons: list[str] = []
    parts = [(w.validation, validation_score(checks)), (w.model, model_confidence(e)), (w.quality, quality)]
    if agreement is not None:
        parts.append((w.agreement, agreement))
    conf = sum(wt * v for wt, v in parts) / (sum(wt for wt, _ in parts) or 1)

    failed = [c for c in checks if c.status == FAIL]
    for c in failed:
        reasons.append(f"{c.name}: {c.detail}" if c.detail else c.name)
    if any(c.name == "has_total" for c in failed):
        conf = min(conf, w.cap_missing_total)
    if any(c.arithmetic for c in failed):
        conf = min(conf, w.cap_arithmetic_fail)
    if not any(c.arithmetic and c.status != SKIP for c in checks):
        conf = min(conf, w.cap_unverifiable)
        reasons.append("arithmetic could not be verified (missing fields)")
    key_disputed = [f for f in disputed if f in ("vendor", "date", "total")]
    if key_disputed:
        conf = min(conf, w.cap_key_disagreement)
        reasons.append(f"extraction passes disagree on: {', '.join(key_disputed)}")
    elif disputed:
        reasons.append(f"passes disagree on minor fields: {', '.join(disputed)}")
    if single_pass:
        conf = min(conf, w.cap_single_pass)
        reasons.append("single extraction pass: no independent cross-check")
    low = [k for k in KEY_FIELDS if (getattr(e.field_confidence, k) or 1.0) < 0.6]
    if low:
        reasons.append(f"model unsure about: {', '.join(low)}")

    conf = round(conf, 3)
    status = "REVIEW" if (conf < w.ok_threshold or failed) else "OK"
    return Score(conf, status, reasons)
