import pytest

from receipt_agent.evaluate import (aggregate, auroc, bucket_table, currency_state, date_ok, items_exact,
                                    items_sum_ok, score_row, signal_ablation, tax_ok, total_ok, vendor_ok)
from receipt_agent.pipeline import RowResult
from receipt_agent.schema import Extraction, LineItem

LABEL = dict(vendor="TONY ROMA'S", vendor_alts=["GRAND COMPANIONS SDN BHD"], date="2018-03-03", date_alts=["2018-03-04"],
             total=269.40, total_alts=[269.41], items=[67.67, 67.67, 9.35], tax=15.25, subtotal=None, currency="MYR",
             items_uncertain=False)


def ex(**kw):
    kw["line_items"] = [LineItem(amount=a) for a in kw.get("line_items", [])]
    return Extraction(**kw)


def test_vendor_matching_is_fuzzy_but_not_loose():
    assert vendor_ok(ex(vendor="TONY ROMAS"), LABEL)
    assert vendor_ok(ex(vendor="Grand Companions Sdn Bhd"), LABEL)
    assert vendor_ok(ex(vendor="TONY ROMA'S AEON TEBRAU CITY"), LABEL)  # label contained in a longer name
    assert not vendor_ok(ex(vendor="UNIHAKKA INTERNATIONAL"), LABEL)
    assert not vendor_ok(ex(vendor=None), LABEL)
    assert not vendor_ok(ex(vendor="X"), LABEL)  # 1-char names never match by containment


def test_date_and_total_accept_documented_alternatives_only():
    assert date_ok(ex(date="2018-03-03"), LABEL) and date_ok(ex(date="2018-03-04"), LABEL)
    assert not date_ok(ex(date="2018-03-05"), LABEL)
    assert not date_ok(ex(date="2018-03-30"), LABEL)
    assert total_ok(ex(total=269.40), LABEL) and total_ok(ex(total=269.41), LABEL)
    assert not total_ok(ex(total=269.50), LABEL) and not total_ok(ex(total=None), LABEL)


def test_items_are_judged_by_sum_and_also_exactly():
    assert items_sum_ok(ex(line_items=[67.67, 67.67, 9.35]), LABEL)
    assert items_sum_ok(ex(line_items=[100.0, 44.69]), LABEL)  # different split, same sum
    assert not items_exact(ex(line_items=[100.0, 44.69]), LABEL)
    assert items_exact(ex(line_items=[9.35, 67.67, 67.67]), LABEL)  # order-independent
    assert not items_sum_ok(ex(line_items=[67.67, 67.67, 9.53]), LABEL)  # transposed digits
    assert not items_sum_ok(ex(line_items=[]), LABEL)
    assert items_sum_ok(ex(line_items=[67.67, 67.67, 9.35, None]), LABEL)  # an unpriced line adds nothing


def test_tax_and_currency():
    assert tax_ok(ex(tax=15.25), LABEL) and not tax_ok(ex(tax=15.52), LABEL)
    nolabel = {**LABEL, "tax": None}
    assert tax_ok(ex(tax=None), nolabel) and tax_ok(ex(tax=0), nolabel) and not tax_ok(ex(tax=3.0), nolabel)
    assert currency_state(ex(currency="MYR"), LABEL) == "correct"
    assert currency_state(ex(currency="USD"), LABEL) == "wrong"
    assert currency_state(ex(currency=None), LABEL) == "abstain"


def row(status="OK", conf=0.9, **fields):
    e = ex(vendor="TONY ROMAS", date="2018-03-03", total=269.40, line_items=[67.67, 67.67, 9.35], **fields)
    return RowResult("f.jpg", status, conf, [], extraction=e, signals=fields.pop("signals", {}))


def test_score_row_correct_and_wrong():
    assert score_row(row(), LABEL)["correct"] is True
    wrong = RowResult("f.jpg", "OK", 0.95, [], extraction=ex(vendor="TONY ROMAS", date="2018-03-03", total=26.94,
                                                              line_items=[67.67, 67.67, 9.35]))
    s = score_row(wrong, LABEL)
    assert s["correct"] is False and s["total"] is False and s["flagged"] is False


