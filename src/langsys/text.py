"""TOK-2's C0 strip, shared by every path that turns text into an id input or a catalog key.

U+0001-U+0008, U+000B, U+000C and U+000E-U+001F are REMOVED - not mapped to a space - before
anything collapses, from text nodes, translatable attributes, `t()` keys and interpolation
templates alike, on register and on lookup. Parsers disagree about these characters (libxml2
before 2.14 deletes them from DOM text before any SDK code runs; 2.14+ keeps them), and removal
is the one treatment every parser can agree with, so it is what makes an id independent of the
parser for them. VT and FF are in JavaScript's whitespace class, but the strip deletes them
first: `a\x0bb` is `ab`. TAB, LF and CR are not stripped; they collapse like any whitespace.
NUL is deliberately outside the set: no strip converges it across parsers.

Written as integers, like the collapse set, so the set is reviewable.
"""

from __future__ import annotations

from typing import Optional

C0_STRIPPED = frozenset([*range(0x01, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20)])
_DELETE = dict.fromkeys(C0_STRIPPED)


def strip_c0(text: Optional[str]) -> str:
    """`text` with the 28 C0 controls removed."""
    return text.translate(_DELETE) if text else ""
