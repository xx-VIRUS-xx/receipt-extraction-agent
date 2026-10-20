"""Field-by-field agreement between two independent extractions of the same receipt."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .schema import Extraction

FIELDS = ("vendor", "date", "currency", "subtotal", "tax", "total", "line_items")


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _money_eq(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 0.005


def field_agrees(name: str, a: Extraction, b: Extraction) -> bool:
    if name == "vendor":
        x, y = _norm(a.vendor), _norm(b.vendor)
        return (not x and not y) or SequenceMatcher(None, x, y).ratio() >= 0.8
    if name == "date":
        return a.date == b.date
    if name == "currency":
        return (a.currency or "").upper() == (b.currency or "").upper()
    if name in ("subtotal", "tax", "total"):
        return _money_eq(getattr(a, name), getattr(b, name))
    if name == "line_items":
        xa = sorted(round(i.amount, 2) for i in a.line_items if i.amount is not None)
        xb = sorted(round(i.amount, 2) for i in b.line_items if i.amount is not None)
        return len(a.line_items) == len(b.line_items) and xa == xb
    raise KeyError(name)


def compare(a: Extraction, b: Extraction) -> dict[str, bool]:
    return {f: field_agrees(f, a, b) for f in FIELDS}


def agreement_with_others(final: Extraction, others: list[Extraction]) -> tuple[float | None, list[str]]:
    """Mean per-field agreement of `final` against the other candidates, plus the fields that disagree."""
    if not others:
        return None, []
    per_field = {f: sum(field_agrees(f, final, o) for o in others) / len(others) for f in FIELDS}
    return sum(per_field.values()) / len(FIELDS), [f for f, v in per_field.items() if v < 1]