def test_unreadable_row_counts_as_wrong_but_flagged():
    s = score_row(RowResult("f.jpg", "UNREADABLE", 0.0, []), LABEL)
    assert s["correct"] is False and s["flagged"] is True and s["produced"] is False


def test_uncertain_items_label_is_not_held_against_the_model():
    lab = {**LABEL, "items_uncertain": True}
    r = RowResult("f.jpg", "OK", 0.9, [], extraction=ex(vendor="TONY ROMAS", date="2018-03-03", total=269.40,
                                                         line_items=[1.0]))
    assert score_row(r, lab)["correct"] is True


def test_auroc_known_values():
    assert auroc([0.9, 0.8], [0.3, 0.2]) == 1.0
    assert auroc([0.3, 0.2], [0.9, 0.8]) == 0.0
    assert auroc([0.5], [0.5]) == 0.5
    assert auroc([0.9], [0.5, 0.95]) == 0.5
    assert auroc([], [0.5]) is None


def R(status, conf, correct, **sig):
    return dict(status=status, confidence=conf, correct=correct, flagged=status != "OK", cost=0.1, latency_s=2.0,
                vendor=correct, date=correct, total=correct, tax=True, items_sum=correct, items_exact=correct,
                currency="correct", signals=sig)


def test_aggregate_hand_computed():
    rows = [R("OK", 0.95, True), R("OK", 0.92, True), R("OK", 0.90, False),  # one silent error
            R("REVIEW", 0.5, False), R("REVIEW", 0.4, True)]                  # one caught error, one false alarm
    m = aggregate(rows)
    assert m["n"] == 5 and m["row_accuracy"] == pytest.approx(3 / 5)
    assert m["n_ok"] == 3 and m["ok_precision"] == pytest.approx(2 / 3)
    assert m["silent_errors"] == 1 and m["silent_error_rate"] == pytest.approx(1 / 5)
    assert m["n_wrong"] == 2 and m["error_catch_rate"] == pytest.approx(1 / 2)
    assert m["false_alarm_rate"] == pytest.approx(1 / 3)
    assert m["flag_rate"] == pytest.approx(2 / 5)
    assert m["cost_per_receipt"] == pytest.approx(0.1) and m["total_cost"] == pytest.approx(0.5)
    # correct confidences {0.95, 0.92, 0.4}, wrong {0.90, 0.5}, computed by hand:
    # 0.95>0.90 yes,>0.5 yes; 0.92>0.90 yes,>0.5 yes; 0.4>0.90 no,>0.5 no  => 4 of 6
    assert m["auroc"] == pytest.approx(4 / 6)


def test_bucket_table_counts_and_accuracy():
    rows = [R("OK", 0.95, True), R("OK", 0.91, False), R("REVIEW", 0.85, True), R("REVIEW", 0.1, False)]
    b = {x["bucket"]: x for x in bucket_table(rows)}
    assert b["0.90-1.00"]["n"] == 2 and b["0.90-1.00"]["accuracy"] == 0.5
    assert b["0.80-0.90"]["n"] == 1 and b["0.80-0.90"]["accuracy"] == 1.0
    assert b["0.00-0.60"]["n"] == 1 and b["0.60-0.80"]["n"] == 0 and b["0.60-0.80"]["accuracy"] is None


def test_signal_ablation_counts_catches_and_false_alarms():
    rows = [R("REVIEW", 0.4, False, passes_disagree=True), R("OK", 0.9, False),
            R("REVIEW", 0.6, True, passes_disagree=True), R("OK", 0.95, True)]
    a = signal_ablation(rows)
    assert a["passes_disagree"] == dict(catches=1, of_wrong=2, false_alarms=1)
    assert a["any_signal"]["catches"] == 1 and a["any_signal"]["of_wrong"] == 2
