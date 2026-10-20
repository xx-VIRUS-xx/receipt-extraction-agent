"""Deterministic validation. No LLM here: arithmetic is checked by code.

Every check returns PASS, FAIL or SKIP. SKIP means "could not verify because a needed field is missing";
it is never counted as a pass, and scoring caps confidence when no arithmetic check could run.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from .schema import Extraction

PASS, FAIL, SKIP = "pass", "fail", "skip"
TOL = 0.05  # absolute, in currency units. Covers cash rounding to the nearest 0.05 (e.g. Malaysia) and penny drift.
MAX_TAX_RATE = 0.30

ISO_4217 = set("""AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL BSD BTN BWP BYN BZD
CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD
HNL HTG HUF IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD MAD MDL MGA
MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB
RWF SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD UYU
UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW""".split())

# Unambiguous symbols/text only. "$" and "¥" are deliberately absent: they map to several currencies.
SYMBOL_TO_CODE = {"RM": "MYR", "MYR": "MYR", "S$": "SGD", "SGD": "SGD", "US$": "USD", "USD": "USD", "€": "EUR",
                  "EUR": "EUR", "£": "GBP", "GBP": "GBP", "RP": "IDR", "IDR": "IDR", "฿": "THB", "THB": "THB",
                  "₹": "INR", "INR": "INR", "RMB": "CNY", "CNY": "CNY", "HK$": "HKD", "HKD": "HKD", "A$": "AUD",
                  "AUD": "AUD", "NT$": "TWD", "TWD": "TWD", "₩": "KRW", "KRW": "KRW", "JPY": "JPY", "CHF": "CHF"}

_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}


def parse_printed_date(raw: str | None) -> str | None:
    """Read a printed date under the documented rule and return ISO YYYY-MM-DD, or None if it cannot be read confidently.

    The rule (see docs/DATA_FORMAT.md): numeric dates are DAY/MONTH/YEAR; a four-digit-year-first date is year/month/day;
    month names are unambiguous in either order; a two-digit year means 20YY. Anything else returns None, and a None
    never causes a failure: the check that uses this only fails on a confident contradiction.
    """
    if not raw:
        return None
    t = raw.strip().lower()
    try:
        if (m := re.search(r"(?<!\d)(\d{4})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{1,2})(?!\d)", t)):
            y, mo, d = int(m[1]), int(m[2]), int(m[3])
        elif (m := re.search(r"(?<!\d)(\d{1,2})\s*[/.\-\s]\s*([a-z]{3})[a-z]*\.?\s*[/.\-\s,]?\s*(\d{2,4})(?!\d)", t)) and m[2] in _MONTHS:
            d, mo, y = int(m[1]), _MONTHS[m[2]], int(m[3])
        elif (m := re.search(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{2,4})(?!\d)", t)) and m[1] in _MONTHS:
            mo, d, y = _MONTHS[m[1]], int(m[2]), int(m[3])
        elif (m := re.search(r"(?<!\d)(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2,4})(?!\d)", t)):
            d, mo, y = int(m[1]), int(m[2]), int(m[3])
        else:
            return None
        if y < 100:
            y += 2000
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


# Relative importance when scoring. Arithmetic and the total matter most.
WEIGHTS = {"has_total": 3, "items_vs_subtotal": 3, "subtotal_tax_total": 3, "items_vs_total": 3, "amounts_sane": 2,
           "date_matches_raw": 2, "has_vendor": 1, "has_date": 1, "date_plausible": 1, "has_currency": 1, "currency_valid": 1,
           "currency_matches_symbol": 1, "tax_plausible": 1}
ARITHMETIC = {"items_vs_subtotal", "subtotal_tax_total", "items_vs_total"}


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""

    @property
    def weight(self) -> int:
        return WEIGHTS.get(self.name, 1)

    @property
    def arithmetic(self) -> bool:
        return self.name in ARITHMETIC


def _close(a: float, b: float) -> bool:
    return abs(round(a, 2) - round(b, 2)) <= TOL + 1e-9


def _total_candidates(base: float, e: Extraction) -> list[tuple[str, float]]:
    """Ways a printed total can be built from a base amount (items sum or subtotal): tax already inside it, or tax
    added on top. Both are accepted whatever prices_include_tax says: that flag is the model's reading of free text
    and real receipts contradict themselves (Secure Parking prints "Rate incl. 6% GST" beside fee 0.94 + GST 0.06
    = 1.00). Also, many receipts print a tax-EXCLUSIVE "Total Sales (Excluding GST)" beside tax-INCLUSIVE items
    (F&P Pharmacy, Yong Tat, Sin Lianhap), so a "subtotal" is ambiguous in the same way.
    """
    other = e.other_charges or 0.0
    tax = e.tax or 0.0
    return [("tax already included", base + other), ("tax added on top", base + other + tax)]


def validate(e: Extraction, today: date | None = None) -> list[Check]:
    today = today or date.today()
    out: list[Check] = []

    def add(name, status, detail=""):
        out.append(Check(name, status, detail))

    # --- presence ---
    add("has_total", PASS if e.total is not None else FAIL, "" if e.total is not None else "total not extracted")
    add("has_vendor", PASS if e.vendor and e.vendor.strip() else FAIL, "" if e.vendor else "vendor not extracted")
    add("has_date", PASS if e.date else FAIL, "" if e.date else "date not extracted")

    # --- date ---
    if e.date:
        try:
            d = datetime.strptime(e.date, "%Y-%m-%d").date()
            if d > today:
                add("date_plausible", FAIL, f"{e.date} is in the future")
            elif d.year < 2000:
                add("date_plausible", FAIL, f"{e.date} is before 2000")
            else:
                add("date_plausible", PASS)
        except ValueError:
            add("date_plausible", FAIL, f"{e.date!r} is not a valid YYYY-MM-DD date")
    else:
        add("date_plausible", SKIP, "no date")

    # The model converts the printed date to ISO. Re-derive it from the printed text with the documented day/month/year
    # rule; a confident contradiction means the model swapped day and month (undetectable otherwise for ~40% of dates).
    printed = parse_printed_date(e.date_raw)
    if e.date and printed:
        ok = printed == e.date
        add("date_matches_raw", PASS if ok else FAIL,
            "" if ok else f"printed {e.date_raw!r} reads as {printed} (day/month/year) but the model returned {e.date}")
    else:
        add("date_matches_raw", SKIP, "printed date missing or not readable by the rule")

    # --- currency ---
    if e.currency:
        code = e.currency.strip().upper()
        add("has_currency", PASS)
        add("currency_valid", PASS if code in ISO_4217 else FAIL, "" if code in ISO_4217 else f"{code!r} is not an ISO 4217 code")
        sym = (e.currency_symbol_raw or "").strip().upper().rstrip(".")
        expected = SYMBOL_TO_CODE.get(sym)
        if expected is None:
            add("currency_matches_symbol", SKIP, "no unambiguous symbol printed" if not sym else f"symbol {sym!r} is ambiguous")
        elif expected == code:
            add("currency_matches_symbol", PASS)
        else:
            add("currency_matches_symbol", FAIL, f"printed symbol {sym!r} implies {expected}, extracted {code}")
    else:
        add("has_currency", FAIL, "currency not determined")
        add("currency_valid", SKIP, "no currency")
        add("currency_matches_symbol", SKIP, "no currency")

    # --- amount sanity ---
    problems = []
    for name in ("total", "subtotal", "tax"):
        v = getattr(e, name)
        if v is not None and v < 0:
            problems.append(f"{name} is negative ({v})")
    if e.total is not None and e.total == 0:
        problems.append("total is zero")
    if e.tax is not None and e.total is not None and e.tax > e.total:
        problems.append(f"tax {e.tax} exceeds total {e.total}")
    add("amounts_sane", FAIL if problems else PASS, "; ".join(problems))

    # --- tax rate plausibility ---
    base = e.subtotal if e.subtotal else (e.total if e.total else None)
    if e.tax is not None and base:
        rate = e.tax / base
        add("tax_plausible", PASS if 0 <= rate <= MAX_TAX_RATE else FAIL, f"tax is {rate:.1%} of {base}")
    else:
        add("tax_plausible", SKIP, "tax or base amount missing")

    # --- arithmetic ---
    amounts = [i.amount for i in e.line_items]
    known = [a for a in amounts if a is not None]
    has_null = len(known) != len(amounts)  # e.g. a "voucher issued" or reference line that prints no price
    items_sum = round(sum(known), 2) if known else None
    null_note = " (some lines have no amount)" if has_null else ""

    def settle(name, ok, detail):
        """A match is a match. A mismatch is a FAIL only if every item had an amount; with unpriced lines the sum
        may simply be incomplete, so say "can't verify" instead of crying wolf."""
        if ok:
            add(name, PASS, detail + null_note)
        elif has_null:
            add(name, SKIP, detail + " - cannot confirm: some line items have no amount")
        else:
            add(name, FAIL, detail)

    if items_sum is not None and e.subtotal is not None:
        # Items may add up to the subtotal, or (tax-exclusive subtotal beside tax-inclusive items) to subtotal + tax.
        ok = _close(items_sum, e.subtotal) or (e.tax is not None and _close(items_sum, e.subtotal + e.tax))
        settle("items_vs_subtotal", ok, f"items sum {items_sum} vs subtotal {e.subtotal} (tax {e.tax})")
    else:
        why = "no subtotal printed" if e.subtotal is None else "line items missing or have no amounts"
        add("items_vs_subtotal", SKIP, why)

    if e.subtotal is not None and e.total is not None:
        match = [label for label, v in _total_candidates(e.subtotal, e) if _close(v, e.total)]
        detail = f"subtotal {e.subtotal} + tax {e.tax} + other {e.other_charges} vs total {e.total}"
        add("subtotal_tax_total", PASS if match else FAIL, detail + (f" ({match[0]})" if match else ""))
    else:
        add("subtotal_tax_total", SKIP, "subtotal or total missing")

    if e.subtotal is None and items_sum is not None and e.total is not None:
        match = [label for label, v in _total_candidates(items_sum, e) if _close(v, e.total)]
        detail = f"items sum {items_sum} + tax {e.tax} + other {e.other_charges} vs total {e.total}"
        settle("items_vs_total", bool(match), detail + (f" ({match[0]})" if match else ""))
    else:
        add("items_vs_total", SKIP, "not applicable or fields missing")

    return out


def failed(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.status == FAIL]


def fail_weight(checks: list[Check]) -> int:
    return sum(c.weight for c in failed(checks))
