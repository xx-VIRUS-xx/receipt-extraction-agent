"""Regression tests for fixes found in code review (Oct 2026). Each test failed before its fix."""
import csv
import json

import pytest
from PIL import Image

from receipt_agent.cache import Cache
from receipt_agent.cli import main
from receipt_agent.guardrails import Guard, amount_in_text, grounding_flags
from receipt_agent.output import write_outputs
from receipt_agent.pipeline import Config, process_image, run_folder
from receipt_agent.providers import make_provider, unpriced
from receipt_agent.schema import Extraction
from tests.test_pipeline import GOOD, Mock


@pytest.fixture
def img(tmp_path):
    p = tmp_path / "r.jpg"
    Image.new("RGB", (300, 600), "white").save(p)
    return p


# ---- 1. a malformed reply must not be cached and replayed forever ----
class Flaky(Mock):
    """First call returns a schema-invalid answer, later calls a good one."""
    def __init__(self):
        super().__init__()
        self.n = 0

    def extract(self, *a, **k):
        self.n += 1
        if self.n == 1:
            return {"total": "not a number", "_meta": {"cost_usd": 0.5, "ms": 1}}
        return super().extract(*a, **k)


def test_malformed_reply_is_not_cached(tmp_path, img):
    m = Flaky()
    cache = Cache(tmp_path / "cache")
    first = process_image(img, Config(provider_a=m, provider_b=None, cache=cache))
    assert first.status == "ERROR"
    second = process_image(img, Config(provider_a=m, provider_b=None, cache=cache))
    assert m.n == 2                      # the bad answer was not replayed: the model was asked again
    assert second.status != "ERROR"


def test_unusable_reply_still_counts_as_spend(img):
    r = process_image(img, Config(provider_a=Flaky(), provider_b=None))
    assert r.status == "ERROR" and r.spend_usd == pytest.approx(0.5)


# ---- 2. --max-cost must not silently do nothing ----
def test_unpriced_backends_cannot_be_capped():
    assert unpriced([make_provider("ollama:m")], 5.0) == ["ollama:m"]
    assert unpriced([make_provider("ollama:m")], None) == []      # no cap, nothing to refuse
    assert unpriced([Mock(), None], 5.0) == []                      # providers that report cost are fine


def test_cli_refuses_max_cost_on_unpriced_backend(tmp_path, capsys):
    code = main(["--provider", "ollama:m", "--max-cost", "1", "--images", str(tmp_path), "--out", str(tmp_path / "o")])
    assert code == 2 and "--max-cost" in capsys.readouterr().err


def test_openai_compatible_reply_reports_latency():
    import httpx
    p = make_provider("ollama:m")
    p.client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})))
    out = p.extract(b"jpeg", "image/jpeg", "s", "u", {"type": "object"})
    assert out["_meta"]["cost_usd"] == 0.0 and out["_meta"]["ms"] >= 0


# ---- 3. grounding: a total of 1.00 must not be "found" in "Qty 1" ----
def test_bare_integer_does_not_ground_on_decimal_receipts():
    assert not amount_in_text(1.0, "Qty 1  Item 4.50  TOTAL 4.50")
    assert grounding_flags(Extraction(total=1.0), "Qty 1  Item 4.50  TOTAL 4.50")
    assert amount_in_text(1.0, "Item 1.00 TOTAL 1.00")
    assert amount_in_text(1200, "Ramen 1200  Total 1,200")         # receipts with no minor units still work


def test_self_grounding_is_disclosed(img):
    with_t = {**GOOD, "transcript": "CONTENTO ... TOTAL RM 21.60"}
    r = process_image(img, Config(provider_a=Mock(a=with_t), provider_b=None))   # only one pass has a transcript
    assert any("own transcription" in x for x in r.reasons)
    r2 = process_image(img, Config(provider_a=Mock(a=GOOD, b=with_t), provider_b=Mock(a=GOOD, b=with_t)))
    assert not any("own transcription" in x for x in r2.reasons)    # Pass A chosen, grounded against Pass B


# ---- 4. CSV: one column to filter on; reasons on OK rows are notes, not action items ----
def test_csv_needs_review_and_notes(tmp_path):
    a = tmp_path / "a.jpg"
    Image.new("RGB", (300, 600), "white").save(a)
    (tmp_path / "b.jpg").write_bytes(a.read_bytes())               # duplicate -> REVIEW at high confidence
    minor = {**GOOD, "currency": None}                              # B abstains on currency -> minor disagreement
    rows = run_folder(tmp_path, Config(provider_a=Mock(), provider_b=Mock(b=minor)))
    write_outputs(rows, tmp_path / "out")
    with open(tmp_path / "out" / "results.csv", encoding="utf-8-sig") as f:
        out = {r["file"]: r for r in csv.DictReader(f)}
    assert out["b.jpg"]["needs_review"] == "yes" and "duplicate" in out["b.jpg"]["review_reasons"]
    ok = out["a.jpg"]
    assert ok["needs_review"] == "no" and ok["review_reasons"] == "" and "minor" in ok["notes"]


def test_default_backend_is_the_one_that_was_tested():
    from receipt_agent import cli
    import inspect
    assert 'default="claude-code:claude-sonnet-5-5"' in inspect.getsource(cli)
