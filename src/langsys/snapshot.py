"""Catalog snapshots (spec SNAP): the catalog for chosen locales and categories, exported into a
file an app can load without calling the API on its render path.

Export is a client-side filter of ``GET /translations/data`` by category (SNAP-1); there is no
export endpoint, and a snapshot carries exactly the entries the API returns for its categories.

A snapshot is a cache, never a source (SNAP-3). It carries a checksum of its contents, and loading
refuses one that no longer matches, so an edited snapshot is caught rather than served. The only
refresh is a new export:

    python -m langsys.snapshot --locale it-it --category UI --category Errors --out snapshot.json

The file format is the PHP SDK's (``langsys-catalog-snapshot``, version 1), checksum included, so
a snapshot exported by either SDK loads in both.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from .exceptions import ApiError, ConfigurationError, NetworkError
from .locale import normalize_locale

if TYPE_CHECKING:
    from .client import LangsysClient

__all__ = ["FORMAT", "VERSION", "Snapshot", "SnapshotError"]

FORMAT = "langsys-catalog-snapshot"
VERSION = 1
_FIELDS = ("project_id", "generated_at", "locales", "categories", "catalog")


class SnapshotError(ConfigurationError):
    """A snapshot that cannot be exported, or that must not be loaded."""


def _canonical(payload: dict[str, Any]) -> str:
    """The JSON the checksum is taken over, byte-identical to PHP's `json_encode` with unescaped
    slashes, unicode and line terminators. An empty map is `[]` there, so it is here too."""
    def php_shape(node: Any) -> Any:
        if isinstance(node, dict):
            return [] if not node else {k: php_shape(v) for k, v in node.items()}
        if isinstance(node, list):
            return [php_shape(v) for v in node]
        return node

    return json.dumps(php_shape(payload), ensure_ascii=False, separators=(",", ":"))


def _checksum(payload: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _read(source: Union[str, Path]) -> str:
    """A path's contents, or `source` itself when it is the JSON text."""
    if isinstance(source, Path):
        return source.read_text(encoding="utf-8")
    try:
        if Path(source).is_file():
            return Path(source).read_text(encoding="utf-8")
    except OSError:  # JSON text too long to be a file name
        pass
    return source


class Snapshot:
    """A loaded or freshly exported snapshot."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = {name: payload[name] for name in _FIELDS}

    @classmethod
    def export(
        cls, client: LangsysClient, locales: Sequence[str], categories: Sequence[str]
    ) -> Snapshot:
        """SNAP-1 - one ``GET /translations/data`` per locale, keeping only `categories`."""
        if not locales or not categories:
            raise SnapshotError("A snapshot names at least one locale and at least one category.")
        wanted = list(dict.fromkeys(str(c) for c in categories))
        chosen = list(dict.fromkeys(normalize_locale(loc) for loc in locales))
        catalog: dict[str, dict[str, Any]] = {}
        for locale in chosen:
            try:
                response = client._http.get(
                    "translations/data",
                    params={"project_id": client._config.project_id, "locale": locale},
                )
            except (NetworkError, ApiError) as exc:
                raise SnapshotError(f"The {locale} catalog could not be read: {exc}") from exc
            data = response.get("data")
            if response.get("status") is False or not isinstance(data, (dict, list)):
                raise SnapshotError(f"The {locale} catalog response carried no catalog.")
            data = data if isinstance(data, dict) else {}
            catalog[locale] = {c: data[c] for c in wanted if c in data}
        return cls({
            "project_id": client._config.project_id,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "locales": chosen,
            "categories": wanted,
            "catalog": catalog,
        })

    @classmethod
    def load(cls, source: Union[str, Path]) -> Snapshot:
        """SNAP-3 - load a snapshot, refusing one that was edited after export."""
        text = _read(source)
        try:
            document = json.loads(text)
        except ValueError as exc:
            raise SnapshotError("This is not a Langsys catalog snapshot.") from exc
        if not isinstance(document, dict) or document.get("format") != FORMAT:
            raise SnapshotError("This is not a Langsys catalog snapshot.")
        if document.get("version") != VERSION:
            raise SnapshotError("This snapshot has an unsupported version; export it again.")
        missing = [name for name in (*_FIELDS, "checksum") if name not in document]
        if missing:
            raise SnapshotError(f"This snapshot has no {missing[0]}; export it again.")
        payload = {name: document[name] for name in _FIELDS}
        if document["checksum"] != _checksum(payload):
            raise SnapshotError(
                "This snapshot was changed after it was exported. A snapshot is a cache of the "
                "catalog and is never edited: export it again."
            )
        return cls(payload)

    def catalog(self, locale: str) -> Optional[dict[str, Any]]:
        """``category -> entries`` for a locale, as the API returned them; None if not held."""
        held = self._payload["catalog"].get(normalize_locale(locale))
        return held if isinstance(held, dict) else ({} if held == [] else None)

    @property
    def locales(self) -> list[str]:
        return list(self._payload["locales"])

    @property
    def categories(self) -> list[str]:
        return list(self._payload["categories"])

    @property
    def project_id(self) -> str:
        return str(self._payload["project_id"])

    def to_json(self) -> str:
        document = {"format": FORMAT, "version": VERSION, **self._payload,
                    "checksum": _checksum(self._payload)}
        return json.dumps(document, ensure_ascii=False, indent=4) + "\n"

    def write_to(self, path: Union[str, Path]) -> Path:
        target = Path(path)
        target.write_text(self.to_json(), encoding="utf-8")
        return target


def main(argv: Optional[list[str]] = None) -> int:
    """`python -m langsys.snapshot --locale L --category C [--out FILE]` (LANGSYS_* credentials)."""
    parser = argparse.ArgumentParser(prog="python -m langsys.snapshot", description=main.__doc__)
    parser.add_argument("--locale", action="append", required=True)
    parser.add_argument("--category", action="append", required=True)
    parser.add_argument("--out", default="langsys-snapshot.json")
    args = parser.parse_args(argv)
    from .client import LangsysClient

    client = LangsysClient(auto_flush=False, debounce=0)
    snapshot = Snapshot.export(client, args.locale, args.category)
    print(snapshot.write_to(args.out))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
