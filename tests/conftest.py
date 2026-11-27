from __future__ import annotations

from collections.abc import Iterator

import pytest
from clone import Clone


@pytest.fixture(scope="module")
def clone(request: pytest.FixtureRequest) -> Iterator[Clone]:
    """The contract fixture for one test file (spec CONF-2): the forwarder to the SDK test clone,
    started once per file, over the worlds the file's `SEED` module declares. It errors, never
    skips, when no clone is named."""
    seed = getattr(request.module, "SEED", None)
    if seed is None:
        raise RuntimeError(f"{request.module.__name__} uses the clone but names no SEED module")
    running = Clone(seed)
    try:
        yield running
    finally:
        running.close()
