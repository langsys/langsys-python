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

**Catalog snapshots (spec 8.2).**

- **SNAP-1, SNAP-3 — `python -m langsys.snapshot` exports the catalog for chosen locales and
  categories** into a checksummed file in the fleet's one snapshot format
  (`langsys-catalog-snapshot` v1), so it loads in every Langsys SDK. `Snapshot.load()` refuses a
  wrong format or version, a missing member, or a snapshot edited after export, naming which;
  the refresh is a new export.
- **`client.load_snapshot()` seeds a client from a snapshot** with no network call; what the
  snapshot lacks is fetched as usual. For framework integrations to call at startup. While the
  API can't be reached, `resolve_request_locale()` serves the snapshot's locales rather than
  falling back to the base locale.

**Legacy-key migration (spec 8.2).**

- **MIG-1..7 — `legacy_files=` lets a keyed app migrate without a codemod.** `translate()`
  resolves its argument as a key in the app's source-language file first: a hit registers the
  key's source text (never the key) under its namespace or `msgctxt`, and a miss is ordinary
  source text. Reads gettext `.po` and plain JSON; placeholders convert to `{name}`, gettext
  plurals to ICU, and anything that can't convert is kept verbatim with a warning. A `.mo` or an
  unsupported format is refused at configuration. `client.translate_legacy()` is the entry point a
  framework's own translation functions (Django's `gettext`, `ngettext`, `pgettext`,
  `{% blocktranslate %}`) delegate to: the same resolver as `translate()`, with a literal miss
  converted under that framework's syntax.

**Failed catalogs and unrenderable phrases (spec 8.2).**

- **CACHE-2 — a failed catalog fetch is remembered for a short window.** Lookups for that locale
  render source text without fetching again, for 3 seconds doubling to 5 minutes and reset on
  success. Every lookup used to repeat the failing request, so an 11-token page against a hung
  API waited the full timeout eleven times. Concurrent fetches for one locale share one request,
  and a response with `status: false` counts as a failure.
- **ICU-6 — a phrase the formatter can't render is rendered through branch selection, and
  warns.** An unsupported argument type, unbalanced braces, or a plural or select with no branch
  for the value used to put raw ICU syntax, or an empty string, on the page. Each construct now
  renders its chosen branch or its value, a missing value stays visible as `{name}`, and one
  warning per phrase and locale names the phrase and the error, whatever the log level.

**Registration shape and markers (spec 8.2).** Content registered from HTML now takes the
same shape in every Langsys SDK, so some phrases and content blocks derive ids they did not
before.

- **TOK-6 — full-page translation registers what it used to drop.** A leaf block's own
  attributes are part of it (`<p title="Tip">Hello</p>` is one block, `[Tip, Hello]`, where
  `Tip` was lost), and inline and void elements directly under a container — an `<img alt>`, a
  link, a `<select>` or `<textarea>` — are translated instead of skipped. A unit registers as a
  phrase only when its one token is its one text node, on the page path and in
  `translate_content_block` alike, so a single-sentence fragment is now a phrase.
- **MARK-3 — a bare `data-ls-contentblock` now declares a block**, as `true`, `1` and `yes` do;
  only `false` and `0` opt out, so `no` and `off` now read as ids. An element carrying another
  SDK's id renders that block's translation from the catalog.
- **MARK-4 — a marked element inside another block is its own unit**, excised from the outer
  block instead of folded into it, which changes the outer block's id.
- **MARK-2 — a `data-ls-phrase` element registers whole**, inline markup encoded as
  `{m0o}…{m0c}` tokens in the JS SDK's format, and its translation is rebuilt around the
  original elements.
- **TOK-2 — control characters U+0001–U+0008, U+000B, U+000C and U+000E–U+001F are removed**
  before whitespace collapses, from markup and from `translate()` keys, so an id no longer
  depends on the HTML parser's version for them.
- **Full-page translation no longer raises on a page containing a control character.** It
  rewrote every text node on the way out, and lxml refuses to write those characters back.

**Request locale and resolved pages (spec 8.2).**

- **SRV-6 — `client.resolve_request_locale()` picks a request's locale** from the URL, then a
  cookie or session value, then `Accept-Language`, then the project's base locale. Every
  candidate is validated against the project's locales, and the result names the `Vary` headers
  the response needs.
- **GATE-10 — `translate_page()` marks a translated page's root `data-ls-resolved`**, so a
  browser SDK on the page doesn't register already-translated text as source. Base-locale
  renders are not marked.

**Server messages (spec 8.2).**

- **MSG-1..8, MSG-11 — validation errors and system messages are translatable.**
  `client.server_message(code, template, params, field)` builds the fleet's entry
  (`{field, code, message, template, params}`) and registers a template the catalog lacks after
  the response. `resolve_server_messages()` finds entries in any response body, and
  `render_server_message()` renders one from the catalog, falling back to `message`.
  `python -m langsys.messages` lists and registers every template an app declares, refusing
  label markers and leftover framework placeholders, and exits non-zero on any message it cannot
  list. A marker filled with a phrase the catalog already holds warns once. Templates live under
  one category, `Errors` by default.

**Request scopes (spec 8.2).**

- **SRV-3 — misses discovered while serving a request are sent only after its response.**
  `langsys.begin_request_scope()` / `end_request_scope(scope)` / `with request_scope():` mark a
  request. A miss recorded inside is held from every flush — the debounce timer, an explicit
  `flush_pending()`, or another request's flush — until a request that recorded it has
  finished. Misses recorded outside any request keep the debounce, and the process-exit flush
  sends everything. Scopes follow the current thread or asyncio task.

**Canonicalization re-row (spec 8.0.1).**

