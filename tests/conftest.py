from __future__ import annotations

from collections.abc import Iterator

import pytest
from contract import ContractDouble


@pytest.fixture(scope="module")
def double() -> Iterator[ContractDouble]:
    """The shared contract double, started once per test file (spec CONF-2)."""
    running = ContractDouble()
    try:
        yield running
    finally:
        running.close()
