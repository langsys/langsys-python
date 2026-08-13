"""Pure catalog-lookup logic (no I/O), so it's trivially unit-testable.

The contract every Langsys SDK shares: **an untranslated phrase always renders as the
source phrase**. A phrase is "missing" (eligible for discovery) only when its key is
absent from the category — a present-but-``null`` value means it's registered and simply
not translated yet, so it falls back to the base phrase *without* re-queuing.
"""

from __future__ import annotations

from typing import Optional

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
