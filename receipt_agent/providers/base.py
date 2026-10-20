from __future__ import annotations

import json
import re
from typing import Protocol


class ProviderError(RuntimeError):
    pass


class VisionProvider(Protocol):
    spec: str  # "backend:model", used for cache keys and reporting

    def extract(self, image: bytes, media_type: str, system: str, user: str, schema: dict) -> dict:
        """Return the model's structured answer as a dict. Raise ProviderError on failure."""
        ...


def parse_json_loose(text: str) -> dict:
    """Parse JSON from a model reply that may be wrapped in prose or code fences."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ProviderError(f"no JSON object in model reply: {text[:200]!r}")
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError(f"invalid JSON in model reply: {exc}") from exc
