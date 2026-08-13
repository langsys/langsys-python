"""The default translatable-attribute list (no lxml dependency).

Kept separate from :mod:`langsys.html.parser` so the client can reference the defaults
without pulling in lxml (which is an optional extra). Matches the PHP SDK exactly.
"""

from __future__ import annotations

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
