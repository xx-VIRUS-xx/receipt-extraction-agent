"""Guardrails. Each one maps to a concrete threat, and each has a test (tests/test_guardrails.py).

Input     : file type/size/pixel limits (corrupt files, decompression bombs).
Prompt    : the receipt is untrusted data. The model gets read-only tools; we also flag injected instructions.
Output    : numbers must be grounded in the transcription; CSV cells are neutralised against formula injection.
Operations: spend cap, circuit breaker for a failing backend, byte-identical duplicate detection.
"""
from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path

from PIL import Image

from .schema import Extraction

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_PIXELS = 60_000_000
ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow raises DecompressionBombError beyond 2x this


class InputRejected(ValueError):
    pass


def check_input(path: Path) -> None:
    """Cheap checks before any pixels are decoded or any model call is paid for."""
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise InputRejected(f"unsupported file type {path.suffix!r}")
    size = path.stat().st_size
    if size == 0:
        raise InputRejected("file is empty")
    if size > MAX_FILE_BYTES:
        raise InputRejected(f"file is {size / 1e6:.1f} MB, limit is {MAX_FILE_BYTES / 1e6:.0f} MB")
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception as exc:  # noqa: BLE001 - any decode failure means "reject", not "crash"
        raise InputRejected(f"not a readable image: {type(exc).__name__}") from exc
    if w * h > MAX_PIXELS:
        raise InputRejected(f"{w}x{h} pixels exceeds the {MAX_PIXELS:,} pixel limit")


# ---------- prompt injection ----------

_INJECTION = [re.compile(p, re.I) for p in (
    r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
    r"disregard\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)",
    r"\bsystem\s+(prompt|notice|message|override|instruction)",
    r"\bnew\s+instructions?\b",
    r"\b(set|change|output|report|return|write)\s+(the\s+)?(total|vendor|amount|date|status|confidence|currency)\s+(to|as)\b",
    r"\bdo\s+not\s+(flag|report|mention|tell)\b",
    r"\bas\s+an?\s+(ai|assistant|language\s+model)\b",
    r"\b(mark|set|label)\b.{0,30}\b(as\s+)?(ok|verified|approved|valid|trusted)\b",
    r"</?\s*(system|instructions?|assistant)\s*>",
)]


def injection_flags(e: Extraction) -> list[str]:
    """Flag text that looks like instructions aimed at the extraction model."""
    # Only text that came FROM the document. `notes` is the model's own commentary ("I set the currency to MYR"),
    # which tripped the pattern on real runs; model-reported injection is covered by suspicious_instructions.
    texts = [e.vendor or "", e.date_raw or "", e.transcript or ""] + [i.description for i in e.line_items]
    hits = {p.pattern.split(r"\b")[0][:40] for t in texts for p in _INJECTION if p.search(t)}
    out = []
    if e.suspicious_instructions:
        out.append("model reported instruction-like text on the document")
    if hits:
        out.append("document text resembles instructions to an AI (possible prompt injection)")
    return out


# ---------- grounding (hallucination guard) ----------

def _forms(amount: float) -> set[str]:
    forms = {f"{amount:.2f}", f"{amount:.1f}"}
    if float(amount).is_integer():
        forms.add(str(int(amount)))
    return forms


def amount_in_text(amount: float, text: str) -> bool:
    t = text.replace(",", "")
    return any(re.search(rf"(?<![\d.]){re.escape(f)}(?!\d)", t) for f in _forms(abs(amount)))


def grounding_flags(e: Extraction, transcript: str | None) -> list[str]:
    """The total must literally appear in an independent transcription of the document. If a model reports a
    number that was never transcribed, it was inferred or invented."""
    if not transcript or not transcript.strip() or e.total is None:
        return []
    return [] if amount_in_text(e.total, transcript) else [f"total {e.total} not found in the transcription"]


# ---------- CSV / output safety ----------

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value, max_len: int = 400) -> str:
    """Neutralise spreadsheet formula injection (OWASP CSV injection) and strip control characters."""
    if value is None:
        return ""
    s = _CONTROL.sub("", str(value))[:max_len]
    return "'" + s if s.startswith(_FORMULA_START) else s


# ---------- operations ----------

def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(files: list[Path]) -> dict[str, str]:
    """Map each byte-identical later file to the first file with the same content."""
    seen: dict[str, str] = {}
    dups: dict[str, str] = {}
    for p in files:
        d = file_digest(p)
        if d in seen:
            dups[p.name] = seen[d]
        else:
            seen[d] = p.name
    return dups


class Guard:
    """Spend cap + circuit breaker, shared across worker threads."""

    def __init__(self, max_cost_usd: float | None = None, max_consecutive_errors: int = 6):
        self.max_cost = max_cost_usd
        self.max_errors = max_consecutive_errors
        self.spent = 0.0
        self.consecutive_errors = 0
        self._lock = threading.Lock()

    def allow(self) -> str | None:
        with self._lock:
            if self.max_cost is not None and self.spent >= self.max_cost:
                return f"spend cap reached (${self.spent:.2f} >= ${self.max_cost:.2f})"
            if self.consecutive_errors >= self.max_errors:
                return f"circuit breaker open: {self.consecutive_errors} consecutive failures"
            return None

    def record(self, cost: float, failed: bool) -> None:
        with self._lock:
            self.spent += cost
            self.consecutive_errors = self.consecutive_errors + 1 if failed else 0
