# Langsys — Python SDK

The official Python SDK for [Langsys](https://langsys.dev) — realtime, continuous
translations. **The phrase in your code is the lookup key *and* the base-language
default** — no keys file, no extraction step. Untranslated phrases render as the source
phrase, so nothing ever breaks.

This is the **base SDK for Python**, framework-agnostic by design: Django / Flask /
FastAPI integrations are thin wrappers built on top of it.

> Status: **feature-complete.** Translation with ICU/CLDR interpolation, the write path
> (register / flush / sync), reference-data utilities, `detect_preferred_locale`, caching
> (memory / file / Redis), and server-side HTML translation (content blocks + full pages).

## Install

```bash
pip install langsys
```

Requires Python 3.9+. Depends on `httpx`, `babel`, and `langcodes`.

## Quick start

```python
from langsys import LangsysClient

client = LangsysClient(api_key="…", project_id="…")   # or env LANGSYS_API_KEY / LANGSYS_PROJECT_ID
client.set_locale("es-ES")

client.translate("Save")                       # -> "Guardar"
client.translate("Save", category="UI")        # category is part of the key
client.translate(
    "Hello, {name}! You have {count, plural, one {# new message} other {# new messages}}.",
    category="Greetings",
    params={"name": "Sarah", "count": 3},
)
```

### Configuration

Pass arguments or set environment variables:

| Argument | Env var | Default |
|---|---|---|
| `api_key` | `LANGSYS_API_KEY` | — (required) |
| `project_id` | `LANGSYS_PROJECT_ID` | — (required) |
| `api_url` | `LANGSYS_API_URL` | `https://api.langsys.dev/api` |
| `base_locale` | `LANGSYS_BASE_LOCALE` | project's base locale |
| `cache_ttl` | `LANGSYS_CACHE_TTL` | `3600` |

A **read** key fetches translations (safe to ship); a **write** key also auto-registers
new phrases as they're rendered (later phase).

### Interpolation

Params accept `str`, `int`, `float`, `bool`, and `datetime`. Numbers and dates are
CLDR-formatted for the loaded locale (pass a string to opt out). ICU MessageFormat —
`{n, plural, …}`, `select`, `selectordinal`, `{n, number|date|time}` — is supported via a
pure-Python parser backed by Babel's plural rules (no system libicu required). An unknown
placeholder is left visible (`{name}`) rather than blanked.

### Reference data & locale detection

```python
client.countries("es-ES")            # -> [Country(code="DE", label="Alemania"), ...]
client.currencies("es-ES")           # -> [Currency(code="USD", name="dólar estadounidense", ...)]
client.dial_codes("es-ES")
client.locale_name("es-ES", in_locale="en-US")   # -> "Spanish (Spain)"
client.detect_preferred_locale("fr,es;q=0.8", ["en-US", "es-ES"])  # -> "es-ES" (or None)
```

### Registering phrases (write key)

```python
client.register_phrases([{"phrase": "Save", "category": "UI"}])
client.sync(local_phrases, locale="en-US")   # register only what's new, then refetch

# Phrases seen while rendering are queued; flush them (no-op on a read key):
client.flush_pending()               # or pass auto_flush=True to flush at exit
```

### Caching

The catalog is cached (in-memory + a persistent tier). Choose the persistent backend:

```python
from langsys import FileCache, MemoryCache, NullCache
from langsys.cache.redis import RedisCache            # pip install langsys[redis]

client = LangsysClient(..., cache=RedisCache(host="localhost"))
```

### Server-side HTML translation

Translate whole blocks of HTML or entire pages (requires `pip install langsys[html]`):

```python
# A content block — registered and translated as one unit:
client.translate_content_block("<ul><li>Home</li><li>About</li></ul>", category="Nav")

# A full page — walks <head> (title, meta, OpenGraph/Twitter, <html lang>) and <body>,
# translating each block; honors data-langsys-category / data-langsys-contentblock and
# translate="no"/data-notrans:
client.translate_page(full_html, category="UI", selector_categories={".site-nav": "Nav"})
```

Translatable attributes (`placeholder`, `alt`, `title`, `aria-label`, …) are harvested
automatically; customize the list with `client.set_translatable_attributes(...)` /
`add_translatable_attributes(...)` / `reset_translatable_attributes()`.

### Errors

Every failure raises a typed exception (subclass of `LangsysError`); HTTP status codes are
mapped so you never see raw transport errors:

```python
from langsys import (
    AuthenticationError,   # 401 — bad/inactive key
    AuthorizationError,    # 403 — e.g. a read key attempting a write
    PaymentRequiredError,  # 402 — usage/units exhausted or subscription suspended
    ValidationError,       # 422 — request rejected (e.g. batch too large)
    RateLimitError,        # 429 — throttled
    ApiError,              # any other non-2xx (has .status_code, .request_id)
    NetworkError,          # the request never reached the backend
    LangsysError,          # base class for all of the above
)

try:
    client.register_phrases([...])
except AuthorizationError:
    ...  # needs a write key
except RateLimitError:
    ...  # back off and retry
```

`translate()` itself never raises for missing translations — it falls back to the source phrase.

### Bring your own locale source

A framework wrapper can drive the locale from its own request-scoped store by passing
`locale_source=` (anything with `get()` and `subscribe()`); the SDK only reads it.

## Development

```bash
python -m virtualenv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -m "not integration"     # unit tests
ruff check . && mypy src
```

## License

MIT
