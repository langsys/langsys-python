"""HTTP transport for the nova API.

Thin wrapper over ``httpx.Client`` that owns the request contract every SDK shares:

* auth header ``X-Authorization: <key>`` (raw key, no ``Bearer``),
* ``X-Langsys-Capabilities: icu`` so nova returns raw ICU MessageFormat strings
  (without it the server pre-flattens plurals to the CLDR ``other`` branch),
* JSON envelope parsing and status-code mapping onto typed exceptions.

Kept sync for phase 1; an async twin lands in phase 3 sharing this routing logic.
"""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

import httpx

from ._log import logger
from .exceptions import (
    ApiError,
    AuthenticationError,
    AuthorizationError,
    NetworkError,
    PaymentRequiredError,
    RateLimitError,
    ValidationError,
)

_USER_AGENT = "langsys-python/0.1.0"


class HttpClient:
    def __init__(self, api_url: str, api_key: str, *, timeout: float = 30.0) -> None:
        self._base = api_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
                "X-Authorization": api_key,
                "X-Langsys-Capabilities": "icu",
                "User-Agent": _USER_AGENT,
            },
        )

    # -- public verbs ---------------------------------------------------------

    def get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self._send("GET", path, params=params)

    def post(self, path: str, json: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self._send("POST", path, json=json)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internals ------------------------------------------------------------

    def _url(self, path: str) -> str:
        # ``path`` may contain already-encoded segments; only join, don't re-encode.
        return f"{self._base}/{path.lstrip('/')}"

    def _send(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        try:
            response = self._client.request(method, self._url(path), params=params, json=json)
        except httpx.HTTPError as exc:  # DNS, connect, timeout, …
            raise NetworkError(f"Could not reach the Langsys API: {exc}") from exc

        request_id = response.headers.get("X-Request-ID")
        logger.debug(
            "langsys %s %s -> %s (request_id=%s)", method, path, response.status_code, request_id
        )

        try:
            body: dict[str, Any] = response.json()
        except ValueError:
            body = {}

        if response.status_code >= 400:
            error = self._map_error(response.status_code, body, request_id)
            logger.warning("langsys %s %s failed: %s", method, path, error.message)
            raise error

        return body

    @staticmethod
    def _map_error(
        status: int, body: dict[str, Any], request_id: Optional[str]
    ) -> ApiError:
        message = body.get("error") or body.get("message") or f"HTTP {status}"
        if status == 401:
            return AuthenticationError(
                message, status_code=status, response=body, request_id=request_id
            )
        if status == 402:
            return PaymentRequiredError(
                message, status_code=status, response=body, request_id=request_id
            )
        if status == 403:
            return AuthorizationError(
                message, status_code=status, response=body, request_id=request_id
            )
        if status == 422:
            return ValidationError(
                message,
                errors=body.get("errors"),
                status_code=status,
                response=body,
                request_id=request_id,
            )
        if status == 429:
            return RateLimitError(
                message, status_code=status, response=body, request_id=request_id
            )
        return ApiError(message, status_code=status, response=body, request_id=request_id)


def encode_segment(value: str) -> str:
    """Percent-encode a single path segment (e.g. a project id or locale)."""
    return quote(value, safe="")
