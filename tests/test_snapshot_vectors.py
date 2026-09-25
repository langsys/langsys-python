"""SNAP-1 against the shared `snapshot-vectors.json` (authored in langsys-js-typescript): the exact
canonical bytes and checksum of every row, the loader's refusals by name, and a re-encoded load."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from langsys.snapshot import Snapshot, SnapshotError, _canonical, _checksum

VECTORS_FILE = Path(__file__).parent / "fixtures" / "snapshot-vectors.json"
#: THE CHECK: the blob. THE PROVENANCE: the ref.
VECTORS_BLOB = "594bd77a0289abfdf608508ac93cc9f4c4f88459"
VECTORS_REF = "langsys-js-typescript a639ae8 tests/fixtures/snapshot-vectors.json"
DOC = json.loads(VECTORS_FILE.read_text(encoding="utf-8"))
ids = lambda row: row["id"]  # noqa: E731
HASHED = ("project_id", "generated_at", "base_locale", "locales", "categories", "catalog")


def test_the_vendored_vectors_are_the_pinned_blob():
    data = VECTORS_FILE.read_bytes()
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == VECTORS_BLOB  # noqa: S324


@pytest.mark.parametrize("row", DOC["rows"], ids=ids)
def test_SNAP1_canonical_bytes_and_checksum(row):
    payload = {k: row["document"][k] for k in HASHED}
    assert _canonical(payload) == row["canonical"]
    assert _checksum(payload) == row["checksum"]
    Snapshot.load(json.dumps(row["document"]))


@pytest.mark.parametrize("row", DOC["refusals"], ids=ids)
def test_SNAP1_refusals_name_their_reason(row):
    with pytest.raises(SnapshotError) as refused:
        Snapshot.load(json.dumps(row["document"]))
    assert row["refuse"].replace("-", " ") in str(refused.value).replace("-", " ")


@pytest.mark.parametrize("row", DOC["loads"], ids=ids)
def test_SNAP1_any_json_encoding_loads(row):
    document = row["document"]
    text = document if isinstance(document, str) else json.dumps(document)
    assert Snapshot.load(text).catalog(row["locale"]) == row["expect_catalog"]
