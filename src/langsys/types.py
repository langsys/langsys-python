"""Shared type aliases and data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional, Union

KeyType = Literal["read", "write"]

#: A translation value is either a phrase string (or ``None`` when untranslated) or a
#: content block: a mapping of child-phrase -> translation.
TranslationValue = Union[str, None, Dict[str, Optional[str]]]

#: The catalog for one locale: category -> phrase -> value. Reserved keys such as
#: ``__category__`` also appear at the category level and are ignored by lookups.
Category = Dict[str, TranslationValue]
Catalog = Dict[str, Category]

#: The default category bucket, matching the backend's sentinel.
UNCATEGORIZED = "__uncategorized__"


@dataclass
class Country:
    """A country, localized into the requested display locale (``countries/{loc}``)."""

    code: str
    label: str


@dataclass
class DialCode:
    """An international dialing code (``countries/dial-codes/{loc}``)."""

    country_code: str
    dial_code: str
    name: str


@dataclass
class Currency:
    """A currency, localized into the requested display locale (``currencies/{loc}``)."""

    code: str
    name: str
    symbol: str
    symbol_native: str = ""
    decimal_digits: int = 2
    rounding: float = 0.0


@dataclass
class LocaleInfo:
    """A locale with display + language names (``locales/data``)."""

    code: str
    locale_name: str
    lang_name: str


@dataclass
class LocaleFlat:
    """A locale code with its display name (``locales/flat``)."""

    code: str
    name: str


@dataclass
class Project:
    """Project metadata returned by ``authorize-project`` (the parts SDKs use)."""

    id: str
    title: str
    base_locale: str
    target_locales: list[str] = field(default_factory=list)
    default_locales: dict[str, str] = field(default_factory=dict)
    key_type: KeyType = "read"
    batch_limit: int = 200
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, data: dict[str, Any]) -> Project:
        settings = data.get("langsys_settings") or {}
        items = settings.get("translatable_items") or {}
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title", "")),
            base_locale=str(data.get("base_locale", "")),
            target_locales=list(data.get("target_locales") or []),
            default_locales=dict(data.get("default_locales") or {}),
            key_type="write" if data.get("key_type") == "write" else "read",
            batch_limit=int(items.get("batch_limit", 200)),
            raw=data,
        )
