"""The worlds of the clone rows in tests/test_request_locale.py: projects whose own target
locales the request-locale resolver validates against."""

INDEX = 5
RUNS = 6

_READ = {"read": {"project": "p1", "type": "read"}}

WORLDS = {
    "es-and-it": {"keys": _READ, "projects": {"p1": {"target_locales": ["it-it", "es-es"]}}},
    "it-only": {"keys": _READ, "projects": {"p1": {"target_locales": ["it-it"]}}},
}
