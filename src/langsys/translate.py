"""Pure catalog-lookup logic (no I/O), so it's trivially unit-testable.

The contract every Langsys SDK shares: **an untranslated phrase always renders as the
source phrase**. A phrase is "missing" (eligible for discovery) only when its key is
absent from the category — a present-but-``null`` value means it's registered and simply
not translated yet, so it falls back to the base phrase *without* re-queuing.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from ._log import logger
from .registration import legacy_custom_ids
from .types import UNCATEGORIZED, Catalog


class Resolution:
    """Result of a catalog lookup."""

    __slots__ = ("text", "missing")

    def __init__(self, text: str, missing: bool) -> None:
        #: The resolved string (a translation, or the base phrase on fallback).
        self.text = text
        #: True only when the phrase key is absent — i.e. a candidate for discovery.
        self.missing = missing


def resolve(
    catalog: Catalog,
    phrase: str,
    category: Optional[str] = None,
    content_block_id: Optional[str] = None,
) -> Resolution:
    cat = catalog.get(category or UNCATEGORIZED)
    if not isinstance(cat, dict):
        return Resolution(phrase, missing=content_block_id is None)

    if content_block_id is not None:
        block = cat.get(content_block_id)
        if isinstance(block, dict):
            value = block.get(phrase)
            return Resolution(value if isinstance(value, str) and value else phrase, missing=False)
        return Resolution(phrase, missing=False)

    if phrase in cat:
        raw = cat[phrase]
        if isinstance(raw, str) and raw != "":
            return Resolution(raw, missing=False)
        # present but null/empty/content-block-collision -> base phrase, already registered
        return Resolution(phrase, missing=False)

    return Resolution(phrase, missing=True)


def _block_matches(block: dict[str, Any], phrases: Sequence[str]) -> bool:
    """CID-4 — does this block's content actually match what we were looking for?

    Compared as normalised for hashing, not as stored. A set comparison, because the
    catalog returns a content block as a map keyed by source phrase and has therefore
    already lost the order by the time this runs — which the rule allows explicitly.
    It still defeats every collision mode, since all of them are collisions over
    *differing* content.
    """
    return set(block.keys()) == set(phrases)


def lookup_block(
    cat: Any, category: Optional[str], custom_id: str, phrases: Sequence[str]
) -> Optional[dict[str, Any]]:
    """Find a registered content block, tolerating historical ids (CID-3).

    A legacy hit is verified against the block's content before being attached to
    (CID-4). The failure direction is deliberate: a false positive attaches the wrong
    text and someone eventually notices, while a false negative silently restores
    nothing and is indistinguishable from "this block had no legacy id".
    """
    if not isinstance(cat, dict):
        return None

    block = cat.get(custom_id)
    if isinstance(block, dict):
        return block

    for legacy_id in legacy_custom_ids(category, phrases):
        candidate = cat.get(legacy_id)
        if not isinstance(candidate, dict):
            continue
        if not _block_matches(candidate, phrases):
            # A collision, not a match. These id spaces are not injective.
            logger.debug(
                "langsys: historical content-block id %s resolved to a block whose "
                "phrases differ; declining it rather than attaching to the wrong text.",
                legacy_id,
            )
            continue
        logger.debug(
            "langsys: content block resolved under a historical id (%s). Its "
            "translations still apply; it is never re-keyed and never re-registered "
            "under that id.",
            legacy_id,
        )
        return candidate
    return None
