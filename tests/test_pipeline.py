import json

import httpx
import pytest
from PIL import Image

from receipt_agent.pipeline import Config, process_image, run_folder
from receipt_agent.providers import ProviderError, make_provider
from receipt_agent.providers.openai_compat import OpenAICompatProvider

GOOD = {
    "legible": True, "vendor": "CONTENTO", "date": "2018-03-20", "currency": "MYR", "currency_symbol_raw": "RM",
    "line_items": [{"description": "Bubble", "amount": 11.80}, {"description": "Yam", "amount": 4.90},
                   {"description": "Add on", "amount": 1.00}, {"description": "Cho", "amount": 3.90}],
    "total": 21.60, "prices_include_tax": None,
    "field_confidence": {"vendor": 0.95, "date": 0.95, "total": 0.95, "line_items": 0.9},
}


class Mock:
    """Returns scripted answers keyed by which prompt is being used (A, B or re-read)."""
    spec = "mock:model"

    def __init__(self, a=GOOD, b=GOOD, c=GOOD):
        self.answers = {"A": a, "B": b, "C": c}
        self.calls = []

    def extract(self, image, media_type, system, user, schema):
        label = "C" if "second look" in user else ("B" if "transcribe every line" in user else "A")
        self.calls.append(label)
        ans = self.answers[label]
        if isinstance(ans, Exception):
            raise ans
        return ans


@pytest.fixture
def img(tmp_path):
    p = tmp_path / "r.jpg"
    Image.new("RGB", (300, 600), "white").save(p)
    return p


def cfg(m, **kw):
    return Config(provider_a=m, provider_b=m, **kw)


def test_clean_receipt_is_ok_with_high_confidence(img):
    m = Mock()
    r = process_image(img, cfg(m))
    assert r.status == "OK" and r.confidence >= 0.85
    assert m.calls == ["A", "B"]  # no re-read needed


def test_misread_digit_is_resolved_by_reread(img):
    bad = json.loads(json.dumps(GOOD))
    bad["line_items"][1]["amount"] = 4.60
    m = Mock(a=bad, b=GOOD, c=GOOD)
    r = process_image(img, cfg(m))
    assert "C" in m.calls
    assert r.extraction.line_items[1].amount == 4.90
    assert r.extraction.total == 21.60


def test_error_that_survives_is_flagged_not_hidden(img):
    bad = json.loads(json.dumps(GOOD))
    bad["line_items"][1]["amount"] = 4.60
    r = process_image(img, cfg(Mock(a=bad, b=bad, c=bad)))
    assert r.status == "REVIEW" and r.confidence <= 0.45
    assert any("items_vs_total" in x for x in r.reasons)


def test_unreadable_gets_zero_confidence_and_no_values(img):
    blank = {"legible": False}
    r = process_image(img, cfg(Mock(a=blank, b=blank)))
    assert r.status == "UNREADABLE" and r.confidence == 0 and r.extraction is None


def test_provider_failure_does_not_crash_the_batch(img, tmp_path):
    m = Mock(a=ProviderError("boom"), b=ProviderError("boom"))
    r = process_image(img, cfg(m))
    assert r.status == "ERROR" and "boom" in r.reasons[0]
    rows = run_folder(tmp_path, cfg(m))  # tmp_path contains the image
    assert rows and rows[0].status == "ERROR"


def test_malformed_model_output_is_an_error_not_a_crash(img):
    r = process_image(img, cfg(Mock(a={"total": "not a number"}, b={"total": "nope"})))
    assert r.status == "ERROR"


def test_single_pass_confidence_is_capped(img):
    m = Mock()
    r = process_image(img, Config(provider_a=m, provider_b=None))
    assert m.calls == ["A"]
    assert r.confidence <= 0.7 and r.status == "REVIEW"
    assert any("no independent cross-check" in x for x in r.reasons)


# ---- model-agnostic backend: fake OpenAI-compatible server (what Ollama / SGLang / vLLM expose) ----

def fake_server(reject_schema: bool, content: str):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if reject_schema and "response_format" in body:
            return httpx.Response(400, json={"error": "response_format unsupported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler), seen


@pytest.mark.parametrize("backend", ["ollama", "sglang", "vllm", "lmstudio"])
def test_openai_compatible_backends_work(backend):
    p = make_provider(f"{backend}:some/model")
    assert isinstance(p, OpenAICompatProvider)
    transport, seen = fake_server(False, json.dumps(GOOD))
    p.client = httpx.Client(transport=transport)
    out = p.extract(b"jpeg", "image/jpeg", "sys", "user", {"type": "object"})
    assert out["vendor"] == "CONTENTO"
    assert seen[0]["response_format"]["type"] == "json_schema"
    assert seen[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_falls_back_when_server_rejects_schema_and_parses_fenced_json():
    p = make_provider("ollama:m")
    transport, seen = fake_server(True, "Here you go:\n```json\n" + json.dumps(GOOD) + "\n```")
    p.client = httpx.Client(transport=transport)
    out = p.extract(b"jpeg", "image/jpeg", "sys", "user", {"type": "object"})
    assert out["total"] == 21.60
    assert "response_format" not in seen[-1] and "JSON schema" in seen[-1]["messages"][1]["content"][0]["text"]


def test_bad_spec_and_unknown_backend():
    with pytest.raises(ProviderError):
        make_provider("nocolon")
    with pytest.raises(ProviderError):
        make_provider("carrierpigeon:x")


def test_default_urls_per_backend():
    assert make_provider("ollama:m").base_url == "http://localhost:11434/v1"
    assert make_provider("sglang:m").base_url == "http://localhost:30000/v1"
    assert make_provider("ollama:m", base_url="http://gpu-box:9/v1/").base_url == "http://gpu-box:9/v1"
