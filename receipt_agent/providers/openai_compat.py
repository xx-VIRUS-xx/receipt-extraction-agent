"""One backend for every server that speaks the OpenAI /v1/chat/completions protocol:
Ollama, SGLang, vLLM, LM Studio, llama.cpp server, OpenAI itself."""
from __future__ import annotations

import base64
import json
import os
import time

import httpx

from .base import ProviderError, parse_json_loose

DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434/v1",
    "sglang": "http://localhost:30000/v1",
    "vllm": "http://localhost:8000/v1",
    "lmstudio": "http://localhost:1234/v1",
    "openai": "https://api.openai.com/v1",
}


class OpenAICompatProvider:
    def __init__(self, backend: str, model: str, base_url: str | None = None, api_key: str | None = None):
        self.backend = backend
        self.model = model
        self.base_url = (base_url or os.environ.get("RECEIPT_BASE_URL") or DEFAULT_BASE_URLS[backend]).rstrip("/")
        key = api_key or os.environ.get("RECEIPT_API_KEY") or os.environ.get("OPENAI_API_KEY") or "none"
        self.client = httpx.Client(timeout=300, headers={"Authorization": f"Bearer {key}"})
        self.spec = f"{backend}:{model}"

    def _post(self, body: dict) -> httpx.Response:
        url = f"{self.base_url}/chat/completions"
        for attempt in range(4):
            try:
                resp = self.client.post(url, json=body)
            except httpx.HTTPError as exc:
                if attempt == 3:
                    raise ProviderError(f"cannot reach {url}: {exc}") from exc
            else:
                if resp.status_code not in (429, 500, 502, 503, 504):
                    return resp
                if attempt == 3:
                    return resp
            time.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    def extract(self, image: bytes, media_type: str, system: str, user: str, schema: dict) -> dict:
        data_url = f"data:{media_type};base64,{base64.b64encode(image).decode()}"

        def body(user_text: str, response_format: dict | None) -> dict:
            b = {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 4096,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": [
                        {"type": "text", "text": user_text},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]},
                ],
            }
            if response_format:
                b["response_format"] = response_format
            return b

        # Preferred: server-enforced JSON schema. Not every server/model supports it, so fall back to
        # prompting for JSON and parsing leniently; pydantic validation downstream catches bad shapes.
        resp = self._post(body(user, {"type": "json_schema",
                                      "json_schema": {"name": "receipt", "schema": schema}}))
        if resp.status_code == 400:
            fallback_user = (f"{user}\n\nRespond with ONLY one JSON object matching this JSON schema, "
                             f"no prose:\n{json.dumps(schema)}")
            resp = self._post(body(fallback_user, None))
        if resp.status_code != 200:
            raise ProviderError(f"{self.spec} HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"unexpected reply shape from {self.spec}: {resp.text[:300]}") from exc
        return parse_json_loose(content)
