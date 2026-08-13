"""Locale helpers — BCP-47 canonicalization and Accept-Language parsing.

``canonicalize_locale`` is the Python analog of the other SDKs' ``canonicalizeLocale``:
language lowercase, script Titlecase, region UPPERCASE (``en-us`` -> ``en-US``,
``zh-hant-tw`` -> ``zh-Hant-TW``), underscores normalized to hyphens. It degrades to a
best-effort manual casing rather than raising on odd input.
"""

from __future__ import annotations

from typing import Optional

import langcodes


def canonicalize_locale(locale: Optional[str]) -> str:
    """Return ``locale`` in canonical BCP-47 casing. Empty/None -> ``""``."""
    if not locale:
        return ""
    cleaned = locale.strip().replace("_", "-")
    if not cleaned:
        return ""
    try:
        return langcodes.standardize_tag(cleaned)
    except Exception:
        return _manual_canonicalize(cleaned)


def _manual_canonicalize(cleaned: str) -> str:
    parts = cleaned.split("-")
    out = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            out.append(part.title())  # script
        elif len(part) == 2 or (len(part) == 3 and part.isdigit()):
            out.append(part.upper())  # region
        else:
            out.append(part.lower())
    return "-".join(out)


def normalize_locale(locale: Optional[str]) -> str:
    """Lowercase, hyphenated form (``en_US`` -> ``en-us``). Some backends key on this."""
    if not locale:
        return ""
    return locale.strip().replace("_", "-").lower()


def parse_accept_language(header: Optional[str]) -> list[str]:
    """Parse an ``Accept-Language`` header into locales, most-preferred first.

    Honors ``;q=`` weights (default ``1``), drops ``*`` and out-of-range/invalid
    weights, and returns a stable order sorted by descending quality.
    """
    if not header:
        return []
    entries: list[tuple[float, int, str]] = []
    for index, raw in enumerate(header.split(",")):
        token = raw.strip()
        if not token or token.startswith("*"):
            continue
        locale, _, params = token.partition(";")
        locale = locale.strip()
        if not locale:
            continue
        quality = 1.0
        if params:
            key, _, value = params.strip().partition("=")
            if key.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    continue
        if not (0.0 < quality <= 1.0):
            continue
        # index keeps the original order stable among equal q-values.
        entries.append((quality, -index, locale))
    entries.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [locale for _, _, locale in entries]


def _lang_script(locale: str) -> str:
    """Return the CLDR-maximized ``language-Script`` for a locale (likely-subtags).

    e.g. ``en`` -> ``en-Latn``, ``zh-TW`` -> ``zh-Hant``. Empty on failure.
    """
    try:
        maximized = langcodes.Language.get(locale).maximize()
        return f"{maximized.language}-{maximized.script or ''}"
    except Exception:
        return ""


def find_best_locale_match(
    user_locales: list[str], supported: list[str]
) -> Optional[str]:
    """Best supported locale for a user's preference list, or ``None``.

    Two tiers, matching the JS SDK: (1) exact canonical match, (2) likely-subtags
    (so ``en`` matches ``en-US`` via ``en-Latn``, ``zh-TW`` matches ``zh-Hant-*``).
    Returns the supported locale in canonical form.
    """
    canonical = [canonicalize_locale(s) for s in supported]
    by_lower = {c.lower(): c for c in canonical}

    for user in user_locales:
        cu = canonicalize_locale(user)
        if cu.lower() in by_lower:
            return by_lower[cu.lower()]

    supported_ls = [(_lang_script(c), c) for c in canonical]
    for user in user_locales:
        uls = _lang_script(user)
        if not uls:
            continue
        for ls, code in supported_ls:
            if ls and ls.lower() == uls.lower():
                return code
    return None


def detect_preferred_locale(
    accept_language: Optional[str] = None,
    supported: Optional[list[str]] = None,
) -> Optional[str]:
    """Pick the best locale from an ``Accept-Language`` header.

    * With ``supported``: returns the best match (exact or likely-subtags), or
      ``None`` if nothing matches — so the caller can fall back to a default.
      (This is the corrected behaviour; some other SDKs return the first preference
      instead of ``None`` here.)
    * Without ``supported``: returns the top preference in canonical form, or ``None``
      when the header yields nothing.
    """
    preferences = parse_accept_language(accept_language)
    if not preferences:
        return None
    if supported:
        return find_best_locale_match(preferences, supported)
    return canonicalize_locale(preferences[0])
