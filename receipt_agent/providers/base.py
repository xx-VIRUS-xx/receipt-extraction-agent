from __future__ import annotations

import json
import re
from typing import Protocol


class ProviderError(RuntimeError):
    pass


class VisionProvider(Protocol):
    spec: str  # "backend:model", used for cache keys and reporting
    # True if extract() reports real per-call cost in _meta.cost_usd. --max-cost is refused for backends that don't,
    # because a cap that always reads $0 would silently never trip.
    reports_cost: bool

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


def unpriced(providers, max_cost: float | None) -> list[str]:
    """Specs of providers that cannot be held to a spend cap (empty when no cap is set)."""
    if max_cost is None:
        return []
    return list(dict.fromkeys(p.spec for p in providers if p is not None and not getattr(p, "reports_cost", True)))
