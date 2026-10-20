"""Use a local Claude Code install (`claude -p`) as the model backend: no API key needed, it uses the
logged-in Claude Code session. Each call is a headless one-shot session that may only use the Read tool
(to look at the image) and returns schema-constrained JSON via --json-schema.

Cost note (measured): with the default config every call carried ~38k tokens of MCP/skills overhead
(~$0.31 list price). --strict-mcp-config + --disable-slash-commands + --setting-sources "" cut that to ~$0.02.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .base import ProviderError, parse_json_loose

EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class ClaudeCodeProvider:
    reports_cost = True  # `claude -p` returns total_cost_usd per call

    def __init__(self, model: str, claude_bin: str | None = None, timeout: int = 300):
        self.binary = claude_bin or os.environ.get("CLAUDE_BIN") or shutil.which("claude")
        if not self.binary:
            raise ProviderError("`claude` CLI not found on PATH (set CLAUDE_BIN to its location)")
        self.model = model
        self.timeout = timeout
        self.spec = f"claude-code:{model}"

    def _command(self, system: str, prompt: str, schema: dict) -> list[str]:
        return [self.binary, "-p", prompt, "--model", self.model, "--system-prompt", system,
                "--output-format", "json", "--json-schema", json.dumps(schema),
                "--tools", "Read", "--allowedTools", "Read",  # the image is untrusted input: read-only, no shell
                "--no-session-persistence", "--setting-sources", "",
                "--strict-mcp-config", "--disable-slash-commands"]

    def extract(self, image: bytes, media_type: str, system: str, user: str, schema: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="receipt_") as tmp:
            name = f"receipt.{EXT.get(media_type, 'jpg')}"
            (Path(tmp) / name).write_bytes(image)
            prompt = f"Use the Read tool to view the image file {name} in the current directory.\n\n{user}"
            last = ""
            for _ in range(2):  # one retry: headless sessions occasionally fail transiently
                try:
                    proc = subprocess.run(self._command(system, prompt, schema), cwd=tmp, capture_output=True,
                                          text=True, timeout=self.timeout, stdin=subprocess.DEVNULL)
                except subprocess.TimeoutExpired:
                    last = f"timed out after {self.timeout}s"
                    continue
                try:
                    reply = json.loads(proc.stdout)
                except json.JSONDecodeError:
                    last = f"exit {proc.returncode}, non-JSON output: {(proc.stdout or proc.stderr)[:200]!r}"
                    continue
                if proc.returncode != 0 or reply.get("is_error"):
                    last = f"exit {proc.returncode}: {str(reply.get('result', ''))[:200]}"
                    continue
                meta = {"_meta": {"cost_usd": float(reply.get("total_cost_usd") or 0.0),
                                  "ms": int(reply.get("duration_ms") or 0)}}
                if isinstance(reply.get("structured_output"), dict):
                    return {**reply["structured_output"], **meta}
                try:
                    return {**parse_json_loose(reply.get("result", "")), **meta}
                except ProviderError as exc:
                    last = str(exc)
            raise ProviderError(f"claude -p failed: {last}")
