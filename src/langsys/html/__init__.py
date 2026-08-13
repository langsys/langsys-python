"""Server-side HTML translation: content blocks and full pages.

These features need an HTML parser; install the extra::

    pip install langsys[html]

Everything here mirrors the PHP base SDK's HTML layer. This package is import-safe
without lxml — the lxml-backed helpers are imported lazily on first access so that
``import langsys`` never requires the optional dependency.
"""

from __future__ import annotations

from typing import Any

from .attributes import DEFAULT_TRANSLATABLE_ATTRIBUTES

__all__ = [
    "DEFAULT_TRANSLATABLE_ATTRIBUTES",
    "extract_phrases",
    "apply_block_translations",
    "normalize_whitespace",
    "translate_page",
]

_LAZY = {
    "extract_phrases": "parser",
    "apply_block_translations": "parser",
    "normalize_whitespace": "parser",
    "translate_page": "page",
}


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(f"{__name__}.{module}"), name)
