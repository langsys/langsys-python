"""The default translatable-attribute list (no lxml dependency).

Kept separate from :mod:`langsys.html.parser` so the client can reference the defaults
without pulling in lxml (which is an optional extra). Matches the PHP SDK exactly.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_TRANSLATABLE_ATTRIBUTES: tuple[str, ...] = (
    "placeholder",
    "alt",
    "title",
    "label",
    "aria-label",
    "aria-placeholder",
    "aria-description",
    "aria-valuetext",
    "aria-roledescription",
    "data-error",
    "data-error-message",
    "data-validation-message",
    "data-invalid-message",
    "data-required-message",
    "data-pattern-message",
    "data-confirm",
    "data-tooltip",
    "data-title",
    "data-content",
    "data-original-title",
    "data-bs-title",
    "data-bs-content",
    "data-loading-text",
    "data-success-message",
    "data-warning-message",
    "data-empty-message",
    "data-placeholder",
)


# -- the block-identity attribute --------------------------------------------
#
# `data-ls-contentblock` (and its `data-langsys-` spelling) carries two different
# meanings and they must be told apart in exactly ONE place. An earlier revision had a
# reader in `page.py` and a reader eight lines below it disagreeing about what `""`,
# `"0"` and `"false"` meant — one walked the subtree normally, the other read it as
# another SDK's id and excised it from discovery entirely. That drift is what this
# module exists to make impossible.

#: MARK-1 — the attribute is an **identity**. This SDK additionally lets an author
#: *declare* a subtree to be one block with a truthy flag; these are those flags.
BLOCK_DECLARATION_VALUES = frozenset({"1", "true", "yes", "on"})

#: The negative forms of the same flag. These are **not** identities: a resolved `custom_id`
#: is never `0` or `false`, so reading them as one gains nothing and costs the subtree its
#: discovery.
BLOCK_OPT_OUT_VALUES = frozenset({"0", "false", "off", "no"})

#: CONTESTED, awaiting the operator's ruling: what the BARE attribute means.
#: `<div data-ls-contentblock>` is the natural boolean-attribute spelling and parses as `""`.
#: This SDK reads it as an opt-out and walks the subtree as ordinary content. PHP's marker
#: helper, which the TS core says it mirrors, treats presence as intent and reads it as a
#: declaration. The identity class is not contested: an id value is excised on every path.
#:
#: Kept as one constant so the ruling flips one line: `"declaration"` adopts PHP's reading.
BARE_BLOCK_ATTRIBUTE = "opt-out"


def classify_block_attribute(value: Optional[str]) -> str:
    """``"absent"`` | ``"declaration"`` | ``"opt-out"`` | ``"identity"``.

    ``opt-out`` and ``absent`` both mean *walk this subtree as ordinary content*; they
    are distinct only so a caller can tell "the author said no" from "the author said
    nothing". ``identity`` means another SDK already owns this block.
    """
    if value is None:
        return "absent"
    normalized = value.strip().lower()
    if normalized == "":
        return BARE_BLOCK_ATTRIBUTE
    if normalized in BLOCK_DECLARATION_VALUES:
        return "declaration"
    if normalized in BLOCK_OPT_OUT_VALUES:
        return "opt-out"
    return "identity"
