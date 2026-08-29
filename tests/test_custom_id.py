"""CID-1..4 — content-block identity, anchored on the shared cross-SDK fixture.

The assertion order in this file is the contract, not a style choice:

1. **Codepoints first.** Rebuild every input from its ``U+XXXX`` list and check it
   against the loaded value *before* anything is hashed. The vendoring pipeline
   itself can normalise ``U+2028`` away, and a hash-first check would simply agree
   with the damaged input.
2. **Bytes next**, via ``serialized_hex``. An md5-only check passes across all of
   ASCII and Latin-1 while being wrong above them — the range an English-language
   suite never reaches.
3. **Blob pin**, so a silently edited copy fails locally with no network.
4. **Hash last**, and computed **through the same function the implementation
   hashes** — never a second expression written inside the test. The PHP lane found
   four sites re-deriving their serialization, one inside the assertion meant to be
   checking it; a parallel reimplementation agrees with itself and keeps agreeing
   after the real one moves.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from langsys.registration import (
    canonical_content_block_json,
    generate_custom_id,
    legacy_custom_ids,
)

FIXTURE = Path(__file__).parent / "fixtures" / "custom-id-reference.json"

#: The source blob this copy was vendored from.
#:
#: THE CHECK is the blob SHA: it is content-derived, so it verifies the bytes with no
#: network and keeps working even if the source branch is deleted.
#: THE PROVENANCE is the ref below. One string never does both jobs.
SOURCE_BLOB_SHA = "60dc9b33ecfd5fa3256fca7d36063ceb8ef1a00a"
SOURCE_REF = "langsys-php origin/feature/838_write_key_gating_reland @ 8862841+"

ROWS = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _git_blob_sha(data: bytes) -> str:
    """Git's own object id for this content. Pure-Python so the check needs no git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324


# -- 1. integrity of the vendored file, before any hashing --------------------


def test_the_vendored_fixture_is_the_pinned_blob():
    assert _git_blob_sha(FIXTURE.read_bytes()) == SOURCE_BLOB_SHA, (
        f"vendored fixture no longer matches {SOURCE_BLOB_SHA} (from {SOURCE_REF}). "
        "Re-vendor deliberately; do not edit this copy."
    )


def test_the_fixture_carries_the_columns_the_assertions_need():
    assert ROWS, "fixture is empty"
    for row in ROWS:
        assert {"category", "tokens", "canonical_json", "custom_id"} <= set(row)
        assert "codepoints" in row and "serialized_hex" in row, (
            "this copy predates the codepoints/serialized_hex columns — the "
            "integrity and byte assertions below cannot run without them"
        )


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["canonical_json"][:40])
def test_CODEPOINTS_FIRST_inputs_survived_vendoring(row):
    """Rebuild each input from its codepoints and compare. This runs before any hash
    so that a pipeline which normalised a character away fails *here*, loudly, rather
    than agreeing with itself downstream."""

    def rebuild(points):
        return "".join(chr(int(p[2:], 16)) for p in points)

    assert rebuild(row["codepoints"].get("category", [])) == row["category"]
    # The zero-token row omits `codepoints.tokens` rather than carrying `[]`. Only a
    # genuinely empty token list may take that default — otherwise a missing key
    # would turn this assertion into a no-op, which is the failure it exists to catch.
    token_points = row["codepoints"].get("tokens")
    if token_points is None:
        assert row["tokens"] == [], "codepoints.tokens is missing for a row that has tokens"
        token_points = []
    assert [rebuild(p) for p in token_points] == row["tokens"]


def test_the_fixture_reaches_above_latin1_and_beyond_the_bmp():
    """CID-1 mandates at least one codepoint above U+00FF and one non-BMP. Asserted
    rather than assumed: an implementation can pass all of ASCII and Latin-1 while
    being wrong everywhere else."""
    codepoints = [
        int(p[2:], 16)
        for row in ROWS
        for token in row["codepoints"].get("tokens", [])
        for p in token
    ]
    assert any(c > 0x00FF for c in codepoints)
    assert any(c > 0xFFFF for c in codepoints), "no non-BMP codepoint in the vectors"


# -- 2. bytes, 3. serialization, 4. hash --------------------------------------


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["canonical_json"][:40])
def test_CID1_serialized_bytes_match_the_fixture(row):
    """The byte assertion. An md5 fed UTF-16 code units agrees with a byte-based one
    across all of ASCII and diverges everywhere above it, so comparing only the hash
    would hide exactly the defect this row exists to catch."""
    produced = canonical_content_block_json(row["category"], row["tokens"])
    assert produced.encode("utf-8").hex() == row["serialized_hex"]


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["canonical_json"][:40])
def test_CID1_canonical_json_matches_the_fixture(row):
    assert canonical_content_block_json(row["category"], row["tokens"]) == row["canonical_json"]


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["canonical_json"][:40])
def test_CID1_custom_id_matches_the_fixture(row):
    assert generate_custom_id(row["category"], row["tokens"]) == row["custom_id"]


def test_CID1_the_line_terminator_row_is_present_and_raw():
    """U+2028 arrives routinely via copy-paste from Word and PDF, and the divergence
    is silent: the lookup misses and the block re-registers forever."""
    row = next(r for r in ROWS if " " in "".join(r["tokens"]))
    produced = canonical_content_block_json(row["category"], row["tokens"])
    assert " " in produced, "U+2028 was escaped; it must stay raw"
    assert "e280a8" in produced.encode("utf-8").hex()


def test_CID1_the_token_array_is_ordered():
    assert generate_custom_id("UI", ["Hello", "World"]) != generate_custom_id(
        "UI", ["World", "Hello"]
    )


