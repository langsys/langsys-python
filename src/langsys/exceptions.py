"""Typed exceptions raised by the SDK.

The HTTP layer maps nova's error envelopes and status codes onto these, so callers
never see raw ``httpx`` types. All inherit from :class:`LangsysError`.
"""

from __future__ import annotations

from typing import Any, Optional


class LangsysError(Exception):
    """Base class for every error raised by the SDK."""

    def __init__(self, message: str, *, response: Optional[dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        #: The parsed JSON body from nova, when the error came from an API response.
        self.response = response


class ConfigurationError(LangsysError):
    """Missing or invalid client configuration (e.g. no API key or project id)."""


class ApiError(LangsysError):
    """A non-2xx response that isn't covered by a more specific subclass."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        response: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(message, response=response)
        #: HTTP status code.
        self.status_code = status_code
        #: nova's ``X-Request-ID`` header, if present (useful for support tickets).
        self.request_id = request_id


class AuthenticationError(ApiError):
    """401 — the API key is missing, invalid, or inactive."""


class AuthorizationError(ApiError):
    """403 — the key is valid but not allowed (e.g. a read key attempting a write)."""


class PaymentRequiredError(ApiError):
    """402 — usage/units exhausted or the organization's subscription is suspended."""


class ValidationError(ApiError):
    """422 — the request failed server-side validation (e.g. batch too large)."""

    def __init__(
        self,
        message: str,
        *,
        errors: Optional[dict[str, Any]] = None,
        status_code: int = 422,
        response: Optional[dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            message, status_code=status_code, response=response, request_id=request_id
        )
        #: Field-level validation errors, when nova provides them.
        self.errors = errors or {}


class RateLimitError(ApiError):
    """429 — throttled (global rate limit or the duplicate-request guard)."""


class NetworkError(LangsysError):
    """The request never reached nova (DNS, connection, timeout)."""
