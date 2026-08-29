"""Registering phrases and content blocks (write-key only).

Builds ``translatable-items`` payloads, chunks them to the project's batch limit, and
POSTs. ``generate_custom_id`` is the canonical content-block hash — see CID-1..4.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, Sequence, Union

from .http import HttpClient

PhraseInput = Union[str, dict[str, Any]]

#: The catalog namespaces uncategorised items under this token. It is a **cache-lookup
#: namespace and never a hash input** (CID-2) — hashing it produces an id no other SDK
#: computes.
_UNCATEGORIZED = "__uncategorized__"


def _hash_category(category: Optional[str]) -> str:
    """CID-2 — no-category is ``''``: never ``None``, never the sentinel.

    Enforced here *and* checked at every caller. Enforcing inside the id function is
    right because the caller list grows; but whether the rule holds is a fact about
    the callers, so both matter. Adding this changes no existing id — ``'' -> ''``,
    a real category is unchanged, and only ``None``/sentinel differ.
    """
    if category is None or category == _UNCATEGORIZED:
        return ""
    return category


def canonical_content_block_json(category: Optional[str], phrases: Sequence[str]) -> str:
    """The exact string whose UTF-8 bytes are hashed to produce a ``custom_id``.

    Extracted so the cross-implementation assertion compares the SAME bytes the id
    function hashes, rather than a second expression that happens to look the same.
    The PHP lane found four sites re-deriving this, one of them inside the test meant
    to be checking it — a parallel reimplementation agrees with itself, and keeps
    agreeing after the real one moves.

    Two settings are required to match ``JSON.stringify`` byte-for-byte:

    * ``ensure_ascii=False`` — Python's default escapes non-ASCII to ``\\uXXXX``.
      It also leaves ``U+2028``/``U+2029`` **raw**, which is what the wire form needs;
      PHP requires a third flag for that, Python does not.
    * ``separators=(",", ":")`` — Python's default inserts ``", "`` and ``": "``,
      which changes the bytes on *every* row, ASCII included.
    """
    return json.dumps(
        [_hash_category(category), list(phrases)], ensure_ascii=False, separators=(",", ":")
    )


def generate_custom_id(category: Optional[str], phrases: Sequence[str]) -> str:
    """CID-1 — the one byte-identical content-block hash, shared by every SDK.

    ``md5`` over the canonical JSON of ``[category, tokens]``. The array is ordered:
    ``["Hello","World"]`` and ``["World","Hello"]`` are different blocks. Anchored on
    ``tests/fixtures/custom-id-reference.json``.
    """
    payload = canonical_content_block_json(category, phrases)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()  # noqa: S324 - id hash, not security


# -- historical id shapes (CID-3) --------------------------------------------
#
# Lookup only. NEVER emit these. A conforming SDK emits only the CID-1 form and
# *accepts* historical shapes so content registered by an older SDK keeps resolving.
# Re-keying stored rows is prohibited: these id spaces are not injective, so an old
# id does not identify one block.


def _md5_utf16_code_units(text: str) -> str:
    """The JS core's ``md5Legacy`` — an MD5 fed UTF-16 code units, not bytes.

    Ported from the implementation in ``langsys-js-typescript`` ``src/utils.ts``, not
    from a description of it, and verified against that implementation across CJK,
    Cyrillic, Greek, Hebrew, Arabic and non-BMP input. A description-based port gets
    ASCII and Latin-1 right and everything above them wrong, which is exactly the
    range an English-language test suite never reaches.

    The packing is an **unmasked** shift over 16-bit units, so a unit above ``0xFF``
    bleeds into the next byte lane. That is the historical behaviour, bug included;
    reproducing it faithfully is the whole point.
    """
    # JS string length and indexing are over UTF-16 code units, so surrogate pairs
    # count as two. Python indexes codepoints, hence the explicit re-encoding.
    units = [
        int.from_bytes(text.encode("utf-16-le")[i : i + 2], "little")
        for i in range(0, len(text.encode("utf-16-le")), 2)
    ]
    n = len(units)

    nblk = ((n + 8) >> 6) + 1
    blks = [0] * (nblk * 16)
    for i, unit in enumerate(units):
        blks[i >> 2] |= (unit << ((i % 4) * 8)) & 0xFFFFFFFF
    blks[n >> 2] |= (0x80 << ((n % 4) * 8)) & 0xFFFFFFFF
    blks[nblk * 16 - 2] = (n * 8) & 0xFFFFFFFF

    def add(x: int, y: int) -> int:
        return (x + y) & 0xFFFFFFFF

    def rol(num: int, count: int) -> int:
        num &= 0xFFFFFFFF
        return ((num << count) | (num >> (32 - count))) & 0xFFFFFFFF

    def cm(q: int, a: int, b: int, x: int, s: int, t: int) -> int:
        return add(rol(add(add(a, q), add(x, t)), s), b)

    def ff(a: int, b: int, c: int, d: int, x: int, s: int, t: int) -> int:
        return cm((b & c) | ((~b & 0xFFFFFFFF) & d), a, b, x, s, t)

    def gg(a: int, b: int, c: int, d: int, x: int, s: int, t: int) -> int:
        return cm((b & d) | (c & (~d & 0xFFFFFFFF)), a, b, x, s, t)

    def hh(a: int, b: int, c: int, d: int, x: int, s: int, t: int) -> int:
        return cm(b ^ c ^ d, a, b, x, s, t)

    def ii(a: int, b: int, c: int, d: int, x: int, s: int, t: int) -> int:
        return cm(c ^ (b | (~d & 0xFFFFFFFF)), a, b, x, s, t)

    a, b, c, d = 1732584193, 4023233417, 2562383102, 271733878
    for i in range(0, len(blks), 16):
        olda, oldb, oldc, oldd = a, b, c, d
        x = blks[i : i + 16]
        a = ff(a, b, c, d, x[0], 7, 3614090360)
        d = ff(d, a, b, c, x[1], 12, 3905402710)
        c = ff(c, d, a, b, x[2], 17, 606105819)
        b = ff(b, c, d, a, x[3], 22, 3250441966)
        a = ff(a, b, c, d, x[4], 7, 4118548399)
        d = ff(d, a, b, c, x[5], 12, 1200080426)
        c = ff(c, d, a, b, x[6], 17, 2821735955)
        b = ff(b, c, d, a, x[7], 22, 4249261313)
        a = ff(a, b, c, d, x[8], 7, 1770035416)
        d = ff(d, a, b, c, x[9], 12, 2336552879)
        c = ff(c, d, a, b, x[10], 17, 4294925233)
        b = ff(b, c, d, a, x[11], 22, 2304563134)
        a = ff(a, b, c, d, x[12], 7, 1804603682)
        d = ff(d, a, b, c, x[13], 12, 4254626195)
        c = ff(c, d, a, b, x[14], 17, 2792965006)
        b = ff(b, c, d, a, x[15], 22, 1236535329)

        a = gg(a, b, c, d, x[1], 5, 4129170786)
        d = gg(d, a, b, c, x[6], 9, 3225465664)
        c = gg(c, d, a, b, x[11], 14, 643717713)
        b = gg(b, c, d, a, x[0], 20, 3921069994)
        a = gg(a, b, c, d, x[5], 5, 3593408605)
        d = gg(d, a, b, c, x[10], 9, 38016083)
        c = gg(c, d, a, b, x[15], 14, 3634488961)
        b = gg(b, c, d, a, x[4], 20, 3889429448)
        a = gg(a, b, c, d, x[9], 5, 568446438)
        d = gg(d, a, b, c, x[14], 9, 3275163606)
        c = gg(c, d, a, b, x[3], 14, 4107603335)
        b = gg(b, c, d, a, x[8], 20, 1163531501)
        a = gg(a, b, c, d, x[13], 5, 2850285829)
        d = gg(d, a, b, c, x[2], 9, 4243563512)
        c = gg(c, d, a, b, x[7], 14, 1735328473)
        b = gg(b, c, d, a, x[12], 20, 2368359562)

        a = hh(a, b, c, d, x[5], 4, 4294588738)
        d = hh(d, a, b, c, x[8], 11, 2272392833)
        c = hh(c, d, a, b, x[11], 16, 1839030562)
        b = hh(b, c, d, a, x[14], 23, 4259657740)
        a = hh(a, b, c, d, x[1], 4, 2763975236)
        d = hh(d, a, b, c, x[4], 11, 1272893353)
        c = hh(c, d, a, b, x[7], 16, 4139469664)
        b = hh(b, c, d, a, x[10], 23, 3200236656)
        a = hh(a, b, c, d, x[13], 4, 681279174)
        d = hh(d, a, b, c, x[0], 11, 3936430074)
        c = hh(c, d, a, b, x[3], 16, 3572445317)
        b = hh(b, c, d, a, x[6], 23, 76029189)
        a = hh(a, b, c, d, x[9], 4, 3654602809)
        d = hh(d, a, b, c, x[12], 11, 3873151461)
        c = hh(c, d, a, b, x[15], 16, 530742520)
        b = hh(b, c, d, a, x[2], 23, 3299628645)

        a = ii(a, b, c, d, x[0], 6, 4096336452)
        d = ii(d, a, b, c, x[7], 10, 1126891415)
        c = ii(c, d, a, b, x[14], 15, 2878612391)
        b = ii(b, c, d, a, x[5], 21, 4237533241)
        a = ii(a, b, c, d, x[12], 6, 1700485571)
        d = ii(d, a, b, c, x[3], 10, 2399980690)
        c = ii(c, d, a, b, x[10], 15, 4293915773)
        b = ii(b, c, d, a, x[1], 21, 2240044497)
        a = ii(a, b, c, d, x[8], 6, 1873313359)
        d = ii(d, a, b, c, x[15], 10, 4264355552)
        c = ii(c, d, a, b, x[6], 15, 2734768916)
        b = ii(b, c, d, a, x[13], 21, 1309151649)
        a = ii(a, b, c, d, x[4], 6, 4149444226)
        d = ii(d, a, b, c, x[11], 10, 3174756917)
        c = ii(c, d, a, b, x[2], 15, 718787259)
        b = ii(b, c, d, a, x[9], 21, 3951481745)

        a, b, c, d = add(a, olda), add(b, oldb), add(c, oldc), add(d, oldd)

    return "".join(_hex_le(v) for v in (a, b, c, d))


def _hex_le(num: int) -> str:
    hc = "0123456789abcdef"
    out = ""
    for j in range(4):
        out += hc[(num >> (j * 8 + 4)) & 0x0F] + hc[(num >> (j * 8)) & 0x0F]
    return out


def legacy_custom_ids(category: Optional[str], phrases: Sequence[str]) -> list[str]:
    """CID-3 — every historical id shape this block could be stored under.

    **Lookup only; never emit these.** Most-likely first, de-duplicated:

    1. the JS core's code-unit hash (published SDKs registered most stored ids), and
    2. the two PHP pipe-join variants, which this SDK's own pre-release form also
       produced — joining on an unescaped delimiter, so category ``UI|Buy now`` with
       no phrases collides with category ``UI`` and phrase ``Buy now``.

    A match from here MUST be verified against the block's content before it is
    attached to (CID-4): none of these spaces is injective.
    """
    tokens = list(phrases)
    if category is None or category in ("", _UNCATEGORIZED):
        # Uncategorised: both spellings the old paths could have written.
        slots = ["", _UNCATEGORIZED]
    else:
        slots = [category]

    ids: list[str] = []
    for slot in slots:
        js_form = json.dumps([slot, tokens], ensure_ascii=False, separators=(",", ":"))
        ids.append(_md5_utf16_code_units(js_form))
    for slot in slots:
        joined = "|".join([slot, *tokens])
        ids.append(hashlib.md5(joined.encode("utf-8")).hexdigest())  # noqa: S324
    return list(dict.fromkeys(ids))


class Registrar:
    """Posts translatable items to nova. Caller is responsible for write-key gating."""

    def __init__(self, http: HttpClient, project_id: str, *, batch_limit: int = 200) -> None:
        self._http = http
        self._project_id = project_id
        self.batch_limit = batch_limit if batch_limit > 0 else 200

    def register_phrases(self, phrases: Sequence[PhraseInput]) -> list[dict[str, Any]]:
        items = [self._phrase_item(p) for p in phrases]
        return self._post_items(items)

    def register_content_block(
        self,
        content: str,
        phrases: Sequence[str],
        *,
        category: Optional[str] = None,
        custom_id: Optional[str] = None,
        label: Optional[str] = None,
    ) -> dict[str, Any]:
        item = self._content_block_item(
            content, phrases, category=category, custom_id=custom_id, label=label
        )
        responses = self._post_items([item])
        return responses[0] if responses else {"status": True}

    def register_content_blocks(
        self, blocks: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """REG-9 — register many blocks in batch-limit-sized POSTs.

        One POST per block turns a first uncached render of a 40-block page into 40
        sequential blocking requests while the visitor waits.
        """
        items = [
            self._content_block_item(
                block["content"],
                block["phrases"],
                category=block.get("category"),
                custom_id=block.get("custom_id"),
                label=block.get("label"),
            )
            for block in blocks
        ]
        return self._post_items(items)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _phrase_item(phrase: PhraseInput) -> dict[str, Any]:
        if isinstance(phrase, str):
            return {"type": "phrase", "phrase": phrase, "category": None, "translatable": True}
        item = {
            "type": "phrase",
            "phrase": phrase["phrase"],
            "category": phrase.get("category"),
            "translatable": phrase.get("translatable", True),
        }
        return item

    @staticmethod
    def _content_block_item(
        content: str,
        phrases: Sequence[str],
        *,
        category: Optional[str] = None,
        custom_id: Optional[str] = None,
        label: Optional[str] = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "type": "content_block",
            "custom_id": custom_id or generate_custom_id(category, phrases),
            "content": content,
            "phrases": [{"phrase": p} for p in phrases],
        }
        # WIRE-3 — an empty category serializes as null; the sentinel is
        # server-internal and is never sent.
        if category is not None and category != _UNCATEGORIZED:
            item["category"] = category
        if label is not None:
            item["label"] = label
        return item

    def _post_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        for start in range(0, len(items), self.batch_limit):
            chunk = items[start : start + self.batch_limit]
            responses.append(
                self._http.post(
                    "translatable-items",
                    json={"project_id": self._project_id, "translatable_items": chunk},
                )
            )
        return responses
