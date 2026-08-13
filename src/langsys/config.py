"""Client configuration, resolved from explicit arguments then ``LANGSYS_*`` env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from .exceptions import ConfigurationError

DEFAULT_API_URL = "https://api.langsys.dev/api"
DEFAULT_CACHE_TTL = 3600


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


@dataclass
class Config:
    """Resolved configuration for a :class:`~langsys.client.LangsysClient`.

    Explicit arguments win; anything left as ``None`` falls back to the matching
    ``LANGSYS_*`` environment variable, then to the documented default.
    """

    api_key: str
    project_id: str
    api_url: str = DEFAULT_API_URL
    base_locale: Optional[str] = None
    cache_ttl: int = DEFAULT_CACHE_TTL
    timeout: float = 30.0
    debug: bool = False
    #: Extra transport options are kept for forward-compat; unused in phase 1.
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def resolve(
        cls,
        api_key: Optional[str] = None,
        project_id: Optional[str] = None,
        *,
        api_url: Optional[str] = None,
        base_locale: Optional[str] = None,
        cache_ttl: Optional[int] = None,
        timeout: Optional[float] = None,
        debug: bool = False,
    ) -> Config:
        api_key = api_key or _env("LANGSYS_API_KEY")
        project_id = project_id or _env("LANGSYS_PROJECT_ID")
        if not api_key:
            raise ConfigurationError(
                "Langsys: missing API key (pass api_key= or set LANGSYS_API_KEY)."
            )
        if not project_id:
            raise ConfigurationError(
                "Langsys: missing project id (pass project_id= or set LANGSYS_PROJECT_ID)."
            )

        resolved_url = (api_url or _env("LANGSYS_API_URL", DEFAULT_API_URL)) or DEFAULT_API_URL
        if cache_ttl is not None:
            resolved_ttl = cache_ttl
        else:
            resolved_ttl = int(_env("LANGSYS_CACHE_TTL", "") or DEFAULT_CACHE_TTL)

        return cls(
            api_key=api_key,
            project_id=project_id,
            api_url=resolved_url.rstrip("/"),
            base_locale=base_locale or _env("LANGSYS_BASE_LOCALE"),
            cache_ttl=resolved_ttl,
            timeout=timeout if timeout is not None else 30.0,
            debug=debug,
        )
