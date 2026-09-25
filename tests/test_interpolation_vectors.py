"""ICU-1..6 against the shared interpolation vectors (authored in langsys-php-sdk).

Every SDK renders these 25 rows identically. The last two are ICU-6's vector: `{count}` inside a
branch of its own plural, which one platform formatter fails on and ours renders natively.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from langsys.interpolate import interpolate

VECTORS_FILE = Path(__file__).parent / "fixtures" / "interpolation-reference.json"
#: THE CHECK: the blob. THE PROVENANCE: the ref.
VECTORS_BLOB = "017bffdd1d83a1b0a00a91f0d157a7fff726ee90"
VECTORS_REF = "langsys-php-sdk c11a711 tests/fixtures/interpolation-reference.json"
ROWS = json.loads(VECTORS_FILE.read_text(encoding="utf-8"))


def row_id(row: dict) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", row["description"].split(".")[0]).strip("-")


def test_the_vendored_vectors_are_the_pinned_blob():
    data = VECTORS_FILE.read_bytes()
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == VECTORS_BLOB  # noqa: S324


@pytest.mark.parametrize("row", ROWS, ids=row_id)
def test_every_interpolation_vector_renders_as_the_fleet_does(row):
    assert interpolate(row["template"], row.get("params") or {}, row["locale"]) == row["expected"]
