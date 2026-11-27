"""Pure catalog-lookup logic (no I/O), so it's trivially unit-testable.

The contract every Langsys SDK shares: **an untranslated phrase always renders as the
source phrase**. A phrase is "missing" (eligible for discovery) only when its key is
absent from the category — a present-but-``null`` value means it's registered and simply
not translated yet, so it falls back to the base phrase *without* re-queuing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple, Optional, Sequence

from ._log import logger
from .registration import legacy_custom_ids
from .types import UNCATEGORIZED, Catalog


@dataclass(frozen=True)
class Miss:
    """What a miss fallback is asked (FRM-3): the phrase looked up, the locale being rendered, the
    category, and the argument as the call wrote it, which is the key for a key-style call."""

    phrase: str
    locale: str
    category: Optional[str]
    argument: str


@dataclass(frozen=True)
class Resolved:
    """A translate call's text, and who wrote it (FRM-3, FRM-8): `"catalog"` (a translation from
    Langsys), `"fallback"` (the framework's own language files, through the miss fallback) or
    `"source"`. A binding's raw-printing paths escape catalog text and only catalog text."""

    text: str
    origin: str


class Lookup(NamedTuple):
    """One phrase lookup: its text, whether it is the current category's translation, the previous
    category it came from instead (MOVE-5), and who wrote it."""

    text: str
    current: bool
    prior: Optional[str]
    origin: str


class Resolution:
    """Result of a catalog lookup."""

    __slots__ = ("text", "missing", "translated")

    def __init__(self, text: str, missing: bool, translated: bool = False) -> None:
        #: The resolved string (a translation, or the base phrase on fallback).
        self.text = text
        #: True only when the phrase key is absent — i.e. a candidate for discovery.
        self.missing = missing
        #: True when the text is the catalog's translation, not the source on fallback.
        self.translated = translated


def displayable(value: Any) -> bool:
    """CAT-2 - a translation renders only as a non-empty string; absent, null or empty is source."""
    return isinstance(value, str) and value != ""


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
            if isinstance(value, str) and value:
                return Resolution(value, missing=False, translated=True)
        return Resolution(phrase, missing=False)

    if phrase in cat:
        raw = cat[phrase]
        if isinstance(raw, str) and raw:
            return Resolution(raw, missing=False, translated=True)
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


# -- MOVE-4: the previous category's entry -------------------------------------------------------


def previous_entry(catalog: Catalog, previous: str, phrase: str) -> Optional[str]:
    """The translation the catalog holds for `phrase` under its previous category, by CAT-2's rule.
    `''` reads the no-category entries, under the catalog's own key for them."""
    cat = catalog.get(previous or UNCATEGORIZED)
    value = cat.get(phrase) if isinstance(cat, dict) else None
    return value if isinstance(value, str) and value else None


def block_translated(block: Any) -> bool:
    """A block entry is a translation when it translates at least one of its phrases; a block with
    no phrases, or none translated, is no translation (MOVE-4)."""
    return isinstance(block, dict) and any(displayable(v) for v in block.values())


def previous_block(
    catalog: Catalog,
    block: Optional[dict[str, Any]],
    previous: Optional[str],
    phrases: Sequence[str],
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """What a block renders from, and the previous category's id when that is where it came from.

    The current category's entry, its historical ids included, is read first (`block`); only when
    it is no translation does the previous category's id, then its historical ids, decide. The
    current entry renders as before when the previous category has nothing either."""
    if previous is None or block_translated(block):
        return block, None
    from .registration import generate_custom_id

    prior_id = generate_custom_id(previous, phrases)
    earlier = lookup_block(catalog.get(previous or UNCATEGORIZED), previous, prior_id, phrases)
    if block_translated(earlier):
        return earlier, prior_id
    return block, None
