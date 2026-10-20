from datetime import date

from receipt_agent.schema import Extraction, LineItem
from receipt_agent.validate import FAIL, PASS, SKIP, validate

TODAY = date(2026, 10, 20)


def ex(**kw) -> Extraction:
    base = dict(vendor="SHOP", date="2018-02-01", currency="MYR", currency_symbol_raw="RM")
    base.update(kw)
    base["line_items"] = [LineItem(description="x", amount=a) for a in base.get("line_items", [])]
    return Extraction(**base)


def status(checks, name):
    return next(c.status for c in checks if c.name == name)


def test_petron_tax_inclusive_no_subtotal_passes():
    # Real sample: items 3.00 + 1.90, "Total RM inc GST" 4.90, GST 0.28 already included.
    e = ex(line_items=[3.00, 1.90], tax=0.28, total=4.90, prices_include_tax=True)
    c = validate(e, TODAY)
    assert status(c, "items_vs_total") == PASS
    assert status(c, "items_vs_subtotal") == SKIP


def test_tax_added_on_top_passes_and_wrong_total_fails():
    ok = ex(line_items=[10.0, 20.0], subtotal=30.0, tax=1.8, total=31.8, prices_include_tax=False)
    assert status(validate(ok, TODAY), "subtotal_tax_total") == PASS
    bad = ex(line_items=[10.0, 20.0], subtotal=30.0, tax=1.8, total=38.1, prices_include_tax=False)
    assert status(validate(bad, TODAY), "subtotal_tax_total") == FAIL


def test_misread_digit_in_item_is_caught():
    # 8 misread as 6 in a line item: the sum no longer matches the printed subtotal.
    e = ex(line_items=[11.80, 4.60, 1.00], subtotal=17.70, total=17.70)
    assert status(validate(e, TODAY), "items_vs_subtotal") == FAIL


def test_cash_rounding_within_tolerance_but_real_error_is_not():
    assert status(validate(ex(line_items=[10.02], total=10.00), TODAY), "items_vs_total") == PASS
    assert status(validate(ex(line_items=[10.10], total=10.00), TODAY), "items_vs_total") == FAIL


def test_unknown_tax_mode_accepts_either_reading():
    e = ex(line_items=[10.0], tax=0.6, total=10.6, prices_include_tax=None)
    assert status(validate(e, TODAY), "items_vs_total") == PASS


def test_missing_fields_are_skipped_not_passed():
    c = validate(ex(total=5.0), TODAY)
    assert status(c, "items_vs_total") == SKIP
    assert status(c, "items_vs_subtotal") == SKIP
    assert status(c, "subtotal_tax_total") == SKIP


def test_missing_total_fails():
    assert status(validate(ex(), TODAY), "has_total") == FAIL


def test_service_charge_in_equation():
    e = ex(line_items=[100.0], subtotal=100.0, other_charges=10.0, tax=6.6, total=116.6, prices_include_tax=False)
    assert status(validate(e, TODAY), "subtotal_tax_total") == PASS


def test_date_checks():
    assert status(validate(ex(date="2030-01-01", total=1), TODAY), "date_plausible") == FAIL
    assert status(validate(ex(date="31/12/2018", total=1), TODAY), "date_plausible") == FAIL
    assert status(validate(ex(date="2018-12-31", total=1), TODAY), "date_plausible") == PASS


def test_currency_symbol_mismatch_is_caught():
    c = validate(ex(currency="USD", currency_symbol_raw="RM", total=1), TODAY)
    assert status(c, "currency_matches_symbol") == FAIL
    assert status(validate(ex(currency="XXX", total=1), TODAY), "currency_valid") == FAIL


def test_ambiguous_dollar_symbol_is_not_judged():
    c = validate(ex(currency="SGD", currency_symbol_raw="$", total=1), TODAY)
    assert status(c, "currency_matches_symbol") == SKIP


def test_implausible_tax_rate_and_negative_total():
    assert status(validate(ex(tax=9.0, total=10.0), TODAY), "tax_plausible") == FAIL
    assert status(validate(ex(total=-5.0), TODAY), "amounts_sane") == FAIL


def test_tax_exclusive_subtotal_beside_tax_inclusive_items():
    # Real sample (F&P Pharmacy): items sum 31.90 (tax-inclusive), printed "Total Sales (Excluding GST)" 30.68, GST 1.22.
    e = ex(line_items=[6.00, 6.00, 4.30, 3.80, 6.50, 5.30], subtotal=30.68, tax=1.22, total=31.90, prices_include_tax=True)
    c = validate(e, TODAY)
    assert status(c, "items_vs_subtotal") == PASS
    assert status(c, "subtotal_tax_total") == PASS


def test_discount_lines_as_negative_items():
    # Real sample (Popular): gross items plus negative member-discount lines sum to the printed total.
    e = ex(line_items=[5.60, -0.56], tax=0.29, total=5.04, prices_include_tax=True)
    assert status(validate(e, TODAY), "items_vs_total") == PASS


def test_wrong_subtotal_still_fails_after_the_relaxation():
    e = ex(line_items=[10.0, 20.0], subtotal=31.0, tax=1.8, total=32.8, prices_include_tax=False)
    assert status(validate(e, TODAY), "items_vs_subtotal") == FAIL


def test_unpriced_line_does_not_break_a_matching_sum():
    # Real sample (Popular): "BTS RM10 Voucher Issued" prints no price but the priced lines still add up.
    e = ex(line_items=[5.60, None, -0.56], tax=0.29, total=5.04, prices_include_tax=True)
    c = validate(e, TODAY)
    assert status(c, "items_vs_total") == PASS


def test_unpriced_line_with_a_mismatch_is_unverifiable_not_wrong():
    e = ex(line_items=[5.60, None], tax=0.29, total=9.99)
    assert status(validate(e, TODAY), "items_vs_total") == SKIP


def test_fully_priced_mismatch_still_fails():
    assert status(validate(ex(line_items=[5.60, 1.0], total=9.99), TODAY), "items_vs_total") == FAIL


def test_self_contradicting_receipt_is_accepted_under_either_reading():
    # Real sample (Secure Parking): says "Rate incl. 6% GST" yet fee 0.94 + GST 0.06 = 1.00.
    e = ex(line_items=[0.94], tax=0.06, total=1.00, prices_include_tax=True)
    assert status(validate(e, TODAY), "items_vs_total") == PASS
