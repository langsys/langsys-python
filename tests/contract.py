"""The shared contract double (spec CONF-2), started from Python.

`tests/contract-fixture/` is vendored byte-exact from langsys-js-typescript and pinned by its
tree id. It is one runnable HTTP double for the whole fleet: every SDK starts the same
`server.mjs` and points its API base at it through WIRE-5's seam. It says no the way the server
does, computes `write_enabled` itself, and exposes only ACCEPTED state, never a request log - so
a test asserts on status and on state read back, never on what the SDK sent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

import httpx

FIXTURE_DIR = Path(__file__).parent / "contract-fixture"

#: THE CHECK: the git tree id of the vendored directory. THE PROVENANCE: the ref below.
FIXTURE_TREE = "542f57f5ffcb9038db1b7411152b7e31b96cb269"
FIXTURE_REF = "langsys-js-typescript contract-fixture/ (tree 542f57f5)"

PROJECT = "proj-contract"
WRITE_KEY = "key-write"
READ_KEY = "key-read"
IP_WRITE_KEY = "key-ipwrite"


class ContractDouble:
    """One running `server.mjs`. Seeding replaces the whole state."""

    def __init__(self) -> None:
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("the contract double needs Node 18+ on PATH (spec CONF-2)")
        self._proc = subprocess.Popen(
            [node, str(FIXTURE_DIR / "server.mjs")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        ready: dict[str, Any] = {}

        def read_ready() -> None:
            assert self._proc.stdout is not None
            ready.update(json.loads(self._proc.stdout.readline()))

        reader = threading.Thread(target=read_ready, daemon=True)
        reader.start()
        reader.join(15)
        if not ready.get("ready"):
            self.close()
            raise RuntimeError("the contract double did not report ready")
        self.base_url: str = ready["base_url"]
        self.fixture_url: str = ready["fixture_url"]
        self._http = httpx.Client(timeout=10)

    # -- setup, through /__fixture/ only ----------------------------------------------------

    def seed(self, document: dict[str, Any]) -> None:
        response = self._http.post(f"{self.fixture_url}/seed", json=document)
        assert response.status_code == 200, response.text

    def advance(self, seconds: float) -> None:
        self._http.post(f"{self.fixture_url}/clock", json={"advance_seconds": seconds})

    def state(self, project: str = PROJECT) -> dict[str, Any]:
        body = self._http.get(f"{self.fixture_url}/state").json()
        return {"hints": body["hints"], **body["projects"].get(project, {"phrases": [], "blocks": []})}

    def phrases(self, project: str = PROJECT) -> list[tuple[Optional[str], str]]:
        """Accepted phrases as (category, phrase), sorted."""
        return sorted(
            ((p["category"], p["phrase"]) for p in self.state(project)["phrases"]),
            key=lambda cp: (cp[0] or "", cp[1]),
        )

    def blocks(self, project: str = PROJECT) -> list[dict[str, Any]]:
        return list(self.state(project)["blocks"])

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if hasattr(self, "_http"):
            self._http.close()


def world(
    *,
    ip_allowlist: Optional[list[str]] = None,
    batch_limit: int = 200,
    legacy: bool = False,
    phrases: Optional[list[dict[str, Any]]] = None,
    blocks: Optional[list[dict[str, Any]]] = None,
    faults: Optional[list[dict[str, Any]]] = None,
    target_locales: Optional[list[str]] = None,
) -> dict[str, Any]:
    """A seed document: one project, a write key, a read key and an ip_write key."""
    return {
        "config": {"batch_limit": batch_limit, "legacy_omit_capability": legacy},
        "projects": [{
            "id": PROJECT, "title": "Contract", "base_locale": "en-us",
            "target_locales": target_locales or ["it-it", "es-es"],
            "website_url": "https://site.test",
            "phrases": phrases or [], "blocks": blocks or [],
        }],
        "keys": [
            {"key": WRITE_KEY, "project": PROJECT, "type": "write"},
            {"key": READ_KEY, "project": PROJECT, "type": "read"},
            {"key": IP_WRITE_KEY, "project": PROJECT, "type": "ip_write",
             "ip_allowlist": ip_allowlist or []},
        ],
        "faults": faults or [],
    }
