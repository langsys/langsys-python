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
client.flush_pending()               # send what has been discovered so far
```

**How discovered phrases actually get sent.** Three paths, and on a long-lived server
you should not rely on the first two alone:

| Path | Default | Notes |
|---|---|---|
| Debounce | on, `debounce=0.4` | A burst from one render coalesces into one request, sent from a short-lived **background timer thread**. Pass `debounce=0` to disable and send only on demand. |
| Process exit | on, `auto_flush=True` | Best-effort only — an `atexit` hook does not run on an OOM kill or a hard timeout. |
| `flush_pending()` | — | The reliable path, and the one a web framework should call at the end of each request. |

Registration is resilient by design: a failed send keeps its queue and backs off
exponentially rather than retrying into a failing endpoint, and a failure to reach the
API at all is never treated as "you may not write" — the queue is held, not discarded.
`flush_pending()` returns a result whose `success` is only ever `True` for work that
actually happened.

If you construct a client per short-lived script or worker run, the defaults are fine as
they are. If you hold one for the life of a server process, call `flush_pending()` at
your request boundary (and `reset_write_decision()` alongside it — write capability is
per-session and must not leak between requests).

**Request scopes (for framework integrations).** Registration is not the visitor's work, so
a phrase discovered while serving a request is sent only after that request's response is
out. Mark the request, and flush once the response has been sent:

```python
import langsys

scope = langsys.begin_request_scope()   # request start
...                                      # render: misses are tagged with this request
langsys.end_request_scope(scope)         # response sent
client.flush_pending()
```

or `with langsys.request_scope(): ...`. While a scope is open, no flush sends the misses it
recorded — not the debounce, not an explicit `flush_pending()`, not another request's flush.
Misses recorded outside any scope (a worker, a management command) keep the debounce. The
open scope follows the current thread or asyncio task, so concurrent requests stay separate,
and a client built part-way through a request joins it. A scope that is never ended holds
its misses until the process-exit flush. `end_request_scope` takes the handle explicitly,
so it can be called from a different task than the one that began the scope.

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
