"""JSON-file cache — survives across processes (the default persistent tier).

Each entry is a small JSON file ``{"expires": <epoch|0>, "value": …}`` under a cache
directory. Keys are sanitized to safe filenames. Corrupt or expired files are treated
as misses (and expired ones are removed lazily).
"""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class FileCache:
    def __init__(self, path: Optional[str] = None) -> None:
        self._dir = Path(path) if path else Path(tempfile.gettempdir()) / "langsys-cache"
        self._dir.mkdir(parents=True, exist_ok=True)

    def _file(self, key: str) -> Path:
        return self._dir / (_SAFE.sub("_", key) + ".json")

    def get(self, key: str) -> Optional[Any]:
        file = self._file(key)
        try:
            raw = file.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        try:
            entry = json.loads(raw)
            expires = entry["expires"]
            if expires and expires < time.time():
                file.unlink(missing_ok=True)
                return None
            return entry["value"]
        except (ValueError, KeyError, TypeError):
            return None

    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        expires = time.time() + ttl if ttl > 0 else 0
        payload = json.dumps({"expires": expires, "value": value}, ensure_ascii=False)
        file = self._file(key)
        # Atomic write: temp file in the same dir, then replace.
        tmp = file.with_suffix(".json.tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(file)
        except OSError:
            tmp.unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        self._file(key).unlink(missing_ok=True)

    def clear(self) -> None:
        for file in self._dir.glob("*.json"):
            file.unlink(missing_ok=True)
