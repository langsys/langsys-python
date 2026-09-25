"""Langsys — the official Python SDK for realtime, continuous translations.

Quick start::

    from langsys import LangsysClient

    client = LangsysClient(api_key="…", project_id="…")   # or LANGSYS_API_KEY / LANGSYS_PROJECT_ID
    client.set_locale("es-ES")
    print(client.translate("Hello, {name}!", category="Greetings", params={"name": "Sarah"}))

The phrase in your code is the lookup key *and* the base-language default — no keys
file, no extraction step. Untranslated phrases render as the source phrase.
"""

from __future__ import annotations

from .cache import FileCache, MemoryCache, NullCache
from .client import LangsysClient
from .exceptions import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    LangsysError,
    NetworkError,
    PaymentRequiredError,
    RateLimitError,
    ValidationError,
)
from .interpolate import interpolate, is_icu
from .locale import (
    canonicalize_locale,
    detect_preferred_locale,
    normalize_locale,
    parse_accept_language,
)
from .observable import LocaleSource, Signal
from .registration import generate_custom_id
from .scope import RequestScope, begin_request_scope, end_request_scope, request_scope
from .types import Country, Currency, DialCode, LocaleFlat, LocaleInfo, Project

__version__ = "0.1.0"

__all__ = [
    "RequestScope",
    "begin_request_scope",
    "end_request_scope",
    "request_scope",
    "LangsysClient",
    "Project",
    "Country",
    "Currency",
    "DialCode",
    "LocaleInfo",
    "LocaleFlat",
    "Signal",
    "LocaleSource",
    "interpolate",
    "is_icu",
    "generate_custom_id",
    "canonicalize_locale",
    "normalize_locale",
    "parse_accept_language",
    "detect_preferred_locale",
    "FileCache",
    "MemoryCache",
    "NullCache",
    # exceptions
    "LangsysError",
    "ConfigurationError",
    "ApiError",
    "AuthenticationError",
    "AuthorizationError",
    "PaymentRequiredError",
    "ValidationError",
    "RateLimitError",
    "NetworkError",
    "__version__",
]
