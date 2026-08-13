"""Registering phrases and content blocks (write-key only).

Builds ``translatable-items`` payloads, chunks them to the project's batch limit, and
POSTs. ``generate_custom_id`` is the canonical content-block hash — see the module note.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional, Sequence, Union

from .http import HttpClient

PhraseInput = Union[str, dict[str, Any]]


def generate_custom_id(category: Optional[str], phrases: Sequence[str]) -> str:
    """Deterministic id for a content block.

    ``md5`` of ``category`` and the phrases joined with ``|`` — the scheme the backend's
    stored content blocks actually use (verified against the live catalog), so a block
    registered by the server-side SDKs resolves to the same id here.
    """
    tokens = [category if category is not None else "", *phrases]
    return hashlib.md5("|".join(tokens).encode("utf-8")).hexdigest()  # noqa: S324 - id hash, not security


class Registrar:
    """Posts translatable items to nova. Caller is responsible for write-key gating."""

    def __init__(self, http: HttpClient, project_id: str, *, batch_limit: int = 200) -> None:
        self._http = http
        self._project_id = project_id
        self.batch_limit = batch_limit if batch_limit > 0 else 200

    def register_phrases(self, phrases: Sequence[PhraseInput]) -> list[dict[str, Any]]:
        items = [self._phrase_item(p) for p in phrases]
        return self._post_items(items)

    def register_content_block(
        self,
        content: str,
        phrases: Sequence[str],
        *,
        category: Optional[str] = None,
        custom_id: Optional[str] = None,
        label: Optional[str] = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "type": "content_block",
            "custom_id": custom_id or generate_custom_id(category, phrases),
            "content": content,
            "phrases": [{"phrase": p} for p in phrases],
        }
        if category is not None:
            item["category"] = category
        if label is not None:
            item["label"] = label
        responses = self._post_items([item])
        return responses[0] if responses else {"status": True}

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _phrase_item(phrase: PhraseInput) -> dict[str, Any]:
        if isinstance(phrase, str):
            return {"type": "phrase", "phrase": phrase, "category": None, "translatable": True}
        item = {
            "type": "phrase",
            "phrase": phrase["phrase"],
            "category": phrase.get("category"),
            "translatable": phrase.get("translatable", True),
        }
        return item

    def _post_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        responses: list[dict[str, Any]] = []
        for start in range(0, len(items), self.batch_limit):
            chunk = items[start : start + self.batch_limit]
            responses.append(
                self._http.post(
                    "translatable-items",
                    json={"project_id": self._project_id, "translatable_items": chunk},
                )
            )
        return responses