- **TOK-1 — `<math>` content is no longer registered, and `<svg>` text now is, on every
  path.** MathML is notation, and translating an operator corrupts it; SVG `<text>` is words
  a reader sees. A standalone `<svg>` reached directly by full-page translation was
  previously skipped entirely. It is now translated in place, and its `<path>` geometry
  survives.
- **TOK-2 — the whitespace that collapses is now exactly JavaScript's set**, so content
  derives the same id here as in every other SDK. `U+FEFF` now collapses and is trimmed;
  `U+0085`, previously collapsed, is now kept, as are `U+180E`, `U+200B` and `U+2060`.
  Content containing these characters derives a different id than before. `U+001C`–`U+001F`
  are unchanged, pending a fleet ruling on control characters.
- **TOK-5 — `%name%` in captured markup now registers as `{name}`**, so
  `<p>Hello %name%</p>` and `<p>Hello {name}</p>` are one phrase with one id instead of two.
- **Attribute values, button values, page titles and meta descriptions containing extra
  whitespace, a no-break space or `%name%` now translate.** Attribute and button values were
  registered under their normalised text and looked up under the raw text; titles and metas
  were registered raw where the body registered normalised text. Either way the lookup missed
  forever and the phrase re-registered on every render. A title or meta with no translation
  yet is left exactly as authored.
- **REG-12 — `sync()` no longer re-registers text equal to a content block's id.** It counted
  a block's child phrases as known but not the block's own key, so it disagreed with
  `translate()` about what already existed and registered that text again on every call.

**Canonicalization and identity (spec v8).**

- **TOK-1** — `<script>`, `<style>`, `<noscript>` and `<template>` content is no longer
  turned into phrases by content-block extraction. It previously was, so a block
  containing any of them registered its code or stylesheet as translatable text and
  derived a content-block id no other SDK agreed with.
- **TOK-5** — `%name%` is now accepted anywhere `{name}` is, for authors whose template
  compiler treats `{` as an expression delimiter. An absent argument stays visible in
  either form, and `100% of 50%` is still prose.
- **MARK-1** — a rendered content block now carries `data-ls-contentblock` with its
  resolved id, so the identity is inspectable in devtools instead of having to be
  reasoned out from source. Blocks with no translation yet are stamped too.
- **MARK-2** — `data-ls-*` and `data-langsys-*` are both accepted when reading a host's
  identity or category. A page mixing them — a PHP-rendered page hosting a JS-rendered
  component — previously had the unfamiliar spelling's blocks re-split and registered a
  second time.
- TOK-2, TOK-3 and TOK-4 were already satisfied; they now have tests.
- **A parameter value could reach another parameter.** `%name%` was resolved on rendered
  output, so a value containing `%other%` pulled in a second argument — user-supplied
  data reaching arguments it was never given. The escape is now resolved on the template
  before rendering, so no substituted value is ever re-scanned. `{name}` never had this
  exposure.
- **Content already carrying a Langsys identity is no longer registered a second time.**
  A host marked `data-ls-phrase` or `data-langsys-phrase` — typically rendered by another
  SDK on the same page — was walked into and its text registered again under a new id. It
  is now excluded from extraction entirely, so it also stops shifting the id of the block
  containing it.
- Stamping a content block no longer rewrites the markup around it: `&nbsp;`, `<br/>`,
  `&eacute;` and unquoted attribute values survive verbatim, where previously a block
  with no translation yet came back re-serialised.
- Blocks rendered by full-page translation are stamped too, not only single-block ones.
- A `data-ls-contentblock` attribute now means one of three things, decided in a single
  place: a truthy value declares a subtree to be one block, an empty/`0`/`false` value or
  the bare attribute opts out and the content is discovered normally, and anything else
  is another SDK's id and is left alone. Two readers previously disagreed about the empty
  value, so `<div data-ls-contentblock>` — the natural boolean spelling — silently
  dropped its contents from discovery.
- Inside a single `translate_content_block` fragment, a nested block marker is folded
  into the enclosing block rather than becoming a block of its own. Full-page
  translation, which has somewhere for sub-blocks to live, still makes it one.

**Registration lane (wave 2).**

- **REG-2** — discovered phrases now send on a short debounce (~400ms, `debounce=`), so a
  burst from one render becomes one request instead of waiting on an explicit call.
- **REG-3** — the end-of-context flush is on by default (`auto_flush=True`). It is
  best-effort by nature: a shutdown hook does not run on an OOM kill or hard timeout, so
  `flush_pending()` remains the reliable path and framework wrappers should call it at the
  end of each request.
- **REG-8** — failed sends back off exponentially (3s → doubling → 5min ceiling) and reset
  on the first success. Previously a failing endpoint was retried as fast as it was asked,
  against a queue that only grew.
- **REG-11** — a phrase ending in `…` or `...` now warns, naming it. It is still
  registered unless the catalog already holds a longer phrase with the same prefix, which
  is the only signal that distinguishes upstream truncation from a legitimate `Loading…`.
- **REG-6 / REG-7** — adding the debounce introduced a second thread, so the send now
  snapshots its batch and removes only what it sent (a miss recorded mid-send is no longer
  dropped), and only one send runs at a time.
- **GATE-2** — a failure to reach the API while checking write capability is now treated
  as **unknown** rather than as "not write-enabled". Previously a transient blip during
  that check discarded every queued phrase permanently, reported the cause as a
  permissions problem, and armed no retry. The queue is now held and retried with backoff.
  An `ip_write` session, which re-checks on every flush, was the most exposed.
- A phrase discovered while a send was already in flight could be left queued with nothing
  scheduled to send it, until an unrelated discovery or process exit.
- `pending_phrases` / `pending_content_blocks` are safe to read while a background send
  mutates the queue.

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
