"""Per-image pipeline: guard -> preprocess -> pass A + pass B -> readable? -> validate -> (re-read once) -> score."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from . import prompts
from .cache import Cache
from .compare import agreement_with_others
from .guardrails import (Guard, InputRejected, check_input, find_duplicates, grounding_flags, injection_flags)
from .preprocess import IMAGE_SUFFIXES, Prepared, prepare
from .providers import ProviderError, VisionProvider
from .schema import Extraction, json_schema
from .score import Score, Weights, score_row
from .validate import Check, fail_weight, failed, validate

CAP_GUARDRAIL = 0.40  # a tripped guardrail (injection, ungrounded total) can never leave a row looking trustworthy


@dataclass
class Config:
    provider_a: VisionProvider
    provider_b: VisionProvider | None = None  # None -> single pass
    reread: bool = True
    cache: Cache = field(default_factory=lambda: Cache(None))
    weights: Weights = field(default_factory=Weights)
    guard: Guard = field(default_factory=Guard)


@dataclass
class Candidate:
    label: str
    extraction: Extraction | None = None
    error: str | None = None
    checks: list[Check] = field(default_factory=list)
    cost_usd: float = 0.0   # list-price cost of the call, replayed from the cache on a hit (for per-config reporting)
    spend_usd: float = 0.0  # money actually spent NOW (0 on a cache hit); this is what the spend cap counts
    ms: int = 0


@dataclass
class RowResult:
    file: str
    status: str  # OK | REVIEW | UNREADABLE | ERROR | SKIPPED
    confidence: float
    reasons: list[str]
    extraction: Extraction | None = None
    checks: list[Check] = field(default_factory=list)
    agreement: float | None = None
    quality: float = 0.0
    passes: list[str] = field(default_factory=list)
    models: str = ""
    cost_usd: float = 0.0
    spend_usd: float = 0.0
    latency_ms: int = 0
    signals: dict = field(default_factory=dict)  # which independent signals fired, for the eval ablation


def run_pass(provider: VisionProvider, label: str, img: Prepared, user: str, cfg: Config) -> Candidate:
    key = Cache.key(provider.spec, label, prompts.SYSTEM, user, img.jpeg)
    raw = cfg.cache.get(key)
    cache_hit = raw is not None
    try:
        if raw is None:
            raw = provider.extract(img.jpeg, img.media_type, prompts.SYSTEM, user, json_schema())
    except ProviderError as exc:
        return Candidate(label, error=f"ProviderError: {str(exc)[:200]}")
    data = dict(raw)
    meta = data.pop("_meta", {}) or {}
    cost = meta.get("cost_usd", 0.0)
    spend = 0.0 if cache_hit else cost
    try:
        extraction = Extraction.model_validate(data)
    except ValidationError as exc:
        # Not cached: a malformed reply must be retried on the next run, not replayed forever.
        # Still counted as spend: the call was paid for even though its answer is unusable.
        return Candidate(label, error=f"ValidationError: {str(exc)[:200]}", cost_usd=cost, spend_usd=spend,
                         ms=meta.get("ms", 0))
    if not cache_hit:
        cfg.cache.put(key, raw)  # only replies that passed schema validation are cached
    return Candidate(label, extraction, cost_usd=cost, spend_usd=spend, ms=meta.get("ms", 0))


def _unreadable(cands: list[Candidate]) -> bool:
    ex = [c.extraction for c in cands if c.extraction]
    if not ex:
        return False
    return all(not e.legible for e in ex) or all(e.vendor is None and e.total is None for e in ex)


def _describe(c: Candidate) -> str:
    e = c.extraction
    return (f"{c.label}: vendor={e.vendor!r} date={e.date!r} subtotal={e.subtotal} tax={e.tax} "
            f"other={e.other_charges} total={e.total} items={[i.amount for i in e.line_items]}")


def _pick(cands: list[Candidate]) -> Candidate:
    """Best candidate = fewest weighted validation failures; ties go to the one most others agree with, then order."""
    def rank(c: Candidate):
        others = [o.extraction for o in cands if o is not c and o.extraction]
        agree, _ = agreement_with_others(c.extraction, others)
        return (fail_weight(c.checks), -(agree if agree is not None else 0))
    return min(cands, key=rank)


def _spend(cands: list[Candidate]) -> tuple[float, float, int]:
    return sum(c.cost_usd for c in cands), sum(c.spend_usd for c in cands), sum(c.ms for c in cands)


def process_image(path: Path, cfg: Config) -> RowResult:
    try:
        check_input(path)
    except InputRejected as exc:
        return RowResult(path.name, "ERROR", 0.0, [f"input rejected: {exc}"])
    img = prepare(path)
    models = ",".join(sorted({cfg.provider_a.spec, *([cfg.provider_b.spec] if cfg.provider_b else [])}))
    cands = [run_pass(cfg.provider_a, "A", img, prompts.PASS_A, cfg)]
    if cfg.provider_b:
        cands.append(run_pass(cfg.provider_b, "B", img, prompts.PASS_B, cfg))

    def result(status, conf, reasons, **kw) -> RowResult:
        cost, spend, ms = _spend(cands)
        return RowResult(path.name, status, conf, reasons, quality=img.quality, passes=[c.label for c in cands],
                         models=models, cost_usd=cost, spend_usd=spend, latency_ms=ms, **kw)

    good = [c for c in cands if c.extraction]
    if not good:
        return result("ERROR", 0.0, ["extraction failed: " + "; ".join(c.error or "" for c in cands)])
    if _unreadable(good):
        return result("UNREADABLE", 0.0, ["model reports the image is unreadable or no vendor/total could be read; "
                                          "nothing was guessed"])
    for c in good:
        c.checks = validate(c.extraction)

    # Targeted re-read, once, only when there is something to resolve.
    primary = _pick(good)
    _, disputed = agreement_with_others(primary.extraction, [c.extraction for c in good if c is not primary])
    if cfg.reread and (failed(primary.checks) or disputed):
        problems = [f"{c.name}: {c.detail}" for c in failed(primary.checks)]
        problems += [f"passes disagree on {f}" for f in disputed]
        user = prompts.REREAD.format(problems="\n".join(f"- {p}" for p in problems),
                                     candidates="\n".join(_describe(c) for c in good))
        c3 = run_pass(cfg.provider_a, "C", img, user, cfg)
        cands.append(c3)
        if c3.extraction:
            c3.checks = validate(c3.extraction)
            good.append(c3)

    final = _pick(good)
    others = [c.extraction for c in good if c is not final]
    agreement, disputed = agreement_with_others(final.extraction, others)
    single = len(good) == 1  # by choice (--single-pass) or because Pass B failed: no independent cross-check
    s: Score = score_row(final.extraction, final.checks, agreement, disputed, img.quality, single, cfg.weights)

    if final.label == "C":
        s.reasons.append("value chosen after a targeted re-read")
    # --- guardrails that act on the final answer ---
    # Ground against a transcription from a DIFFERENT pass when one exists. Only Pass B transcribes, so when Pass B's
    # own answer is chosen the check falls back to its own transcript: weaker, and the reason text says so.
    other_t = next((c.extraction.transcript for c in good if c is not final and c.extraction.transcript), None)
    own_t = final.extraction.transcript
    transcript = other_t or own_t
    if transcript and not other_t:
        s.reasons.append("total grounded against the same pass's own transcription (weaker check)")
    inj = [f for c in good for f in injection_flags(c.extraction)]
    grd = grounding_flags(final.extraction, transcript)
    for msg in dict.fromkeys(inj + grd):
        s.reasons.append("GUARDRAIL: " + msg)
    if inj or grd:
        s.confidence = min(s.confidence, CAP_GUARDRAIL)
        s.status = "REVIEW"
    signals = {
        "arithmetic_fail": any(c.arithmetic and c.status == "fail" for c in final.checks),
        "any_check_fail": bool(failed(final.checks)),
        "passes_disagree": bool(disputed),
        "model_unsure": any((getattr(final.extraction.field_confidence, k) or 1.0) < 0.6
                            for k in ("vendor", "date", "total")),
        "ungrounded_total": bool(grd),
        "injection": bool(inj),
        "single_pass": single,
    }
    return result(s.status, s.confidence, s.reasons, extraction=final.extraction, checks=final.checks,
                  agreement=agreement, signals=signals)


def run_folder(folder: Path, cfg: Config, workers: int = 4, limit: int | None = None) -> list[RowResult]:
    files = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)[:limit]
    dups = find_duplicates(files)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda p: _safe(p, cfg), files))
    for r in results:
        if r.file in dups:
            r.reasons.append(f"byte-identical duplicate of {dups[r.file]} (counted once in totals?)")
            r.signals["duplicate"] = True
            if r.status == "OK":
                r.status = "REVIEW"
    # Anything that needs a human (REVIEW/UNREADABLE/ERROR/SKIPPED) first, then OK rows; lowest confidence first within each.
    # A flag raised by a guardrail can carry a high confidence number and must not sink to the bottom of the sheet.
    return sorted(results, key=lambda r: (r.status == "OK", r.confidence, r.file))


def _safe(path: Path, cfg: Config) -> RowResult:
    """One bad file must never kill the batch; a failing backend or a spend cap must stop it cleanly."""
    if (why := cfg.guard.allow()):
        return RowResult(path.name, "SKIPPED", 0.0, [f"not processed: {why}"])
    try:
        row = process_image(path, cfg)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all at the per-file boundary
        row = RowResult(path.name, "ERROR", 0.0, [f"unexpected error: {type(exc).__name__}: {exc}"])
    cfg.guard.record(row.spend_usd, failed=row.status == "ERROR")
    return row
