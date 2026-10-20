import json
import subprocess
from pathlib import Path

import pytest

from receipt_agent.providers import ProviderError, make_provider
from receipt_agent.providers.claude_code_provider import ClaudeCodeProvider


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("CLAUDE_BIN", "/fake/claude")
    return make_provider("claude-code:claude-sonnet-5-5")


def fake_run(stdout="", returncode=0, record=None, exc=None):
    def run(cmd, **kw):
        if record is not None:
            record.append((cmd, kw, sorted(p.name for p in Path(kw["cwd"]).iterdir())))
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, returncode, stdout, "")
    return run


def test_builds_locked_down_command_and_returns_structured_output(provider, monkeypatch):
    seen = []
    reply = json.dumps({"is_error": False, "structured_output": {"vendor": "PETRON", "total": 4.9},
                        "total_cost_usd": 0.019, "duration_ms": 4300})
    monkeypatch.setattr(subprocess, "run", fake_run(reply, record=seen))
    out = provider.extract(b"jpeg-bytes", "image/jpeg", "SYS", "do it", {"type": "object"})
    assert (out["vendor"], out["total"]) == ("PETRON", 4.9)
    assert out["_meta"] == {"cost_usd": 0.019, "ms": 4300}  # cost and latency are captured for the eval
    cmd, kw, files = seen[0]
    assert provider.spec == "claude-code:claude-sonnet-5-5"
    assert files == ["receipt.jpg"]  # the image is handed over as a file in an isolated cwd
    assert cmd[cmd.index("--tools") + 1] == "Read"  # untrusted image content must not get shell/write tools
    assert cmd[cmd.index("--allowedTools") + 1] == "Read"
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"
    assert kw["stdin"] == subprocess.DEVNULL


def test_falls_back_to_parsing_result_text(provider, monkeypatch):
    reply = json.dumps({"is_error": False, "result": "```json\n{\"total\": 1.5}\n```"})
    monkeypatch.setattr(subprocess, "run", fake_run(reply))
    assert provider.extract(b"x", "image/jpeg", "s", "u", {})["total"] == 1.5


def test_retries_once_then_raises_provider_error(provider, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run("not json", returncode=1, record=calls))
    with pytest.raises(ProviderError, match="claude -p failed"):
        provider.extract(b"x", "image/jpeg", "s", "u", {})
    assert len(calls) == 2


def test_is_error_reply_and_timeout_are_provider_errors(provider, monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run(json.dumps({"is_error": True, "result": "rate limited"})))
    with pytest.raises(ProviderError, match="rate limited"):
        provider.extract(b"x", "image/jpeg", "s", "u", {})
    monkeypatch.setattr(subprocess, "run", fake_run(exc=subprocess.TimeoutExpired("claude", 1)))
    with pytest.raises(ProviderError, match="timed out"):
        provider.extract(b"x", "image/jpeg", "s", "u", {})


def test_missing_binary_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(ProviderError, match="not found"):
        ClaudeCodeProvider("sonnet")
