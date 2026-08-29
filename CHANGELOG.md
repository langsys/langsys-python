# Changelog

All notable changes to the `langsys` Python SDK are documented here. This project follows
[Semantic Versioning](https://semver.org).

## 0.1.0 (unreleased)

### SDK Behaviour Spec conformance (v7)

Brought the SDK in line with the SDK Behaviour Spec. Nothing here has ever been published,
so none of it is a breaking change for an installed user — see `CONFORMANCE.md` for the
rule-by-rule status, the evidence tier behind each row, and the ranked gaps.

- **WIRE-4 — `translate()` no longer throws.** It fetched the catalog inline and did not
  catch, so an unreachable API served a 500 to every visitor on a page with a `t()` call.
  Every entry point now degrades to source text and logs. Paired obligation: **a failed
  catalog fetch registers nothing** — without a catalog a miss is indistinguishable from a
  hit, so the intuitive "everything is unknown" turns each outage into a write storm.
- **GATE-1/3/4/8 — the write decision now comes from the server's `write_enabled`**, read
  per response from either endpoint shape (inside `data` on authorize, envelope-level on
  translations), never from `key_type`. The decision is resolved at the send site and is
  stripped before anything is cached or retained, so it cannot outlive the call. Absence
  is treated as a version signal, never as permission, and never inferred for `ip_write`.
- **CID-1/2/3/4 — corrected the content-block `custom_id`.** It was
  `md5("|".join([category, *phrases]))`, which no other SDK computes, so blocks never
  resolved and re-registered on every render. Now the canonical
  `md5(json([category, tokens]))`, anchored byte-for-byte on the shared cross-SDK fixture.
  No-category hashes as `''` at the function and at every caller. Ids registered by older
  SDKs are still resolved on lookup — including the JS code-unit form — and verified
  against block content before being attached to; they are never emitted and never re-keyed.
- **ICU-1/3/4 — a missing `select`/`plural` argument now renders its `other` branch** rather
  than printing `{gender}` at the user, which is what a gendered or pluralised target locale
  routinely needs. `#` inside a recovered plural prints the argument name, never a plausible
  `0`. Recovery emits one debug notice per `(template, locale)`. Supplied arguments keep full
  CLDR selection — recovery rewrites only the missing nodes and adds no second renderer.
- **REG-9** — content blocks register in one batched POST per chunk instead of one POST each.
- **REG-10** — a skipped or failed write no longer returns a success-shaped result.
- **WIRE-3** — locales go on the wire lowercase and cache keys use the same form, so `en-US`
  and `en-us` are one entry rather than two fetches.
- **OBS-1** — one warning per process when a write-capable key type resolves to
  not-write-enabled, which is otherwise completely silent.
- `KeyType` gained `ip_write`; it was previously collapsed into `read`.

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