# -- CID-2 --------------------------------------------------------------------


def test_CID2_none_and_the_sentinel_and_empty_all_hash_as_empty():
    expected = generate_custom_id("", ["Hi"])
    assert generate_custom_id(None, ["Hi"]) == expected
    assert generate_custom_id("__uncategorized__", ["Hi"]) == expected


def test_CID2_a_real_category_is_untouched():
    """The guard changes no existing id: `'' -> ''`, a real category is unchanged, and
    only None/sentinel differ — so no migration follows from adding it."""
    assert generate_custom_id("Blog", ["Hi"]) != generate_custom_id("", ["Hi"])


def test_CID2_no_null_ever_reaches_the_serialization():
    assert "null" not in canonical_content_block_json(None, ["Hi"])


# -- CID-3 --------------------------------------------------------------------


def test_CID3_legacy_ids_are_offered_and_are_not_the_current_form():
    current = generate_custom_id("Blog", ["Hello", "World"])
    legacy = legacy_custom_ids("Blog", ["Hello", "World"])
    assert legacy, "no historical shapes offered"
    assert current not in legacy or len(legacy) > 1


def test_CID3_uncategorised_offers_both_historical_spellings():
    """The old code disagreed with itself about the category slot: one path sent the
    sentinel literally and another omitted it."""
    ids = legacy_custom_ids(None, ["Hi"])
    pipe_empty = hashlib.md5("|".join(["", "Hi"]).encode()).hexdigest()  # noqa: S324
    pipe_sentinel = hashlib.md5(
        "|".join(["__uncategorized__", "Hi"]).encode()
    ).hexdigest()  # noqa: S324
    assert pipe_empty in ids and pipe_sentinel in ids


def test_CID3_the_js_code_unit_form_is_offered_for_ascii_and_non_ascii():
    """Published SDKs registered most stored ids, so the JS code-unit hash is the
    shape most likely to be sitting in a customer's catalog."""
    # ASCII: the code-unit hash coincides with the byte hash, so this row also
    # doubles as a positive control that the legacy list contains the JS form.
    assert generate_custom_id("Blog", ["Hello", "World"]) in legacy_custom_ids(
        "Blog", ["Hello", "World"]
    )
    # Above ASCII they must diverge — otherwise the port is silently a byte hash.
    assert generate_custom_id("ключ", ["Привет"]) not in legacy_custom_ids(
        "ключ", ["Привет"]
    )


def test_CID3_legacy_ids_are_deduplicated():
    ids = legacy_custom_ids("Blog", ["Hi"])
    assert len(ids) == len(set(ids))


def test_CID3_the_current_form_is_the_only_one_ever_emitted():
    """Never emit a historical shape. The registrar builds its own id from
    generate_custom_id, so this pins the producing path rather than the lookup one."""
    from langsys.registration import Registrar

    item = Registrar._content_block_item("<p>Hi</p>", ["Hi"], category="Blog")
    assert item["custom_id"] == generate_custom_id("Blog", ["Hi"])
    assert item["custom_id"] not in legacy_custom_ids("Blog", ["Hi"])[1:]


# -- CID-4: verify a legacy match on content before attaching -----------------


def _catalog_with(block_id, phrases_to_translations):
    return {"status": True, "data": {"CAT": {block_id: phrases_to_translations}}}


def test_CID4_a_block_stored_under_a_legacy_id_still_resolves(httpx_mock):
    """CID-3's tolerating half, end to end: content registered by an older SDK keeps
    resolving instead of being orphaned and re-registered forever."""
    pytest.importorskip("lxml")
    import re as _re

    from langsys import LangsysClient
    from langsys.cache import MemoryCache

    html = "<div><p>Hello there</p><p>Second line</p></div>"
    phrases = ["Hello there", "Second line"]
    legacy_id = legacy_custom_ids("CAT", phrases)[-1]  # the pipe-join shape
    assert legacy_id != generate_custom_id("CAT", phrases)

    httpx_mock.add_response(
        url=_re.compile(r"https://api\.test/api/translations"),
        json=_catalog_with(legacy_id, {"Hello there": "Hola", "Second line": "Segunda"}),
        is_reusable=True,
    )
    client = LangsysClient(
        "k", "p", api_url="https://api.test/api", cache=MemoryCache(), base_locale="en-us"
    )
    out = client.translate_content_block(html, category="CAT")
    assert "Hola" in out and "Segunda" in out
    assert client.pending_content_blocks == [], "resolved block must not re-register"


def test_CID4_a_legacy_id_whose_content_differs_is_declined(httpx_mock):
    """The historical id spaces are not injective, so a hit is not proof. A false
    positive attaches the wrong text; declining leaves the block to register cleanly."""
    pytest.importorskip("lxml")
    import re as _re

    from langsys import LangsysClient
    from langsys.cache import MemoryCache

    html = "<div><p>Hello there</p><p>Second line</p></div>"
    phrases = ["Hello there", "Second line"]
    legacy_id = legacy_custom_ids("CAT", phrases)[-1]

    httpx_mock.add_response(
        url=_re.compile(r"https://api\.test/api/translations"),
        # Same id, DIFFERENT content — a collision, not a match.
        json=_catalog_with(legacy_id, {"Totally": "Otro", "Different": "Distinto"}),
        is_reusable=True,
    )
    client = LangsysClient(
        "k", "p", api_url="https://api.test/api", cache=MemoryCache(), base_locale="en-us"
    )
    out = client.translate_content_block(html, category="CAT")
    assert "Otro" not in out and "Distinto" not in out
    assert out == html, "declined block must degrade to source HTML"
    assert client.pending_content_blocks, "a declined legacy match should still register"
