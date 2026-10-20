"""Tiny on-disk cache for raw model answers, so re-running after changing validation/scoring costs nothing."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class Cache:
    def __init__(self, directory: Path | None):
        self.dir = directory
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: str | bytes) -> str:
        h = hashlib.sha256()
        for p in parts:
            h.update(p if isinstance(p, bytes) else p.encode())
            h.update(b"\0")
        return h.hexdigest()[:32]

    def get(self, key: str) -> dict | None:
        if self.dir and (f := self.dir / f"{key}.json").exists():
            return json.loads(f.read_text())
        return None

    def put(self, key: str, value: dict) -> None:
        if self.dir:
            (self.dir / f"{key}.json").write_text(json.dumps(value))
