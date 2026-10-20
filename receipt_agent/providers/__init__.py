from __future__ import annotations

from .base import ProviderError, VisionProvider
from .openai_compat import DEFAULT_BASE_URLS, OpenAICompatProvider


def make_provider(spec: str, base_url: str | None = None) -> VisionProvider:
    """spec is "backend:model", e.g. anthropic:claude-sonnet-5-5, ollama:qwen2.5vl:7b,
    sglang:Qwen/Qwen2.5-VL-7B-Instruct, vllm:..., lmstudio:..., openai:gpt-4o."""
    backend, sep, model = spec.partition(":")
    if not sep or not model:
        raise ProviderError(f"provider spec must look like backend:model, got {spec!r}")
    backend = backend.lower()
    if backend == "anthropic":
        from .anthropic_provider import AnthropicProvider
        return AnthropicProvider(model)
    if backend in ("claude-code", "claude_code", "claudecode"):
        from .claude_code_provider import ClaudeCodeProvider
        return ClaudeCodeProvider(model)
    if backend in DEFAULT_BASE_URLS:
        return OpenAICompatProvider(backend, model, base_url=base_url)
    raise ProviderError(f"unknown backend {backend!r}; use anthropic, claude-code or one of {sorted(DEFAULT_BASE_URLS)}")
