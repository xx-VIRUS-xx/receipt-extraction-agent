from __future__ import annotations

import base64
import os

from .base import ProviderError

TOOL_NAME = "record_receipt"


class AnthropicProvider:
    def __init__(self, model: str, api_key: str | None = None):
        import anthropic  # imported lazily so the OpenAI-compatible path needs no Anthropic install

        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("ANTHROPIC_API_KEY is not set")
        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=key, max_retries=4)
        self.model = model
        self.spec = f"anthropic:{model}"

    def extract(self, image: bytes, media_type: str, system: str, user: str, schema: dict) -> dict:
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                tools=[{"name": TOOL_NAME, "description": "Record the extracted receipt data.", "input_schema": schema}],
                tool_choice={"type": "tool", "name": TOOL_NAME},
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                                     "data": base64.b64encode(image).decode()}},
                        {"type": "text", "text": user},
                    ],
                }],
            )
        except self._anthropic.APIError as exc:
            raise ProviderError(f"anthropic API error: {exc}") from exc
        for block in resp.content:
            if block.type == "tool_use":
                # Token counts only: this backend has no per-call price from the API, so cost stays 0 here.
                return {**dict(block.input), "_meta": {"cost_usd": 0.0, "ms": 0,
                                                       "in_tokens": resp.usage.input_tokens,
                                                       "out_tokens": resp.usage.output_tokens}}
        raise ProviderError("anthropic reply had no tool_use block")
