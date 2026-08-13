# Changelog

All notable changes to the `langsys` Python SDK are documented here. This project follows
[Semantic Versioning](https://semver.org).

## 0.1.0 (unreleased)

Initial release of the Python base SDK.

### Added

- `LangsysClient`: `authorize` / `key_type` / `can_write` / `project`, `get_translations`,
  `translate` (alias `t`), `set_locale` / `locale`, and `clear_cache`.
- Interpolation: `{name}` slots with locale-aware CLDR number/date formatting (Babel) and a
  pure-Python ICU MessageFormat parser (plural / select / selectordinal / number / date / time)
  — no system libicu dependency. Untranslated phrases fall back to the source text.
- Locale helpers: `canonicalize_locale` (BCP-47), `normalize_locale`, `parse_accept_language`,
  and `detect_preferred_locale` (exact + likely-subtags; returns `None` on no match).
- Write path: `register_phrases`, `register_content_block`, `sync`, a phrase/content-block
  discovery queue with `flush_pending` (and opt-in `auto_flush` at exit). Write-key gated.
- Reference-data utilities: `countries`, `dial_codes`, `currencies`, `locales`, `locales_flat`,
  `locales_data`, `country_name`, `currency_name`, `locale_name`. `refresh`.
- Caching: `MemoryCache`, `FileCache`, `NullCache`, and `RedisCache` (`pip install langsys[redis]`).
- Server-side HTML translation (`pip install langsys[html]`): `translate_content_block` and
  `translate_page` (head + body, content-block classification, category rules,
  `data-langsys-category` / `data-langsys-contentblock`, selector categories, `translate="no"`),
  plus translatable-attribute configuration.
- Typed exceptions mapping the API's 401 / 402 / 403 / 422 / 429 responses; `py.typed`.
- A framework-agnostic core (`Signal` / `LocaleSource`) designed to be wrapped by future
  Django / Flask / FastAPI integrations.
