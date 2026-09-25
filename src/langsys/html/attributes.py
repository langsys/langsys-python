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


# -- the block-identity attribute (MARK-3) ------------------------------------
#
# `data-ls-contentblock` (and its `data-langsys-contentblock` spelling) means one of three things
# by its value, trimmed and compared case-insensitively. One classifier answers for every reader,
# so the block path and the page path cannot drift apart about it.

#: A declaration: register this element as one content block. Bare and empty are here because
#: presence is intent - the convention every boolean marker in the fleet follows - and the truthy
#: words because existing markup carries them. No md5 `custom_id` can be one of these.
BLOCK_DECLARATION_VALUES = frozenset({"", "true", "1", "yes"})

#: An opt-out: the attribute is ignored and the element is walked as ordinary markup. Only these
#: two, as for every marker in the fleet; `no` and `off` are not opt-outs.
MARKER_OPT_OUT_VALUES = frozenset({"false", "0"})


def classify_block_attribute(value: Optional[str]) -> str:
    """``"absent"`` | ``"declaration"`` | ``"opt-out"`` | ``"identity"``.

    ``identity`` means the value is the host's `custom_id`, stamped by a renderer: the host
    renders from the catalog entry under that id, registers nothing, and is excised from any
    enclosing walk.
    """
    if value is None:
        return "absent"
    normalized = value.strip().lower()
    if normalized in BLOCK_DECLARATION_VALUES:
        return "declaration"
    if normalized in MARKER_OPT_OUT_VALUES:
        return "opt-out"
    return "identity"


def marker_is_on(value: Optional[str]) -> bool:
    """A presence marker (the phrase marker, the resolved marker): present and not opted out."""
    return value is not None and value.strip().lower() not in MARKER_OPT_OUT_VALUES
